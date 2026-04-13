import csv
import datetime
import io
import re

import streamlit as st

from ecb_fx import fetch_usdeur_for_date
from oekb_scraper import fetch_oekb_tax_data


def _safe_float_csv(val) -> float:
    """Convert string from IBKR CSV to float, handling commas as thousands separators."""
    if isinstance(val, (int, float)):
        return float(val)
    if not val:
        return 0.0
    try:
        # User says: 1,048 is 1048; 1.048 is 1.048.
        # So we remove commas and keep dots.
        cleaned = str(val).strip().replace(",", "").replace("--", "0")
        return float(cleaned) if cleaned else 0.0
    except (ValueError, TypeError):
        return 0.0


def extract_data_from_ibkr_csv(file, target_isin):
    """
    Robustly parses an IBKR/Mexem CSV to extract shares and cost data for a target ISIN.
    Identifies columns by header keywords dynamically for EACH section.
    """
    file.seek(0)
    try:
        content = file.read().decode("utf-8")
    except UnicodeDecodeError:
        file.seek(0)
        try:
            content = file.read().decode("latin-1")
        except UnicodeDecodeError:
            file.seek(0)
            content = file.read().decode("cp1252")
    
    file.seek(0)
    reader = csv.reader(io.StringIO(content))
    all_rows = list(reader)
    if not all_rows:
        return None

    target_isin_upper = target_isin.upper().strip()
    
    # Mapping keywords for column detection
    # Note: Using substrings that avoid accented character issues (e.g. 'quantit' instead of 'quantità')
    keywords = {
        "symbol": ["simbolo", "symbol", "ticker", "instrument"],
        "shares": ["quantit", "quantity", "qta", "qt", "shares", "posizioni", "positions", "unit", "units"],
        "cost": ["costo", "prezzo medio", "cost price", "avg cost", "cost base"],
        "close": ["chiusura", "prezzo di chiusura", "close price", "market price", "last price", "valore di mercato"],
        "code": ["id titolo", "codice isin", "isin", "identifier", "id", "listing id", "asset id", "codice"]
    }

    # Helper to find column indices in a specific header row
    def get_row_mapping(row):
        row_lower = [c.lower().strip() for c in row]
        mapping = {}
        for cat, kws in keywords.items():
            best_idx = -1
            # Special logic: prefer columns that match specifically
            for i, cell in enumerate(row_lower):
                # 1. Try exact match
                if cell in kws:
                    best_idx = i
                    break
                # 2. Try substring match (but be careful with ambiguous ones)
                if any(kw in cell for kw in kws):
                    # For 'close', prioritize 'chiusura' or 'market' or 'last' 
                    # to avoid matching 'prezzo' in 'prezzo di costo'
                    if cat == "close" and "costo" in cell:
                        continue
                    best_idx = i
                    # Don't break yet if we're just matching a substring, 
                    # maybe there is an exact match later
            if best_idx != -1:
                mapping[cat] = best_idx
        return mapping

    # Step 1: Find the Symbol for this ISIN globally
    found_symbol = None
    # We look for the "Financial Instrument Information" section specifically if possible
    for row in all_rows:
        if not row: continue
        row_str = " ".join(row).upper()
        if target_isin_upper in row_str:
            # Try to identify which column IS the symbol in this row's section
            # As a shortcut, look for short uppercase word that is NOT the ISIN
            for cell in row:
                cell = cell.strip()
                if 1 <= len(cell) <= 10 and cell.isupper() and cell.upper() not in target_isin_upper:
                    found_symbol = cell.upper()
                    break
            if found_symbol: break

    # Step 2: Extract data using section-aware headers
    portfolio_data = None
    current_col_map = {}
    
    for row in all_rows:
        if not row or len(row) < 2: continue
        
        row_type = row[1].strip().lower() # Column 1 usually says 'Header' or 'Data'
        
        if row_type == "header":
            # Update the mapping for the current section
            current_col_map = get_row_mapping(row)
            continue
            
        elif row_type == "data":
            # If we haven't found a valid mapping for key columns, skip
            if not current_col_map or "symbol" not in current_col_map:
                continue
                
            # Check if this row belongs to our target symbol or ISIN
            row_symbol = row[current_col_map["symbol"]].strip().upper() if current_col_map["symbol"] < len(row) else ""
            
            # Check for match (either symbol matches OR the row explicitly contains the ISIN)
            match = False
            if found_symbol and row_symbol == found_symbol:
                match = True
            elif target_isin_upper in " ".join(row).upper():
                match = True
                
            if match:
                # Extract numerical values using the CURRENT section's mapping
                try:
                    shares = 0.0
                    if "shares" in current_col_map and current_col_map["shares"] < len(row):
                        shares = _safe_float_csv(row[current_col_map["shares"]])
                        
                    cost = 0.0
                    if "cost" in current_col_map and current_col_map["cost"] < len(row):
                        cost = _safe_float_csv(row[current_col_map["cost"]])
                        
                    close = 0.0
                    if "close" in current_col_map and current_col_map["close"] < len(row):
                        close = _safe_float_csv(row[current_col_map["close"]])
                    
                    # We prioritize rows that have at least shares and prices
                    if shares != 0 or cost != 0 or close != 0:
                        portfolio_data = {
                            "shares": shares,
                            "initial_cost": cost,
                            "value_after": close
                        }
                        # If we found a row with all non-zero values (like in Open Positions), we can stop
                        if shares > 0 and cost > 0 and close > 0:
                            break
                except Exception:
                    continue

    return portfolio_data


