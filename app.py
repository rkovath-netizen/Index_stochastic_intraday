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

# --- GITHUB PUSH FUNCTION ---
def push_csv_to_github(csv_string, filename, repo_path):
    try:
        github_token = st.secrets.get("GITHUB_TOKEN", "")
        if not github_token:
            st.error("❌ Secret missing: 'GITHUB_TOKEN' is not in Streamlit Secrets.")
            return False
            
        g = Github(github_token)
        repo = g.get_repo(repo_path)
        
        try:
            contents = repo.get_contents(filename, ref="main")
            repo.update_file(contents.path, f"Updating {filename}", csv_string, contents.sha, branch="main")
        except:
            repo.create_file(filename, f"Creating {filename}", csv_string, branch="main")
            
        return True
    except Exception as e:
        # This will display the exact rejection reason on your screen
        st.error(f"❌ GitHub API Error for {filename}: {e}")
        return False

st.set_page_config(page_title="Stochastic Momentum Backtester", layout="wide")

st.title("📈 Stochastic Index Intraday Momentum")
st.markdown("Backtest engine for Nifty/Sensex Future signals mapped to Options Spreads.")

st.sidebar.header("1. API Configuration")
default_upstox = st.secrets.get("UPSTOX_API_TOKEN", "")
api_token = st.sidebar.text_input("Upstox API Token", type="password", value=default_upstox)

st.sidebar.header("2. Backtest Parameters")
days_to_fetch = st.sidebar.number_input("Days of History", min_value=1, max_value=365, value=5)
st.sidebar.caption("💡 Run with 5 days first to safely generate the GitHub cache file!")

selected_instruments = st.sidebar.multiselect(
    "Select Underlying Index", 
    options=list(INDEX_CONFIG.keys()), 
    default=["NIFTY", "SENSEX"]
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
        for idx, symbol in enumerate(selected_instruments):
            try:
                st.markdown(f"### ⚙️ Processing: {symbol}")
                start_date_str = (datetime.datetime.now(IST) - datetime.timedelta(days=days_to_fetch)).strftime('%Y-%m-%d')
                
                st.text(f"[{get_time()}] Building Continuous Chart from {start_date_str}...")
                
                df_1m, all_expiries, is_newly_built = build_continuous_futures(symbol, start_date_str, api_token, github_repo)
                
                if df_1m is None or df_1m.empty:
                    st.error(f"[{get_time()}] Failed to fetch data for {symbol}.")
                    continue
                    
                st.text(f"[{get_time()}] Loaded {len(df_1m)} rows.")
                
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
                
            except Exception as e:
                st.error(f"❌ Error during execution for {symbol}:")
                st.code(traceback.format_exc(), language="python")
                st.warning(f"Skipping {symbol} and moving to the next step...")
                continue
                
        status.update(label="✅ Backtest Operations Concluded!", state="complete", expanded=False)
            
    my_bar.empty() 
    
    # --- DISPLAY & EXPORT RESULTS ---
    if not all_trades.empty:
        st.success(f"[{get_time()}] Backtest successful. Preparing data...")
        metrics_df = calculate_portfolio_metrics(all_trades)
        
        csv_trades_str = all_trades.to_csv(index=False)
        csv_metrics_str = metrics_df.to_csv(index=False)
        
        timestamp = datetime.datetime.now(IST).strftime("%Y%m%d_%H%M%S")
        trades_filename = f"stochastic_momentum_trades_{timestamp}.csv"
        metrics_filename = f"stochastic_momentum_metrics_{timestamp}.csv"
        
        # 1. INSTANT GITHUB PUSH
        if github_repo and github_token:
            with st.spinner("☁️ Securing data to GitHub..."):
                t_success = push_csv_to_github(csv_trades_str, f"backtest_results/{trades_filename}", github_repo)
                m_success = push_csv_to_github(csv_metrics_str, f"backtest_results/{metrics_filename}", github_repo)
                
                if t_success and m_success:
                    st.success(f"✅ Data instantly secured! You can safely close this page. Files are in your GitHub repo under `/backtest_results/`")
                else:
                    st.warning("GitHub auto-push failed. Please check the error message above and use the manual download buttons below.")
        
        # 2. RENDER UI
        st.subheader("Performance Metrics")
        st.dataframe(metrics_df, use_container_width=True)
        
        st.subheader("Detailed Trade Log")
        st.dataframe(all_trades, use_container_width=True)
        
        # 3. MANUAL DOWNLOAD BUTTONS
        st.divider()
        st.subheader("💾 Export Options")
        col1, col2 = st.columns(2)
        col1.download_button("📥 Download Trade Log", data=csv_trades_str.encode('utf-8'), file_name=trades_filename, mime='text/csv', use_container_width=True)
        col2.download_button("📥 Download Metrics", data=csv_metrics_str.encode('utf-8'), file_name=metrics_filename, mime='text/csv', use_container_width=True)

    else:
        st.warning(f"[{get_time()}] Execution finished, but no trades were generated based on your strategy conditions.")
