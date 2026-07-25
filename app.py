"""
=====================================================================================
STOCHASTIC INDEX INTRADAY MOMENTUM - STREAMLIT WEB INTERFACE (APP.PY)
=====================================================================================
DESCRIPTION:
This script acts as the frontend user interface for the backtesting engine. 
It collects user inputs (API tokens, days of history, instruments), runs the 
modular functions from `backtest_engine.py`, and displays the results interactively.

STRATEGY LOGIC:
- Long: Futures HTF Bias (Close > EMA 25, Stoch %K > %D, OBV > SMA 20) + 
        LTF Execution (Close > Open, Stoch K crosses D, Volume Surge).
        Executes a Bull Put Spread (Sell ATM PE, Buy OTM2 PE).
- Short: Reverse of the above conditions. Executes a Bear Call Spread.
- Risk/Reward: 15% Stop Loss on ATM Entry, 30% Take Profit on Net Credit.

FEATURES INCLUDED:
1. Live Status Console: Shows exact execution steps and row counts in real-time.
2. Local CSV Download: Allows saving the metrics and trade log to your local device.
3. GitHub Cloud Push: Uses the PyGithub API to push the generated CSVs directly 
   to your GitHub repository.
=====================================================================================
"""

import streamlit as st
import pandas as pd
import datetime
from github import Github

# Import modular functions from your backtest engine
from backtest_engine import (
    INSTRUMENTS, TIMEFRAME_COMBOS, fetch_historical_1m_data,
    resample_timeframes, calculate_strategy_indicators,
    generate_signals, simulate_trades, calculate_portfolio_metrics
)

# --- GITHUB PUSH FUNCTION ---
def push_csv_to_github(csv_string, filename, repo_path):
    """Pushes a CSV string directly to the specified GitHub repository."""
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
# Pull tokens from secrets if they exist, otherwise default to empty string
default_upstox = st.secrets.get("UPSTOX_API_TOKEN", "")
api_token = st.sidebar.text_input("Upstox API Token", type="password", value=default_upstox)

st.sidebar.header("2. Backtest Parameters")
days_to_fetch = st.sidebar.number_input("Days of History", min_value=1, max_value=100, value=30)
selected_instruments = st.sidebar.multiselect(
    "Select Instruments", 
    options=list(INSTRUMENTS.keys()), 
    default=list(INSTRUMENTS.keys())
)

st.sidebar.header("3. GitHub Export Config")
github_repo = st.sidebar.text_input("GitHub Repo Path", value="your_username/index_stochastic_intraday")

# --- MAIN EXECUTION ---
if st.sidebar.button("Run Backtest", type="primary"):
    if not api_token:
        st.error("Please enter your Upstox API Token in the sidebar.")
        st.stop()
        
    if not selected_instruments:
        st.warning("Please select at least one instrument.")
        st.stop()
        
    all_trades = pd.DataFrame()
    my_bar = st.progress(0, text="Initializing engine...")
    
    # Live execution console
    with st.status("🚀 Running Backtest Engine...", expanded=True) as status:
        
        for idx, symbol in enumerate(selected_instruments):
            key = INSTRUMENTS[symbol]
            st.markdown(f"### ⚙️ Processing: {symbol}")
            
            st.text(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Fetching {days_to_fetch} days of 1-minute historical data...")
            df_1m = fetch_historical_1m_data(key, api_token, days=days_to_fetch)
            
            if df_1m.empty:
                st.error(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Failed to fetch data for {symbol}. Check API token.")
                continue
                
            st.text(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✔️ Data fetch successful! Loaded {len(df_1m)} rows.")
                
            for ltf_str, htf_str in TIMEFRAME_COMBOS:
                st.text(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⏳ Evaluating Timeframes: {ltf_str} / {htf_str}")
                
                ltf_df, htf_df = resample_timeframes(df_1m, ltf_str, htf_str)
                ltf_df, htf_df = calculate_strategy_indicators(ltf_df, htf_df)
                st.text(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🧮 Calculated Indicators.")
                
                signals_df = generate_signals(ltf_df, htf_df)
                combo_trades = simulate_trades(signals_df, symbol, ltf_str, htf_str)
                
                if not combo_trades.empty:
                    st.text(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ✅ Found {len(combo_trades)} trades.")
                    all_trades = pd.concat([all_trades, combo_trades], ignore_index=True)
                else:
                    st.text(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ❌ No trades triggered.")
                    
                st.text("---")
                    
            my_bar.progress((idx + 1) / len(selected_instruments), text=f"Completed {symbol}")
            
        status.update(label="✅ Backtest Execution Complete!", state="complete", expanded=False)
            
    my_bar.empty() 
    
    # --- DISPLAY & EXPORT RESULTS ---
    if not all_trades.empty:
        st.success("Results compiled successfully!")
        
        # Display Metrics
        metrics_df = calculate_portfolio_metrics(all_trades)
        st.subheader("Performance Metrics")
        st.dataframe(metrics_df, use_container_width=True)
        
        # Display Trades
        st.subheader("Detailed Trade Log")
        st.dataframe(all_trades, use_container_width=True)
        
        # Convert DataFrames to CSV strings
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
        
        # Local Download Row
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
        
        # GitHub Push Row
        st.markdown("#### Cloud Backup")
        if st.button("☁️ Push Results to GitHub Repository", type="secondary", use_container_width=True):
            with st.spinner("Pushing files to GitHub..."):
                t_success = push_csv_to_github(csv_trades_str, f"backtest_results/{trades_filename}", github_repo)
                m_success = push_csv_to_github(csv_metrics_str, f"backtest_results/{metrics_filename}", github_repo)
                
                if t_success and m_success:
                    st.success("Successfully pushed both Trade Log and Metrics to GitHub!")
    else:
        st.warning("No trades were generated across any timeframe combinations for the selected period.")
