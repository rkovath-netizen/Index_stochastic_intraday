"""
STRATEGY LOGIC: Stochastic Index Intraday Momentum
--------------------------------------------------
DYNAMIC INSTRUMENT RESOLUTION:
1. Fetches historical expiry dates for a given underlying (e.g., NIFTY).
2. Uses the entry date to find the current active historical expiry.
3. Dynamically fetches the exact historical instrument_keys for Expired Futures and Options.
4. Generates Option Spread signals (ATM and OTM2 strikes) dynamically based on the entry price.
"""

import os
import requests
import pandas as pd
import pandas_ta as ta
import datetime
from datetime import timedelta
import traceback
import math

# ==========================================
# MODULE 1: CONFIGURATION & CONSTANTS
# ==========================================
BASE_URL = "https://api.upstox.com/v2"

# Map human-readable index names to Upstox base symbol parameters
UNDERLYING_MAP = {
    "NIFTY": {"symbol": "NIFTY", "strike_step": 50},
    "SENSEX": {"symbol": "SENSEX", "strike_step": 100}
}

TIMEFRAME_COMBOS = [
    ('3min', '15min'),
    ('5min', '30min'),
    ('10min', '60min')
]

# ==========================================
# MODULE 2: DYNAMIC CONTRACT RESOLUTION (UPSTOX API)
# ==========================================
def get_historical_expiries(instrument_name, token):
    """
    Fetches the list of all historical expiries for a given index.
    Endpoint Ref: https://upstox.com/developer/api-documentation/get-expiries
    """
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    # Note: Adjust the exact URL path based on Upstox's current v2 schema for expiries
    url = f"{BASE_URL}/historical/expiries/{instrument_name}" 
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json().get('data', [])
            # Convert to datetime objects and sort ascending
            expiries = sorted([datetime.datetime.strptime(d, '%Y-%m-%d').date() for d in data])
            return expiries
        else:
            print(f"[API ERROR] Failed to fetch expiries: {response.text}")
            return []
    except Exception as e:
        print(f"[API EXCEPTION] Expiry fetch failed: {e}")
        return []

def get_expired_future_contract(instrument_name, expiry_date_str, token):
    """
    Retrieves the exact instrument_key for a future contract that has already expired.
    Endpoint Ref: https://upstox.com/developer/api-documentation/get-expired-future-contracts
    """
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    url = f"{BASE_URL}/historical-contracts/future/{instrument_name}?expiry_date={expiry_date_str}"
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            # Assuming the API returns a list of contracts for that expiry, extract the instrument_key
            contracts = response.json().get('data', [])
            if contracts:
                return contracts[0].get('instrument_key')
        return None
    except Exception as e:
        print(f"[API EXCEPTION] Future contract fetch failed: {e}")
        return None

def get_expired_option_contract(instrument_name, expiry_date_str, strike, option_type, token):
    """
    Retrieves the exact instrument_key for an expired option contract (CE/PE) at a specific strike.
    Endpoint Ref: https://upstox.com/developer/api-documentation/get-expired-option-contracts
    """
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    url = f"{BASE_URL}/historical-contracts/option/{instrument_name}?expiry_date={expiry_date_str}"
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            contracts = response.json().get('data', [])
            # Filter the JSON response for the exact strike and option type (CE or PE)
            for contract in contracts:
                if contract.get('strike') == strike and contract.get('instrument_type') == option_type:
                    return contract.get('instrument_key')
        return None
    except Exception as e:
        print(f"[API EXCEPTION] Option contract fetch failed: {e}")
        return None

def find_next_expiry(current_date, expiries_list):
    """Utility to find the closest expiry date strictly after the current entry date."""
    for expiry in expiries_list:
        if expiry >= current_date.date():
            return expiry
    return None

