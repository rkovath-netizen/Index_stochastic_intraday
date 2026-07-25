"""
STRATEGY: Stochastic Index Intraday Momentum
--------------------------------------------------
ARCHITECTURE UPDATES:
- Aligns weekly option expiries with monthly future expiries.
- Dynamically resolves Base Index -> Future Key -> Option Keys.
- Fetches Option OHLC data at the exact signal minute to calculate the spread entry.
"""

import os
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
import urllib.parse
import gzip
import csv
import io
import time
import re

# ==========================================
# MODULE 1: CONSTANTS & CONFIGURATION
# ==========================================
TIMEFRAME_COMBOS = [
    ('3min', '15min'),
    ('5min', '30min'),
    ('10min', '60min')
]

INDEX_CONFIG = {
    "NIFTY": {"underlying": "NSE_INDEX|Nifty 50", "step": 50, "segment": "NSE_FO"},
    "SENSEX": {"underlying": "BSE_INDEX|SENSEX", "step": 100, "segment": "BSE_FO"}
}

def robust_api_get(url, headers, max_retries=3, params=None):
    """Handles rate limits and connection drops gracefully.[span_3](start_span)[span_3](end_span)"""
    for attempt in range(max_retries):
        res = requests.get(url, headers=headers, params=params)
        if res.status_code == 200: return res
        elif res.status_code == 429: time.sleep(2 ** attempt) 
        else: time.sleep(1)
    return res

# ==========================================
# MODULE 2: EXPIRY & CONTRACT RESOLUTION
# ==========================================
def get_closest_expiry(symbol, trade_date_str, token, require_monthly=False):
    """
    Fetches all valid expiries (live and expired) and finds the closest one to the trade date.
    Index Options use Weekly (require_monthly=False). Index Futures use Monthly (require_monthly=True).[span_4](start_span)[span_4](end_span)
    """
    available_expiries = set()
    underlying_key = INDEX_CONFIG[symbol]["underlying"]
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    
    # 1. Fetch expired expiries from Upstox
    url = "https://api.upstox.com/v2/expired-instruments/expiries"
    res = robust_api_get(url, headers, params={"instrument_key": underlying_key})
    if res and res.status_code == 200 and res.json().get("status") == "success":
        for d in res.json().get("data", []):
            if isinstance(d, str): available_expiries.add(d)
            elif isinstance(d, dict) and "expiry_date" in d: available_expiries.add(d["expiry_date"])
                
    # 2. Fetch live expiries from the gzip dump
    url_csv = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
    res_csv = requests.get(url_csv)
    if res_csv.status_code == 200:
        with gzip.open(io.BytesIO(res_csv.content), 'rt', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if symbol in row.get('tradingsymbol', '').upper() and row.get('expiry'):
                    available_expiries.add(row.get('expiry'))
                    
    # Filter and sort dates
    valid_dates = sorted([d for d in available_expiries if d >= trade_date_str])
    
    if not valid_dates:
        return None
        
    # Standard monthly derivatives generally expire on the last Thursday of the month. 
    # For robust historical mapping, we approximate monthly by checking if the date is near month-end.
    if require_monthly:
        for date_str in valid_dates:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            # If the next month is different from this date + 7 days, it's likely the last week of the month
            if (dt + timedelta(days=7)).month != dt.month:
                return date_str
        return valid_dates[-1] # Fallback
        
    return valid_dates[0] # Return the immediate closest weekly expiry

def resolve_exact_contract(symbol, expiry_date_str, token, inst_type="FUTIDX", strike=None, opt_type=None):
    """
    Finds the exact instrument_key. Switches between the 'search' API for active 
    contracts and 'expired-instruments' API for historical ones.[span_5](start_span)[span_5](end_span)[span_6](start_span)[span_6](end_span)
    """
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    expiry_dt = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
    today_dt = datetime.today().date()
    
    segment = INDEX_CONFIG[symbol]["segment"]
    
    if expiry_dt >= today_dt:
        # LIVE CONTRACT: Use the v2 search API[span_7](start_span)[span_7](end_span)
        query = f"{symbol}"
        if strike: query += f" {int(strike)}"
        
        search_params = {
            "query": query,
            "segments": segment,
            "instrument_types": inst_type if not opt_type else opt_type,
            "expiry": expiry_date_str
        }
        res = robust_api_get("https://api.upstox.com/v2/instruments/search", headers, params=search_params)
        if res and res.status_code == 200:
            data = res.json().get('data', [])
            if data: return data[0].get('instrument_key')
            
    else:
        # EXPIRED CONTRACT: Use v2 expired API[span_8](start_span)[span_8](end_span)
        api_type = "option" if inst_type == "OPTIDX" else "future"
        underlying = INDEX_CONFIG[symbol]["underlying"]
        
        url = f"https://api.upstox.com/v2/expired-instruments/{api_type}/contract"
        params = {"instrument_key": underlying, "expiry_date": expiry_date_str}
        
        res = robust_api_get(url, headers, params=params)
        if res and res.status_code == 200:
            contracts = res.json().get("data", [])
            for c in contracts:
                tsym = c.get("trading_symbol", "").upper()
                if inst_type == "FUTIDX" and "FUT" in tsym:
                    return c.get("instrument_key")
                elif inst_type == "OPTIDX" and opt_type in tsym:
                    # Parse strike from symbol (e.g., NIFTY26JUL24500CE)
                    match = re.search(r'(\d+(\.\d+)?)(CE|PE)$', tsym)
                    if match and float(match.group(1)) == float(strike):
                        return c.get("instrument_key")
    return None

# ==========================================
# MODULE 3: DATA FETCHING
# ==========================================
def fetch_candle_data(instrument_key, start_date_str, token, interval='1minute'):
    """
    Fetches base historical data for generating signals. 
    Uses V3 for intraday active, V2 for deep historical.[span_9](start_span)[span_9](end_span)
    """
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    encoded_key = urllib.parse.quote(instrument_key)
    today_str = datetime.today().strftime('%Y-%m-%d')
    
    url = f"https://api.upstox.com/v2/historical-candle/{encoded_key}/{interval}/{today_str}/{start_date_str}"
    print(f"[ENGINE] Fetching base chart: {url}")
    
    res = robust_api_get(url, headers)
    if res and res.status_code == 200:
        candles = res.json().get('data', {}).get('candles', [])
        if not candles: return pd.DataFrame()
        
        df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'vol', 'oi'])
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        return df.sort_index().astype(float)
    return pd.DataFrame()

