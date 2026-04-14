import csv
import datetime
import io
import re
import json

import streamlit as st
from fpdf import FPDF

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


def _sanitize_pdf_text(text):
    """Replaces characters outside of Latin-1 range to avoid FPDF encoding errors."""
    if not isinstance(text, str):
        return str(text)
    # Replace common problematic characters
    replacements = {
        "—": "-",  # Em-dash
        "–": "-",  # En-dash
        "\u20ac": "EUR",  # Euro sign (sometimes issues in old fonts)
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Encode to latin-1 and back to ignore other unsupported chars
    return text.encode("latin-1", "replace").decode("latin-1")


def create_pdf_report(report_data):
    """
    Generates a PDF report using fpdf2.
    """
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)

    # Header
    pdf.cell(0, 10, _sanitize_pdf_text("ETF Tax Report - Austria"), ln=True, align="C")
    pdf.ln(10)

    # Table-like structure
    pdf.set_font("Helvetica", "", 12)
    for key, value in report_data.items():
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(80, 10, f"{_sanitize_pdf_text(key)}:", border=0)
        pdf.set_font("Helvetica", "", 12)
        pdf.cell(0, 10, _sanitize_pdf_text(value), border=0, ln=True)

    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(
        0,
        10,
        _sanitize_pdf_text(
            f"Generated on: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ),
        ln=True,
    )
    pdf.cell(
        0,
        10,
        _sanitize_pdf_text(
            "Disclaimer: This report is for informational purposes only."
        ),
        ln=True,
    )

    return bytes(pdf.output())


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
        "shares": [
            "quantit",
            "quantity",
            "qta",
            "qt",
            "shares",
            "posizioni",
            "positions",
            "unit",
            "units",
        ],
        "cost": ["costo", "prezzo medio", "cost price", "avg cost", "cost base"],
        "close": [
            "chiusura",
            "prezzo di chiusura",
            "close price",
            "market price",
            "last price",
            "valore di mercato",
        ],
        "code": [
            "id titolo",
            "codice isin",
            "isin",
            "identifier",
            "id",
            "listing id",
            "asset id",
            "codice",
        ],
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
        if not row:
            continue
        row_str = " ".join(row).upper()
        if target_isin_upper in row_str:
            # Try to identify which column IS the symbol in this row's section
            # As a shortcut, look for short uppercase word that is NOT the ISIN
            for cell in row:
                cell = cell.strip()
                if (
                    1 <= len(cell) <= 10
                    and cell.isupper()
                    and cell.upper() not in target_isin_upper
                ):
                    found_symbol = cell.upper()
                    break
            if found_symbol:
                break

    # Step 2: Extract data using section-aware headers
    portfolio_data = None
    current_col_map = {}

    for row in all_rows:
        if not row or len(row) < 2:
            continue

        row_type = row[1].strip().lower()  # Column 1 usually says 'Header' or 'Data'

        if row_type == "header":
            # Update the mapping for the current section
            current_col_map = get_row_mapping(row)
            continue

        elif row_type == "data":
            # If we haven't found a valid mapping for key columns, skip
            if not current_col_map or "symbol" not in current_col_map:
                continue

            # Check if this row belongs to our target symbol or ISIN
            row_symbol = (
                row[current_col_map["symbol"]].strip().upper()
                if current_col_map["symbol"] < len(row)
                else ""
            )

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
                    if "shares" in current_col_map and current_col_map["shares"] < len(
                        row
                    ):
                        shares = _safe_float_csv(row[current_col_map["shares"]])

                    cost = 0.0
                    if "cost" in current_col_map and current_col_map["cost"] < len(row):
                        cost = _safe_float_csv(row[current_col_map["cost"]])

                    close = 0.0
                    if "close" in current_col_map and current_col_map["close"] < len(
                        row
                    ):
                        close = _safe_float_csv(row[current_col_map["close"]])

                    # We prioritize rows that have at least shares and prices
                    if shares != 0 or cost != 0 or close != 0:
                        portfolio_data = {
                            "shares": shares,
                            "initial_cost": cost,
                            "value_after": close,
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
        "code": [
            "id titolo",
            "codice isin",
            "isin",
            "identifier",
            "id",
            "listing id",
            "asset id",
            "codice",
        ],
        "type": ["tipo", "asset class", "categoria"],
    }

    # State for the single-pass scan
    symbol_col = None
    code_col = None
    type_col = None
    instruments = []
    seen = set()

    # Process rows one by one
    for idx, row in enumerate(all_rows):
        if not row:
            continue
        row_lower = [c.lower().strip() for c in row]
        row_str = " ".join(row_lower)

        # Check if this is a header row for instrument mapping
        is_mapping_header = (
            ("informazioni" in row_str and "strumento" in row_str)
            or ("informazioni" in row_str and "strumenti" in row_str)
            or ("financial" in row_str and "instrument" in row_str)
        ) and "header" in row_str

        if is_mapping_header:
            # Update columns for the current section
            for i, cell in enumerate(row_lower):
                if cell in keywords["symbol"]:
                    symbol_col = i
                if cell in keywords["code"]:
                    code_col = i
                if cell in keywords["type"]:
                    type_col = i
            continue

        # If it's a data row and we have a valid mapping, try to extract
        if (
            symbol_col is not None
            and code_col is not None
            and len(row) > 1
            and row[1].strip().lower() == "data"
        ):

            # Strict ETF check if type column exists
            is_etf = False
            if type_col is not None and len(row) > type_col:
                if row[type_col].strip().upper() == "ETF":
                    is_etf = True

            if is_etf:
                v_isin = row[code_col].strip().upper() if len(row) > code_col else ""
                v_sym = row[symbol_col].strip().upper() if len(row) > symbol_col else ""

                # Basic validation: ISIN pattern and reasonable Ticker length
                if (
                    re.match(r"[A-Z]{2}[A-Z0-9]{9,12}", v_isin)
                    and 1 <= len(v_sym) <= 12
                ):
                    pair = f"{v_sym} - {v_isin}"
                    if pair not in seen:
                        instruments.append(pair)
                        seen.add(pair)

    # Step 2: Failsafe (Fuzzy scan) if the strict section missed everything
    if not instruments:
        for row in all_rows:
            if not row:
                continue
            row_str = " ".join(row).upper()
            if "ETF" not in row_str:
                continue
            isin_matches = re.findall(r"[A-Z]{2}[A-Z0-9]{9,12}", row_str)
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
tab_calc, tab_agg = st.tabs(
    ["📊 Single ETF Calculator", "🏛️ Tax Declaration Aggregator"]
)

with tab_calc:
    st.title("ETF TAX Calculator AUSTRIA")
    st.caption("Calculate Austrian ETF taxes based on OeKB data and your portfolio.")

    st.markdown(
        r"""
    **Calculation formulas:**

    $$ \text{Capital Gain} = \text{Österreichische KESt} \times \text{Shares}_{\text{Meldedatum}} $$


    $$ \text{Tax Paid (EUR)} = \frac{\text{Capital Gain (USD)}}{\text{USD/EUR}} $$


    $$ \text{Percentage Tax Paid} = \frac{\text{Österreichische KESt} \times 100}{\text{abs}(\text{Taxable ETF Gain}) \times \text{USD/EUR}} $$


    $$ \text{New Average Cost} = \text{Actual Avg Cost} + \frac{\text{Shares}_{\text{Meldedatum}}}{\text{Total Shares Owned}} \times \frac{\text{Fondsergebnis (USD)}}{\text{USD/EUR}} $$
    

    *USD/EUR is the ECB reference rate published on the OeKB Meldedatum.*
    *KZ 994 (Überschüsse) corresponds to the 'Tax Paid Total (EUR)' amount in the official report.*
    """
    )

    # Initialize Session State for Aggregator
    if "agg_manual_entries_dict" not in st.session_state:
        st.session_state["agg_manual_entries_dict"] = {
            "KZ 861 (Domestic Dividend)": 0.0,
            "KZ 863 (Foreign Dividend)": 0.0,
            "KZ 174 (Withholding Tax)": 0.0,
            "KZ 998 (Others)": 0.0,
        }

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
        if "actual_avg_cost" not in st.session_state:
            st.session_state["actual_avg_cost"] = 0.0
        if "total_shares_owned" not in st.session_state:
            st.session_state["total_shares_owned"] = 0.0

        st.subheader("Broker Data Import")
        uploaded_csv = st.file_uploader(
            "Upload IBKR/Mexem CSV to autofill fields", type=["csv"]
        )

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
                    on_change=on_instrument_change,
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

                    # Try to extract the date from the report
                    # Usually in row 0: ['Rendiconto...', 'Data', '2025-01-01 a 2025-06-27']
                    # We want the second date.
                    try:
                        uploaded_csv.seek(0)
                        content = uploaded_csv.read().decode("utf-8")
                    except:
                        uploaded_csv.seek(0)
                        content = uploaded_csv.read().decode("latin-1")

                    report_date = datetime.date.today().strftime("%d-%m-%Y")
                    for line in content.splitlines()[:10]:
                        if "Data" in line and " a " in line:
                            match = re.search(r"a\s+(\d{4}-\d{2}-\d{2})", line)
                            if match:
                                y, m, d = match.group(1).split("-")
                                report_date = f"{d}-{m}-{y}"
                                break
                    st.session_state["report_date"] = report_date

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
                        with st.spinner(
                            f"Fetching ECB USD/EUR rate for {meldedatum}..."
                        ):
                            try:
                                dt = datetime.datetime.strptime(
                                    meldedatum, "%Y-%m-%d"
                                ).date()
                                rate, actual_date = fetch_usdeur_for_date(dt)
                                if rate:
                                    st.session_state["ecb_usdeur"] = rate
                            except Exception:
                                pass

                    if (
                        tax_data.get("kest") is not None
                        and tax_data.get("fondsergebnis") is not None
                    ):
                        st.success(
                            f"Dati OeKB estratti con successo! (Data: {meldedatum})"
                        )
                    else:
                        st.warning(
                            "⚠️ Estrazione OeKB parziale. Controlla manualmente i dati sulla pagina OeKB."
                        )
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
                        st.error(
                            f"Could not find data for ISIN {isin} in the uploaded CSV."
                        )

        if isin:
            oekb_url = f"https://my.oekb.at/kapitalmarkt-services/kms-output/fonds-info/sd/af/f?isin={isin}"
            st.markdown(
                f"[Open OeKB page for this ISIN]({oekb_url})", unsafe_allow_html=True
            )
            if st.button("Fetch Data from OeKB", key="fetch_kest"):
                run_oekb_logic(isin)

        shares = st.number_input(
            "Number of Shares at Meldedatum (n1)",
            min_value=0.0,
            value=st.session_state["shares"],
            step=0.00001,
            format="%.5f",
            key="shares_input",
            help="The amount of shares you held at the date of the tax event (Meldedatum).",
        )
        st.session_state["shares"] = shares

        st.subheader("Current Portfolio Status")
        actual_avg_cost = st.number_input(
            "Actual Average Cost of the ETF (EUR) (M_old)",
            min_value=0.0,
            value=st.session_state["actual_avg_cost"],
            step=0.00001,
            format="%.5f",
            key="actual_avg_cost_input",
            help="Your current average purchase price per share.",
        )
        st.session_state["actual_avg_cost"] = actual_avg_cost

        total_shares_owned = st.number_input(
            "Actual Number of Shares owned (N)",
            min_value=0.0,
            value=st.session_state["total_shares_owned"],
            step=0.00001,
            format="%.5f",
            key="total_shares_owned_input",
            help="The total number of shares you own currently.",
        )
        st.session_state["total_shares_owned"] = total_shares_owned

        value_year_before = st.number_input(
            "ETF Value Year Before (EUR)",
            min_value=0.0,
            value=st.session_state["value_before"],
            step=0.00001,
            format="%.5f",
            key="value_before_input",
        )
        st.session_state["value_before"] = value_year_before

        value_year_after = st.number_input(
            "ETF Value Year After (EUR)",
            min_value=0.0,
            value=st.session_state["value_after"],
            step=0.00001,
            format="%.5f",
            key="value_after_input",
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
            taxable_etf_gain_eur = (
                value_year_after - value_year_before
            )  # already in EUR

            percentage_tax_paid = (
                (oekb_kest * 100) / (abs(taxable_etf_gain_eur) * usdeur)
                if taxable_etf_gain_eur and usdeur
                else 0
            )

            # New calculation logic based on user's reasoning
            # M_new = M_old + (n1 / N) * delta_m
            delta_m = fondsergebnis / usdeur
            if total_shares_owned > 0:
                etf_new_average_cost = (
                    actual_avg_cost + (shares / total_shares_owned) * delta_m
                )
            else:
                etf_new_average_cost = (
                    actual_avg_cost + delta_m if shares > 0 else actual_avg_cost
                )

            # New Total Value is based on new avg cost and total shares owned
            etf_new_total_value = etf_new_average_cost * total_shares_owned

            col_r1, col_r2, col_r3 = st.columns(3)
            with col_r1:
                st.metric(
                    "KZ 994 - Überschüsse (EUR)",
                    f"€ {capital_gain_eur:,.2f}",
                    help="Amt to put in the KZ 994 field of the Finanzamt report.",
                )
                st.metric("Total Tax (USD)", f"{capital_gain_usd:,.2f} USD")
            with col_r2:
                st.metric("Tax Paid Total (EUR)", f"{capital_gain_eur:,.2f} EUR")
                st.metric("Taxable ETF Gain (EUR)", f"{taxable_etf_gain_eur:,.2f} EUR")
            with col_r3:
                st.metric("New Average Cost (EUR)", f"{etf_new_average_cost:,.5f} EUR")
                st.metric(
                    "New Portfolio Value (EUR)", f"{etf_new_total_value:,.2f} EUR"
                )

            # Percentage metric
            st.metric("Percentage Tax Paid (%)", f"{percentage_tax_paid:,.5f} %")

            st.divider()
            st.subheader("Final JSON Report Summary")

            # Prepare JSON report
            # Priority: 1. OeKB Meldedatum, 2. CSV Report Date, 3. Today
            oekb_date_raw = st.session_state.get("oekb_meldedatum")
            if oekb_date_raw:
                try:
                    y, m, d = oekb_date_raw.split("-")
                    report_date = f"{d}-{m}-{y}"
                except:
                    report_date = st.session_state.get(
                        "report_date", datetime.date.today().strftime("%d-%m-%Y")
                    )
            else:
                report_date = st.session_state.get(
                    "report_date", datetime.date.today().strftime("%d-%m-%Y")
                )
            json_report = {
                "Instrument": st.session_state.get("isin", "Unknown"),
                "Date": report_date,
                "Shares at Event (n1)": round(shares, 5),
                "Actual Shares Owned (N)": round(total_shares_owned, 5),
                "Actual Average Cost (EUR)": round(actual_avg_cost, 5),
                "New Average Cost (EUR)": round(etf_new_average_cost, 5),
                "New Portfolio Value (EUR)": round(etf_new_total_value, 2),
                "Tax Paid Total (EUR)": round(capital_gain_eur, 2),
                "KZ 994 - Überschüsse (EUR)": round(capital_gain_eur, 2),
            }

            st.json(json_report)

            col_dl1, col_dl2 = st.columns(2)
            with col_dl1:
                # Download button for JSON
                st.download_button(
                    label="Download JSON Report",
                    data=json.dumps(json_report, indent=4),
                    file_name=f"ETF_Tax_Report_{report_date}.json",
                    mime="application/json",
                )

            with col_dl2:
                # Download button for PDF
                pdf_bytes = create_pdf_report(json_report)
                st.download_button(
                    label="Download PDF Report",
                    data=pdf_bytes,
                    file_name=f"ETF_Tax_Report_{report_date}.pdf",
                    mime="application/pdf",
                )
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

with tab_agg:
    st.header("🏛️ Tax Declaration Aggregator (Finanzonline)")
    st.info(
        "Upload multiple JSON reports from this calculator or the IBKR Tax Calculator to aggregate your results. You can also add manual entries for other fields."
    )

    col_agg_l, col_agg_r = st.columns([1, 1])

    with col_agg_l:
        st.subheader("📥 Upload JSON Reports")
        uploaded_jsons = st.file_uploader(
            "Select one or more JSON files", type=["json"], accept_multiple_files=True
        )

        aggregated_kz = {}  # KZ -> total

        if uploaded_jsons:
            for f in uploaded_jsons:
                try:
                    f.seek(0)
                    raw_content = f.read().decode("utf-8")

                    # Robust cleaning for common "broken" JSON patterns like np.float64(1.23)
                    # This handles existing reports generated before the fix.
                    clean_content = re.sub(
                        r"np\.float\d+\(([^)]+)\)", r"\1", raw_content
                    )

                    report = json.loads(clean_content)
                    for key, val in report.items():
                        # Look for KZ pattern e.g. "KZ 994"
                        match = re.search(r"KZ\s?(\d{3})", key)
                        if match:
                            kz_num = match.group(1)
                            label = f"KZ {kz_num}"
                            if isinstance(val, (int, float)):
                                aggregated_kz[label] = aggregated_kz.get(
                                    label, 0.0
                                ) + float(val)
                except Exception as e:
                    st.error(f"Error parsing {f.name}: {e}")

        st.subheader("📝 Manual Entries (Calculator)")
        st.info("Use the +/- steppers to update the totals for each field.")

        # New manual entry UX: Accumulator with Delta inputs
        for kz_key in st.session_state["agg_manual_entries_dict"].keys():
            col_in1, col_in2, col_in3, col_in4, col_in5 = st.columns([3, 2, 2, 1, 1])
            with col_in1:
                st.write(f"**{kz_key}**")
            with col_in2:
                current_total = st.session_state["agg_manual_entries_dict"][kz_key]
                st.markdown(f"**Total:** € {current_total:,.2f}")
            with col_in3:
                # Delta input
                delta = st.number_input(
                    "Delta",
                    value=0.0,
                    step=1.0,
                    format="%.2f",
                    key=f"delta_input_{kz_key}",
                    label_visibility="collapsed",
                )
            with col_in4:
                if st.button("➕", key=f"btn_add_{kz_key}", help="Add to total"):
                    st.session_state["agg_manual_entries_dict"][kz_key] += delta
                    st.rerun()
            with col_in5:
                if st.button("➖", key=f"btn_sub_{kz_key}", help="Subtract from total"):
                    st.session_state["agg_manual_entries_dict"][kz_key] -= delta
                    st.rerun()

        if st.button("Reset Manual Fields"):
            for k in st.session_state["agg_manual_entries_dict"]:
                st.session_state["agg_manual_entries_dict"][k] = 0.0
            st.rerun()

    with col_agg_r:
        st.subheader("📊 Final Aggregation Summary")

        # Combine Report Data and Manual Data
        final_summary = {}

        # 1. Add aggregated report data
        for kz, val in aggregated_kz.items():
            final_summary[kz] = final_summary.get(kz, 0.0) + val

        # 2. Add manual entries from the dict
        for kz_label_full, amount in st.session_state[
            "agg_manual_entries_dict"
        ].items():
            # Extract the KZ number to match report keys
            match = re.search(r"KZ\s?(\d{3})", kz_label_full)
            if match:
                kz_simple = f"KZ {match.group(1)}"
                final_summary[kz_simple] = final_summary.get(kz_simple, 0.0) + float(
                    amount
                )

        if not final_summary:
            st.info(
                "No data yet. Upload JSONs or enter manual data to see the summary."
            )
        else:
            col_kz1, col_kz2 = st.columns(2)
            sorted_kz = sorted(final_summary.keys())
            half = (len(sorted_kz) + 1) // 2

            with col_kz1:
                for kz in sorted_kz[:half]:
                    val = final_summary[kz]
                    color = "green" if val >= 0 else "red"
                    st.markdown(
                        f"**{kz}**: <span style='color:{color}; font-size: 1.1em;'>{val:+,.2f} EUR</span>",
                        unsafe_allow_html=True,
                    )
            with col_kz2:
                for kz in sorted_kz[half:]:
                    val = final_summary[kz]
                    color = "green" if val >= 0 else "red"
                    st.markdown(
                        f"**{kz}**: <span style='color:{color}; font-size: 1.1em;'>{val:+,.2f} EUR</span>",
                        unsafe_allow_html=True,
                    )

            st.divider()
            st.subheader("🧾 Estimated Tax Breakdown")

            # Calculation logic for magnitudes
            kz_994 = abs(final_summary.get("KZ 994", 0.0))
            kz_892 = abs(final_summary.get("KZ 892", 0.0))
            kz_995 = abs(final_summary.get("KZ 995", 0.0))
            kz_896 = abs(final_summary.get("KZ 896", 0.0))
            kz_861 = abs(final_summary.get("KZ 861", 0.0))
            kz_863 = abs(final_summary.get("KZ 863", 0.0))
            kz_174 = abs(final_summary.get("KZ 174", 0.0))
            kz_998 = abs(final_summary.get("KZ 998", 0.0))

            tax_861 = kz_861 * 0.25
            tax_863 = kz_863 * 0.275
            tax_174 = kz_174 * 0.275
            tax_998 = kz_998 * 0.275

            net_stocks = max(0.0, kz_994 - kz_892)
            tax_stocks = net_stocks * 0.275

            net_deriv = max(0.0, kz_995 - kz_896)
            tax_deriv = net_deriv * 0.275

            total_est_tax = (
                tax_861 + tax_863 + tax_174 + tax_998 + tax_stocks + tax_deriv
            )

            col_t1, col_t2 = st.columns(2)
            with col_t1:
                st.write(f"Dividends (KZ 861 @ 25%): **€ {tax_861:,.2f}**")
                st.write(f"Foreign Div. (KZ 863 @ 27.5%): **€ {tax_863:,.2f}**")
                st.write(f"Stocks/ETFs (Net @ 27.5%): **€ {tax_stocks:,.2f}**")
            with col_t2:
                st.write(f"Derivatives (Net @ 27.5%): **€ {tax_deriv:,.2f}**")
                st.write(
                    f"Others (KZ 174/998 @ 27.5%): **€ {(tax_174 + tax_998):,.2f}**"
                )

            st.metric(
                "Total Estimated Tax to Pay",
                f"€ {total_est_tax:,.2f}",
                delta_color="inverse",
            )

            est_taxes_dict = {
                "Domestic Dividends Tax (KZ 861)": round(tax_861, 2),
                "Foreign Dividends Tax (KZ 863)": round(tax_863, 2),
                "Stocks Net Tax (KZ 994-892)": round(tax_stocks, 2),
                "Derivatives Net Tax (KZ 995-896)": round(tax_deriv, 2),
                "Other Taxes (KZ 174, 998)": round(tax_174 + tax_998, 2),
                "TOTAL_ESTIMATED_TAX": round(total_est_tax, 2),
            }

            st.divider()
            st.success("Use these totals for your Finanzonline (E1kv) declaration.")

            # Export aggregated reports
            col_exp1, col_exp2 = st.columns(2)
            with col_exp1:
                # Prepare JSON report
                agg_report = {
                    "Generated_At": datetime.datetime.now().isoformat(),
                    "Aggregated_Values": final_summary,
                    "Estimated_Taxes": est_taxes_dict,
                    "Manual_Entries": st.session_state["agg_manual_entries_dict"],
                }
                st.download_button(
                    label="💾 Download Aggregated JSON",
                    data=json.dumps(agg_report, indent=4),
                    file_name=f"Aggregated_Tax_Report_{datetime.date.today()}.json",
                    mime="application/json",
                )

            with col_exp2:
                # Prepare PDF report - flatten final_summary for the PDF function
                pdf_data = {
                    "Report Type": "Aggregated Tax Summary (Finanzonline)",
                    "Date": datetime.date.today().strftime("%d-%m-%Y"),
                    "------": "--- KZ TOTALS ---",
                    **{k: f"€ {v:,.2f}" for k, v in sorted(final_summary.items())},
                    "-------": "--- ESTIMATED TAXES ---",
                    **{k: f"€ {v:,.2f}" for k, v in est_taxes_dict.items()},
                }
                pdf_bytes = create_pdf_report(pdf_data)
                st.download_button(
                    label="📄 Download Aggregated PDF",
                    data=pdf_bytes,
                    file_name=f"Aggregated_Tax_Report_{datetime.date.today()}.pdf",
                    mime="application/pdf",
                )

st.caption(
    "This dashboard is for informational purposes only. Always consult a tax professional for your specific situation."
)
