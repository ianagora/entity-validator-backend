 # resolver.py
import os, time, unicodedata, re, json
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Dict, List, Tuple, Optional, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from dotenv import load_dotenv
load_dotenv()

print(f"[BOOT] resolver loaded from {__file__}", flush=True)
print(
    f"[BOOT] CH key? {bool(os.getenv('CH_API_KEY'))} "
    f"| CCEW key? {bool(os.getenv('CHARITY_API_KEY'))} "
    f"| CharityBase key? {bool(os.getenv('CHARITYBASE_API_KEY'))}",
    flush=True,
)

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
CH_API_KEY = os.getenv("CH_API_KEY")
CHARITY_API_KEY = os.getenv("CHARITY_API_KEY")  # official CCEW REST

# CharityBase (feature-flagged OFF by default)
CHARITYBASE_URL = "https://charitybase.uk/api/graphql"
CHARITYBASE_API_KEY = os.getenv("CHARITYBASE_API_KEY")
USE_CHARITYBASE = os.getenv("USE_CHARITYBASE", "0") in ("1", "true", "True", "YES", "yes")

BASE_URL_CH = "https://api.company-information.service.gov.uk"

# Preferred → fallback Charity Commission REST bases (we'll use the first)
CCEW_BASES = [
    "https://register-of-charities.charitycommission.gov.uk/api",  # newer REST
    "https://api.charitycommission.gov.uk/register/api",           # legacy REST
]

CACHE_TTL   = int(os.getenv("CACHE_TTL_SECONDS", "86400"))   # 24h default
REQ_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "15"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
BACKOFF     = float(os.getenv("BACKOFF_SECONDS", "1.5"))

if not CH_API_KEY:
    print("⚠️  WARNING: CH_API_KEY is not set. Companies House API will not work.", flush=True)
    print("⚠️  Set CH_API_KEY in Railway environment variables.", flush=True)
    # Don't crash - let the app start so we can debug
    # raise RuntimeError("CH_API_KEY is not set. Put it in .env or env vars.")

# -----------------------------------------------------------------------------
# HTTP sessions with retries
# -----------------------------------------------------------------------------
def build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=MAX_RETRIES, read=MAX_RETRIES, connect=MAX_RETRIES,
        backoff_factor=BACKOFF,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD", "POST"),
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s

SESSION = build_session()
AUTH_CH = (CH_API_KEY, "")

# -----------------------------------------------------------------------------
# Tiny in-memory cache (GET only)
# -----------------------------------------------------------------------------
_CACHE: Dict[str, Tuple[float, dict]] = {}

def _cache_get(key: str):
    rec = _CACHE.get(key)
    if not rec:
        return None
    exp, data = rec
    if time.time() > exp:
        _CACHE.pop(key, None)
        return None
    return data

def _cache_set(key: str, data, ttl: int):
    _CACHE[key] = (time.time() + ttl, data)

def _hdrsig(headers: Optional[Dict[str, str]]) -> str:
    if not headers:
        return ""
    return "|".join(f"{k}:{v}" for k, v in sorted(headers.items()))

