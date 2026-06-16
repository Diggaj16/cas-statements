import re
import requests
import pandas as pd
import logging
import diskcache
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)
cache = diskcache.Cache('.nav_cache')

_INVALID_CODES = {'', 'none', 'nan', 'null'}

# Tokens that describe the plan/option/filler rather than the fund identity. They
# are stripped from the name "core" so the same fund matches across CAS and AMFI
# spellings; plan/option are captured separately in the signature.
_NAME_FILLER = {
    'direct', 'regular', 'plan', 'growth', 'idcw', 'dividend', 'payout',
    'reinvestment', 'reinvest', 'div', 'option', 'fund', 'scheme', 'the', 'an',
    'of', 'and', 'mutual',
}
_IDCW_TOKENS = {'idcw', 'dividend', 'payout', 'reinvestment', 'reinvest', 'div'}


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


def _name_signature(name):
    """Reduce a scheme name to a (plan|option|core) key for confident matching.

    Built identically from CAS and AMFI names so a fund matches regardless of
    spelling, while still distinguishing Direct/Regular and Growth/IDCW (a wrong
    variant has a different NAV and would skew XIRR).
    """
    tokens = re.sub(r'[^a-z0-9]+', ' ', str(name).lower()).split()
    if not tokens:
        return ''
    plan = 'direct' if 'direct' in tokens else 'regular'
    option = 'idcw' if any(t in _IDCW_TOKENS for t in tokens) else 'growth'
    core = sorted(t for t in tokens if t not in _NAME_FILLER)
    if not core:
        return ''
    return f"{plan}|{option}|{' '.join(core)}"


def _build_amfi_master():
    """Build ISIN->code and name-signature->code maps from one NAVAll.txt fetch.

    AMFI's NAVAll.txt is the canonical, current scheme master (its first column
    is the same code mfapi.in uses). Both maps are cached 24h so we fetch once.
    Name signatures that map to more than one code are dropped (ambiguous → we
    only auto-match unique signatures).
    """
    isin_map = cache.get('__isin_amfi_map__')
    name_map = cache.get('__name_amfi_map__')
    if isin_map is not None and name_map is not None:
        return isin_map, name_map

    url = 'https://www.amfiindia.com/spages/NAVAll.txt'

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    def _fetch_map():
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        return resp.text

    isin_map = {}
    name_sig_codes = {}  # sig -> set(codes), collapsed to unique below
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
                    isin_map[isin.upper()] = code
            sig = _name_signature(parts[3])
            if sig:
                name_sig_codes.setdefault(sig, set()).add(code)
        name_map = {sig: next(iter(codes)) for sig, codes in name_sig_codes.items()
                    if len(codes) == 1}
        if isin_map:
            cache.set('__isin_amfi_map__', isin_map, expire=86400)
            cache.set('__name_amfi_map__', name_map, expire=86400)
    except Exception as e:
        logger.warning(f"Failed to build AMFI master maps: {e}")
        isin_map = isin_map or {}
        name_map = name_map or {}
    return isin_map, name_map


def get_isin_amfi_map():
    """ISIN -> AMFI scheme code, from AMFI's canonical NAVAll.txt (cached 24h)."""
    return _build_amfi_master()[0]


def get_name_amfi_map():
    """Unique name-signature -> AMFI scheme code, from NAVAll.txt (cached 24h)."""
    return _build_amfi_master()[1]


def resolve_amfi_from_isin(isin):
    """Look up an AMFI scheme code from an ISIN, or '' if not found."""
    if not isin:
        return ''
    isin = str(isin).strip().upper()
    if not isin or isin == '-':
        return ''
    return get_isin_amfi_map().get(isin, '')


def resolve_amfi_from_name(name):
    """Confidence-gated name match -> AMFI code, or '' if absent/ambiguous.

    Only returns a code when the (plan|option|core) signature maps to exactly one
    scheme in AMFI's master, so a wrong Direct/Regular or Growth/IDCW pick can't
    slip through.
    """
    sig = _name_signature(name)
    if not sig:
        return ''
    return get_name_amfi_map().get(sig, '')


def get_manual_map():
    """User-saved scheme overrides (key -> AMFI code), persisted across uploads."""
    return cache.get('__manual_scheme_map__') or {}


def set_manual_map(key, code):
    """Persist one user override. Key is an ISIN (preferred) or normalized name."""
    key = str(key or '').strip()
    code = normalize_amfi(code)
    if not key or not code:
        return
    current = get_manual_map()
    current[key] = code
    cache.set('__manual_scheme_map__', current)  # no expiry — explicit user choice


def save_manual_override(scheme_name, isin, code):
    """Persist a user override under the same keys resolve_scheme_code looks up
    (ISIN-upper and/or name signature), so it auto-applies on future uploads.
    """
    code = normalize_amfi(code)
    if not code:
        return
    isin = str(isin or '').strip().upper()
    if isin and isin != '-':
        set_manual_map(isin, code)
    sig = _name_signature(scheme_name)
    if sig:
        set_manual_map(sig, code)


def _manual_keys(isin, name):
    """Candidate lookup keys for the manual map: ISIN first, then normalized name."""
    keys = []
    isin = str(isin or '').strip().upper()
    if isin and isin != '-':
        keys.append(isin)
    sig = _name_signature(name)
    if sig:
        keys.append(sig)
    return keys


def resolve_scheme_code(amfi, isin, name, manual_map=None):
    """Resolve a scheme to an AMFI code through the tiered chain (first hit wins):
    casparser amfi -> ISIN -> saved manual override -> confident name match -> ''.
    """
    code = normalize_amfi(amfi)
    if code:
        return code

    code = resolve_amfi_from_isin(isin)
    if code:
        return code

    manual_map = manual_map if manual_map is not None else get_manual_map()
    for key in _manual_keys(isin, name):
        code = normalize_amfi(manual_map.get(key))
        if code:
            return code

    return resolve_amfi_from_name(name)


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
