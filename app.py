"""
=====================================================================================
STOCHASTIC INDEX INTRADAY MOMENTUM - STREAMLIT WEB INTERFACE
=====================================================================================
"""

import streamlit as st
import pandas as pd
import datetime
import traceback
from github import Github

# Import modular functions from your backtest engine
from backtest_engine import (
    INDEX_CONFIG, TIMEFRAME_COMBOS, get_closest_expiry, resolve_exact_contract,
    fetch_candle_data, resample_timeframes, calculate_strategy_indicators,
    generate_signals, simulate_trades, calculate_portfolio_metrics
)

# --- GITHUB PUSH FUNCTION ---
def push_csv_to_github(csv_string, filename, repo_path):
    try:
        github_token = st.secrets.get("GITHUB_TOKEN", "")
        if not github_token:
            st.error("GitHub Token not found in Streamlit Secrets.")
            return False
            
        g = Github(github_token)
        repo = g.get_repo(repo_path)
        
        repo.create_file(
            path=filename, 
            message=f"Auto-generated backtest results: {filename}", 
            content=csv_string, 
            branch="main" 
        )
        return True
    except Exception as e:
        st.error(f"Failed to save to GitHub: {e}")
        return False

# --- UI INITIALIZATION ---
st.set_page_config(page_title="Stochastic Momentum Backtester", layout="wide")

st.title("📈 Stochastic Index Intraday Momentum")
st.markdown("Backtest engine for Nifty/Sensex Future signals mapped to Options Spreads.")

# --- SIDEBAR CONFIGURATION ---
st.sidebar.header("1. API Configuration")
default_upstox = st.secrets.get("UPSTOX_API_TOKEN", "")
api_token = st.sidebar.text_input("Upstox API Token", type="password", value=default_upstox)

st.sidebar.header("2. Backtest Parameters")
days_to_fetch = st.sidebar.number_input("Days of History", min_value=1, max_value=100, value=30)
selected_instruments = st.sidebar.multiselect(
    "Select Underlying Index", 
    options=list(INDEX_CONFIG.keys()), 
    default=["NIFTY"]
)

st.sidebar.header("3. GitHub Export Config")
github_repo = st.sidebar.text_input("GitHub Repo Path", value="your_username/index_stochastic_intraday")

def get_time():
    return datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]