# ==========================================
# MODULE 3: HISTORICAL DATA FETCHING
# ==========================================
def fetch_historical_candle_data(instrument_key, token, interval='1minute', days=30):
    """
    Fetches historical candle data for the dynamically resolved instrument_key.
    The user can pass '1minute', '30minute', or '1hour' as needed.
    """
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {token}'}
    to_date = datetime.datetime.now().strftime('%Y-%m-%d')
    from_date = (datetime.datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    url = f"{BASE_URL}/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"[ENGINE ERROR] Candle API returned: {response.text}")
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
    except Exception as e:
        print(f"[ENGINE CRITICAL] Crash during candle fetch: {e}")
        return pd.DataFrame()

# ==========================================
# MODULE 4: TIMEFRAMES & INDICATORS
# ==========================================
def resample_timeframes(df_base, ltf_interval, htf_interval):
    """Resamples the base fetched data into strategy-specific timeframes."""
    agg_dict = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
    ltf_df = df_base.resample(ltf_interval).agg(agg_dict).dropna()
    htf_df = df_base.resample(htf_interval).agg(agg_dict).dropna()
    return ltf_df, htf_df

def calculate_strategy_indicators(ltf, htf):
    """Calculates EMAs, Stochastics, and OBV."""
    # HTF
    htf['ema_25'] = ta.ema(htf['close'], length=25)
    stoch_htf = ta.stoch(htf['high'], htf['low'], htf['close'], k=14, d=3, smooth_k=3)
    if stoch_htf is not None: htf = htf.join(stoch_htf)
    htf['obv'] = ta.obv(htf['close'], htf['volume'])
    htf['obv_sma_20'] = ta.sma(htf['obv'], length=20)
    
    htf.rename(columns={col: 'htf_stoch_k' for col in htf.columns if 'STOCHk' in col}, inplace=True)
    htf.rename(columns={col: 'htf_stoch_d' for col in htf.columns if 'STOCHd' in col}, inplace=True)

    # LTF
    stoch_ltf = ta.stoch(ltf['high'], ltf['low'], ltf['close'], k=14, d=3, smooth_k=3)
    if stoch_ltf is not None: ltf = ltf.join(stoch_ltf)
    ltf.rename(columns={col: 'ltf_stoch_k' for col in ltf.columns if 'STOCHk' in col}, inplace=True)
    ltf.rename(columns={col: 'ltf_stoch_d' for col in ltf.columns if 'STOCHd' in col}, inplace=True)
    
    if 'ltf_stoch_k' in ltf.columns and 'ltf_stoch_d' in ltf.columns:
        ltf['stoch_cross_up'] = (ltf['ltf_stoch_k'] > ltf['ltf_stoch_d']) & (ltf['ltf_stoch_k'].shift(1) <= ltf['ltf_stoch_d'].shift(1))
        ltf['stoch_cross_down'] = (ltf['ltf_stoch_k'] < ltf['ltf_stoch_d']) & (ltf['ltf_stoch_k'].shift(1) >= ltf['ltf_stoch_d'].shift(1))
        
    return ltf, htf

def generate_signals(ltf, htf):
    """Merges timeframes and identifies entry triggers."""
    required_htf_cols = ['ema_25', 'htf_stoch_k', 'htf_stoch_d', 'obv', 'obv_sma_20']
    htf_aligned = htf[[c for c in required_htf_cols if c in htf.columns]].reindex(ltf.index, method='ffill').fillna(0)
    df = ltf.join(htf_aligned)
    
    df['htf_long_bias'] = (df['close'] > df['ema_25']) & (df['htf_stoch_k'] > df['htf_stoch_d']) & (df['obv'] > df['obv_sma_20'])
    df['htf_short_bias'] = (df['close'] < df['ema_25']) & (df['htf_stoch_k'] < df['htf_stoch_d']) & (df['obv'] < df['obv_sma_20'])
    df['vol_surge'] = (df['volume'] > df['volume'].shift(1)) & (df['volume'] > df['volume'].shift(2))
    
    if 'stoch_cross_up' in df.columns:
        df['long_signal'] = (df['close'] > df['open']) & df['stoch_cross_up'].shift(1).fillna(False) & df['vol_surge'] & df['htf_long_bias']
        df['short_signal'] = (df['close'] < df['open']) & df['stoch_cross_down'].shift(1).fillna(False) & df['vol_surge'] & df['htf_short_bias']
    else:
        df['long_signal'], df['short_signal'] = False, False
        
    return df.dropna()

# ==========================================
# MODULE 5: TRADE SIMULATION & SPREAD GENERATION
# ==========================================
def simulate_trades(df, symbol_key, ltf_str, htf_str, token):
    """
    Iterates through signals. When a signal is found:
    1. Looks up the expiry for that specific timestamp.
    2. Calculates the ATM and OTM2 strikes based on underlying rules.
    3. Fetches the historical options instrument keys to simulate the spread.
    """
    trades = []
    underlying_info = UNDERLYING_MAP.get(symbol_key)
    if not underlying_info:
        return pd.DataFrame()
        
    strike_step = underlying_info['strike_step']
    instrument_name = underlying_info['symbol']
    
    # Pre-fetch expiries once to save API calls in the loop
    expiries = get_historical_expiries(instrument_name, token)
    
    for idx, row in df.iterrows():
        entry_price = row['close']
        current_date = pd.to_datetime(idx)
        
        if row['long_signal'] or row['short_signal']:
            # 1. Determine active expiry for this exact trade date
            active_expiry = find_next_expiry(current_date, expiries)
            expiry_str = active_expiry.strftime('%Y-%m-%d') if active_expiry else "UNKNOWN"
            
            # 2. Calculate ATM Strike dynamically
            atm_strike = round(entry_price / strike_step) * strike_step
            
            if row['long_signal']:
                # Bull Put Spread: Sell ATM PE, Buy OTM2 PE (OTM Put is lower strike)
                otm2_strike = atm_strike - (strike_step * 2)
                
                trades.append({
                    'Entry_Time': idx,
                    'Underlying': instrument_name,
                    'Expiry': expiry_str,
                    'TF_Combo': f"{ltf_str}/{htf_str}",
                    'Trade_Type': 'Bull Put Spread',
                    'Future_Price': entry_price,
                    'Sell_Leg': f"{atm_strike} PE",
                    'Buy_Leg': f"{otm2_strike} PE",
                    # Simulated Lookup (Requires Options API Call)
                    # 'Sell_Leg_Key': get_expired_option_contract(instrument_name, expiry_str, atm_strike, 'PE', token),
                    # 'Buy_Leg_Key': get_expired_option_contract(instrument_name, expiry_str, otm2_strike, 'PE', token),
                    'Result': 'Pending_Data'
                })
                
            elif row['short_signal']:
                # Bear Call Spread: Sell ATM CE, Buy OTM2 CE (OTM Call is higher strike)
                otm2_strike = atm_strike + (strike_step * 2)
                
                trades.append({
                    'Entry_Time': idx,
                    'Underlying': instrument_name,
                    'Expiry': expiry_str,
                    'TF_Combo': f"{ltf_str}/{htf_str}",
                    'Trade_Type': 'Bear Call Spread',
                    'Future_Price': entry_price,
                    'Sell_Leg': f"{atm_strike} CE",
                    'Buy_Leg': f"{otm2_strike} CE",
                    'Result': 'Pending_Data'
                })
                
    return pd.DataFrame(trades)

def calculate_portfolio_metrics(trades_df):
    if trades_df.empty: return pd.DataFrame()
    metrics = []
    for (symbol, tf), group in trades_df.groupby(['Underlying', 'TF_Combo']):
        metrics.append({
            'Underlying': symbol,
            'Timeframes': tf,
            'Total_Trades': len(group)
        })
    return pd.DataFrame(metrics)
