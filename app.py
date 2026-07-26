"""
=====================================================================================
STOCHASTIC INDEX INTRADAY MOMENTUM - STREAMLIT WEB INTERFACE (APP.PY)
=====================================================================================
"""

import streamlit as st
import pandas as pd
import datetime
import traceback
import pytz
import threading
from github import Github

from backtest_engine import (
    INDEX_CONFIG, TIMEFRAME_COMBOS, build_continuous_futures,
    resample_timeframes, calculate_strategy_indicators,
    generate_signals, simulate_trades, calculate_portfolio_metrics
)

IST = pytz.timezone('Asia/Kolkata')

class Logger:
    def __init__(self):
        self.logs = []

    def log(self, message):
        timestamped = f"[{datetime.datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        print(timestamped)
        self.logs.append(timestamped)
        
    def get_log_string(self):
        return "\n".join(self.logs)

def push_to_github(content_string, filename, repo_path, github_token, logger=None):
    try:
        if not github_token:
            if logger: logger.log(f"Skipping GitHub push for {filename}: No token provided.")
            return False
        g = Github(github_token)
        repo = g.get_repo(repo_path)
        
        try:
            contents = repo.get_contents(filename, ref="main")
            repo.update_file(contents.path, f"Updating {filename}", content_string, contents.sha, branch="main")
            if logger: logger.log(f"Successfully updated {filename} on GitHub.")
        except:
            repo.create_file(filename, f"Creating {filename}", content_string, branch="main")
            if logger: logger.log(f"Successfully created {filename} on GitHub.")
            
        return True
    except Exception as e:
        if logger: logger.log(f"GitHub push failed for {filename}: {e}")
        return False

def run_background_backtest(selected_instruments, days_to_fetch, api_token, github_repo, github_token):
    logger = Logger()
    logger.log(f"--- BACKGROUND BACKTEST STARTED FOR {days_to_fetch} DAYS ---")
    all_trades = pd.DataFrame()
    
    try:
        for symbol in selected_instruments:
            logger.log(f"Processing {symbol}...")
            start_date_str = (datetime.datetime.now(IST) - datetime.timedelta(days=days_to_fetch)).strftime('%Y-%m-%d')
            
            df_1m, all_expiries, is_newly_built = build_continuous_futures(symbol, start_date_str, api_token, github_repo, logger=logger.log)
            
            if df_1m is None or df_1m.empty:
                logger.log(f"Failed to fetch base data for {symbol}. Skipping.")
                continue
                
            logger.log(f"Loaded {len(df_1m)} base rows for {symbol}.")
            
            if is_newly_built and github_repo and github_token:
                logger.log(f"Caching new {symbol} continuous data to GitHub...")
                push_to_github(df_1m.to_csv(), f"data_cache/{symbol}_continuous.csv", github_repo, github_token, logger)
                
            for ltf_str, htf_str in TIMEFRAME_COMBOS:
                logger.log(f"Evaluating Strategy: {ltf_str} / {htf_str} for {symbol}...")
                ltf_df, htf_df = resample_timeframes(df_1m, ltf_str, htf_str)
                ltf_df, htf_df = calculate_strategy_indicators(ltf_df, htf_df)
                signals_df = generate_signals(ltf_df, htf_df)
                
                logger.log(f"Simulating trades and fetching Option prices for {ltf_str}/{htf_str}...")
                combo_trades = simulate_trades(signals_df, symbol, ltf_str, htf_str, api_token, all_expiries, logger=logger.log)
                
                if not combo_trades.empty:
                    logger.log(f"Found {len(combo_trades)} trades for {ltf_str}/{htf_str}.")
                    all_trades = pd.concat([all_trades, combo_trades], ignore_index=True)
                else:
                    logger.log(f"0 trades for {ltf_str}/{htf_str}.")
                    
        if not all_trades.empty:
            logger.log("Backtest complete! Formatting metrics...")
            metrics_df = calculate_portfolio_metrics(all_trades)
            
            csv_trades_str = all_trades.to_csv(index=False)
            csv_metrics_str = metrics_df.to_csv(index=False)
            
            timestamp = datetime.datetime.now(IST).strftime("%Y%m%d_%H%M%S")
            trades_filename = f"stochastic_momentum_trades_{timestamp}.csv"
            metrics_filename = f"stochastic_momentum_metrics_{timestamp}.csv"
            
            logger.log("Pushing final results to GitHub...")
            push_to_github(csv_trades_str, f"backtest_results/{trades_filename}", github_repo, github_token, logger)
            push_to_github(csv_metrics_str, f"backtest_results/{metrics_filename}", github_repo, github_token, logger)
            logger.log("--- BACKGROUND BACKTEST FULLY COMPLETE & UPLOADED ---")
        else:
            logger.log("--- BACKGROUND BACKTEST COMPLETE: 0 TRADES GENERATED ---")
            
    except Exception as e:
        logger.log(f"CRITICAL ERROR IN BACKGROUND THREAD:\n{traceback.format_exc()}")
        
    finally:
        # ALWAYS push the execution log to GitHub at the very end, whether it succeeded or crashed
        if github_repo and github_token:
            push_to_github(logger.get_log_string(), "backtest_results/background_execution.log", github_repo, github_token)

# =========================================================================
# STREAMLIT UI (FRONTEND)
# =========================================================================
st.set_page_config(page_title="Stochastic Momentum Backtester", layout="wide")

st.title("📈 Stochastic Index Intraday Momentum")
st.markdown("Backtest engine for Nifty/Sensex Future signals mapped to Options Spreads.")

st.sidebar.header("1. API Configuration")
default_upstox = st.secrets.get("UPSTOX_API_TOKEN", "")
api_token = st.sidebar.text_input("Upstox API Token", type="password", value=default_upstox)

st.sidebar.header("2. Backtest Parameters")
days_to_fetch = st.sidebar.number_input("Days of History", min_value=1, max_value=365, value=30)
st.sidebar.caption("The engine will automatically run in the background for massive historical ranges.")

selected_instruments = st.sidebar.multiselect(
    "Select Underlying Index", 
    options=list(INDEX_CONFIG.keys()), 
    default=["NIFTY", "SENSEX"]
)

st.sidebar.header("3. GitHub Export Config")
github_repo = st.sidebar.text_input("GitHub Repo Path", value="rkovath-netizen/Index_stochastic_intraday")

def get_time():
    return datetime.datetime.now(IST).strftime('%H:%M:%S')

if st.sidebar.button("Run Background Backtest", type="primary"):
    st.info(f"[{get_time()}] Initializing...")
    
    if not api_token:
        st.error("Upstox API Token is missing.")
        st.stop()
    if not selected_instruments:
        st.warning("No instruments selected.")
        st.stop()
        
    github_token = st.secrets.get("GITHUB_TOKEN", "")
    
    # Detach execution
    background_thread = threading.Thread(
        target=run_background_backtest, 
        args=(selected_instruments, days_to_fetch, api_token, github_repo, github_token)
    )
    background_thread.start()
    
    st.success("✅ **Execution Detached and Pushed to the Background!**")
    st.markdown("""
    ### 📱 You may now safely close this browser window.
    The server is processing all option data directly in the background. Because pricing historical options takes time, please allow **15 to 45 minutes** for a long backtest. 
    
    Once the engine completes, it will drop the Trade Log, Metrics, and an **Execution Log** directly into your GitHub repository under the **`/backtest_results/`** folder.
    """)