def get_all_instruments_from_csv(file):
    """
    Extracts all instrument pairs (Symbol - ISIN) from the CSV.
    """
    file.seek(0)
    try:
        content = file.read().decode("utf-8")
    except UnicodeDecodeError:
        file.seek(0)
        content = file.read().decode("latin-1")
    
    file.seek(0)
    reader = csv.reader(io.StringIO(content))
    all_rows = list(reader)
    if not all_rows:
        return []

    keywords = {
        "symbol": ["simbolo", "symbol", "ticker", "instrument"],
        "code": ["id titolo", "codice isin", "isin", "identifier", "id", "listing id", "asset id", "codice"],
        "type": ["tipo", "asset class", "categoria"]
    }

    # State for the single-pass scan
    symbol_col = None
    code_col = None
    type_col = None
    instruments = []
    seen = set()
    
    # Process rows one by one
    for idx, row in enumerate(all_rows):
        if not row: continue
        row_lower = [c.lower().strip() for c in row]
        row_str = " ".join(row_lower)
        
        # Check if this is a header row for instrument mapping
        is_mapping_header = (
            ("informazioni" in row_str and "strumento" in row_str) or \
            ("informazioni" in row_str and "strumenti" in row_str) or \
            ("financial" in row_str and "instrument" in row_str)
        ) and "header" in row_str
        
        if is_mapping_header:
            # Update columns for the current section
            for i, cell in enumerate(row_lower):
                if cell in keywords["symbol"]: symbol_col = i
                if cell in keywords["code"]: code_col = i
                if cell in keywords["type"]: type_col = i
            continue

        # If it's a data row and we have a valid mapping, try to extract
        if symbol_col is not None and code_col is not None and \
           len(row) > 1 and row[1].strip().lower() == "data":
            
            # Strict ETF check if type column exists
            is_etf = False
            if type_col is not None and len(row) > type_col:
                if row[type_col].strip().upper() == "ETF":
                    is_etf = True
            
            if is_etf:
                v_isin = row[code_col].strip().upper() if len(row) > code_col else ""
                v_sym = row[symbol_col].strip().upper() if len(row) > symbol_col else ""
                
                # Basic validation: ISIN pattern and reasonable Ticker length
                if re.match(r'[A-Z]{2}[A-Z0-9]{9,12}', v_isin) and 1 <= len(v_sym) <= 12:
                    pair = f"{v_sym} - {v_isin}"
                    if pair not in seen:
                        instruments.append(pair)
                        seen.add(pair)
    
    # Step 2: Failsafe (Fuzzy scan) if the strict section missed everything
    if not instruments:
        for row in all_rows:
            if not row: continue
            row_str = " ".join(row).upper()
            if "ETF" not in row_str: continue
            isin_matches = re.findall(r'[A-Z]{2}[A-Z0-9]{9,12}', row_str)
            for isin in isin_matches:
                for cell in row:
                    cell = cell.strip()
                    # Ticker: uppercase, short, not the ISIN itself
                    if 1 <= len(cell) <= 10 and cell.isupper() and cell not in isin:
                        pair = f"{cell} - {isin}"
                        if pair not in seen:
                            instruments.append(pair)
                            seen.add(pair)
                        break
    
    return sorted(instruments)

st.set_page_config(page_title="ETF Tax Calculator Austria", layout="wide")
st.title("ETF TAX Calculator AUSTRIA")
st.caption("Calculate Austrian ETF taxes based on OeKB data and your portfolio.")

