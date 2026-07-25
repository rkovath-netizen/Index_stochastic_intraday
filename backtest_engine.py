"""
STRATEGY: Stochastic Index Intraday Momentum
--------------------------------------------------
ARCHITECTURE UPDATES:
- Continuous Futures Builder: Stitches together front-month futures to create a historical chart.
- Strict Expiry Matching: Prevents FINNIFTY/BANKNIFTY contamination for NIFTY expiries.
- Option Price Polling: Sorts candles chronologically to pull the exact entry minute price.
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
    for attempt in range(max_retries):
        res = requests.get(url, headers=headers, params=params)
        if res.status_code == 200: return res
        elif res.status_code == 429: time.sleep(2 ** attempt) 
        else: time.sleep(1)
    return res

# ==========================================
# MODULE 2: EXPIRY & CONTRACT RESOLUTION
# ==========================================
def get_all_expiries(symbol, token):
    """Fetches every known expiry date (live and expired) and returns a sorted list."""
    available_expiries = set()
    underlying_key = INDEX_CONFIG[symbol]["underlying"]
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    
    # Expired Expiries
    url = "https://api.upstox.com/v2/expired-instruments/expiries"
    res = robust_api_get(url, headers, params={"instrument_key": underlying_key})
    if res and res.status_code == 200:
        for d in res.json().get("data", []):
            if isinstance(d, str): available_expiries.add(d)
            elif isinstance(d, dict) and "expiry_date" in d: available_expiries.add(d["expiry_date"])
    
    # Live Expiries
    url_csv = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
    res_csv = requests.get(url_csv)
    if res_csv.status_code == 200:
        with gzip.open(io.BytesIO(res_csv.content), 'rt', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                tsym = row.get('tradingsymbol', '').upper()
                # CRITICAL FIX: startswith prevents FINNIFTY from bleeding into NIFTY expiries
                if tsym.startswith(symbol) and row.get('expiry'):
                    available_expiries.add(row.get('expiry'))
                    
    return sorted(list(available_expiries))

def get_monthly_expiries(all_expiries):
    """Groups expiries by YYYY-MM and extracts the maximum date (the monthly expiry)."""
    months = {}
    for exp in all_expiries:
        ym = exp[:7] # Extract YYYY-MM
        if ym not in months or exp > months[ym]:
            months[ym] = exp
    return sorted(list(months.values()))

def get_closest_weekly_expiry(all_expiries, target_date_str):
    """Finds the immediate next expiry after a given historical trade date."""
    valid_dates = [d for d in all_expiries if d >= target_date_str]
    return valid_dates[0] if valid_dates else None

def resolve_exact_contract(symbol, expiry_date_str, token, inst_type="FUTIDX", strike=None, opt_type=None):
    """Resolves the exact instrument key using Active or Expired APIs."""
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    expiry_dt = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
    today_dt = datetime.today().date()
    
    segment = INDEX_CONFIG[symbol]["segment"]
    
    if expiry_dt >= today_dt:
        # LIVE
        query = f"{symbol}"
        if strike: query += f" {int(strike)}"
        search_params = {"query": query, "segments": segment, "instrument_types": inst_type if not opt_type else opt_type, "expiry": expiry_date_str}
        res = robust_api_get("https://api.upstox.com/v2/instruments/search", headers, params=search_params)
        if res and res.status_code == 200:
            data = res.json().get('data', [])
            if data: return data[0].get('instrument_key')
    else:
        # EXPIRED
        api_type = "option" if inst_type == "OPTIDX" else "future"
        underlying = INDEX_CONFIG[symbol]["underlying"]
        url = f"https://api.upstox.com/v2/expired-instruments/{api_type}/contract"
        res = robust_api_get(url, headers, params={"instrument_key": underlying, "expiry_date": expiry_date_str})
        if res and res.status_code == 200:
            for c in res.json().get("data", []):
                tsym = c.get("trading_symbol", "").upper()
                if inst_type == "FUTIDX" and "FUT" in tsym:
                    return c.get("instrument_key")
                elif inst_type == "OPTIDX" and opt_type in tsym:
                    match = re.search(r'(\d+(\.\d+)?)(CE|PE)$', tsym)
                    if match and float(match.group(1)) == float(strike):
                        return c.get("instrument_key")
    return None

# ==========================================
# MODULE 3: DATA FETCHING & STITCHING
# ==========================================
def fetch_candle_chunk(instrument_key, from_date, to_date, token, interval='1minute'):
    """Fetches a specific timeframe chunk, routing to active or expired automatically."""
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    encoded_key = urllib.parse.quote(instrument_key)
    
    # Try Active
    url_active = f"https://api.upstox.com/v2/historical-candle/{encoded_key}/{interval}/{to_date}/{from_date}"
    res = robust_api_get(url_active, headers)
    candles = []
    if res and res.status_code == 200:
        candles = res.json().get('data', {}).get('candles', [])
        
    # Fallback to Expired
    if not candles:
        url_expired = f"https://api.upstox.com/v2/expired-instruments/historical-candle/{encoded_key}/{interval}/{to_date}/{from_date}"
        res_exp = robust_api_get(url_expired, headers)
        if res_exp and res_exp.status_code == 200:
            candles = res_exp.json().get('data', {}).get('candles', [])
            
    if not candles: return pd.DataFrame()
    
    df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'vol', 'oi'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    return df.sort_index().astype(float)

def build_continuous_futures(symbol, start_date_str, token):
    """
    Core Engine Function: Stitches multiple front-month futures together 
    to create a single continuous timeframe from start_date to today.
    """
    today_str = datetime.today().strftime('%Y-%m-%d')
    start_dt = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    
    all_expiries = get_all_expiries(symbol, token)
    monthly_expiries = get_monthly_expiries(all_expiries)
    
    # Get all monthly expiries required to bridge the date gap
    relevant_expiries = [e for e in monthly_expiries if datetime.strptime(e, '%Y-%m-%d').date() >= start_dt]
    
    continuous_df = pd.DataFrame()
    current_start = start_date_str
    
    for exp in relevant_expiries:
        future_key = resolve_exact_contract(symbol, exp, token, inst_type="FUTIDX")
        if not future_key: continue
        
        # Don't fetch past today
        end_fetch = min(exp, today_str)
        
        print(f"[ENGINE] Fetching {symbol} FUT chunk: {current_start} to {end_fetch}")
        df = fetch_candle_chunk(future_key, current_start, end_fetch, token)
        
        if not df.empty:
            continuous_df = pd.concat([continuous_df, df])
            
        # Move the start date to the day after this expiry
        current_start = (datetime.strptime(exp, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
        if current_start > today_str:
            break
            
    if not continuous_df.empty:
        # Drop overlapping artifacts
        continuous_df = continuous_df[~continuous_df.index.duplicated(keep='first')]
        
    return continuous_df, all_expiries

def get_specific_candle_close(instrument_key, target_dt_str, token):
    """Fetches the exact Option Price at the given historical minute."""
    if not instrument_key: return 0.0
    target_date = target_dt_str[:10]
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    encoded_key = urllib.parse.quote(instrument_key)
    
    if instrument_key.count('|') >= 2:
        url = f"https://api.upstox.com/v2/expired-instruments/historical-candle/{encoded_key}/1minute/{target_date}/{target_date}"
    else:
        url = f"https://api.upstox.com/v3/historical-candle/intraday/{encoded_key}/minutes/1"
        
    res = robust_api_get(url, headers)
    if res and res.status_code == 200:
        candles = res.json().get("data", {}).get("candles", [])
        # CRITICAL FIX: Sort chronologically so we catch the exact minute properly
        candles.sort(key=lambda x: x[0]) 
        for candle in candles:
            if str(candle[0])[:16].replace('T', ' ') >= target_dt_str:
                return float(candle[4])
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
    htf['ema_25'] = ta.ema(htf['close'], length=25)
    stoch_htf = ta.stoch(htf['high'], htf['low'], htf['close'], k=14, d=3, smooth_k=3)
    if stoch_htf is not None: htf = htf.join(stoch_htf)
    htf['obv'] = ta.obv(htf['close'], htf['vol'])
    htf['obv_sma_20'] = ta.sma(htf['obv'], length=20)
    
    htf.rename(columns={c: 'htf_stoch_k' for c in htf.columns if 'STOCHk' in c}, inplace=True)
    htf.rename(columns={c: 'htf_stoch_d' for c in htf.columns if 'STOCHd' in c}, inplace=True)

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
# MODULE 5: SIMULATION
# ==========================================
def simulate_trades(df, symbol, ltf_str, htf_str, token, all_expiries):
    trades = []
    step = INDEX_CONFIG[symbol]["step"]
    
    for idx, row in df.iterrows():
        if row['long_signal'] or row['short_signal']:
            entry_dt_str = str(idx)[:16]
            entry_date = entry_dt_str[:10]
            future_price = row['close']
            
            weekly_expiry = get_closest_weekly_expiry(all_expiries, entry_date)
            if not weekly_expiry: continue
            
            atm_strike = round(future_price / step) * step
            is_long = row['long_signal']
            trade_type = 'Bull Put Spread' if is_long else 'Bear Call Spread'
            opt_type = 'PE' if is_long else 'CE'
            otm2_strike = atm_strike - (step * 2) if is_long else atm_strike + (step * 2)
            
            sell_leg_key = resolve_exact_contract(symbol, weekly_expiry, token, inst_type="OPTIDX", strike=atm_strike, opt_type=opt_type)
            buy_leg_key = resolve_exact_contract(symbol, weekly_expiry, token, inst_type="OPTIDX", strike=otm2_strike, opt_type=opt_type)
            
            sell_price = get_specific_candle_close(sell_leg_key, entry_dt_str, token) if sell_leg_key else 0.0
            buy_price = get_specific_candle_close(buy_leg_key, entry_dt_str, token) if buy_leg_key else 0.0
            
            # Formatting to 2 decimal places to avoid massive floats
            sell_price = round(sell_price, 2)
            buy_price = round(buy_price, 2)
            net_credit = round(sell_price - buy_price, 2)
            
            trades.append({
                'Entry_Time': entry_dt_str,
                'Symbol': symbol,
                'TF_Combo': f"{ltf_str}/{htf_str}",
                'Trade_Type': trade_type,
                'Weekly_Expiry': weekly_expiry,
                'Future_Price': round(future_price, 2),
                'Sell_Leg': f"{atm_strike} {opt_type}",
                'Buy_Leg': f"{otm2_strike} {opt_type}",
                'Sell_Entry_Price': sell_price,
                'Buy_Entry_Price': buy_price,
                'Net_Credit_Received': net_credit,
                'Stop_Loss': round(sell_price * 1.15, 2) if sell_price > 0 else 0.0,
                'Take_Profit_Target': round(net_credit * 0.30, 2) if net_credit > 0 else 0.0
            })
            
    return pd.DataFrame(trades)

def calculate_portfolio_metrics(trades_df):
    if trades_df.empty: return pd.DataFrame()
    metrics = []
    for (symbol, tf), group in trades_df.groupby(['Symbol', 'TF_Combo']):
        metrics.append({'Symbol': symbol, 'Timeframes': tf, 'Total_Trades': len(group)})
    return pd.DataFrame(metrics)
