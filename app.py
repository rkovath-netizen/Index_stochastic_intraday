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
from github import Github

from backtest_engine import (
    INDEX_CONFIG, TIMEFRAME_COMBOS, build_continuous_futures,
    resample_timeframes, calculate_strategy_indicators,
    generate_signals, simulate_trades, calculate_portfolio_metrics
)

IST = pytz.timezone('Asia/Kolkata')

def push_csv_to_github(csv_string, filename, repo_path):
    try:
        github_token = st.secrets.get("GITHUB_TOKEN", "")
        if not github_token:
            return False
        g = Github(github_token)
        repo = g.get_repo(repo_path)
        
        # Check if file exists to update it, otherwise create it
        try:
            contents = repo.get_contents(filename, ref="main")
            repo.update_file(contents.path, f"Updating {filename}", csv_string, contents.sha, branch="main")
        except:
            repo.create_file(filename, f"Creating {filename}", csv_string, branch="main")
            
        return True
    except Exception as e:
        print(f"GitHub push failed for {filename}: {e}")
        return False

st.set_page_config(page_title="Stochastic Momentum Backtester", layout="wide")

st.title("📈 Stochastic Index Intraday Momentum")
st.markdown("Backtest engine for Nifty/Sensex Future signals mapped to Options Spreads.")

st.sidebar.header("1. API Configuration")
default_upstox = st.secrets.get("UPSTOX_API_TOKEN", "")
api_token = st.sidebar.text_input("Upstox API Token", type="password", value=default_upstox)

st.sidebar.header("2. Backtest Parameters")
days_to_fetch = st.sidebar.number_input("Days of History", min_value=1, max_value=365, value=90)
selected_instruments = st.sidebar.multiselect(
    "Select Underlying Index", 
    options=list(INDEX_CONFIG.keys()), 
    default=["NIFTY"]
)

st.sidebar.header("3. GitHub Export Config")
github_repo = st.sidebar.text_input("GitHub Repo Path", value="rkovath-netizen/Index_stochastic_intraday")

def get_time():
    return datetime.datetime.now(IST).strftime('%H:%M:%S')

if st.sidebar.button("Run Backtest", type="primary"):
    st.info(f"[{get_time()}] Backtest button clicked. Initializing...")
    
    if not api_token:
        st.error(f"[{get_time()}] Upstox API Token is missing.")
        st.stop()
        
    if not selected_instruments:
        st.warning(f"[{get_time()}] No instruments selected.")
        st.stop()
        
    all_trades = pd.DataFrame()
    my_bar = st.progress(0, text="Preparing to fetch data...")
    github_token = st.secrets.get("GITHUB_TOKEN", "")
    
    with st.status("🚀 Running Backtest Engine...", expanded=True) as status:
        try:
            for idx, symbol in enumerate(selected_instruments):
                st.markdown(f"### ⚙️ Processing: {symbol}")
                start_date_str = (datetime.datetime.now(IST) - datetime.timedelta(days=days_to_fetch)).strftime('%Y-%m-%d')
                
                st.text(f"[{get_time()}] Building Continuous Futures Chart from {start_date_str}...")
                
                # Notice the new flag: is_newly_built
                df_1m, all_expiries, is_newly_built = build_continuous_futures(symbol, start_date_str, api_token)
                
                if df_1m is None or df_1m.empty:
                    st.error(f"[{get_time()}] Failed to fetch data for {symbol}.")
                    continue
                    
                st.text(f"[{get_time()}] Loaded {len(df_1m)} rows.")
                
                # Automatically back up the huge continuous data file to GitHub if we just downloaded it
                if is_newly_built and github_repo and github_token:
                    st.text(f"[{get_time()}] Backing up {symbol} continuous base data to GitHub to speed up future runs...")
                    push_csv_to_github(df_1m.to_csv(), f"data_cache/{symbol}_continuous.csv", github_repo)
                    
                for ltf_str, htf_str in TIMEFRAME_COMBOS:
                    st.text(f"[{get_time()}] Evaluating: {ltf_str} / {htf_str}")
                    ltf_df, htf_df = resample_timeframes(df_1m, ltf_str, htf_str)
                    ltf_df, htf_df = calculate_strategy_indicators(ltf_df, htf_df)
                    signals_df = generate_signals(ltf_df, htf_df)
                    
                    combo_trades = simulate_trades(signals_df, symbol, ltf_str, htf_str, api_token, all_expiries)
                    
                    if not combo_trades.empty:
                        st.text(f"[{get_time()}] Found {len(combo_trades)} trades for {ltf_str}/{htf_str}.")
                        all_trades = pd.concat([all_trades, combo_trades], ignore_index=True)
                    else:
                        st.text(f"[{get_time()}] 0 trades for {ltf_str}/{htf_str}.")
                        
                my_bar.progress((idx + 1) / len(selected_instruments), text=f"Completed {symbol}")
                
            status.update(label="✅ Backtest Complete!", state="complete", expanded=False)
            
        except Exception as e:
            status.update(label="❌ ERROR: Code Crashed!", state="error", expanded=True)
            st.error(f"Error during execution:")
            st.code(traceback.format_exc(), language="python")
            st.stop()
            
    my_bar.empty() 
    
    if not all_trades.empty:
        st.success(f"[{get_time()}] Rendering results...")
        metrics_df = calculate_portfolio_metrics(all_trades)
        
        st.subheader("Performance Metrics")
        st.dataframe(metrics_df, use_container_width=True)
        
        st.subheader("Detailed Trade Log")
        st.dataframe(all_trades, use_container_width=True)
        
        csv_trades_str = all_trades.to_csv(index=False)
        csv_metrics_str = metrics_df.to_csv(index=False)
        
        timestamp = datetime.datetime.now(IST).strftime("%Y%m%d_%H%M%S")
        trades_filename = f"stochastic_momentum_trades_{timestamp}.csv"
        metrics_filename = f"stochastic_momentum_metrics_{timestamp}.csv"
        
        col1, col2 = st.columns(2)
        col1.download_button("📥 Download Trade Log", data=csv_trades_str.encode('utf-8'), file_name=trades_filename, mime='text/csv', use_container_width=True)
        col2.download_button("📥 Download Metrics", data=csv_metrics_str.encode('utf-8'), file_name=metrics_filename, mime='text/csv', use_container_width=True)
        
        if github_repo and github_token:
            with st.spinner("☁️ Auto-pushing trade results to GitHub repository..."):
                t_success = push_csv_to_github(csv_trades_str, f"backtest_results/{trades_filename}", github_repo)
                m_success = push_csv_to_github(csv_metrics_str, f"backtest_results/{metrics_filename}", github_repo)
                
                if t_success and m_success:
                    st.success("✅ Results safely pushed to your GitHub repository folder: /backtest_results/")
    else:
        st.warning(f"[{get_time()}] Execution finished, but no trades were generated.")
