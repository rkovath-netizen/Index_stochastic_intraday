# --- MAIN EXECUTION ---
if st.sidebar.button("Run Backtest", type="primary"):
    # (Initial Checks removed for brevity, keep yours intact)
    
    with st.status("🚀 Running Backtest Engine... (Expand to see live logs)", expanded=True) as status:
        try:
            for idx, symbol in enumerate(selected_instruments):
                st.markdown(f"### ⚙️ Processing: {symbol}")
                
                start_date_str = (datetime.datetime.now() - datetime.timedelta(days=days_to_fetch)).strftime('%Y-%m-%d')
                
                # The engine now handles building the continuous futures chart internally
                st.text(f"[{get_time()}] Constructing Continuous Futures Chart from {start_date_str} to today...")
                df_1m, all_expiries = build_continuous_futures(symbol, start_date_str, api_token)
                
                if df_1m is None or df_1m.empty:
                    st.error(f"[{get_time()}] ERROR: Failed to stitch futures data. API returned empty.")
                    continue
                    
                st.text(f"[{get_time()}] << Continuous Chart built successfully! Total Rows: {len(df_1m)}.")
                    
                for ltf_str, htf_str in TIMEFRAME_COMBOS:
                    st.text(f"[{get_time()}] --- Starting timeframe: {ltf_str} / {htf_str} ---")
                    
                    ltf_df, htf_df = resample_timeframes(df_1m, ltf_str, htf_str)
                    ltf_df, htf_df = calculate_strategy_indicators(ltf_df, htf_df)
                    signals_df = generate_signals(ltf_df, htf_df)
                    
                    st.text(f"[{get_time()}] >> Simulating options trades (Fetching exact weekly OHLC)...")
                    combo_trades = simulate_trades(signals_df, symbol, ltf_str, htf_str, api_token, all_expiries)
                    
                    if not combo_trades.empty:
                        st.text(f"[{get_time()}] ✅ FOUND {len(combo_trades)} trades for {ltf_str}/{htf_str}.")
                        all_trades = pd.concat([all_trades, combo_trades], ignore_index=True)
                    else:
                        st.text(f"[{get_time()}] ❌ 0 trades triggered for this combination.")
                        
                my_bar.progress((idx + 1) / len(selected_instruments), text=f"Completed {symbol}")
                
            status.update(label="✅ Backtest Execution Complete!", state="complete", expanded=False)
            
        except Exception as e:
            # Error handling remains the same...
