"""
STRATEGY LOGIC: Stochastic Index Intraday Momentum
--------------------------------------------------
Includes debug printing for cloud terminal monitoring.
"""

import os
import requests
import pandas as pd
import pandas_ta as ta
import datetime
from datetime import timedelta
import traceback

# ==========================================
# CONFIGURATION
# ==========================================
STRATEGY_NAME = "stochastic_index_intraday_momentum"
INSTRUMENTS = {
    "NIFTY_FUT": "NSE_FO|NIFTY24AUGFUT", 
    "SENSEX_FUT": "BSE_FO|SENSEX24AUGFUT"
}
TIMEFRAME_COMBOS = [
    ('3min', '15min'),
    ('5min', '30min'),
    ('10min', '60min')
]

# ==========================================
# REUSABLE MODULE 1: Data Fetching
# ==========================================
def fetch_historical_1m_data(instrument_key, token, days=30):
    """Fetches historical 1-minute data from Upstox with deep debug checks."""
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    to_date = datetime.datetime.now().strftime('%Y-%m-%d')
    from_date = (datetime.datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/1minute/{to_date}/{from_date}"
    print(f"[ENGINE DEBUG] Fetching URL: https://api.upstox.com/v2/historical-candle/{instrument_key}/1minute/...")
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"[ENGINE DEBUG] API Status Code: {response.status_code}")
        
        if response.status_code != 200:
            print(f"[ENGINE ERROR] API returned non-200 status: {response.text}")
            return pd.DataFrame()
            
        json_data = response.json()
        
        # Safely extract data
        if 'data' not in json_data or 'candles' not in json_data['data']:
            print(f"[ENGINE ERROR] Unexpected JSON structure: {str(json_data)[:200]}")
            return pd.DataFrame()
            
        data = json_data['data']['candles']
        if not data:
            print("[ENGINE WARNING] API returned an empty 'candles' list (Market might be closed or key is expired).")
            return pd.DataFrame()
            
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df = df.sort_index()
        
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col])
            
        print(f"[ENGINE DEBUG] Successfully parsed dataframe with shape {df.shape}")
        return df
        
    except Exception as e:
        print(f"[ENGINE CRITICAL] Crash during API fetch/parsing: {e}")
        traceback.print_exc()
        return None

# ==========================================
# REUSABLE MODULE 2: Timeframe Management
# ==========================================
def resample_timeframes(df_1m, ltf_interval, htf_interval):
    """Resamples base 1-minute data."""
    try:
        agg_dict = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
        ltf_df = df_1m.resample(ltf_interval).agg(agg_dict).dropna()
        htf_df = df_1m.resample(htf_interval).agg(agg_dict).dropna()
        return ltf_df, htf_df
    except Exception as e:
        print(f"[ENGINE ERROR] Failed to resample timeframes: {e}")
        raise e

# ==========================================
# REUSABLE MODULE 3: Signal Generation
# ==========================================
def calculate_strategy_indicators(ltf, htf):
    """Calculates strategy-specific indicators."""
    try:
        # HTF Indicators
        htf['ema_25'] = ta.ema(htf['close'], length=25)
        stoch_htf = ta.stoch(htf['high'], htf['low'], htf['close'], k=14, d=3, smooth_k=3)
        if stoch_htf is not None:
            htf = htf.join(stoch_htf)
        else:
            print("[ENGINE WARNING] Pandas-TA returned None for HTF Stochastic.")
            
        htf['obv'] = ta.obv(htf['close'], htf['volume'])
        htf['obv_sma_20'] = ta.sma(htf['obv'], length=20)
        
        # Standardize names
        htf.rename(columns={col: 'htf_stoch_k' for col in htf.columns if 'STOCHk' in col}, inplace=True)
        htf.rename(columns={col: 'htf_stoch_d' for col in htf.columns if 'STOCHd' in col}, inplace=True)

        # LTF Indicators
        stoch_ltf = ta.stoch(ltf['high'], ltf['low'], ltf['close'], k=14, d=3, smooth_k=3)
        if stoch_ltf is not None:
            ltf = ltf.join(stoch_ltf)
        
        ltf.rename(columns={col: 'ltf_stoch_k' for col in ltf.columns if 'STOCHk' in col}, inplace=True)
        ltf.rename(columns={col: 'ltf_stoch_d' for col in ltf.columns if 'STOCHd' in col}, inplace=True)
        
        # Only calculate cross if columns exist
        if 'ltf_stoch_k' in ltf.columns and 'ltf_stoch_d' in ltf.columns:
            ltf['stoch_cross_up'] = (ltf['ltf_stoch_k'] > ltf['ltf_stoch_d']) & (ltf['ltf_stoch_k'].shift(1) <= ltf['ltf_stoch_d'].shift(1))
            ltf['stoch_cross_down'] = (ltf['ltf_stoch_k'] < ltf['ltf_stoch_d']) & (ltf['ltf_stoch_k'].shift(1) >= ltf['ltf_stoch_d'].shift(1))
        else:
            print("[ENGINE ERROR] Stochastic columns missing on LTF timeframe. Cannot compute crossover.")
            
        return ltf, htf
    except Exception as e:
        print(f"[ENGINE ERROR] Indicator calculation failed: {e}")
        raise e