st.markdown(
    r"""
**Calculation formulas:**

$$
\text{Capital Gain} = \text{Österreichische KESt} \times \text{Shares}
$$
$$
\text{Taxable ETF Gain} = \text{Value}_{\text{Year After}} - \text{Value}_{\text{Year Before}}
$$
$$
\text{Tax Paid (EUR)} = \frac{\text{Capital Gain (USD)}}{\text{USD/EUR}}
$$
$$
\text{Percentage Tax Paid} = \frac{\text{Österreichische KESt} \times 100}{\text{Taxable ETF Gain} \times \text{USD/EUR}}
$$
$$
\text{ETF New Average Cost} = \text{ETF Initial Cost} + \frac{\text{Fondsergebnis (USD)}}{\text{USD/EUR}}
$$

*USD/EUR is the ECB reference rate published on the OeKB Meldedatum.*
"""
)

st.header("Input Data")
col1, col2 = st.columns(2)

# ── Column 1: ISIN / shares / ETF values ────────────────────────────────────
with col1:
    # Initialize session state for inputs if not present
    if "isin_field" not in st.session_state:
        st.session_state["isin_field"] = ""
    # We still use 'isin' as a shortcut in the code
    isin = st.session_state.get("isin_field", "")
    
    if "shares" not in st.session_state:
        st.session_state["shares"] = 0.0
    if "initial_cost" not in st.session_state:
        st.session_state["initial_cost"] = 0.0
    if "value_before" not in st.session_state:
        st.session_state["value_before"] = 0.0
    if "value_after" not in st.session_state:
        st.session_state["value_after"] = 0.0
    if "available_instruments" not in st.session_state:
        st.session_state["available_instruments"] = []
    if "last_uploaded_file_name" not in st.session_state:
        st.session_state["last_uploaded_file_name"] = None
    if "trigger_oekb_fetch" not in st.session_state:
        st.session_state["trigger_oekb_fetch"] = False

    st.subheader("Broker Data Import")
    uploaded_csv = st.file_uploader("Upload IBKR/Mexem CSV to autofill fields", type=["csv"])
    
    if uploaded_csv:
        # Re-parse instruments if a new file is uploaded
        if st.session_state["last_uploaded_file_name"] != uploaded_csv.name:
            with st.spinner("Finding ETFs in CSV..."):
                instr = get_all_instruments_from_csv(uploaded_csv)
            st.session_state["available_instruments"] = instr
            st.session_state["last_uploaded_file_name"] = uploaded_csv.name
            if instr:
                st.toast(f"Found {len(instr)} ETF(s) in CSV!")
            else:
                st.toast("No ETFs found in CSV. Check format.", icon="⚠️")
            
        instr_list = st.session_state["available_instruments"]
        if instr_list:
            def on_instrument_change():
                selected = st.session_state["instrument_selector"]
                if " - " in selected:
                    isin_part = selected.split(" - ")[1]
                    st.session_state["isin_field"] = isin_part
                    st.session_state["isin"] = isin_part
                    # Flag to trigger extraction and OeKB fetch in the main loop
                    st.session_state["trigger_extraction"] = True
                    st.session_state["trigger_oekb_fetch"] = True

            st.selectbox(
                "Select Instrument found in CSV",
                options=["--- Select ---"] + instr_list,
                key="instrument_selector",
                on_change=on_instrument_change
            )

    isin = st.text_input("ETF ISIN (Manual/Selection)", key="isin_field")
    # Ensure redundant 'isin' state is kept in sync for old code references
    st.session_state["isin"] = isin

    if uploaded_csv and isin:
        # Helper to perform extraction and update ALL state/keys
        def do_extract():
            uploaded_csv.seek(0)
            data = extract_data_from_ibkr_csv(uploaded_csv, isin)
            if data:
                # Update base state
                st.session_state["shares"] = data["shares"]
                st.session_state["initial_cost"] = data["initial_cost"]
                st.session_state["value_before"] = data["initial_cost"]
                st.session_state["value_after"] = data["value_after"]
                # Update widget keys (CRITICAL for autofill UI)
                st.session_state["shares_input"] = data["shares"]
                st.session_state["initial_cost_input"] = data["initial_cost"]
                st.session_state["value_before_input"] = data["initial_cost"]
                st.session_state["value_after_input"] = data["value_after"]
                return data
            return None

    def run_oekb_logic(isin_val):
        if not isin_val:
            return
        with st.spinner("Fetching data from OeKB..."):
            tax_data = fetch_oekb_tax_data(isin_val)
        if tax_data:
            if "error" in tax_data:
                st.error(f"Errore durante l'estrazione: {tax_data['error']}")
            else:
                meldedatum = tax_data.get("meldedatum")
                st.session_state["oekb_kest_val"] = tax_data.get("kest")
                st.session_state["oekb_fonds_val"] = tax_data.get("fondsergebnis")
                st.session_state["oekb_meldedatum"] = meldedatum

                # Automate ECB rate fetch
                if meldedatum:
                    with st.spinner(f"Fetching ECB USD/EUR rate for {meldedatum}..."):
                        try:
                            dt = datetime.datetime.strptime(meldedatum, "%Y-%m-%d").date()
                            rate, actual_date = fetch_usdeur_for_date(dt)
                            if rate:
                                st.session_state["ecb_usdeur"] = rate
                        except Exception:
                            pass

                if tax_data.get("kest") is not None and tax_data.get("fondsergebnis") is not None:
                    st.success(f"Dati OeKB estratti con successo! (Data: {meldedatum})")
                else:
                    st.warning("⚠️ Estrazione OeKB parziale. Controlla manualmente i dati sulla pagina OeKB.")
        else:
            st.error("Nessun dato o risposta vuota dal server OeKB.")

    if isin:
        # Auto-trigger extraction (CSV)
        if st.session_state.get("trigger_extraction"):
            data = do_extract()
            if data:
                st.success(f"Autofill CSV completed for {isin}.")
            st.session_state["trigger_extraction"] = False
        
        # Auto-trigger OeKB fetch
        if st.session_state.get("trigger_oekb_fetch"):
            run_oekb_logic(isin)
            st.session_state["trigger_oekb_fetch"] = False

        col_btn1, col_btn2 = st.columns([1, 1])
        with col_btn1:
            if st.button("Extract data from CSV"):
                data = do_extract()
                if data:
                    st.success(f"Data found for {isin}: {data['shares']} shares.")
                else:
                    st.error(f"Could not find data for ISIN {isin} in the uploaded CSV.")

    if isin:
        oekb_url = f"https://my.oekb.at/kapitalmarkt-services/kms-output/fonds-info/sd/af/f?isin={isin}"
        st.markdown(
            f"[Open OeKB page for this ISIN]({oekb_url})", unsafe_allow_html=True
        )
        if st.button("Fetch Data from OeKB", key="fetch_kest"):
            run_oekb_logic(isin)

    shares = st.number_input(
        "Number of Shares",
        min_value=0.0,
        value=st.session_state["shares"],
        step=0.00001,
        format="%.5f",
        key="shares_input"
    )
    st.session_state["shares"] = shares

    etf_initial_cost = st.number_input(
        "ETF Initial Cost (EUR)",
        min_value=0.0,
        value=st.session_state["initial_cost"],
        step=0.00001,
        format="%.5f",
        key="initial_cost_input"
    )
    st.session_state["initial_cost"] = etf_initial_cost

    value_year_before = st.number_input(
        "ETF Value Year Before (EUR)",
        min_value=0.0,
        value=st.session_state["value_before"],
        step=0.00001,
        format="%.5f",
        key="value_before_input"
    )
    st.session_state["value_before"] = value_year_before

    value_year_after = st.number_input(
        "ETF Value Year After (EUR)",
        min_value=0.0,
        value=st.session_state["value_after"],
        step=0.00001,
        format="%.5f",
        key="value_after_input"
    )
    st.session_state["value_after"] = value_year_after

