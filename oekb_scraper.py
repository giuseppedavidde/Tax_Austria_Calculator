import json
import requests


def _build_oekb_headers():
    return {
        "Accept": "application/json",
        "Accept-Language": "de",
        "Connection": "keep-alive",
        "OeKB-Platform-Context": "=",
        "Referer": "https://my.oekb.at/kapitalmarkt-services/kms-output/fonds-info/sd/af/m",
        "Sec-Fetch-Dest": "empty",
    }


def _get_fond_id(isin: str, headers: dict) -> str | None:
    """
    Resolve the OeKB internal fondId (numeric) for a given ISIN.
    Tries the wp-info REST API first, then falls back to the fond-info search.
    """
    # Try wp-info / wertpapier lookup (returns fondId directly)
    try:
        url = f"https://my.oekb.at/wp-info/rest/public/wertpapier?isin={isin}"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            # Look for fondId in the response
            if isinstance(data, list) and data:
                fond_id = (data[0].get("fondId") or data[0].get("numWfsKu") or
                           data[0].get("id") or data[0].get("fondNr"))
                if fond_id:
                    return str(fond_id)
            elif isinstance(data, dict):
                fond_id = (data.get("fondId") or data.get("numWfsKu") or
                           data.get("id") or data.get("fondNr"))
                if fond_id:
                    return str(fond_id)
    except Exception:
        pass
    return None


def _fetch_ertraege_per_share(stm_id: str, headers: dict) -> dict | None:
    """
    Fetch per-share tax data using the specific endpoint discovered.
    Returns a dict with {fondsergebnis, kest} or None.
    """
    url = f"https://my.oekb.at/fond-info/rest/public/steuerMeldung/stmId/{stm_id}/ertrStBeh"
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        
        data = r.json()
        rows = data.get("list", [])
        
        result = {"fondsergebnis": None, "kest": None}
        
        for row in rows:
            # Position "1." is Fondsergebnis (steuerName: StB_Fondsergebnis_gesamt)
            if row.get("position") == "1.":
                result["fondsergebnis"] = row.get("pvMitOption4")
            # Position "12." is Austrian KESt (steuerName: StB_KESt)
            if row.get("position") == "12.":
                result["kest"] = row.get("pvMitOption4")
        
        if result["fondsergebnis"] is not None or result["kest"] is not None:
            return result
            
    except Exception:
        pass
    return None


def fetch_oekb_tax_data(isin: str) -> dict:
    """
    Fetch Austrian tax data (Fondsergebnis + KeSt per share) from OeKB REST API.
    Uses the stmId-based per-share endpoint discovered by the user.

    Returns a dict with keys:
        kest          – Österreichische KeSt per share (fund currency)
        fondsergebnis – Fondsergebnis per share (fund currency)
        meldedatum    – Zufluss date (YYYY-MM-DD)
        waehrung      – Fund currency (e.g. 'USD')
        isinBez       – Fund name
    Or a dict with a single key 'error' on failure.
    """
    headers = _build_oekb_headers()
    # Use the platform context found in the successful curl
    headers["OeKB-Platform-Context"] = (
        "eyJzdGFnZSI6IlBST0QiLCJsYW5ndWFnZSI6ImRlIiwicGxhdGZvcm0iOiJLTVMiLCJkYXNoYm9hcmQiOiJLTVNfT1VUUFVUIn0="
    )

    # ── Step 1: Resolve ISIN to stmId ───────────────────────────────────────
    # The /liste endpoint provides the stmId (tax report ID) and other metadata.
    liste_url = (
        "https://my.oekb.at/fond-info/rest/public/steuerMeldung/liste"
        f"?offset=0&limit=10&ctxListArt=ALLE&ctxEqIsin={isin}"
        "&meldgNurGuelt=true&meldgJahresM=true&sortField=isinBez&sortOrder=1"
    )
    try:
        resp = requests.get(liste_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return {"error": f"API OeKB /liste HTTP {resp.status_code}"}
        
        liste_json = resp.json()
        if not liste_json.get("list"):
            return {"error": f"Nessun report fiscale trovato per ISIN {isin}."}

        entry          = liste_json["list"][0]
        stm_id         = entry["stmId"]
        waehrung       = entry.get("waehrung", "?")
        isin_bez       = entry.get("isinBez", isin)
        meldedatum_raw = entry.get("zufluss", entry.get("guelt", ""))
        meldedatum     = meldedatum_raw[:10] if meldedatum_raw else ""

    except Exception as e:
        return {"error": f"Errore durante la ricerca ISIN: {str(e)}"}

    # ── Step 2: Fetch per-share data using stmId ───────────────────────────
    # This uses the endpoint: /rest/public/steuerMeldung/stmId/{stm_id}/ertrStBeh
    per_share = _fetch_ertraege_per_share(str(stm_id), headers)
    
    if not per_share:
        return {"error": "Impossibile recuperare i dati pro-quota dall'endpoint ertrStBeh."}

    return {
        "fondsergebnis": per_share["fondsergebnis"],
        "kest":          per_share["kest"],
        "meldedatum":    meldedatum,
        "waehrung":      waehrung,
        "isinBez":       isin_bez,
        "_source": f"OeKB stmId {stm_id} (ertrStBeh)"
    }