def get_specific_candle_close(instrument_key, target_dt_str, token):
    """
    Fetches the specific 1-minute close price for the option leg at the exact signal time.[span_10](start_span)[span_10](end_span)
    target_dt_str format: 'YYYY-MM-DD HH:MM'
    """
    if not instrument_key: return 0.0
    
    target_date = target_dt_str[:10]
    target_time = target_dt_str[11:16]
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    encoded_key = urllib.parse.quote(instrument_key)
    
    if instrument_key.count('|') >= 2:
        url = f"https://api.upstox.com/v2/expired-instruments/historical-candle/{encoded_key}/1minute/{target_date}/{target_date}"
    else:
        url = f"https://api.upstox.com/v3/historical-candle/intraday/{encoded_key}/minutes/1" # v3 syntax[span_11](start_span)[span_11](end_span)
        
    res = robust_api_get(url, headers)
    if res and res.status_code == 200:
        candles = res.json().get("data", {}).get("candles", [])
        for candle in candles:
            if str(candle[0])[:16].replace('T', ' ') >= target_dt_str:
                return float(candle[4]) # Close price
    return 0.0

# ==========================================
# MODULE 4: STRATEGY LOGIC
# ==========================================
def resample_timeframes(df_base, ltf_interval, htf_interval):
    agg_dict = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'vol': 'sum'}
    ltf_df = df_base.resample(ltf_interval).agg(agg_dict).dropna()
    htf_df = df_base.resample(htf_interval).agg(agg_dict).dropna()
    return ltf_df, htf_df

def calculate_strategy_indicators(ltf, htf):
    # HTF
    htf['ema_25'] = ta.ema(htf['close'], length=25)
    stoch_htf = ta.stoch(htf['high'], htf['low'], htf['close'], k=14, d=3, smooth_k=3)
    if stoch_htf is not None: htf = htf.join(stoch_htf)
    htf['obv'] = ta.obv(htf['close'], htf['vol'])
    htf['obv_sma_20'] = ta.sma(htf['obv'], length=20)
    
    htf.rename(columns={c: 'htf_stoch_k' for c in htf.columns if 'STOCHk' in c}, inplace=True)
    htf.rename(columns={c: 'htf_stoch_d' for c in htf.columns if 'STOCHd' in c}, inplace=True)

    # LTF
    stoch_ltf = ta.stoch(ltf['high'], ltf['low'], ltf['close'], k=14, d=3, smooth_k=3)
    if stoch_ltf is not None: ltf = ltf.join(stoch_ltf)
    ltf.rename(columns={c: 'ltf_stoch_k' for c in ltf.columns if 'STOCHk' in c}, inplace=True)
    ltf.rename(columns={c: 'ltf_stoch_d' for c in ltf.columns if 'STOCHd' in c}, inplace=True)
    
    if 'ltf_stoch_k' in ltf.columns and 'ltf_stoch_d' in ltf.columns:
        ltf['stoch_cross_up'] = (ltf['ltf_stoch_k'] > ltf['ltf_stoch_d']) & (ltf['ltf_stoch_k'].shift(1) <= ltf['ltf_stoch_d'].shift(1))
        ltf['stoch_cross_down'] = (ltf['ltf_stoch_k'] < ltf['ltf_stoch_d']) & (ltf['ltf_stoch_k'].shift(1) >= ltf['ltf_stoch_d'].shift(1))
        
    return ltf, htf

