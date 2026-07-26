"""
STRATEGY: Stochastic Index Intraday Momentum (Methodical API-Driven Edition)
--------------------------------------------------
MODULES:
- Automatic Timeframe Fallback: Dynamically scales from 1-min -> 3-min -> 5-min if futures are illiquid.
- Transparent Data Logging: Exposes Upstox API empty responses for illiquid BSE Futures.
- Official Expiry Endpoint Parsing: Fetches exact exchange-approved expiry dates from Upstox.
- Direct Contract Resolution: Methodically queries expired futures/options contracts via official endpoints.
- Master CSV Live Fallback: Resolves active running contracts seamlessly.
- Micro-Chunking & Rate Limiting: Prevents Upstox truncation and 429 timeouts.
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
import pytz

# ==========================================
# MODULE 1: CONSTANTS & CONFIGURATION
# ==========================================
IST = pytz.timezone('Asia/Kolkata')

TIMEFRAME_COMBOS = [
    ('3min', '15min'),
    ('5min', '30min'),
    ('10min', '60min')
]

INDEX_CONFIG = {
    "NIFTY": {"underlying": "NSE_INDEX|Nifty 50", "step": 50, "segment": "NSE_FO"},
    "SENSEX": {"underlying": "BSE_INDEX|SENSEX", "step": 100, "segment": "BSE_FO"}
}

_LIVE_INSTRUMENTS_CACHE = None

def robust_api_get(url, headers, max_retries=3, params=None):
    for attempt in range(max_retries):
        res = requests.get(url, headers=headers, params=params)
        if res.status_code == 200: return res
        elif res.status_code == 429: time.sleep(1 + attempt) 
        else: time.sleep(0.5)
    return res

def is_exact_symbol(tsym, symbol):
    tsym = str(tsym).upper().strip()
    symbol = symbol.upper()
    
    if symbol == "SENSEX":
        if tsym.startswith("BSX") or tsym.startswith("SENSEX"):
            return True
        return False
        
    if not tsym.startswith(symbol): return False
    if len(tsym) > len(symbol):
        if tsym[len(symbol)].isalpha(): return False
    return True

def get_live_instruments():
    global _LIVE_INSTRUMENTS_CACHE
    if _LIVE_INSTRUMENTS_CACHE is not None:
        return _LIVE_INSTRUMENTS_CACHE
        
    csv_file = "upstox_active_instruments.csv"
    today_dt = datetime.now(IST).date()
    
    if os.path.exists(csv_file):
        mtime = datetime.fromtimestamp(os.path.getmtime(csv_file), tz=IST).date()
        if mtime == today_dt:
            try:
                _LIVE_INSTRUMENTS_CACHE = pd.read_csv(csv_file, low_memory=False)
                return _LIVE_INSTRUMENTS_CACHE
            except: pass
            
    url_csv = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
    res_csv = requests.get(url_csv)
    if res_csv.status_code == 200:
        with gzip.open(io.BytesIO(res_csv.content), 'rt', encoding='utf-8') as f:
            df = pd.read_csv(f, low_memory=False)
            df.to_csv(csv_file, index=False)
            _LIVE_INSTRUMENTS_CACHE = df
            return df
    return pd.DataFrame()

# ==========================================
# MODULE 2: METHODICAL EXPIRY & CONTRACT RESOLUTION
# ==========================================
def get_all_expiries(symbol, token, logger=None):
    available_expiries = set()
    underlying_key = INDEX_CONFIG[symbol]["underlying"]
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    
    url = "https://api.upstox.com/v2/expired-instruments/expiries"
    res = robust_api_get(url, headers, params={"instrument_key": underlying_key})
    if res and res.status_code == 200:
        data = res.json().get("data", [])
        if logger: logger(f"[METHODICAL] Official API returned {len(data)} expiries for {symbol}")
        for d in data:
            if isinstance(d, str): available_expiries.add(d)
            elif isinstance(d, dict) and "expiry_date" in d: available_expiries.add(d["expiry_date"])
            
    df = get_live_instruments()
    if not df.empty:
        subset = df[df['underlying_key'] == underlying_key] if 'underlying_key' in df.columns else df
        for _, row in subset.iterrows():
            tsym = str(row.get('tradingsymbol', ''))
            exp = str(row.get('expiry', ''))
            if exp and exp != 'nan' and is_exact_symbol(tsym, symbol):
                available_expiries.add(exp)
                
    sorted_exp = sorted(list(available_expiries))
    if logger: logger(f"[METHODICAL] Total unified expiries mapped for {symbol}: {len(sorted_exp)}")
    return sorted_exp

def get_monthly_expiries(all_expiries):
    months = {}
    for exp in all_expiries:
        ym = exp[:7]
        if ym not in months or exp > months[ym]:
            months[ym] = exp
    return sorted(list(months.values()))

def get_closest_weekly_expiry(all_expiries, target_date_str):
    valid_dates = [d for d in all_expiries if d >= target_date_str]
    return valid_dates[0] if valid_dates else None

def resolve_exact_contract(symbol, expiry_date_str, token, inst_type="FUTIDX", strike=None, opt_type=None, logger=None):
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    expiry_dt = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
    today_dt = datetime.now(IST).date()
    
    if expiry_dt >= today_dt:
        # --- LIVE RUNNING CONTRACTS VIA MASTER CSV ---
        df = get_live_instruments()
        if not df.empty:
            subset = df[(df['expiry'] == expiry_date_str) & (df['instrument_type'] == inst_type)]
            for _, row in subset.iterrows():
                tsym_raw = str(row.get('tradingsymbol', ''))
                if not is_exact_symbol(tsym_raw, symbol): continue
                
                if inst_type == "FUTIDX":
                    return str(row.get('instrument_key'))
                
                if inst_type == "OPTIDX":
                    match = re.search(rf'(\d+(?:\.\d+)?)\s*{opt_type}', tsym_raw.upper())
                    if match and float(match.group(1)) == float(strike):
                        return str(row.get("instrument_key"))
    else:
        # --- METHODICAL EXPIRED CONTRACTS ENDPOINT ---
        api_type = "option" if inst_type == "OPTIDX" else "future"
        underlying = INDEX_CONFIG[symbol]["underlying"]
        url = f"https://api.upstox.com/v2/expired-instruments/{api_type}/contract"
        res = robust_api_get(url, headers, params={"instrument_key": underlying, "expiry_date": expiry_date_str})
        
        if res and res.status_code == 200:
            contracts = res.json().get("data", [])
            for c in contracts:
                tsym_raw = str(c.get("trading_symbol", ""))
                if not is_exact_symbol(tsym_raw, symbol): continue
                
                if inst_type == "FUTIDX":
                    return c.get("instrument_key")
                
                if inst_type == "OPTIDX":
                    match = re.search(rf'(\d+(?:\.\d+)?)\s*{opt_type}', tsym_raw.upper())
                    if match and float(match.group(1)) == float(strike):
                        return c.get("instrument_key")
                        
    return None

# ==========================================
# MODULE 3: DATA FETCHING & GITHUB CACHING
# ==========================================
def fetch_candle_chunk(instrument_key, from_date, to_date, token, interval='1minute', logger=None):
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    encoded_key = urllib.parse.quote(instrument_key)
    
    start_dt = datetime.strptime(from_date, '%Y-%m-%d').date()
    end_dt = datetime.strptime(to_date, '%Y-%m-%d').date()
    
    all_candles = []
    current = start_dt
    
    while current <= end_dt:
        chunk_end = min(current + timedelta(days=2), end_dt)
        str_from = current.strftime('%Y-%m-%d')
        str_to = chunk_end.strftime('%Y-%m-%d')
        
        url_active = f"https://api.upstox.com/v2/historical-candle/{encoded_key}/{interval}/{str_to}/{str_from}"
        res = robust_api_get(url_active, headers)
        chunk_candles = []
        
        if res and res.status_code == 200:
            chunk_candles = res.json().get('data', {}).get('candles', [])
            
        if not chunk_candles:
            url_expired = f"https://api.upstox.com/v2/expired-instruments/historical-candle/{encoded_key}/{interval}/{str_to}/{str_from}"
            res_exp = robust_api_get(url_expired, headers)
            if res_exp and res_exp.status_code == 200:
                chunk_candles = res_exp.json().get('data', {}).get('candles', [])
                
        if chunk_candles:
            all_candles.extend(chunk_candles)
            
        current = chunk_end + timedelta(days=1)
        time.sleep(0.2)
            
    if not all_candles: return pd.DataFrame()
    
    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'vol', 'oi'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    return df.sort_index().astype(float)

def build_continuous_futures(symbol, start_date_str, token, github_repo="", logger=None):
    today_str = datetime.now(IST).strftime('%Y-%m-%d')
    start_dt = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    all_expiries = get_all_expiries(symbol, token, logger=logger)
    
    if not all_expiries:
        if logger: logger(f"CRITICAL: 0 expiries found for {symbol}.")
        return pd.DataFrame(), [], False
    
    if github_repo:
        raw_url = f"https://raw.githubusercontent.com/{github_repo}/main/data_cache/{symbol}_continuous.csv"
        try:
            res = requests.get(raw_url, timeout=15)
            if res.status_code == 200:
                df = pd.read_csv(io.StringIO(res.text))
                if not df.empty:
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    df.set_index('timestamp', inplace=True)
                    if df.index.tz is not None: df.index = df.index.tz_localize(None)
                    
                    if df.index.min() <= pd.to_datetime(start_date_str):
                        if df.index.max() >= pd.to_datetime(today_str) - timedelta(days=5):
                            df = df[df.index >= pd.to_datetime(start_date_str)]
                            if not df.empty:
                                if logger: logger(f"Successfully loaded {len(df)} rows from GitHub cache.")
                                return df, all_expiries, False
        except Exception: pass

    local_filename = f"{symbol}_continuous.csv"
    if os.path.exists(local_filename):
        df = pd.read_csv(local_filename)
        if not df.empty:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
            if df.index.tz is not None: df.index = df.index.tz_localize(None)
                
            if df.index.min() <= pd.to_datetime(start_date_str):
                if df.index.max() >= pd.to_datetime(today_str) - timedelta(days=5):
                    df = df[df.index >= pd.to_datetime(start_date_str)]
                    if not df.empty: return df, all_expiries, False

    if logger: logger(f"Fetching methodical continuous data for {symbol}...")
    monthly_expiries = get_monthly_expiries(all_expiries)
    relevant_expiries = [e for e in monthly_expiries if datetime.strptime(e, '%Y-%m-%d').date() >= start_dt]
    
    continuous_df = pd.DataFrame()
    current_start = start_date_str
    
    for exp in relevant_expiries:
        future_key = resolve_exact_contract(symbol, exp, token, inst_type="FUTIDX", logger=logger)
        if not future_key: 
            if logger: logger(f"WARN: Failed to locate Future Key for {symbol} on expiry {exp}")
            continue
        
        end_fetch = min(exp, today_str)
        if logger: logger(f"Fetching Upstox 1-min data for {symbol} Future Key: {future_key} ({current_start} to {end_fetch})")
        
        df = fetch_candle_chunk(future_key, current_start, end_fetch, token, interval='1minute', logger=logger)
        
        # --- AUTOMATIC TIMEFRAME FALLBACK FOR ILLIQUID CONTRACTS ---
        if df.empty:
            if logger: logger(f"⚠️ 1-min empty for {future_key}. Falling back to 3-min candles...")
            df = fetch_candle_chunk(future_key, current_start, end_fetch, token, interval='3minute', logger=logger)
            
        if df.empty:
            if logger: logger(f"⚠️ 3-min empty for {future_key}. Falling back to 5-min candles...")
            df = fetch_candle_chunk(future_key, current_start, end_fetch, token, interval='5minute', logger=logger)
        
        if not df.empty:
            continuous_df = pd.concat([continuous_df, df])
        else:
            if logger: logger(f"⚠️ UPSTOX DATA LIMIT: 0 historical candles returned for {future_key} across all intraday timeframes.")
            
        current_start = (datetime.strptime(exp, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
        if current_start > today_str: break
            
    if not continuous_df.empty:
        continuous_df = continuous_df[~continuous_df.index.duplicated(keep='first')]
        try: continuous_df.to_csv(local_filename)
        except: pass
        
    return continuous_df, all_expiries, True

def get_specific_candle_close(instrument_key, target_dt_str, token):
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
        candles.sort(key=lambda x: x[0]) 
        for candle in candles:
            if str(candle[0])[:16].replace('T', ' ') >= target_dt_str:
                return float(candle[4])
    return 0.0

# ==========================================
# MODULE 4: STRATEGY & SIMULATION
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
    for col in required_htf_cols:
        if col not in htf.columns: htf[col] = 0.0
            
    required_ltf_cols = ['stoch_cross_up', 'stoch_cross_down']
    for col in required_ltf_cols:
        if col not in ltf.columns: ltf[col] = False

    htf_aligned = htf[[c for c in required_htf_cols if c in htf.columns]].reindex(ltf.index, method='ffill').fillna(0)
    df = ltf.join(htf_aligned)
    
    df['htf_long_bias'] = (df['close'] > df['ema_25']) & (df['htf_stoch_k'] > df['htf_stoch_d']) & (df['obv'] > df['obv_sma_20'])
    df['htf_short_bias'] = (df['close'] < df['ema_25']) & (df['htf_stoch_k'] < df['htf_stoch_d']) & (df['obv'] < df['obv_sma_20'])
    
    if 'vol' in df.columns:
        df['vol_surge'] = (df['vol'] > df['vol'].shift(1)) & (df['vol'] > df['vol'].shift(2))
    else:
        df['vol_surge'] = False
    
    df['long_signal'] = (df['close'] > df['open']) & df['stoch_cross_up'].shift(1).fillna(False) & df['vol_surge'] & df['htf_long_bias']
    df['short_signal'] = (df['close'] < df['open']) & df['stoch_cross_down'].shift(1).fillna(False) & df['vol_surge'] & df['htf_short_bias']
        
    return df.dropna()

def simulate_trades(df, symbol, ltf_str, htf_str, token, all_expiries, logger=None):
    trades = []
    step = INDEX_CONFIG[symbol]["step"]
    
    for idx, row in df.iterrows():
        if row['long_signal'] or row['short_signal']:
            entry_dt_str = str(idx)[:16]
            entry_date = entry_dt_str[:10]
            future_price = row['close']
            
            weekly_expiry = get_closest_weekly_expiry(all_expiries, entry_date)
            if not weekly_expiry: 
                if logger: logger(f"[{entry_dt_str}] No valid weekly expiry found for {symbol}.")
                continue
            
            atm_strike = round(future_price / step) * step
            is_long = row['long_signal']
            trade_type = 'Bull Put Spread' if is_long else 'Bear Call Spread'
            opt_type = 'PE' if is_long else 'CE'
            otm2_strike = atm_strike - (step * 2) if is_long else atm_strike + (step * 2)
            
            sell_leg_key = resolve_exact_contract(symbol, weekly_expiry, token, inst_type="OPTIDX", strike=atm_strike, opt_type=opt_type, logger=logger)
            buy_leg_key = resolve_exact_contract(symbol, weekly_expiry, token, inst_type="OPTIDX", strike=otm2_strike, opt_type=opt_type, logger=logger)
            
            sell_price = round(get_specific_candle_close(sell_leg_key, entry_dt_str, token), 2) if sell_leg_key else 0.0
            buy_price = round(get_specific_candle_close(buy_leg_key, entry_dt_str, token), 2) if buy_leg_key else 0.0
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
