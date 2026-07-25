import streamlit as st
import pandas as pd
import datetime

# Import modular functions from your backtest engine
from backtest_engine import (
    INSTRUMENTS, TIMEFRAME_COMBOS, fetch_historical_1m_data,
    resample_timeframes, calculate_strategy_indicators,
    generate_signals, simulate_trades, calculate_portfolio_metrics
)

st.set_page_config(page_title="Stochastic Momentum Backtester", layout="wide")

st.title("📈 Stochastic Index Intraday Momentum")
st.markdown("Backtest engine for Nifty/Sensex Future signals mapped to Options Spreads.")

# --- SIDEBAR CONFIGURATION ---
st.sidebar.header("Configuration")

# Secure token input via UI instead of file for cloud compatibility
api_token = st.sidebar.text_input("Upstox API Token", type="password")
days_to_fetch = st.sidebar.number_input("Days of History", min_value=1, max_value=100, value=30)

selected_instruments = st.sidebar.multiselect(
    "Select Instruments", 
    options=list(INSTRUMENTS.keys()), 
    default=list(INSTRUMENTS.keys())
)

# --- MAIN EXECUTION ---
if st.sidebar.button("Run Backtest", type="primary"):
    if not api_token:
        st.error("Please enter your Upstox API Token in the sidebar.")
        st.stop()
        
    if not selected_instruments:
        st.warning("Please select at least one instrument.")
        st.stop()
        
    all_trades = pd.DataFrame()
    
    # Progress tracking
    progress_text = "Operation in progress. Please wait."
    my_bar = st.progress(0, text=progress_text)
    
    with st.spinner('Fetching data and crunching numbers...'):
        for idx, symbol in enumerate(selected_instruments):
            key = INSTRUMENTS[symbol]
            st.write(f"**Processing {symbol}...**")
            
            # 1. Fetch Data
            df_1m = fetch_historical_1m_data(key, api_token, days=days_to_fetch)
            
            if df_1m.empty:
                st.error(f"Failed to fetch data for {symbol}. Check token or instrument key.")
                continue
                
            # 2. Process Timeframe Combinations
            for ltf_str, htf_str in TIMEFRAME_COMBOS:
                ltf_df, htf_df = resample_timeframes(df_1m, ltf_str, htf_str)
                ltf_df, htf_df = calculate_strategy_indicators(ltf_df, htf_df)
                signals_df = generate_signals(ltf_df, htf_df)
                
                combo_trades = simulate_trades(signals_df, symbol, ltf_str, htf_str)
                if not combo_trades.empty:
                    all_trades = pd.concat([all_trades, combo_trades], ignore_index=True)
                    
            my_bar.progress((idx + 1) / len(selected_instruments), text=f"Completed {symbol}")
            
    my_bar.empty() # Clear progress bar
    
    # --- DISPLAY RESULTS ---
    if not all_trades.empty:
        st.success("Backtest Completed Successfully!")
        
        # Metrics Tab
        metrics_df = calculate_portfolio_metrics(all_trades)
        st.subheader("Performance Metrics")
        st.dataframe(metrics_df, use_container_width=True)
        
        # Trade Log Tab
        st.subheader("Detailed Trade Log")
        st.dataframe(all_trades, use_container_width=True)
        
        # CSV Download Buttons
        col1, col2 = st.columns(2)
        
        # Function to convert DF to CSV in memory
        def convert_df(df):
            return df.to_csv(index=False).encode('utf-8')
            
        csv_trades = convert_df(all_trades)
        csv_metrics = convert_df(metrics_df)
        
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        
        col1.download_button(
            label="📥 Download Trade Log (CSV)",
            data=csv_trades,
            file_name=f"stochastic_momentum_trades_{timestamp}.csv",
            mime='text/csv',
        )
        col2.download_button(
            label="📥 Download Metrics (CSV)",
            data=csv_metrics,
            file_name=f"stochastic_momentum_metrics_{timestamp}.csv",
            mime='text/csv',
        )
    else:
        st.info("No trades generated across any timeframe combinations for the selected period.")
