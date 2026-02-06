import streamlit as st
import pandas as pd
from oekb_scraper import fetch_oekb_kest

st.set_page_config(page_title="ETF Tax Calculator Austria", layout="wide")
st.title("ETF TAX Calculator AUSTRIA")
st.caption("Calculate Austrian ETF taxes based on OeKB data and your portfolio.")

st.markdown("""
**Calculation formulas:**

Capital_Gain = Österreichische_KESt × #Shares  
Taxable_ETF_Gain = Value_Year_After − Value_Year_Before  
Tax_Paid = Capital_Gain / USDEUR  
Percentage_Tax_Paid = (Österreichische KESt × 100) / (Taxable_ETF_Gain × USDEUR)  
ETF_New_Average_Cost = ETF_Initial_Cost + (Fondsergebnis der Meldeperiode × USDEUR)
USDEUR Must be evaluated in the day when OekB data is released.
""", unsafe_allow_html=True)

st.header("Input Data")
col1, col2 = st.columns(2)
with col1:
    isin = st.text_input("ETF ISIN", value="")
    kest_auto = None
    meldedatum = None
    if isin:
        oekb_url = f"https://my.oekb.at/kapitalmarkt-services/kms-output/fonds-info/sd/af/f?isin={isin}"
        st.markdown(f"[Open OeKB page for this ISIN]({oekb_url})", unsafe_allow_html=True)
        if st.button("Fetch KESt from OeKB", key="fetch_kest"):
            with st.spinner("Fetching KESt from OeKB..."):
                kest_auto, meldedatum, stmId = fetch_oekb_kest(isin)
            if kest_auto is not None:
                st.success(f"KESt found: {kest_auto} (Meldedatum: {meldedatum})")
            else:
                st.warning("KESt value not found for this ISIN.")
    shares = st.number_input("Number of Shares", min_value=0.0, value=0.0, step=0.00001, format="%.5f")
    etf_initial_cost = st.number_input("ETF Initial Cost (EUR)", min_value=0.0, value=0.0, step=0.00001, format="%.5f")
    value_year_before = st.number_input("ETF Value Year Before (EUR)", min_value=0.0, value=0.0, step=0.00001, format="%.5f")
    value_year_after = st.number_input("ETF Value Year After (EUR)", min_value=0.0, value=0.0, step=0.00001, format="%.5f")
with col2:
    oekb_kest = st.number_input("Österreichische KESt (USD)", min_value=0.0, value=0.0, step=0.00001, format="%.5f")
    fondsergebnis = st.number_input("Fondsergebnis der Meldeperiode (USD)", min_value=0.0, value=0.0, step=0.00001, format="%.5f")
    usdeur = st.number_input("USD/EUR Exchange Rate", min_value=0.0001, value=1.0, step=0.00001, format="%.5f")
    st.markdown('[Get USD/EUR exchange rate from ECB](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html)', unsafe_allow_html=True)

st.header("Results")
if shares > 0 and usdeur > 0:
    capital_gain = oekb_kest * shares
    taxable_etf_gain = value_year_after - value_year_before
    tax_paid = capital_gain / usdeur if usdeur else 0
    percentage_tax_paid = (oekb_kest * 100) / (taxable_etf_gain * usdeur) if taxable_etf_gain and usdeur else 0
    etf_new_average_cost = etf_initial_cost + (fondsergebnis * usdeur)

    st.metric("Capital Gain (USD)", f"{capital_gain:,.5f}")
    st.metric("Taxable ETF Gain (EUR)", f"{taxable_etf_gain:,.5f}")
    st.metric("Tax Paid (EUR)", f"{tax_paid:,.5f}")
    st.metric("Percentage Tax Paid (%)", f"{percentage_tax_paid:,.5f}")
    st.metric("ETF New Average Cost (EUR)", f"{etf_new_average_cost:,.5f}")
else:
    st.info("Please enter all required values to see the results.")

st.caption("This dashboard is for informational purposes only. Always consult a tax professional for your specific situation.")
