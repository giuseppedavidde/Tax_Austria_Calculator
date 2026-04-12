import datetime

import streamlit as st

from ecb_fx import fetch_usdeur_for_date
from oekb_scraper import fetch_oekb_tax_data

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
    isin = st.text_input("ETF ISIN", value="")
    if isin:
        oekb_url = f"https://my.oekb.at/kapitalmarkt-services/kms-output/fonds-info/sd/af/f?isin={isin}"
        st.markdown(
            f"[Open OeKB page for this ISIN]({oekb_url})", unsafe_allow_html=True
        )
        if st.button("Fetch Data from OeKB", key="fetch_kest"):
            with st.spinner("Fetching data from OeKB..."):
                tax_data = fetch_oekb_tax_data(isin)
            if tax_data:
                if "error" in tax_data:
                    st.error(f"Errore durante l'estrazione: {tax_data['error']}")
                else:
                    st.session_state["oekb_kest_val"] = tax_data.get("kest")
                    st.session_state["oekb_fonds_val"] = tax_data.get("fondsergebnis")
                    st.session_state["oekb_meldedatum"] = tax_data.get("meldedatum")
                    
                    if tax_data.get("kest") is not None and tax_data.get("fondsergebnis") is not None:
                        st.success(f"Dati estratti con successo: KESt={tax_data['kest']} | Fondsergebnis={tax_data['fondsergebnis']} (Data: {tax_data['meldedatum']})")
                    else:
                        st.warning("⚠️ Estrazione parziale. Controlla manualmente i dati sulla pagina OeKB.")
            else:
                st.error("Nessun dato o risposta vuota dal server.")

    shares = st.number_input(
        "Number of Shares", min_value=0.0, value=0.0, step=0.00001, format="%.5f"
    )
    etf_initial_cost = st.number_input(
        "ETF Initial Cost (EUR)", min_value=0.0, value=0.0, step=0.00001, format="%.5f"
    )
    value_year_before = st.number_input(
        "ETF Value Year Before (EUR)",
        min_value=0.0,
        value=0.0,
        step=0.00001,
        format="%.5f",
    )
    value_year_after = st.number_input(
        "ETF Value Year After (EUR)",
        min_value=0.0,
        value=0.0,
        step=0.00001,
        format="%.5f",
    )

# ── Column 2: KESt / Fondsergebnis / USD-EUR rate ───────────────────────────
with col2:
    val_kest = st.session_state.get("oekb_kest_val", 0.0)
    if val_kest is None: val_kest = 0.0
    oekb_kest = st.number_input(
        "Österreichische KESt (USD)",
        min_value=0.0,
        value=float(val_kest),
        step=0.00001,
        format="%.5f",
    )
    
    val_fonds = st.session_state.get("oekb_fonds_val", 0.0)
    if val_fonds is None: val_fonds = 0.0
    fondsergebnis = st.number_input(
        "Fondsergebnis der Meldeperiode (USD)",
        min_value=0.0,
        value=float(val_fonds),
        step=0.00001,
        format="%.5f",
    )

    st.subheader("USD/EUR Exchange Rate (ECB)")

    # Date picker – pre-fills from OeKB Meldedatum if available
    auto_date_str = st.session_state.get("oekb_meldedatum")
    default_date = datetime.date.today()
    if auto_date_str:
        try:
            default_date = datetime.datetime.strptime(auto_date_str.strip(), "%d.%m.%Y").date()
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
            st.error("❌ Could not fetch ECB rate. Check your internet connection or try another date.")

    # The actual exchange rate field – pre-filled from session state if fetched
    default_usdeur = st.session_state.get("ecb_usdeur", 1.0)
    usdeur = st.number_input(
        "USD/EUR Exchange Rate (editable)",
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
if shares > 0 and usdeur > 0:
    # All KeSt / Fondsergebnis values come from OeKB in USD.
    # We divide by usdeur (USD per 1 EUR) to convert to EUR.
    capital_gain_usd     = oekb_kest * shares                    # USD
    capital_gain_eur     = capital_gain_usd / usdeur             # EUR
    taxable_etf_gain_eur = value_year_after - value_year_before  # already in EUR

    tax_paid_eur = capital_gain_eur  # same thing, just clearer alias

    percentage_tax_paid = (
        (oekb_kest * 100) / (taxable_etf_gain_eur * usdeur)
        if taxable_etf_gain_eur and usdeur
        else 0
    )

    # Fondsergebnis is in USD → convert to EUR before adding to the EUR cost base
    etf_new_average_cost = etf_initial_cost + (fondsergebnis / usdeur)

    col_r1, col_r2, col_r3 = st.columns(3)
    with col_r1:
        st.metric("Capital Gain (USD)", f"{capital_gain_usd:,.5f} USD")
        st.metric("Capital Gain (EUR)", f"{capital_gain_eur:,.5f} EUR")
    with col_r2:
        st.metric("Taxable ETF Gain (EUR)", f"{taxable_etf_gain_eur:,.5f} EUR")
        st.metric("Tax Paid (EUR)", f"{tax_paid_eur:,.5f} EUR")
    with col_r3:
        st.metric("Percentage Tax Paid (%)", f"{percentage_tax_paid:,.5f} %")
        st.metric("ETF New Average Cost (EUR)", f"{etf_new_average_cost:,.5f} EUR")

    # ── Conversion summary box ─────────────────────────────────────────────
    st.info(
        f"**Exchange rate used:** 1 EUR = {usdeur:.5f} USD  "
        f"(ECB reference rate for {meldedatum_date})\n\n"
        f"Division applied: USD values ÷ {usdeur:.5f} = EUR values"
    )
else:
    st.info("Please enter all required values (Shares > 0 and a valid USD/EUR rate) to see the results.")

st.caption(
    "This dashboard is for informational purposes only. Always consult a tax professional for your specific situation."
)
