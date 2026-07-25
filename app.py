# ... inside the main execution loop ...
for idx, symbol in enumerate(selected_instruments):
    st.markdown(f"### ⚙️ Processing: {symbol}")
    
    start_date = (datetime.datetime.now() - datetime.timedelta(days=days_to_fetch)).strftime('%Y-%m-%d')
    
    # Let the engine find the active MONTHLY future for the base chart
    monthly_expiry = get_closest_expiry(symbol, start_date, api_token, require_monthly=True)
    future_key = resolve_exact_contract(symbol, monthly_expiry, api_token, inst_type="FUTIDX")
    
    st.text(f"[{get_time()}] Fetching base data for Monthly Future Key: {future_key}")
    df_1m = fetch_candle_data(future_key, start_date, api_token, interval='1minute')
    
    if df_1m.empty:
        st.error(f"[{get_time()}] Failed to fetch base Future data for {symbol}.")
        continue
        
    # The rest remains identical...
    for ltf_str, htf_str in TIMEFRAME_COMBOS:
        # resample... calculate... simulate_trades...
