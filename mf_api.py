import requests
import pandas as pd
import logging
import diskcache
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)
cache = diskcache.Cache('.nav_cache')

_INVALID_CODES = {'', 'none', 'nan', 'null'}


def normalize_amfi(amfi_code):
    """Coerce an AMFI code to a clean string, or '' if it is missing/invalid.

    Guards against casparser handing back the literal string 'None' (str(None))
    for schemes its bundled ISIN map can't resolve — that string is truthy and
    was being sent to the API as /mf/None, silently breaking NAV lookups.
    """
    if amfi_code is None:
        return ''
    s = str(amfi_code).strip()
    return '' if s.lower() in _INVALID_CODES else s


def get_isin_amfi_map():
    """ISIN -> AMFI scheme code, from AMFI's canonical NAVAll.txt (cached 24h).

    casparser ships a bundled ISIN->amfi map that goes stale for newer schemes,
    leaving scheme.amfi = None. AMFI's live feed covers them, so we recover the
    code from the ISIN casparser DID capture. The first NAVAll column ('Scheme
    Code') is the same code mfapi.in uses.
    """
    cached = cache.get('__isin_amfi_map__')
    if cached is not None:
        return cached

    url = 'https://www.amfiindia.com/spages/NAVAll.txt'

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    def _fetch_map():
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        return resp.text

    mapping = {}
    try:
        text = _fetch_map()
        for line in text.splitlines():
            parts = line.split(';')
            if len(parts) < 4:
                continue
            code = parts[0].strip()
            if not code.isdigit():
                continue  # skip the header row and any category separators
            for isin in (parts[1].strip(), parts[2].strip()):
                if isin and isin != '-':
                    mapping[isin.upper()] = code
        if mapping:
            cache.set('__isin_amfi_map__', mapping, expire=86400)
    except Exception as e:
        logger.warning(f"Failed to build ISIN->AMFI map: {e}")
    return mapping


def resolve_amfi_from_isin(isin):
    """Look up an AMFI scheme code from an ISIN, or '' if not found."""
    if not isin:
        return ''
    isin = str(isin).strip().upper()
    if not isin or isin == '-':
        return ''
    return get_isin_amfi_map().get(isin, '')


def fetch_historical_nav(amfi_code):
    amfi_code = normalize_amfi(amfi_code)
    if not amfi_code:
        return None

    # Check persistent disk cache first
    if amfi_code in cache:
        return cache[amfi_code]
        
    url = f"https://api.mfapi.in/mf/{amfi_code}"
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    def _fetch():
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()

    try:
        data = _fetch()
        if data.get("status") == "SUCCESS" and "data" in data:
            df = pd.DataFrame(data["data"])
            df['date'] = pd.to_datetime(df['date'], format='%d-%m-%Y')
            df['nav'] = pd.to_numeric(df['nav'], errors='coerce')
            df.set_index('date', inplace=True)
            df = df.sort_index()
            # Cache successfully parsed dataframe for 24 hours
            cache.set(amfi_code, df, expire=86400)
            return df
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch NAV for AMFI {amfi_code}: {e}")
        return None
