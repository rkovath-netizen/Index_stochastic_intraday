"""
STRATEGY LOGIC: Stochastic Index Intraday Momentum
--------------------------------------------------
INSTRUMENTS: Nifty and Sensex Monthly Futures.
OPTIONS LEGS: 
- Long Signal: Bull Put Spread (Sell ATM PE, Buy OTM2 PE) 
- Short Signal: Bear Call Spread (Sell ATM CE, Buy OTM2 CE)
TIMEFRAMES: LTF/HTF combinations (3m/15m, 5m/30m, 10m/60m).

ENTRY CONDITIONS:
1. HTF Bias: Close > EMA 25 AND Stochastic %K > %D AND OBV > SMA(OBV, 20). (Reverse for short).
2. LTF Execution: Close > Open (Close < Open for short) AND Stochastic %K crosses %D on the PREVIOUS candle AND Volume > previous 2 candles.

EXIT CONDITIONS:
- Stop Loss: 15% of the ATM option entry price.
- Take Profit: 30% of the net credit received.
"""

import os
import requests
import pandas as pd
import pandas_ta as ta
import datetime
from datetime import timedelta

# ==========================================
# CONFIGURATION
# ==========================================
TOKEN_FILE = "upstox_analytics_token.txt"
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
def get_access_token(filepath):
    """Reads the API token from a file."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"{filepath} not found.")
    with open(filepath, 'r') as f:
        return f.read().strip()

def fetch_historical_1m_data(instrument_key, token, days=30):
    """Fetches historical 1-minute data from Upstox. Reusable for any strategy."""
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    to_date = datetime.datetime.now().strftime('%Y-%m-%d')
    from_date = (datetime.datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/1minute/{to_date}/{from_date}"
    response = requests.get(url, headers=headers)
    
    if response.status_code != 200:
        print(f"API Error ({instrument_key}): {response.text}")
        return pd.DataFrame()
        
    data = response.json().get('data', {}).get('candles', [])
    if not data:
        return pd.DataFrame()
        
    df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    df = df.sort_index()
    
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col])
        
    return df

# ==========================================
# REUSABLE MODULE 2: Timeframe Management
# ==========================================
def resample_timeframes(df_1m, ltf_interval, htf_interval):
    """Resamples base 1-minute data into desired LTF and HTF dataframes."""
    agg_dict = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
    
    ltf_df = df_1m.resample(ltf_interval).agg(agg_dict).dropna()
    htf_df = df_1m.resample(htf_interval).agg(agg_dict).dropna()
    
    return ltf_df, htf_df

# ==========================================
# REUSABLE MODULE 3: Signal Generation
# ==========================================
def calculate_strategy_indicators(ltf, htf):
    """Calculates strategy-specific indicators."""
    # HTF Indicators
    htf['ema_25'] = ta.ema(htf['close'], length=25)
    stoch_htf = ta.stoch(htf['high'], htf['low'], htf['close'], k=14, d=3, smooth_k=3)
    htf = htf.join(stoch_htf)
    htf['obv'] = ta.obv(htf['close'], htf['volume'])
    htf['obv_sma_20'] = ta.sma(htf['obv'], length=20)
    
    # Standardize column names for HTF
    htf.rename(columns={col: 'htf_stoch_k' for col in htf.columns if 'STOCHk' in col}, inplace=True)
    htf.rename(columns={col: 'htf_stoch_d' for col in htf.columns if 'STOCHd' in col}, inplace=True)

    # LTF Indicators
    stoch_ltf = ta.stoch(ltf['high'], ltf['low'], ltf['close'], k=14, d=3, smooth_k=3)
    ltf = ltf.join(stoch_ltf)
    ltf.rename(columns={col: 'ltf_stoch_k' for col in ltf.columns if 'STOCHk' in col}, inplace=True)
    ltf.rename(columns={col: 'ltf_stoch_d' for col in ltf.columns if 'STOCHd' in col}, inplace=True)
    
    ltf['stoch_cross_up'] = (ltf['ltf_stoch_k'] > ltf['ltf_stoch_d']) & (ltf['ltf_stoch_k'].shift(1) <= ltf['ltf_stoch_d'].shift(1))
    ltf['stoch_cross_down'] = (ltf['ltf_stoch_k'] < ltf['ltf_stoch_d']) & (ltf['ltf_stoch_k'].shift(1) >= ltf['ltf_stoch_d'].shift(1))
    
    return ltf, htf

def generate_signals(ltf, htf):
    """Aligns timeframes and evaluates entry logic."""
    # Align HTF to LTF using forward fill to prevent lookahead bias
    htf_aligned = htf[['ema_25', 'htf_stoch_k', 'htf_stoch_d', 'obv', 'obv_sma_20']].reindex(ltf.index, method='ffill')
    df = ltf.join(htf_aligned)
    
    # HTF Conditions
    df['htf_long_bias'] = (df['close'] > df['ema_25']) & (df['htf_stoch_k'] > df['htf_stoch_d']) & (df['obv'] > df['obv_sma_20'])
    df['htf_short_bias'] = (df['close'] < df['ema_25']) & (df['htf_stoch_k'] < df['htf_stoch_d']) & (df['obv'] < df['obv_sma_20'])
    
    # LTF Conditions
    df['vol_surge'] = (df['volume'] > df['volume'].shift(1)) & (df['volume'] > df['volume'].shift(2))
    
    df['long_signal'] = (df['close'] > df['open']) & df['stoch_cross_up'].shift(1) & df['vol_surge'] & df['htf_long_bias']
    df['short_signal'] = (df['close'] < df['open']) & df['stoch_cross_down'].shift(1) & df['vol_surge'] & df['htf_short_bias']
    
    return df.dropna()

# ==========================================
# REUSABLE MODULE 4: Trade Simulation & Metrics
# ==========================================
def simulate_trades(df, symbol, ltf_str, htf_str):
    """
    Simulates option spreads based on futures signals.
    Note: Exact Option SL/TP requires historical option ticks. 
    This scaffold establishes the trade framework and placeholders for the CSV.
    """
    trades = []
    
    for idx, row in df.iterrows():
        if row['long_signal']:
            trades.append({
                'Entry_Time': idx,
                'Symbol': symbol,
                'TF_Combo': f"{ltf_str}/{htf_str}",
                'Trade_Type': 'Bull Put Spread',
                'Future_Entry_Price': row['close'],
                'ATM_Strike': round(row['close'] / 50) * 50 if 'NIFTY' in symbol else round(row['close'] / 100) * 100,
                'Exit_Time': idx + timedelta(minutes=int(ltf_str.replace('min',''))*3), # Mock exit time for testing
                'Net_Credit_Received': 0, # Requires Option Data Map
                'PnL': 0,                 # Requires Option Data Map 
                'PnL_Pct': 0,             # Requires Option Data Map
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
            
    return pd.DataFrame(trades)

def calculate_portfolio_metrics(trades_df):
    """Calculates win rate, profit factors, and aggregate counts."""
    if trades_df.empty:
        return pd.DataFrame()
        
    metrics = []
    grouped = trades_df.groupby(['Symbol', 'TF_Combo'])
    
    for (symbol, tf), group in grouped:
        total_trades = len(group)
        # Mock logic to show how win rate maps out once Options PnL is active
        # win_trades = len(group[group['PnL'] > 0])
        # loss_trades = len(group[group['PnL'] < 0])
        
        metrics.append({
            'Symbol': symbol,
            'Timeframes': tf,
            'Total_Trades': total_trades,
            'Profitable_Trades': 0,  # Map to actual PnL logic
            'Losing_Trades': 0,      # Map to actual PnL logic
            'Win_Rate_%': 0          # Map to actual PnL logic
        })
        
    return pd.DataFrame(metrics)

# ==========================================
# MAIN EXECUTION
# ==========================================
def write_csv_with_header(df, filename, header_text):
    """Writes a DataFrame to CSV with a commented strategy header."""
    with open(filename, 'w') as f:
        for line in header_text.strip().split('\n'):
            f.write(f"# {line}\n")
        df.to_csv(f, index=False)

def main():
    token = get_access_token(TOKEN_FILE)
    timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    
    all_trades = pd.DataFrame()
    
    for symbol, key in INSTRUMENTS.items():
        print(f"Fetching base 1-minute data for {symbol}...")
        df_1m = fetch_historical_1m_data(key, token, days=30)
        
        if df_1m.empty:
            continue
            
        for ltf_str, htf_str in TIMEFRAME_COMBOS:
            print(f"Processing Strategy for {symbol} -> {ltf_str}/{htf_str}...")
            ltf_df, htf_df = resample_timeframes(df_1m, ltf_str, htf_str)
            ltf_df, htf_df = calculate_strategy_indicators(ltf_df, htf_df)
            signals_df = generate_signals(ltf_df, htf_df)
            
            combo_trades = simulate_trades(signals_df, symbol, ltf_str, htf_str)
            if not combo_trades.empty:
                all_trades = pd.concat([all_trades, combo_trades], ignore_index=True)

    if not all_trades.empty:
        log_file = f"{STRATEGY_NAME}_tradelog_{timestamp_str}.csv"
        metrics_file = f"{STRATEGY_NAME}_metrics_{timestamp_str}.csv"
        
        # Add strategy documentation to the top of the CSV file
        strategy_doc = __doc__ 
        
        write_csv_with_header(all_trades, log_file, strategy_doc)
        
        metrics_df = calculate_portfolio_metrics(all_trades)
        write_csv_with_header(metrics_df, metrics_file, strategy_doc)
        
        print(f"\nBacktest completed successfully.")
        print(f"Detailed Trade Log saved to: {log_file}")
        print(f"Summary Metrics saved to: {metrics_file}")
    else:
        print("No trades generated across any timeframe combinations.")

if __name__ == "__main__":
    main()