def generate_signals(ltf, htf):
    required_htf_cols = ['ema_25', 'htf_stoch_k', 'htf_stoch_d', 'obv', 'obv_sma_20']
    htf_aligned = htf[[c for c in required_htf_cols if c in htf.columns]].reindex(ltf.index, method='ffill').fillna(0)
    df = ltf.join(htf_aligned)
    
    df['htf_long_bias'] = (df['close'] > df['ema_25']) & (df['htf_stoch_k'] > df['htf_stoch_d']) & (df['obv'] > df['obv_sma_20'])
    df['htf_short_bias'] = (df['close'] < df['ema_25']) & (df['htf_stoch_k'] < df['htf_stoch_d']) & (df['obv'] < df['obv_sma_20'])
    df['vol_surge'] = (df['vol'] > df['vol'].shift(1)) & (df['vol'] > df['vol'].shift(2))
    
    if 'stoch_cross_up' in df.columns:
        df['long_signal'] = (df['close'] > df['open']) & df['stoch_cross_up'].shift(1).fillna(False) & df['vol_surge'] & df['htf_long_bias']
        df['short_signal'] = (df['close'] < df['open']) & df['stoch_cross_down'].shift(1).fillna(False) & df['vol_surge'] & df['htf_short_bias']
    else:
        df['long_signal'], df['short_signal'] = False, False
        
    return df.dropna()

# ==========================================
# MODULE 5: TRADE SIMULATION & SPREAD PRICING
# ==========================================
def simulate_trades(df, symbol, ltf_str, htf_str, token):
    """
    1. Detects signal.
    2. Calculates ATM strike.
    3. Finds Option Keys for Weekly Expiry.
    4. Fetches EXACT entry prices for both legs at the signal minute.
    """
    trades = []
    step = INDEX_CONFIG[symbol]["step"]
    
    for idx, row in df.iterrows():
        if row['long_signal'] or row['short_signal']:
            entry_dt_str = str(idx)[:16]
            entry_date = entry_dt_str[:10]
            future_price = row['close']
            
            # 1. Get exact weekly expiry for this trade
            weekly_expiry = get_closest_expiry(symbol, entry_date, token, require_monthly=False)
            if not weekly_expiry: continue
            
            # 2. Calculate Strikes
            atm_strike = round(future_price / step) * step
            
            is_long = row['long_signal']
            trade_type = 'Bull Put Spread' if is_long else 'Bear Call Spread'
            opt_type = 'PE' if is_long else 'CE'
            otm2_strike = atm_strike - (step * 2) if is_long else atm_strike + (step * 2)
            
            # 3. Resolve Exact Contract Keys for Options[span_12](start_span)[span_12](end_span)
            sell_leg_key = resolve_exact_contract(symbol, weekly_expiry, token, inst_type="OPTIDX", strike=atm_strike, opt_type=opt_type)
            buy_leg_key = resolve_exact_contract(symbol, weekly_expiry, token, inst_type="OPTIDX", strike=otm2_strike, opt_type=opt_type)
            
            # 4. Fetch the exact entry prices at that specific minute
            sell_price = get_specific_candle_close(sell_leg_key, entry_dt_str, token) if sell_leg_key else 0.0
            buy_price = get_specific_candle_close(buy_leg_key, entry_dt_str, token) if buy_leg_key else 0.0
            net_credit = sell_price - buy_price
            
            trades.append({
                'Entry_Time': entry_dt_str,
                'Symbol': symbol,
                'TF_Combo': f"{ltf_str}/{htf_str}",
                'Trade_Type': trade_type,
                'Weekly_Expiry': weekly_expiry,
                'Future_Price': future_price,
                'Sell_Leg': f"{atm_strike} {opt_type}",
                'Buy_Leg': f"{otm2_strike} {opt_type}",
                'Sell_Entry_Price': sell_price,
                'Buy_Entry_Price': buy_price,
                'Net_Credit_Received': net_credit,
                'Stop_Loss': sell_price * 1.15 if sell_price > 0 else 0.0, # 15% SL on ATM
                'Take_Profit_Target': net_credit * 0.30 if net_credit > 0 else 0.0 # 30% of Net Credit
            })
            
    return pd.DataFrame(trades)

def calculate_portfolio_metrics(trades_df):
    if trades_df.empty: return pd.DataFrame()
    metrics = []
    for (symbol, tf), group in trades_df.groupby(['Symbol', 'TF_Combo']):
        metrics.append({
            'Symbol': symbol,
            'Timeframes': tf,
            'Total_Trades': len(group)
        })
    return pd.DataFrame(metrics)