# ── Column 2: KESt / Fondsergebnis / USD-EUR rate ───────────────────────────
with col2:
    val_kest = st.session_state.get("oekb_kest_val", 0.0)
    if val_kest is None:
        val_kest = 0.0
    oekb_kest = st.number_input(
        "Österreichische KESt (USD)",
        min_value=0.0,
        value=float(val_kest),
        step=0.00001,
        format="%.5f",
    )

    val_fonds = st.session_state.get("oekb_fonds_val", 0.0)
    if val_fonds is None:
        val_fonds = 0.0
    fondsergebnis = st.number_input(
        "Fondsergebnis der Meldeperiode (USD)",
        min_value=0.0,
        value=float(val_fonds),
        step=0.00001,
        format="%.5f",
    )

    st.subheader("EUR/USD Exchange Rate (ECB)")

    # Date picker – pre-fills from OeKB Meldedatum if available
    auto_date_str = st.session_state.get("oekb_meldedatum")
    default_date = datetime.date.today()
    if auto_date_str:
        try:
            # New scraper uses YYYY-MM-DD
            default_date = datetime.datetime.strptime(
                auto_date_str.strip(), "%Y-%m-%d"
            ).date()
        except Exception:
            try:
                # Fallback for old format if session state was saved before update
                default_date = datetime.datetime.strptime(
                    auto_date_str.strip(), "%d.%m.%Y"
                ).date()
            except Exception:
                pass

    meldedatum_date = st.date_input(
        "OeKB Meldedatum (date of the official OeKB publication)",
        value=default_date,
        help=(
            "Select the OeKB Meldedatum. The ECB reference rate published on that "
            "day (or the nearest prior business day) will be fetched automatically."
        ),
    )

    # Auto-fetch button
    fetched_rate: float | None = None
    if st.button("🔄 Fetch ECB Rate for this date", key="fetch_ecb"):
        with st.spinner(f"Fetching ECB USD/EUR rate for {meldedatum_date}…"):
            fetched_rate, actual_date = fetch_usdeur_for_date(meldedatum_date)
        if fetched_rate is not None:
            if actual_date != meldedatum_date:
                st.info(
                    f"ℹ️ No ECB rate available for {meldedatum_date} (weekend/holiday). "
                    f"Using the closest prior business day: **{actual_date}**."
                )
            st.success(
                f"✅ ECB reference rate on {actual_date}: **{fetched_rate:.5f} USD/EUR**"
            )
            # Store in session state so the number_input below picks it up
            st.session_state["ecb_usdeur"] = fetched_rate
        else:
            st.error(
                "❌ Could not fetch ECB rate. Check your internet connection or try another date."
            )

    # The actual exchange rate field – pre-filled from session state if fetched
    default_usdeur = st.session_state.get("ecb_usdeur", 1.0)
    usdeur = st.number_input(
        "EUR/USD Exchange Rate (editable)",
        min_value=0.0001,
        value=default_usdeur,
        step=0.00001,
        format="%.5f",
        help="Automatically populated when you click the fetch button, but you can override it manually.",
    )

    st.markdown(
        "[📊 ECB exchange rate chart (USD)](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/"
        "euro_reference_exchange_rates/html/eurofxref-graph-usd.en.html)",
        unsafe_allow_html=True,
    )