def cached_get_json(url: str, *, ttl: int = CACHE_TTL, auth=None, headers: Optional[Dict[str, str]] = None):
    key = f"{url}||{_hdrsig(headers)}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    resp = SESSION.get(url, auth=auth, headers=headers, timeout=REQ_TIMEOUT)
    if resp.status_code == 429:
        time.sleep(BACKOFF)
        resp = SESSION.get(url, auth=auth, headers=headers, timeout=REQ_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    _cache_set(key, data, ttl)
    return data

# -----------------------------------------------------------------------------
# Charity Commission REST helpers
# -----------------------------------------------------------------------------
def _cc_url(path: str) -> Optional[str]:
    for base in CCEW_BASES:
        if base:
            return f"{base.rstrip('/')}/{path.lstrip('/')}"
    return None

def _cc_get(path: str, params: Optional[dict] = None, timeout: float = 20.0) -> Optional[dict]:
    """
    GET JSON from CCEW (using SESSION for retries/backoff). Adds subscription key
    when CHARITY_API_KEY is set. Treats 204/404 as empty.
    """
    url = _cc_url(path)
    if not url:
        return None
    headers = {"Accept": "application/json"}
    if CHARITY_API_KEY:
        headers["Ocp-Apim-Subscription-Key"] = CHARITY_API_KEY
    try:
        r = SESSION.get(url, headers=headers, params=params or {}, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None

def _cc_profile_link(reg_no: str) -> str:
    try:
        n = int(str(reg_no).strip())
        return f"https://register-of-charities.charitycommission.gov.uk/charity-details/?regId={n}&subId=0"
    except Exception:
        return "https://register-of-charities.charitycommission.gov.uk/"

def get_charity_bundle_cc(charity_number: str) -> Dict[str, Any]:
    """
    Build a normalised enrichment bundle from CCEW only (no CharityBase).
    Returns:
      {
        profile: {...},           # name, status, address/postcode, etc
        trustees: [ {name,...} ],
        filings: [...],           # if available
        sources: { ... }          # deep links to CCEW pages
      }
    """
    reg = str(charity_number).strip()
    bundle: Dict[str, Any] = {
        "profile": {
            "registry": "Charity Commission",
            "charity_number": reg,
            "source_url": _cc_profile_link(reg),
        },
        "trustees": [],
        "filings": [],
        "sources": {
            "profile": _cc_profile_link(reg),
        },
    }

    # ---- Try REST detail endpoint(s)
    detail = (
        _cc_get(f"charity/{reg}")
        or _cc_get(f"charityDetails/{reg}")
        or _cc_get(f"charity/{reg}/details")
        or {}
    )

    # Minimal mapping with fallbacks
    name = (
        detail.get("name")
        or detail.get("charityName")
        or (detail.get("charity") or {}).get("name")
        or None
    )
    status = (
        detail.get("status")
        or (detail.get("registration") or {}).get("status")
        or detail.get("charityStatus")
        or None
    )
    postcode = (
        (detail.get("address") or {}).get("postcode")
        or detail.get("postcode")
        or None
    )
    address = (
        detail.get("address")
        or {k: detail.get(k) for k in ("addressLine1","addressLine2","addressLine3","town","county","postcode") if detail.get(k)}
        or None
    )

    if name:    bundle["profile"]["name"] = name
    if status:  bundle["profile"]["status"] = status
    if address: bundle["profile"]["address"] = address
    if postcode:bundle["profile"]["postcode"] = postcode

    # ---- Trustees
    trustees = (
        _cc_get(f"charity/{reg}/trustees")
        or _cc_get(f"charityTrustees/{reg}")
        or _cc_get(f"trustees/{reg}")
        or []
    )
    # Normalise whatever shape we get
    norm_trustees = []
    if isinstance(trustees, dict) and "trustees" in trustees:
        trustees = trustees["trustees"]
    for t in (trustees or []):
        nm = t.get("name") or t.get("trusteeName") or t.get("personName") or t.get("displayName")
        if nm:
            norm_trustees.append({"name": nm})
    bundle["trustees"] = norm_trustees

    # ---- Filings / documents if exposed
    filings = (
        _cc_get(f"charity/{reg}/documents")
        or _cc_get(f"charityDocuments/{reg}")
        or []
    )
    if isinstance(filings, dict) and "documents" in filings:
        filings = filings["documents"]
    norm_docs = []
    for d in (filings or []):
        title = d.get("title") or d.get("documentName")
        url   = d.get("url")   or d.get("link")
        date  = d.get("date")  or d.get("uploaded") or d.get("periodEnd")
        if title or url:
            norm_docs.append({"title": title, "url": url, "date": date})
    bundle["filings"] = norm_docs

    # Track which JSON endpoints were considered (handy for debugging)
    for p in ("charity/{reg}", "charityDetails/{reg}", "charity/{reg}/details",
              "charity/{reg}/trustees", "charityTrustees/{reg}", "trustees/{reg}",
              "charity/{reg}/documents", "charityDocuments/{reg}"):
        bundle["sources"].setdefault("api_candidates", []).append(p.replace("{reg}", reg))

    return bundle

# -----------------------------------------------------------------------------
# Name canonicalisation
# -----------------------------------------------------------------------------
LEGAL_SUFFIXES = [
    "limited", "ltd",
    "public limited company", "plc",
    "limited liability partnership", "llp",
    "limited partnership", "lp",
    "community interest company", "cic",
    "charitable incorporated organisation", "cio",
    "charity",
    "foundation",
    "trust",
]

def _strip_legal_suffix(s: str) -> str:
    s = s.strip().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    changed = True
    while changed and s:
        changed = False
        for suf in LEGAL_SUFFIXES:
            if s.endswith(" " + suf):
                s = s[: -len(suf)].strip()
                s = re.sub(r"[^\w\s]", " ", s)
                s = re.sub(r"\s+", " ", s).strip()
                changed = True
                break
    return s

def canonicalise_name(name: str) -> str:
    if not name:
        return ""
    name = ''.join(c for c in unicodedata.normalize('NFKD', name) if not unicodedata.combining(c)).lower()
    name = _strip_legal_suffix(name)
    # collapse "k i n d" → "kind"
    if re.fullmatch(r'(?:[a-z]\s+){2,}[a-z]', name):
        name = name.replace(' ', '')
    return name

def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

# -----------------------------------------------------------------------------
# Companies House
# -----------------------------------------------------------------------------
def ch_candidates(subject_name: str) -> Tuple[List[dict], Optional[dict], str]:
    q = requests.utils.quote(subject_name)
    search_url = f"{BASE_URL_CH}/search/companies?q={q}"
    data = cached_get_json(search_url, auth=AUTH_CH)
    results = data.get("items", [])
    canonical_input = canonicalise_name(subject_name)
    matches, exact_match = [], None
    
    # Track all canonical matches to choose the best one
    canonical_matches = []
    
    for item in results:
        candidate_name = item.get("title", "")
        candidate_id   = item.get("company_number", "")
        ch_status      = item.get("company_status", "")
        address        = item.get("address_snippet", "")
        canonical_candidate = canonicalise_name(candidate_name)
        score = similarity(canonical_input, canonical_candidate)
        match_obj = {
            "source": "Companies House",
            "entity_name": candidate_name,
            "company_number": candidate_id,
            "company_status": ch_status,
            "address": address,
            "confidence": round(score, 3),
            "retrieved_at": datetime.utcnow().isoformat() + "Z",
            "source_url": f"https://find-and-update.company-information.service.gov.uk/company/{candidate_id}/",
        }
        matches.append(match_obj)
        
        # Collect all canonical matches
        if canonical_candidate == canonical_input:
            canonical_matches.append(match_obj)
    
    # Choose best exact match when multiple companies have same canonical name
    if canonical_matches:
        # Priority 1: Exact case-insensitive string match (before canonicalization)
        for match in canonical_matches:
            if match["entity_name"].lower() == subject_name.lower():
                exact_match = {**match, "confidence": 1.0}
                break
        
        # Priority 2: If no exact string match, prefer longer name (more specific)
        if not exact_match:
            canonical_matches.sort(key=lambda m: len(m["entity_name"]), reverse=True)
            exact_match = {**canonical_matches[0], "confidence": 1.0}
    
    return matches, exact_match, search_url

def _json_from(path: str):
    url = f"{BASE_URL_CH}{path}"
    return cached_get_json(url, auth=AUTH_CH)

def get_company_bundle(company_number: str) -> dict:
    if not company_number or not company_number.strip():
        raise ValueError("company_number is required")
    prof     = _json_from(f"/company/{company_number}")
    officers = _json_from(f"/company/{company_number}/officers")
    pscs     = _json_from(f"/company/{company_number}/persons-with-significant-control")
    charges  = _json_from(f"/company/{company_number}/charges")
    return {
        "company_number": company_number,
        "retrieved_at": datetime.utcnow().isoformat() + "Z",
        "sources": {
            "profile":  f"{BASE_URL_CH}/company/{company_number}",
            "officers": f"{BASE_URL_CH}/company/{company_number}/officers",
            "pscs":     f"{BASE_URL_CH}/company/{company_number}/persons-with-significant-control",
            "charges":  f"{BASE_URL_CH}/company/{company_number}/charges",
        },
        "profile": prof,
        "officers": officers,
        "pscs": pscs,
        "charges": charges
    }

# -----------------------------------------------------------------------------
# Filing History & Document Retrieval
# -----------------------------------------------------------------------------
def get_company_filing_history(company_number: str, category: str = None, items_per_page: int = None, start_index: int = None) -> dict:
    """Get filing history for a company with optional query parameters."""
    if not company_number or not company_number.strip():
        raise ValueError("company_number is required")

    # Build the path with query parameters
    path = f"/company/{company_number}/filing-history"
    query_params = []
    if category:
        query_params.append(f"category={category}")
    if items_per_page:
        query_params.append(f"items_per_page={items_per_page}")
    if start_index is not None:
        query_params.append(f"start_index={start_index}")

    if query_params:
        path += "?" + "&".join(query_params)

    filing_history = _json_from(path)
    return {
        "company_number": company_number,
        "retrieved_at": datetime.utcnow().isoformat() + "Z",
        "source": f"{BASE_URL_CH}{path}",
        "filing_history": filing_history
    }

def get_filing_detail(company_number: str, transaction_id: str) -> dict:
    """Get detailed metadata for a specific filing."""
    if not company_number or not company_number.strip():
        raise ValueError("company_number is required")
    if not transaction_id or not transaction_id.strip():
        raise ValueError("transaction_id is required")

    filing_detail = _json_from(f"/company/{company_number}/filing-history/{transaction_id}")
    return {
        "company_number": company_number,
        "transaction_id": transaction_id,
        "retrieved_at": datetime.utcnow().isoformat() + "Z",
        "source": f"{BASE_URL_CH}/company/{company_number}/filing-history/{transaction_id}",
        "filing_detail": filing_detail
    }

def get_document_metadata(document_id: str) -> dict:
    """Get metadata for a document."""
    if not document_id or not document_id.strip():
        raise ValueError("document_id is required")

    # Document API base URL
    doc_base_url = "https://document-api.company-information.service.gov.uk"
    doc_url = f"{doc_base_url}/document/{document_id}"

    response = SESSION.get(doc_url, auth=AUTH_CH, timeout=REQ_TIMEOUT)
    if response.status_code == 429:
        time.sleep(BACKOFF)
        response = SESSION.get(doc_url, auth=AUTH_CH, timeout=REQ_TIMEOUT)
    response.raise_for_status()

    doc_metadata = response.json()
    return {
        "document_id": document_id,
        "retrieved_at": datetime.utcnow().isoformat() + "Z",
        "source": doc_url,
        "document_metadata": doc_metadata
    }

def download_cs01_pdf(document_id: str) -> bytes:
    """Download the actual CS01 PDF content."""
    if not document_id or not document_id.strip():
        raise ValueError("document_id is required")

    # Document API base URL
    doc_base_url = "https://document-api.company-information.service.gov.uk"
    content_url = f"{doc_base_url}/document/{document_id}/content"

    response = SESSION.get(content_url, auth=AUTH_CH, timeout=REQ_TIMEOUT)
    if response.status_code == 429:
        time.sleep(BACKOFF)
        response = SESSION.get(content_url, auth=AUTH_CH, timeout=REQ_TIMEOUT)
    response.raise_for_status()

    return response.content

def get_cs01_filings_for_company(company_number: str) -> List[dict]:
    """Get all CS01 filings for a company with their document IDs.
    
    Returns filings sorted with 'with updates' first, then 'with no updates'.
    This optimizes shareholder extraction by prioritizing filings that contain changes.
    """
    filing_history = get_company_filing_history(company_number,"confirmation-statement")

    cs01_filings = []
    items = filing_history.get("filing_history", {}).get("items", [])

    for item in items:
        if item.get("type") == "CS01":
            # Get detailed filing info to find document metadata
            transaction_id = item.get("transaction_id")
            if transaction_id:
                try:
                    filing_detail = get_filing_detail(company_number, transaction_id)
                    links = filing_detail.get("filing_detail", {}).get("links", {})

                    # Look for document_metadata link
                    doc_meta_url = links.get("document_metadata")
                    if doc_meta_url:
                        # Extract document ID from URL
                        # URL format: https://document-api.company-information.service.gov.uk/document/{document_id}
                        doc_id = doc_meta_url.split("/")[-1]

                        cs01_filings.append({
                            "company_number": company_number,
                            "transaction_id": transaction_id,
                            "date": item.get("date"),
                            "description": item.get("description"),
                            "document_id": doc_id,
                            "document_metadata_url": doc_meta_url,
                            "filing_detail": filing_detail
                        })
                except Exception as e:
                    print(f"Warning: Could not get details for CS01 filing {transaction_id}: {e}")
                    continue

    # Sort filings by date (most recent first) to ensure latest shareholder data
    # CRITICAL: Always use the MOST RECENT CS01 with shareholders to avoid outdated data
    # Example: MEI MEI (LIVERPOOL) - should use 2024 CS01 (1 shareholder), not older 2023 AR01 (2 shareholders)
    def sort_key(filing):
        # Sort by date only (most recent first) to get latest shareholder information
        return filing.get("date", "")
    
    cs01_filings.sort(key=sort_key, reverse=True)  # Sort by date (most recent first)
    
    return cs01_filings

def get_ar01_filings_for_company(company_number: str) -> List[dict]:
    """Get all AR01 filings for a company with their document IDs."""
    filing_history = get_company_filing_history(company_number,"confirmation-statement")

    ar01_filings = []
    items = filing_history.get("filing_history", {}).get("items", [])
    #print(f"AR01 filings: {items}")
    for item in items:
        if item.get("type") == "AR01":
            # Get detailed filing info to find document metadata
            transaction_id = item.get("transaction_id")
            if transaction_id:
                try:
                    filing_detail = get_filing_detail(company_number, transaction_id)
                    links = filing_detail.get("filing_detail", {}).get("links", {})

                    # Look for document_metadata link
                    doc_meta_url = links.get("document_metadata")
                    if doc_meta_url:
                        # Extract document ID from URL
                        # URL format: https://document-api.company-information.service.gov.uk/document/{document_id}
                        doc_id = doc_meta_url.split("/")[-1]

                        ar01_filings.append({
                            "company_number": company_number,
                            "transaction_id": transaction_id,
                            "date": item.get("date"),
                            "description": item.get("description"),
                            "document_id": doc_id,
                            "document_metadata_url": doc_meta_url,
                            "filing_detail": filing_detail
                        })
                except Exception as e:
                    print(f"Warning: Could not get details for AR01 filing {transaction_id}: {e}")
                    continue

    # Sort filings by date (most recent first) to ensure latest shareholder data
    # Same logic as CS01: Always use the MOST RECENT AR01 with shareholders
    ar01_filings.sort(key=lambda f: f.get("date", ""), reverse=True)
    
    return ar01_filings

def download_ar01_pdf(document_id: str) -> bytes:
    """Download the actual AR01 PDF content."""
    # AR01 uses the same document API as CS01, so we can reuse the download function
    return download_cs01_pdf(document_id)

def get_in01_filings_for_company(company_number: str) -> List[dict]:
    """Get all IN01/NEWINC filings for a company with their document IDs."""
    filing_history = get_company_filing_history(company_number)
    in01_filings = []
    items = filing_history.get("filing_history", {}).get("items", [])
    for item in items:
        if item.get("type") in ["IN01", "NEWINC"]:
            # Get detailed filing info to find document metadata
            transaction_id = item.get("transaction_id")
            if transaction_id:
                try:
                    filing_detail = get_filing_detail(company_number, transaction_id)
                    links = filing_detail.get("filing_detail", {}).get("links", {})

                    # Look for document_metadata link
                    doc_meta_url = links.get("document_metadata")
                    if doc_meta_url:
                        # Extract document ID from URL
                        # URL format: https://document-api.company-information.service.gov.uk/document/{document_id}
                        doc_id = doc_meta_url.split("/")[-1]

                        in01_filings.append({
                            "company_number": company_number,
                            "transaction_id": transaction_id,
                            "date": item.get("date"),
                            "description": item.get("description"),
                            "document_id": doc_id,
                            "document_metadata_url": doc_meta_url,
                            "filing_detail": filing_detail
                        })
                except Exception as e:
                    print(f"Warning: Could not get details for IN01 filing {transaction_id}: {e}")
                    continue

    # Sort by date (most recent first) - IN01 filings typically don't have "with updates" descriptions
    in01_filings.sort(key=lambda f: f.get("date", ""), reverse=True)
    
    return in01_filings

def download_in01_pdf(document_id: str) -> bytes:
    """Download the actual IN01 PDF content."""
    # IN01 uses the same document API as CS01, so we can reuse the download function
    return download_cs01_pdf(document_id)

# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------
def _utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _get(obj, *path, default=None):
    cur = obj
    for p in path:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and isinstance(p, int) and 0 <= p < len(cur):
            cur = cur[p]
        else:
            return default
    return cur if cur is not None else default

def _score(rank: int) -> float:
    base = 0.90
    step = 0.10
    s = base - (rank * step)
    return max(0.50, round(s, 3))

# -----------------------------------------------------------------------------
# CharityBase (feature-flagged)
# -----------------------------------------------------------------------------
def charitybase_search(name: str, limit: int = 10) -> dict:
    """
    Attempt to query CharityBase for charities matching `name`.
    NOTE: Their schema appears unstable; this function tries multiple shapes.
    """
    if not CHARITYBASE_API_KEY:
        return {
            "registry": "charity_commission",
            "search_url": f"https://register-of-charities.charitycommission.gov.uk/en/charity-search/-/results/page/1?keywords={requests.utils.quote(name)}",
            "retrieved_at": _utc_now_iso(),
            "resolved": {},
            "candidates": [],
            "error": "charitybase_missing_key",
        }

    headers = {
        "Authorization": f"Apikey {CHARITYBASE_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Scrutinise/1.0 (+entity-resolver)",
    }

    selection_variants = [
        ("charities", "charities { id names { value primary } registrationNumber status contact { address } url }"),
        ("data",      "data { id names { value primary } registrationNumber status contact { address } url }"),
        ("results",   "results { id names { value primary } registrationNumber status contact { address } url }"),
        ("items",     "items { id names { value primary } registrationNumber status contact { address } url }"),
        ("list",      "list { id names { value primary } registrationNumber status contact { address } url }"),
        ("edges",     "edges { node { id names { value primary } registrationNumber status contact { address } url } }"),
    ]

    def _exec(query_str):
        resp = SESSION.post(
            CHARITYBASE_URL,
            headers=headers,
            json={"query": query_str, "variables": {"q": name}},
            timeout=REQ_TIMEOUT,
        )
        print(f"[CCEW DEBUG] CharityBase (search-arg) HTTP {resp.status_code} for “{name}”", flush=True)
        if resp.status_code >= 400:
            print(f"[CCEW DEBUG] CharityBase (search-arg) error body: {resp.text[:400]}", flush=True)
        resp.raise_for_status()
        return resp.json()

    items = None
    last_err = None

    for key, sel in selection_variants:
        query = f"""
        query Search($q: String!) {{
          CHC {{
            getCharities(filters: {{ search: $q }}) {{
              count
              {sel}
            }}
          }}
        }}"""
        try:
            data = _exec(query)
            if isinstance(data, dict) and data.get("errors"):
                raise ValueError(f"GraphQL errors: {data['errors']}")
            container = _get(data, "data", "CHC", "getCharities", default={}) or {}

            if key == "edges":
                edges = container.get("edges")
                if isinstance(edges, list):
                    items = [e.get("node") for e in edges if isinstance(e, dict) and isinstance(e.get("node"), dict)]
                else:
                    items = None
            else:
                maybe = container.get(key)
                items = maybe if isinstance(maybe, list) else None

            if items is not None:
                print(f"[CCEW DEBUG] CharityBase shape '{key}' worked; items={len(items)}", flush=True)
                break
            else:
                print(f"[CCEW DEBUG] CharityBase shape '{key}' present but not a list; trying next…", flush=True)
        except Exception as e:
            last_err = e
            print(f"[CCEW DEBUG] CharityBase shape '{key}' failed: {e}", flush=True)
            continue

    if items is None:
        print(f"[CCEW] CharityBase query failed to match any shape: {last_err}", flush=True)
        return {
            "registry": "charity_commission",
            "search_url": f"https://register-of-charities.charitycommission.gov.uk/en/charity-search/-/results/page/1?keywords={requests.utils.quote(name)}",
            "retrieved_at": _utc_now_iso(),
            "resolved": {},
            "candidates": [],
            "error": f"charitybase_no_shape_matched: {last_err}",
        }

    print(f"[CCEW DEBUG] CharityBase (search-arg) items: {len(items or [])} for “{name}”", flush=True)

    candidates = []
    for i, item in enumerate((items or [])[: max(1, int(limit))]):
        names = _get(item, "names", default=[]) or []
        primary = next((n.get("value") for n in names if n.get("primary")), None)
        name_val = primary or (names[0]["value"] if names else None) or _get(item, "name")

        reg_no = _get(item, "registrationNumber")
        status = (_get(item, "status") or "").strip() or None
        addr   = _get(item, "contact", "address") or None
        url    = _get(item, "url") or None

        candidates.append({
            "source": "Charity Commission (via CharityBase)",
            "registry": "CCEW",
            "entity_name": name_val or name,
            "company_number": None,
            "charity_number": reg_no or None,
            "company_status": status,
            "confidence": _score(i),
            "address": addr,
            "source_url": url or (f"https://register-of-charities.charitycommission.gov.uk/charity-details/?regid={reg_no}" if reg_no else None),
            "retrieved_at": _utc_now_iso(),
        })

    return {
        "registry": "charity_commission",
        "search_url": f"https://register-of-charities.charitycommission.gov.uk/en/charity-search/-/results/page/1?keywords={requests.utils.quote(name)}",
        "retrieved_at": _utc_now_iso(),
        "resolved": {},
        "candidates": candidates,
    }

# -----------------------------------------------------------------------------
# Charity Commission (England & Wales) – official REST search
# -----------------------------------------------------------------------------
def _ccew_legacy_probe(subject_name: str) -> Tuple[List[dict], Optional[dict], Optional[str]]:
    """
    Official CCEW REST:
      - searchCharityName/{charityname}
      - allcharitydetailsV2/{RegisteredNumber}/0  (optional enrichment)
    """
    if not CHARITY_API_KEY:
        print("[CCEW LEGACY] No CHARITY_API_KEY set.", flush=True)
        return [], None, None

    headers = {"Ocp-Apim-Subscription-Key": CHARITY_API_KEY}
    q = requests.utils.quote(subject_name)
    search_url = f"{CCEW_BASES[-1]}/searchCharityName/{q}"  # legacy search lives on legacy base

    try:
        resp = SESSION.get(search_url, headers=headers, timeout=REQ_TIMEOUT)
        print(f"[CCEW LEGACY] {search_url} → {resp.status_code}", flush=True)
        if not resp.ok:
            return [], None, search_url
        payload = resp.json()
    except Exception as e:
        print(f"[CCEW LEGACY] error calling searchCharityName: {e}", flush=True)
        return [], None, search_url

    if isinstance(payload, dict):
        items = payload.get("results") or payload.get("items") or payload.get("charities") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    canonical_input = canonicalise_name(subject_name)
    candidates: List[dict] = []
    exact: Optional[dict] = None

    for it in items:
        regno = (
            it.get("RegisteredNumber")
            or it.get("registeredNumber")
            or it.get("charityNumber")
            or it.get("registrationNumber")
            or it.get("regNumber")
            or ""
        )
        regno = str(regno).strip()

        name = (
            it.get("CharityName")
            or it.get("charityName")
            or it.get("registeredCharityName")
            or it.get("name")
            or ""
        ).strip()

        status = it.get("status") or it.get("charityStatus") or None
        address = it.get("address") or it.get("charityAddress") or None

        # Optional enrichment
        if regno:
            details_url = f"{CCEW_BASES[-1]}/allcharitydetailsV2/{regno}/0"
            try:
                d = SESSION.get(details_url, headers=headers, timeout=REQ_TIMEOUT)
                if d.ok:
                    djson = d.json()
                    status = djson.get("status") or djson.get("charityStatus") or status
                    contact = djson.get("contact") or djson.get("contactInformation") or {}
                    if isinstance(contact, dict):
                        parts = [
                            contact.get("addressLine1"),
                            contact.get("addressLine2"),
                            contact.get("addressLine3"),
                            contact.get("town"),
                            contact.get("postcode"),
                            contact.get("country"),
                        ]
                        address = ", ".join([str(p) for p in parts if p]) or address
            except Exception as e:
                print(f"[CCEW LEGACY] enrich error for {regno}: {e}", flush=True)

        conf = similarity(canonical_input, canonicalise_name(name)) if name else 0.0
        row = {
            "source": "Charity Commission (England & Wales)",
            "registry": "CCEW",
            "entity_name": name or subject_name,
            "company_number": None,
            "charity_number": regno or None,
            "company_status": status,
            "address": address,
            "confidence": round(conf, 3),
            "retrieved_at": _utc_now_iso(),
            "source_url": (f"https://register-of-charities.charitycommission.gov.uk/charity-details/?regid={regno}&subid=0" if regno else None),
        }
        candidates.append(row)

        if name and canonicalise_name(name) == canonical_input:
            exact = {**row, "confidence": 1.0}

    return candidates, exact, search_url

def ccew_candidates(subject_name: str, *, limit: int = 20, allow_legacy_fallback: bool = True
                   ) -> Tuple[List[dict], Optional[dict], Optional[str]]:
    print(f"[CCEW] wrapper for: {subject_name}", flush=True)
    canonical_input = canonicalise_name(subject_name)

    # Use CharityBase only if explicitly enabled and key present
    if USE_CHARITYBASE and CHARITYBASE_API_KEY:
        print("[CCEW] CharityBase ENABLED", flush=True)
        cb = charitybase_search(subject_name, limit=limit)
        if cb.get("error"):
            print("[CCEW] CharityBase returned error; will fall back if allowed.", flush=True)
        else:
            items = cb.get("candidates") or []
            for row in items:
                row.setdefault("source", "Charity Commission (via CharityBase)")
                row.setdefault("registry", "CCEW")
            exact = next(
                ({**r, "confidence": 1.0} for r in items if canonicalise_name(r.get("entity_name","")) == canonical_input),
                None
            )
            return items, exact, cb.get("search_url")

    # Default path: official CCEW REST (legacy search endpoint)
    if allow_legacy_fallback and CHARITY_API_KEY:
        cands, exact, url = _ccew_legacy_probe(subject_name)
        print(f"[CCEW] Using CCEW REST; candidates={len(cands)}", flush=True)
        return cands, exact, url

    print("[CCEW] No CharityBase (disabled or no key) and no CCEW key; returning empty.", flush=True)
    return [], None, None

# -----------------------------------------------------------------------------
# Companies House Search
# -----------------------------------------------------------------------------
def search_companies_house(company_name: str, items_per_page: int = 5) -> List[Dict[str, Any]]:
    """
    Search Companies House by company name
    Returns list of matching companies
    """
    if not company_name or not company_name.strip():
        return []
    
    url = f"{BASE_URL_CH}/search/companies"
    params = {
        'q': company_name.strip(),
        'items_per_page': items_per_page
    }
    
    try:
        # Retry with exponential backoff on 429 errors
        max_retries = 3
        retry_delays = [BACKOFF, BACKOFF * 2, BACKOFF * 4]  # 1.5s, 3s, 6s
        
        for attempt in range(max_retries + 1):
            resp = SESSION.get(url, params=params, auth=AUTH_CH, timeout=REQ_TIMEOUT)
            
            if resp.status_code == 429:
                if attempt < max_retries:
                    delay = retry_delays[attempt]
                    print(f"[CH Search] ⚠️  Rate limited (429), retry {attempt + 1}/{max_retries} after {delay}s...", flush=True)
                    time.sleep(delay)
                else:
                    # Last attempt failed, raise the error
                    print(f"[CH Search] ❌ Rate limit persists after {max_retries} retries", flush=True)
                    resp.raise_for_status()
            else:
                # Success or other error
                break
        
        resp.raise_for_status()
        data = resp.json()
        
        items = data.get('items', [])
        
        results = []
        for item in items:
            results.append({
                'company_number': item.get('company_number'),
                'title': item.get('title'),
                'company_status': item.get('company_status'),
                'company_type': item.get('company_type'),
                'date_of_creation': item.get('date_of_creation'),
                'address': item.get('address', {}),
                'matches': item.get('matches', {})
            })
        
        return results
        
    except Exception as e:
        import traceback
        print(f"[CH Search] Error searching for '{company_name}': {e}", flush=True)
        print(f"[CH Search] Traceback: {traceback.format_exc()}", flush=True)
        return []

# -----------------------------------------------------------------------------
# Unified resolver
# -----------------------------------------------------------------------------
def resolve_company(subject_name: str, top_n: int = 3, sources: Tuple[str, ...] = ("ch","ccew")) -> dict:
    if not subject_name or not subject_name.strip():
        raise ValueError("subject_name is required")

    combined: List[dict] = []
    exact: Optional[dict] = None
    search_links: List[str] = []

    # Companies House
    if "ch" in sources:
        ch_cands, ch_exact, ch_url = ch_candidates(subject_name)
        combined.extend(ch_cands)
        if ch_url: search_links.append(ch_url)
        if ch_exact and (not exact or float(ch_exact["confidence"]) > float(exact.get("confidence", 0))):
            exact = {**ch_exact, "_registry": "Companies House"}

    # Charity Commission (via CCEW REST / CharityBase if enabled)
    if "ccew" in sources:
        cc_cands, cc_exact, cc_url = ccew_candidates(subject_name, limit=max(top_n*3, 20))
        combined.extend(cc_cands)
        if cc_url: search_links.append(cc_url)
        if cc_exact and (not exact or float(cc_exact["confidence"]) > float(exact.get("confidence", 0))):
            exact = {**cc_exact, "_registry": "Charity Commission (England & Wales)"}

    # Sort by confidence desc, then name
    combined.sort(key=lambda r: (-float(r.get("confidence") or 0), r.get("entity_name") or ""))

    print(
        "[DEBUG] Combined candidates: "
        f"CH={sum(1 for r in combined if (r.get('source','') or '').startswith('Companies House'))} "
        f"CCEW={sum(1 for r in combined if r.get('registry')=='CCEW')}",
        flush=True,
    )

    out = {
        "input": {"subject_name": subject_name},
        "registry": "Multi (CH + CCEW)" if len(sources) > 1 else ("Companies House" if sources == ("ch",) else "CCEW"),
        "search_url": " | ".join(search_links) or None,
        "retrieved_at": datetime.utcnow().isoformat() + "Z",
    }

    if exact:
        profile = None
        if exact.get("_registry") == "Companies House" and exact.get("company_number"):
            try:
                profile = cached_get_json(f"{BASE_URL_CH}/company/{exact['company_number']}", auth=AUTH_CH)
            except Exception:
                profile = None

        # NEW: carry charity_number when the exact hit is from CCEW
        is_cc = "charity" in str(exact.get("_registry", "")).lower()
        out["resolved"] = {
            "status": "auto",
            "match_type": f"Exact after canonicalisation ({exact.get('_registry')})",
            "entity_name": exact["entity_name"],
            "company_number": exact.get("company_number") if not is_cc else None,
            "charity_number": exact.get("charity_number") if is_cc else None,  # <— critical
            "company_status": exact.get("company_status"),
            "confidence": exact.get("confidence", 1.0),
            "reason": None,
            "source_url": exact.get("source_url"),
            "profile": profile,
            "registry": exact.get("_registry"),
        }
        out["candidates"] = []
        return out

    # Check for high-confidence matches (> 0.9) to allow auto-processing
    if combined:
        best_match = combined[0]  # combined is already sorted by confidence desc
        best_confidence = float(best_match.get("confidence") or 0)

        if best_confidence > 0.9:
            # Auto-process high-confidence matches
            reg = best_match.get("registry") or best_match.get("_registry")
            is_cc = "charity" in str(reg or "").lower()

            out["resolved"] = {
                "status": "auto",
                "match_type": f"High-confidence match ({best_confidence:.3f})",
                "entity_name": best_match["entity_name"],
                "company_number": best_match.get("company_number") if not is_cc else None,
                "charity_number": best_match.get("charity_number") if is_cc else None,
                "company_status": best_match.get("company_status"),
                "confidence": best_confidence,
                "reason": None,
                "source_url": best_match.get("source_url"),
                "profile": None,  # No profile for non-exact matches
                "registry": reg,
            }
            out["candidates"] = []
            return out

    out["resolved"] = {"status": "manual_required", "reason": "No exact canonicalised match found across registries"}
    out["candidates"] = combined
    return out

# -----------------------------------------------------------------------------
# Quick test harness
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python resolver.py <search term>", flush=True)
        sys.exit(1)

    term = sys.argv[1]
    print(f"\n[TEST] Resolving: {term}", flush=True)
    try:
        result = resolve_company(term, top_n=5, sources=("ch","ccew"))
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"[TEST ERROR] {e}", flush=True)