def generate_signals(ltf, htf):
    """Aligns timeframes and evaluates entry logic."""
    try:
        required_htf_cols = ['ema_25', 'htf_stoch_k', 'htf_stoch_d', 'obv', 'obv_sma_20']
        missing = [c for c in required_htf_cols if c not in htf.columns]
        if missing:
            print(f"[ENGINE WARNING] Missing HTF columns before merging: {missing}")
            
        htf_aligned = htf[required_htf_cols].reindex(ltf.index, method='ffill')
        df = ltf.join(htf_aligned)
        
        # Safety fill for NaN values before comparing
        df.fillna(0, inplace=True)
        
        df['htf_long_bias'] = (df['close'] > df['ema_25']) & (df['htf_stoch_k'] > df['htf_stoch_d']) & (df['obv'] > df['obv_sma_20'])
        df['htf_short_bias'] = (df['close'] < df['ema_25']) & (df['htf_stoch_k'] < df['htf_stoch_d']) & (df['obv'] < df['obv_sma_20'])
        
        df['vol_surge'] = (df['volume'] > df['volume'].shift(1)) & (df['volume'] > df['volume'].shift(2))
        
        # Check if cross columns exist before evaluating signal
        if 'stoch_cross_up' in df.columns and 'stoch_cross_down' in df.columns:
            df['long_signal'] = (df['close'] > df['open']) & df['stoch_cross_up'].shift(1).fillna(False) & df['vol_surge'] & df['htf_long_bias']
            df['short_signal'] = (df['close'] < df['open']) & df['stoch_cross_down'].shift(1).fillna(False) & df['vol_surge'] & df['htf_short_bias']
        else:
            df['long_signal'] = False
            df['short_signal'] = False
            
        return df.dropna()
    except Exception as e:
        print(f"[ENGINE ERROR] Signal generation failed: {e}")
        raise e

# ==========================================
# REUSABLE MODULE 4: Trade Simulation & Metrics
# ==========================================
def simulate_trades(df, symbol, ltf_str, htf_str):
    """Simulates option spreads based on futures signals."""
    trades = []
    
    if 'long_signal' not in df.columns or 'short_signal' not in df.columns:
        print("[ENGINE DEBUG] No signal columns found in dataframe.")
        return pd.DataFrame(trades)
        
    for idx, row in df.iterrows():
        if row['long_signal']:
            trades.append({
                'Entry_Time': idx,
                'Symbol': symbol,
                'TF_Combo': f"{ltf_str}/{htf_str}",
                'Trade_Type': 'Bull Put Spread',
                'Future_Entry_Price': row['close'],
                'ATM_Strike': round(row['close'] / 50) * 50 if 'NIFTY' in symbol else round(row['close'] / 100) * 100,
                'Exit_Time': idx + timedelta(minutes=int(ltf_str.replace('min',''))*3),
                'Net_Credit_Received': 0, 
                'PnL': 0,                 
                'PnL_Pct': 0,             
                'Result': 'Pending_Data'
            })
        elif row['short_signal']:
            trades.append({
                'Entry_Time': idx,
                'Symbol': symbol,
                'TF_Combo': f"{ltf_str}/{htf_str}",
                'Trade_Type': 'Bear Call Spread',
                'Future_Entry_Price': row['close'],
                'ATM_Strike': round(row['close'] / 50) * 50 if 'NIFTY' in symbol else round(row['close'] / 100) * 100,
                'Exit_Time': idx + timedelta(minutes=int(ltf_str.replace('min',''))*3),
                'Net_Credit_Received': 0,
                'PnL': 0,
                'PnL_Pct': 0,
                'Result': 'Pending_Data'
            })
            
    print(f"[ENGINE DEBUG] Simulation loop finished. Captured {len(trades)} trades.")
    return pd.DataFrame(trades)

def calculate_portfolio_metrics(trades_df):
    """Calculates win rate, profit factors, and aggregate counts."""
    if trades_df.empty:
        return pd.DataFrame()
        
    metrics = []
    grouped = trades_df.groupby(['Symbol', 'TF_Combo'])
    
    for (symbol, tf), group in grouped:
        metrics.append({
            'Symbol': symbol,
            'Timeframes': tf,
            'Total_Trades': len(group),
            'Profitable_Trades': 0,  
            'Losing_Trades': 0,      
            'Win_Rate_%': 0          
        })
        
    return pd.DataFrame(metrics)