# ── Results ──────────────────────────────────────────────────────────────────
st.header("Results")

if usdeur > 0:
    # Per-share calculations (independent of number of shares)
    kest_eur = oekb_kest / usdeur
    fondsergebnis_eur = fondsergebnis / usdeur

    st.subheader("Per-Share Tax Data")
    col_per1, col_per2 = st.columns(2)
    with col_per1:
        st.metric("KESt (USD)", f"{oekb_kest:,.5f} USD")
        st.metric("KESt (EUR)", f"{kest_eur:,.5f} EUR")
    with col_per2:
        st.metric("Fondsergebnis (USD)", f"{fondsergebnis:,.5f} USD")
        st.metric("Fondsergebnis (EUR)", f"{fondsergebnis_eur:,.5f} EUR")

    if shares > 0:
        st.divider()
        st.subheader("Portfolio Calculations")

        capital_gain_usd = oekb_kest * shares  # USD
        capital_gain_eur = capital_gain_usd / usdeur  # EUR
        taxable_etf_gain_eur = value_year_after - value_year_before  # already in EUR

        percentage_tax_paid = (
            (oekb_kest * 100) / (taxable_etf_gain_eur * usdeur)
            if taxable_etf_gain_eur and usdeur
            else 0
        )

        # Fondsergebnis is in USD → convert to EUR before adding to the EUR cost base
        etf_new_average_cost = etf_initial_cost + (fondsergebnis / usdeur)

        col_r1, col_r2, col_r3 = st.columns(3)
        with col_r1:
            st.metric("Total Capital Gain (USD)", f"{capital_gain_usd:,.2f} USD")
            st.metric("Total Capital Gain (EUR)", f"{capital_gain_eur:,.2f} EUR")
        with col_r2:
            st.metric("Taxable ETF Gain (EUR)", f"{taxable_etf_gain_eur:,.2f} EUR")
            st.metric("Tax Paid Total (EUR)", f"{capital_gain_eur:,.2f} EUR")
        with col_r3:
            st.metric("New Average Cost (EUR)", f"{etf_new_average_cost:,.5f} EUR")
            st.metric("Percentage Tax Paid (%)", f"{percentage_tax_paid:,.5f} %")
    else:
        st.info(
            "💡 Inserisci il **Number of Shares** per vedere i calcoli totali del portafoglio."
        )

    # ── Conversion summary box ─────────────────────────────────────────────
    st.info(
        f"**Exchange rate used:** 1 EUR = {usdeur:.5f} USD  "
        f"(ECB reference rate for {meldedatum_date})\n\n"
        f"Division applied: USD values ÷ {usdeur:.5f} = EUR values"
    )
else:
    st.info("Please enter a valid USD/EUR rate to see the results.")

st.caption(
    "This dashboard is for informational purposes only. Always consult a tax professional for your specific situation."
)
