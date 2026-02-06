import requests
from bs4 import BeautifulSoup

def fetch_oekb_kest(isin):
    """
    Fetch the latest Österreichische KESt value for a given ISIN from the OeKB website.
    Returns (kest_value, meldedatum, stmId) or (None, None, None) if not found.
    """
    base_url = "https://my.oekb.at/kapitalmarkt-services/kms-output/fonds-info/sd/af/f"
    params = {"isin": isin}
    resp = requests.get(base_url, params=params)
    if resp.status_code != 200:
        return None, None, None
    soup = BeautifulSoup(resp.text, "html.parser")
    # Find the Meldedatum and stmId
    meldedatum_link = soup.find("a", string=lambda s: s and "Meldedatum" in s)
    if not meldedatum_link:
        return None, None, None
    stmId = meldedatum_link.get("href", "").split("stmId=")[-1]
    meldedatum = meldedatum_link.text.strip()
    # Follow the stmId link
    detail_url = f"{base_url}?isin={isin}&stmId={stmId}"
    resp2 = requests.get(detail_url)
    if resp2.status_code != 200:
        return None, meldedatum, stmId
    soup2 = BeautifulSoup(resp2.text, "html.parser")
    # Find the KESt value (look for the label or table row)
    kest_value = None
    for row in soup2.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2 and "Österreichische KESt" in cells[0].text:
            kest_value = cells[1].text.strip().replace(".", "").replace(",", ".")
            try:
                kest_value = float(kest_value)
            except Exception:
                pass
            break
    return kest_value, meldedatum, stmId