# --- MAIN EXECUTION ---
if st.sidebar.button("Run Backtest", type="primary"):
    st.info(f"[{get_time()}] Backtest button clicked. Initializing checks...")
    
    if not api_token:
        st.error(f"[{get_time()}] ABORT: Upstox API Token is missing.")
        st.stop()
        
    if not selected_instruments:
        st.warning(f"[{get_time()}] ABORT: No instruments selected.")
        st.stop()
        
    st.success(f"[{get_time()}] Checks passed. Token and instruments registered. Starting engine...")
        
    all_trades = pd.DataFrame()
    my_bar = st.progress(0, text="Preparing to fetch data...")
    
    with st.status("🚀 Running Backtest Engine... (Expand to see live logs)", expanded=True) as status:
        try:
            for idx, symbol in enumerate(selected_instruments):
                st.markdown(f"### ⚙️ Processing: {symbol}")
                
                # 1. Date Calculation
                start_date_str = (datetime.datetime.now() - datetime.timedelta(days=days_to_fetch)).strftime('%Y-%m-%d')
                
                # 2. Dynamic Monthly Future Resolution
                st.text(f"[{get_time()}] Resolving closest Monthly Expiry for {symbol}...")
                monthly_expiry = get_closest_expiry(symbol, start_date_str, api_token, require_monthly=True)
                
                if not monthly_expiry:
                    st.error(f"[{get_time()}] Could not find a valid monthly expiry for {symbol}.")
                    continue
                    
                st.text(f"[{get_time()}] Monthly Expiry found: {monthly_expiry}. Resolving exact Future Key...")
                future_key = resolve_exact_contract(symbol, monthly_expiry, api_token, inst_type="FUTIDX")
                
                if not future_key:
                    st.error(f"[{get_time()}] Could not resolve the exact future instrument key for {monthly_expiry}.")
                    continue
                
                # 3. Fetch Base Data
                st.text(f"[{get_time()}] >> Requesting {days_to_fetch} days of 1m data for {future_key}...")
                df_1m = fetch_candle_data(future_key, start_date_str, api_token, interval='1minute')
                
                if df_1m is None or df_1m.empty:
                    st.error(f"[{get_time()}] ERROR: Dataframe is empty. The API returned no data for {symbol} Future.")
                    continue
                    
                st.text(f"[{get_time()}] << Data fetch complete! Loaded {len(df_1m)} rows.")
                    
                # 4. Timeframe Loop
                for ltf_str, htf_str in TIMEFRAME_COMBOS:
                    st.text(f"[{get_time()}] --- Starting timeframe: {ltf_str} / {htf_str} ---")
                    
                    st.text(f"[{get_time()}] >> Resampling timeframes...")
                    ltf_df, htf_df = resample_timeframes(df_1m, ltf_str, htf_str)
                    
                    st.text(f"[{get_time()}] >> Calculating indicators...")
                    ltf_df, htf_df = calculate_strategy_indicators(ltf_df, htf_df)
                    
                    st.text(f"[{get_time()}] >> Generating entry/exit signals...")
                    signals_df = generate_signals(ltf_df, htf_df)
                    st.text(f"[{get_time()}] << {len(signals_df)} valid signal timestamps found.")
                    
                    st.text(f"[{get_time()}] >> Simulating options trades (Fetching historical Option OHLC)...")
                    # Note: Passing api_token down to simulate_trades so it can fetch the option prices
                    combo_trades = simulate_trades(signals_df, symbol, ltf_str, htf_str, api_token)
                    
                    if not combo_trades.empty:
                        st.text(f"[{get_time()}] ✅ FOUND {len(combo_trades)} trades for {ltf_str}/{htf_str}.")
                        all_trades = pd.concat([all_trades, combo_trades], ignore_index=True)
                    else:
                        st.text(f"[{get_time()}] ❌ 0 trades triggered for this combination.")
                        
                my_bar.progress((idx + 1) / len(selected_instruments), text=f"Completed {symbol}")
                
            status.update(label="✅ Backtest Execution Complete!", state="complete", expanded=False)
            
        except Exception as e:
            status.update(label="❌ ERROR: Code Crashed!", state="error", expanded=True)
            st.error(f"An unexpected error occurred during execution:")
            st.code(traceback.format_exc(), language="python")
            st.stop()
            
    my_bar.empty() 
    
    # --- DISPLAY & EXPORT RESULTS ---
    if not all_trades.empty:
        st.success(f"[{get_time()}] UI Rendering: Displaying results tables...")
        
        metrics_df = calculate_portfolio_metrics(all_trades)
        st.subheader("Performance Metrics")
        st.dataframe(metrics_df, use_container_width=True)
        
        st.subheader("Detailed Trade Log")
        st.dataframe(all_trades, use_container_width=True)
        
        def convert_df(df):
            return df.to_csv(index=False).encode('utf-8')
            
        csv_trades_bytes = convert_df(all_trades)
        csv_metrics_bytes = convert_df(metrics_df)
        csv_trades_str = all_trades.to_csv(index=False)
        csv_metrics_str = metrics_df.to_csv(index=False)
        
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        trades_filename = f"stochastic_momentum_trades_{timestamp}.csv"
        metrics_filename = f"stochastic_momentum_metrics_{timestamp}.csv"
        
        st.divider()
        st.subheader("💾 Export Options")
        
        col1, col2 = st.columns(2)
        col1.download_button(
            label="📥 Download Trade Log to Device",
            data=csv_trades_bytes,
            file_name=trades_filename,
            mime='text/csv',
            use_container_width=True
        )
        col2.download_button(
            label="📥 Download Metrics to Device",
            data=csv_metrics_bytes,
            file_name=metrics_filename,
            mime='text/csv',
            use_container_width=True
        )
        
        st.markdown("#### Cloud Backup")
        if st.button("☁️ Push Results to GitHub Repository", type="secondary", use_container_width=True):
            with st.spinner("Pushing files to GitHub..."):
                t_success = push_csv_to_github(csv_trades_str, f"backtest_results/{trades_filename}", github_repo)
                m_success = push_csv_to_github(csv_metrics_str, f"backtest_results/{metrics_filename}", github_repo)
                
                if t_success and m_success:
                    st.success("Successfully pushed both Trade Log and Metrics to GitHub!")
    else:
        st.warning(f"[{get_time()}] Execution finished, but the final trade log was empty.")
