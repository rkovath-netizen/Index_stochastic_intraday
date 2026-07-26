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

def push_csv_to_github(csv_string, filename, repo_path, github_token):
    """Silent push to GitHub for background threads."""
    try:
        if not github_token:
            print(f"Skipping GitHub push for {filename}: No token provided.")
            return False
        g = Github(github_token)
        repo = g.get_repo(repo_path)
        
        try:
            contents = repo.get_contents(filename, ref="main")
            repo.update_file(contents.path, f"Updating {filename}", csv_string, contents.sha, branch="main")
            print(f"Successfully updated {filename} on GitHub.")
        except:
            repo.create_file(filename, f"Creating {filename}", csv_string, branch="main")
            print(f"Successfully created {filename} on GitHub.")
            
        return True
    except Exception as e:
        print(f"GitHub push failed for {filename}: {e}")
        return False

def run_background_backtest(selected_instruments, days_to_fetch, api_token, github_repo, github_token):
    """
    This function runs completely detached from the Streamlit UI.
    It logs to the server console and pushes results directly to GitHub.
    """
    print(f"\n--- BACKGROUND BACKTEST STARTED FOR {days_to_fetch} DAYS ---")
    all_trades = pd.DataFrame()
    
    try:
        for symbol in selected_instruments:
            print(f"Processing {symbol}...")
            start_date_str = (datetime.datetime.now(IST) - datetime.timedelta(days=days_to_fetch)).strftime('%Y-%m-%d')
            
            # Fetch continuous futures data (Will hit GitHub cache instantly if available)
            df_1m, all_expiries, is_newly_built = build_continuous_futures(symbol, start_date_str, api_token, github_repo)
            
            if df_1m is None or df_1m.empty:
                print(f"Failed to fetch base data for {symbol}. Skipping.")
                continue
                
            print(f"Loaded {len(df_1m)} base rows for {symbol}.")
            
            # Backup new massive datasets to cache if they were just stitched
            if is_newly_built and github_repo and github_token:
                push_csv_to_github(df_1m.to_csv(), f"data_cache/{symbol}_continuous.csv", github_repo, github_token)
                
            for ltf_str, htf_str in TIMEFRAME_COMBOS:
                print(f"Evaluating Strategy: {ltf_str} / {htf_str} for {symbol}...")
                ltf_df, htf_df = resample_timeframes(df_1m, ltf_str, htf_str)
                ltf_df, htf_df = calculate_strategy_indicators(ltf_df, htf_df)
                signals_df = generate_signals(ltf_df, htf_df)
                
                print(f"Simulating trades and fetching Option prices for {ltf_str}/{htf_str} (This takes time)...")
                combo_trades = simulate_trades(signals_df, symbol, ltf_str, htf_str, api_token, all_expiries)
                
                if not combo_trades.empty:
                    print(f"Found {len(combo_trades)} trades for {ltf_str}/{htf_str}.")
                    all_trades = pd.concat([all_trades, combo_trades], ignore_index=True)
                else:
                    print(f"0 trades for {ltf_str}/{htf_str}.")
                    
        # --- EXECUTION FINISHED: PUSH TO GITHUB ---
        if not all_trades.empty:
            print("Backtest processing complete! Formatting metrics...")
            metrics_df = calculate_portfolio_metrics(all_trades)
            
            csv_trades_str = all_trades.to_csv(index=False)
            csv_metrics_str = metrics_df.to_csv(index=False)
            
            timestamp = datetime.datetime.now(IST).strftime("%Y%m%d_%H%M%S")
            trades_filename = f"stochastic_momentum_trades_{timestamp}.csv"
            metrics_filename = f"stochastic_momentum_metrics_{timestamp}.csv"
            
            print("Pushing final results to GitHub...")
            push_csv_to_github(csv_trades_str, f"backtest_results/{trades_filename}", github_repo, github_token)
            push_csv_to_github(csv_metrics_str, f"backtest_results/{metrics_filename}", github_repo, github_token)
            print("--- BACKGROUND BACKTEST FULLY COMPLETE & UPLOADED ---")
        else:
            print("--- BACKGROUND BACKTEST COMPLETE: 0 TRADES GENERATED ---")
            
    except Exception as e:
        print(f"CRITICAL ERROR IN BACKGROUND THREAD:\n{traceback.format_exc()}")

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
days_to_fetch = st.sidebar.number_input("Days of History", min_value=1, max_value=365, value=365)
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
    
    # Detach execution from the browser by spinning up a backend thread
    background_thread = threading.Thread(
        target=run_background_backtest, 
        args=(selected_instruments, days_to_fetch, api_token, github_repo, github_token)
    )
    background_thread.start()
    
    st.success("✅ **Execution Detached and Pushed to the Background!**")
    st.markdown("""
    ### 📱 You may now safely close this browser window.
    The server is processing all option data directly in the background. Because pricing hundreds of historical options takes time, please allow **30 to 45 minutes** for a 1-year backtest. 
    
    Once the engine completes the final mathematical calculations, it will automatically drop the Trade Log and Metrics files into your GitHub repository under the **`/backtest_results/`** folder.
    """)
