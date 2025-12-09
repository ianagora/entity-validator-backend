# app.py
import os, json, tempfile, sqlite3, threading, hashlib, io, csv
from datetime import datetime, date
from contextlib import contextmanager, asynccontextmanager
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse, quote_plus, parse_qs as _parse_qs
from fastapi.staticfiles import StaticFiles
from fastapi import Query
from fastapi.responses import StreamingResponse
from typing import Optional, Union, List, Dict, Any, Tuple, Literal
import queue
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import time
import re
import re as _re
import math
from pandas import json_normalize
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.status import HTTP_302_FOUND
from resolver import ccew_candidates
from resolver import canonicalise_name
from schema import SCHEMA_ENTITY_FIELDS, LP_PREFIX, LP_COUNT

# resolver integrations
from resolver import resolve_company, get_company_bundle, get_charity_bundle_cc
from resolver import get_company_filing_history, get_filing_detail, get_document_metadata, download_cs01_pdf, get_cs01_filings_for_company, get_in01_filings_for_company, download_in01_pdf

# shareholder extraction
from shareholder_information import extract_shareholders_for_company
from shareholder_information import identify_parent_companies

# corporate structure (recursive ownership tree)
from corporate_structure import build_ownership_tree, flatten_ownership_tree

# ---------------- Worker Pool Configuration ----------------
# CRITICAL: Limit concurrent enrichments to prevent memory exhaustion
# With 512MB Railway free tier: max 1 worker (sequential processing)
# With 8GB Railway Hobby: max 3-6 workers (testing 6 after successful 3-worker tests)
# With 32GB Railway Pro: max 5-10 workers
MAX_CONCURRENT_WORKERS = int(os.environ.get('MAX_WORKERS', '6'))  # Default: 6 (testing higher concurrency)
enrichment_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORKERS, thread_name_prefix='enrich')
print(f"[WORKER_POOL] Initialized with {MAX_CONCURRENT_WORKERS} concurrent workers")

# ---------------- App Setup ----------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    os.makedirs(RESULTS_BASE, exist_ok=True)
    init_db()

    # Backfill / repair enrichment:
    #  - Route CH items to CH worker
    #  - Route CCEW items to Charity worker (lookup charity_number if missing)
    with db() as conn:
        cur = conn.cursor()
        rows = cur.execute("""
            SELECT id, input_name, entity_name, company_number, charity_number, resolved_registry, enrich_status
            FROM items
            WHERE pipeline_status='auto'
              AND (enrich_status IS NULL OR enrich_status IN ('pending','queued'))
            ORDER BY id ASC
        """).fetchall()

    for r in rows:
        reg   = canonical_registry_name(r["resolved_registry"])
        comp  = (r["company_number"] or "").strip() or None
        chno  = (r["charity_number"] or "").strip() or None

        if reg == "Companies House" and comp:
            # Route to CH enrichment worker
            enqueue_enrich(r["id"])
        elif reg == "Charity Commission":
            # Route to Charity enrichment worker
            enqueue_enrich_charity(r["id"])

    yield

    # Shutdown logic (if needed)
    pass

app = FastAPI(title="Entity Batch Validator (Queues + Admin, no-login)", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

# Serve /static/* from the local "static" folder
app.mount("/static", StaticFiles(directory="static"), name="static")

DB_PATH = "entity_workflow.db"
RESULTS_BASE = "results"

# CH link helpers
CH_HOST = "find-and-update.company-information.service.gov.uk"
def ch_company_url(company_number: str) -> str:
    company_number = (company_number or "").strip()
    return f"https://{CH_HOST}/company/{company_number}/"  # trailing slash helps avoid 403s

# ---------------- DB helpers ----------------
@contextmanager
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")  # wait up to 5s on locks
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

def _ref_for_item(row) -> str:
    """Prefer company_number, else charity_number, else ''."""
    try:
        c = (row["company_number"] or "").strip()
    except Exception:
        c = ""
    if c:
        return c
    try:
        ch = (row["charity_number"] or "").strip()
    except Exception:
        ch = ""
    return ch

def _ref_for_item(r) -> str:
    try:
        return (r["company_number"] or r["charity_number"] or "") or ""
    except Exception:
        return ""

def _extract_charity_number(base: dict, candidates: list) -> Optional[str]:
    """
    Try to find a Charity Commission registration number from:
      1) the resolved base['source_url'] (…regId=123456&subId=0),
      2) the candidate rows (various possible keys),
      3) anything that looks like digits inside the URL path as a last resort.
    """
    # 1) from the resolved source_url (preferred)
    src = (base or {}).get("source_url") or ""
    if src:
        try:
            q = _parse_qs(urlparse(src).query)
            regid = (q.get("regId") or q.get("regid") or q.get("regID") or [None])[0]
            if regid and str(regid).isdigit():
                return str(int(regid))
        except Exception:
            pass
        # fallback: look for '/charity-details/?regId=123456' pattern by regex
        m = _re.search(r"[?&]regId=(\d+)", src)
        if m:
            return m.group(1)

    # 2) from first charity-like candidate
    for c in candidates or []:
        for k in ("charity_number", "candidate_charity_number",
                  "registered_charity_number", "registeredCharityNumber",
                  "registration_number"):
            v = c.get(k)
            if v and str(v).strip().isdigit():
                return str(int(str(v).strip()))

    # 3) last-resort: scrape digits from URL path
    if src:
        m = _re.search(r"/(\d{4,7})(?:/|$)", urlparse(src).path or "")
        if m:
            return m.group(1)

    return None

def _iso_to_ddmmyyyy(iso: Optional[str]) -> Optional[str]:
    if not iso: return None
    d = str(iso)[:10]  # 'YYYY-MM-DD'
    if len(d) != 10: return None
    return f"{d[8:10]}-{d[5:7]}-{d[0:4]}"

def _row_get(row, key, default=None):
    """Safe lookup for sqlite3.Row (no .get on Row)."""
    try:
        v = row[key]
    except Exception:
        return default
    return v if v is not None else default

# ------------ helpers used by roll-up (place once) ------------
_ENRICH_IGNORE = {
    "entity_name", "name", "company_name", "company_number", "charity_number",
    "registry", "register", "source", "source_url", "retrieved_at",
    "created_at", "updated_at", "id"
}

def _is_meaningful(v) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict, set, tuple)):
        return len(v) > 0
    return True

def _safe_read_json(path: str) -> dict:
    try:
        if not path:
            return {}
        p = path if os.path.isabs(path) else os.path.abspath(path)
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[rollup] failed to load JSON {path}: {e}")
    return {}

def _clean_cell(v):
    """
    Normalise uploaded cell values:
    - pandas NaT/NaN/None/'' -> None
    - datetime/date/Timestamp -> 'YYYY-MM-DD'
    - everything else -> trimmed string ('' -> None)
    """
    try:
        import pandas as pd
        if v is None or (isinstance(v, float) and v != v) or pd.isna(v):
            return None
    except Exception:
        if v is None or (isinstance(v, float) and v != v):
            return None
    # empty string
    if isinstance(v, str) and not v.strip():
        return None
    # dates
    try:
        from datetime import date, datetime
        import pandas as pd  # noqa
        if isinstance(v, (pd.Timestamp, datetime, date)):
            return str(v)[:10]  # 'YYYY-MM-DD'
    except Exception:
        pass
    s = str(v).strip()
    return s if s else None
# --------------------------------------------------------------


def _record_compare_rollup(row) -> dict:
    """
    Compute record-level flags that mirror what is *visible* on the compare page:
      - has_mismatch  (uploaded present & != enriched)
      - has_enrichment (uploaded empty & enriched present OR LP-only OR bundle-only enrichment)
      - potential_risk (any name/DoB differences or LP name/DoB enrichment)
    """
    # ---- safe accessor (sqlite3.Row or dict)
    def _rg(r, k, default=None):
        try:
            return r[k]
        except Exception:
            try:
                return r.get(k, default)  # type: ignore[attr-defined]
            except Exception:
                return default

    # ---- Build uploaded_map from EXACT schema (cleaned, non-empty only)
    uploaded_map = {}
    for h in ALL_SCHEMA_FIELDS:
        try:
            v = row[h]
        except Exception:
            v = None
        cv = _clean_cell(v)
        if cv is not None:
            uploaded_map[_norm_key_for_match(h)] = cv

    # ---- Seed sensible fallbacks (mirrors compare page UX) — also cleaned
    in_name = _clean_cell(_rg(row, "input_name"))
    if "entity_name" not in uploaded_map and in_name:
        uploaded_map["entity_name"] = in_name

    client_pc = _clean_cell(_rg(row, "client_address_postcode"))
    if "entity_primary_address_postcode" not in uploaded_map and client_pc:
        uploaded_map["entity_primary_address_postcode"] = client_pc

    client_ctry = _clean_cell(_rg(row, "client_address_country"))
    if "entity_primary_address_country" not in uploaded_map and client_ctry:
        uploaded_map["entity_primary_address_country"] = client_ctry

    # ---- Read enrichment bundle (robust)
    bundle = {}
    enrich_path = (
        _rg(row, "enrich_json_path")
        or _rg(row, "enriched_json_path")
        or _rg(row, "bundle_path")
        or _rg(row, "auto_detail_path")
    )
    if enrich_path:
        bundle = _safe_read_json(enrich_path) or {}

    enriched_focus = {}
    if bundle:
        for root in ("profile", "officers", "pscs", "charges", "trustees", "filings", "sources"):
            if root in bundle:
                enriched_focus[root] = bundle[root]
        try:
            enriched_focus.setdefault("_derived", {})
            enriched_focus["_derived"]["counts.officers"] = len((bundle.get("officers") or {}).get("items") or [])
            enriched_focus["_derived"]["counts.pscs"] = len((bundle.get("pscs") or {}).get("items") or [])
            enriched_focus["_derived"]["counts.charges"] = len((bundle.get("charges") or {}).get("items") or [])
            enriched_focus["_derived"]["counts.trustees"] = len(bundle.get("trustees") or [])
            enriched_focus["_derived"]["counts.filings"] = len(bundle.get("filings") or [])
        except Exception:
            pass

    enriched_flat = _flatten_enriched(enriched_focus) if enriched_focus else {}

    # ---- Authoritative values (for lookups)
    reg = _rg(row, "resolved_registry") or ""
    is_ch = reg.startswith("Companies House")
    is_cc = "Charity Commission" in reg
    auth_map, _ = _authoritative_map(bundle, is_ch=is_ch, is_cc=is_cc)
    auth_map_norm = { _norm_key_for_match(k): v for k, v in auth_map.items() }

    # ---- Build quick index from flattened enriched keys
    enriched_index = {}
    for k, v in (enriched_flat or {}).items():
        nf = _norm_key_for_match(k)
        lf = _norm_key_for_match(k.split(".")[-1])
        enriched_index.setdefault(nf, []).append(v)
        if lf != nf:
            enriched_index.setdefault(lf, []).append(v)

    def _first_enriched_for(norm_key):
        # prefer authoritative mapping
        if norm_key in auth_map_norm and auth_map_norm[norm_key] not in (None, ""):
            return auth_map_norm[norm_key]
        # else any flattened candidate
        for v in enriched_index.get(norm_key, []):
            if v not in (None, ""):
                return v
        return None

    has_mismatch = False
    has_enrichment = False
    potential_risk = False

    # ---- ONLY uploaded+seeded keys drive mismatches
    for norm_key in set(uploaded_map.keys()):
        up_val = uploaded_map.get(norm_key)
        ev = _first_enriched_for(norm_key)

        if (up_val in (None, "")) and (ev in (None, "")):
            continue

        same = False
        if ev is not None and up_val is not None:
            same = _smart_equal(norm_key, str(up_val), str(ev))

        if not same:
            if ev is None and up_val:
                # enriched missing
                pass
            elif ev is not None and (up_val is None or up_val == ""):
                # visible as "enriched"
                has_enrichment = True
                if norm_key == "entity_name" or "dob" in norm_key or norm_key.startswith("linked_party_full_name_"):
                    potential_risk = True
            else:
                # both present & different -> mismatch
                has_mismatch = True
                if norm_key == "entity_name" or "dob" in norm_key or norm_key.startswith("linked_party_full_name_"):
                    potential_risk = True

    # ---- LP-only enrichment when no LP upload fields existed
    for k in list(enriched_index.keys()):
        if k.startswith("linked_party_full_name_") or "dob" in k:
            if k not in uploaded_map and _first_enriched_for(k) not in (None, ""):
                has_enrichment = True
                potential_risk = True

    # ---- Generic enrichment: any meaningful bundle field not uploaded
    for k, v in auth_map_norm.items():
        if k not in uploaded_map and k not in _ENRICH_IGNORE and _is_meaningful(v):
            has_enrichment = True
            if k == "entity_name" or "dob" in k:
                potential_risk = True

    return {
        "has_mismatch": has_mismatch,
        "has_enrichment": has_enrichment,
        "potential_risk": potential_risk,
    }

def _smart_equal(field_norm: str, a: str, b: str) -> bool:
    """Same tolerant comparison rules used on the compare page."""
    if a is None or b is None: return False
    sa, sb = str(a).strip(), str(b).strip()
    if sa == sb: return True

    # Case-insensitive for roles/words
    if field_norm in {"type","company_status","entity_primary_city","entity_primary_address_country"}:
        return sa.lower() == sb.lower()

    # Normalise names
    if field_norm in {"entity_name"} or field_norm.startswith("linked_party_full_name_"):
        def _norm_name(s):
            s = s.replace(",", " ")
            s = re.sub(r"\s+", " ", s).strip().lower()
            return s
        return _norm_name(sa) == _norm_name(sb)

    # DoB tolerance: 'YYYY-MM' ~ 'YYYY-MM-01 00:00:00'
    if ("dob" in field_norm) or ("date_of_birth" in field_norm):
        def _ym(s):
            m = re.match(r"^\s*(\d{4})-(\d{2})(?:-\d{2})?", s)
            return m.group(1)+"-"+m.group(2) if m else None
        ya, yb = _ym(sa), _ym(sb)
        if ya and yb: return ya == yb

    # Postcode/country/etc: collapse spaces/case
    if "postcode" in field_norm:
        return sa.replace(" ", "").upper() == sb.replace(" ", "").upper()

    return False

# ---- Authoritative value + source tracking ----------------------------------

def _map_from_ch_with_sources(bundle: dict):
    """
    Return (value_map, consumed_paths) where:
      - value_map maps normalized schema headers (incl. aliases) -> value
      - consumed_paths is a set of flattened bundle paths we used
    """
    val = {}
    used = set()

    prof = (bundle or {}).get("profile") or {}
    addr = prof.get("registered_office_address") or {}

    def use(path, value):
        if path: used.add(path)
        return value

    # identity / status / dates / type
    val["entity_name"]       = use("profile.company_name",    prof.get("company_name"))
    val["company_number"]    = use("profile.company_number",  prof.get("company_number"))
    val["company_status"]    = use("profile.company_status",  prof.get("company_status"))
    val["date_of_creation"]  = use("profile.date_of_creation",prof.get("date_of_creation"))
    val["type"]              = use("profile.type",            prof.get("type"))

    # address parts (expose both split + full)
    line1   = addr.get("address_line_1") or ""
    line2   = addr.get("address_line_2") or ""
    city    = addr.get("locality") or ""
    region  = addr.get("region") or ""
    pcode   = addr.get("postal_code") or ""
    country = addr.get("country") or ""

    val["entity_primary_address_line1"] = use("profile.registered_office_address.address_line_1", line1 or None)
    val["entity_primary_address_line2"] = use("profile.registered_office_address.address_line_2", line2 or None)
    val["entity_primary_city"]          = use("profile.registered_office_address.locality",       city or None)
    val["entity_primary_address_postcode"] = use("profile.registered_office_address.postal_code", pcode or None)
    val["entity_primary_address_country"]  = use("profile.registered_office_address.country",     country or None)

    # full address (also mark parts so they don’t appear as extras)
    for p in (
        "profile.registered_office_address.address_line_1",
        "profile.registered_office_address.address_line_2",
        "profile.registered_office_address.locality",
        "profile.registered_office_address.region",
        "profile.registered_office_address.postal_code",
        "profile.registered_office_address.country",
    ):
        used.add(p)
    full_addr = ", ".join([x for x in (line1, line2, city, region, pcode, country) if x]).strip(", ")
    val["entity_primary_address"] = full_addr or None

    # SIC codes
    sic = prof.get("sic_codes") or []
    if isinstance(sic, list):
        for i in range(len(sic)):
            used.add(f"profile.sic_codes[{i}]")
        sic_join = ", ".join([str(x) for x in sic if x])
    else:
        used.add("profile.sic_codes")
        sic_join = str(sic) if sic else None
    val["sic_codes"] = sic_join

    # counts
    off_items = (bundle.get("officers") or {}).get("items") or []
    psc_items = (bundle.get("pscs") or {}).get("items") or []
    chg_items = (bundle.get("charges") or {}).get("items") or []
    val["officer_count"] = len(off_items); used.add("officers.items")
    val["psc_count"]     = len(psc_items); used.add("pscs.items")
    val["charge_count"]  = len(chg_items); used.add("charges.items")

    # ---- alias expansion so your sheet headers match directly ----
    alias_map = {
        _norm_key_for_match("entity_name"): ["Entity_name", "name", "company_name"],
        _norm_key_for_match("company_number"): [
            "Entity_registration_number", "registration_number", "company_registration_number", "reg_number",
        ],
        _norm_key_for_match("type"): ["Entity_type", "entitytype", "organisation_type", "organization_type"],
        _norm_key_for_match("company_status"): ["Entity_status (active/dissolved etc)", "entity_status", "status"],
        _norm_key_for_match("date_of_creation"): ["Entity_incorporation_date", "incorporation_date", "date_of_incorporation"],

        _norm_key_for_match("entity_primary_address_line1"): ["Entity_primary_address_line1", "address_line_1"],
        _norm_key_for_match("entity_primary_address_line2"): ["Entity_primary_address_line2", "address_line_2"],
        _norm_key_for_match("entity_primary_city"):          ["Entity_primary_city", "city", "locality"],
        _norm_key_for_match("entity_primary_address_postcode"): [
            "postcode", "postal_code", "zip",
            "entity_address_postcode", "entity_primary_address_postcode",
            "entity_primary_postcode",  # your sheet
        ],
        _norm_key_for_match("entity_primary_address_country"): ["country", "entity_primary_address_country"],
        _norm_key_for_match("entity_primary_address"): ["address", "entity_address", "registered_office_address", "entity_primary_address"],

        _norm_key_for_match("sic_codes"): ["sic_codes", "industry_codes", "industry", "Existing_SIC_codes"],

        _norm_key_for_match("officer_count"): ["officer_count"],
        _norm_key_for_match("psc_count"):     ["psc_count"],
        _norm_key_for_match("charge_count"):  ["charge_count"],
    }

    return _aliasify(val, alias_map), used

def _map_from_ccew_with_sources(bundle: dict):
    """
    Charity Commission variant that also exposes aliases expected by the sheet.
    """
    val = {}
    used = set()

    prof = (bundle or {}).get("profile") or {}
    trustees = (bundle or {}).get("trustees") or []

    def use(path, value):
        if path: used.add(path)
        return value

    val["entity_name"]    = use("profile.name",           prof.get("name"))
    val["charity_number"] = use("profile.charity_number", prof.get("charity_number"))
    # keep cross-source consistency: status under 'company_status'
    val["company_status"] = use("profile.status",         prof.get("status"))

    addr = prof.get("address")
    if isinstance(addr, dict):
        for p in ("addressLine1","address_line_1","addressLine2","address_line_2",
                  "addressLine3","town","locality","postcode","country"):
            if p in addr: used.add(f"profile.address.{p}")
        addr_str = ", ".join([
            addr.get("addressLine1") or addr.get("address_line_1") or "",
            addr.get("addressLine2") or addr.get("address_line_2") or "",
            addr.get("addressLine3") or "",
            addr.get("town") or addr.get("locality") or "",
            addr.get("postcode") or "",
            addr.get("country") or "",
        ]).strip(", ").replace(",,", ",")
        val["entity_primary_address"] = addr_str or None
        val["entity_primary_address_postcode"] = addr.get("postcode") or prof.get("postcode")
        if "postcode" in prof: used.add("profile.postcode")
    else:
        if addr: used.add("profile.address")
        val["entity_primary_address"] = addr or None
        val["entity_primary_address_postcode"] = prof.get("postcode")
        if "postcode" in prof: used.add("profile.postcode")

    val["trustee_count"] = len(trustees); used.add("trustees")
    trustee_names = ", ".join([str(t.get("name") or t.get("displayName") or "")
                               for t in trustees if (t.get("name") or t.get("displayName"))])
    val["trustee_names"] = trustee_names or None

    if prof.get("type") or prof.get("organisationType"):
        val["type"] = use("profile.type" if "type" in prof else "profile.organisationType",
                          prof.get("type") or prof.get("organisationType"))

    alias_map = {
        _norm_key_for_match("entity_name"): ["Entity_name", "name"],
        _norm_key_for_match("charity_number"): ["Entity_registration_number", "charity_registration_number"],
        _norm_key_for_match("company_status"): ["Entity_status (active/dissolved etc)", "entity_status", "status"],
        _norm_key_for_match("type"): ["Entity_type", "entitytype", "organisation_type", "organization_type"],
        _norm_key_for_match("entity_primary_address_postcode"): [
            "postcode", "postal_code", "zip",
            "entity_primary_address_postcode", "entity_address_postcode",
            "entity_primary_postcode",  # keep in step with CH alias
        ],
        _norm_key_for_match("entity_primary_address"): ["address", "entity_address", "entity_primary_address"],
        _norm_key_for_match("trustee_names"): ["trustee_names"],
        _norm_key_for_match("trustee_count"): ["trustee_count"],
    }

    val = _aliasify(val, alias_map)
    return val, used

def _authoritative_map(bundle: dict, *, is_ch: bool, is_cc: bool):
    """
    Centralised authoritative mapping. Returns (value_map, consumed_paths)
    where 'value_map' uses *normalized* schema headers as keys.
    """
    if is_ch:
        return _map_from_ch_with_sources(bundle)
    if is_cc:
        return _map_from_ccew_with_sources(bundle)
    return {}, set()

# ---------- compare helpers: key normaliser + bundle mappers ----------

def _norm_key_for_match(s: str) -> str:
    """lowercase, remove non-alnum, collapse spaces/underscores to align headers/paths."""
    if not s:
        return ""
    s = str(s).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s

def _aliasify(values_by_primary_key: Dict[str, Any], alias_map: Dict[str, list]) -> Dict[str, Any]:
    """
    Expand a mapping so each 'primary key' is also exposed under its aliases.
    All keys (primary and aliases) are normalised with _norm_key_for_match.
    """
    out = {}
    for primary, val in values_by_primary_key.items():
        prim_norm = _norm_key_for_match(primary)
        out[prim_norm] = val
        for alias in alias_map.get(prim_norm, []):
            out[_norm_key_for_match(alias)] = val
    return out

def _flatten_json(obj, prefix=""):
    """Flatten a nested dict/list into {'a.b[0].c': value} for loose matching."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten_json(v, f"{prefix}{k}." if prefix else f"{k}."))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flatten_json(v, f"{prefix}[{i}]."))
    else:
        out[prefix[:-1]] = obj
    return out

def _get_in(dct, *path, default=None):
    cur = dct
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return default
    return cur if cur is not None else default

def _map_from_ch(bundle: dict) -> dict:
    """
    Authoritative values for common headers from a Companies House bundle,
    projected onto *your* upload schema names (plus sensible aliases).
    """
    prof = (bundle or {}).get("profile") or {}
    addr = prof.get("registered_office_address") or {}
    sic  = prof.get("sic_codes") or []

    if isinstance(sic, list):
        sic_join = ", ".join([str(x) for x in sic if x])
    else:
        sic_join = str(sic) if sic else None

    values = {
        "entity_name":                        prof.get("company_name"),
        "company_number":                     prof.get("company_number"),
        "type":                               prof.get("type"),
        "company_status":                     prof.get("company_status"),
        "date_of_creation":                   prof.get("date_of_creation"),
        "entity_primary_address_postcode":    addr.get("postal_code"),
        "entity_primary_address_country":     addr.get("country"),
        "entity_primary_address":             ", ".join([
            addr.get("address_line_1") or "",
            addr.get("address_line_2") or "",
            addr.get("locality") or "",
            addr.get("region") or "",
            addr.get("postal_code") or "",
            addr.get("country") or "",
        ]).strip(", ").replace(",,", ","),
        "sic_codes":                          sic_join,
        "officer_count":                      len((_get_in(bundle, "officers", "items") or [])),
        "psc_count":                          len((_get_in(bundle, "pscs", "items") or [])),
        "charge_count":                       len((_get_in(bundle, "charges", "items") or [])),
    }

    alias_map = {
        # --- Identity
        _norm_key_for_match("entity_name"): [
            "Entity_name", "name", "company_name", "registered_name", "legal_name",
            "organisation_name", "organization_name", "entity legal name",
        ],
        _norm_key_for_match("company_number"): [
            "Entity_registration_number", "registration_number", "company_registration_number",
            "reg_number", "company number", "companies house number", "ch_number",
            "crn", "company_reg_number", "company reg no", "reg no", "reg. no",
        ],
        _norm_key_for_match("type"): [
            "Entity_type", "entitytype", "organisation_type", "organization_type",
            "company_type", "legal_form", "org_type",
        ],

        # --- Status & dates
        _norm_key_for_match("company_status"): [
            "Entity_status (active/dissolved etc)", "entity_status", "status",
            "company_status", "current_status",
        ],
        _norm_key_for_match("date_of_creation"): [
            "Entity_incorporation_date", "incorporation_date", "date_of_incorporation",
            "incorporated", "founded_date", "formation_date",
        ],

        # --- Address (split & full)
        _norm_key_for_match("entity_primary_address_postcode"): [
            "postcode", "postal_code", "post_code", "zip", "zip_code",
            "entity_address_postcode", "entity_primary_address_postcode",
            "registered_office_postcode",
        ],
        _norm_key_for_match("entity_primary_address_country"): [
            "country", "entity_primary_address_country", "registered_office_country",
            "country_of_registered_office", "country/region",
        ],
        _norm_key_for_match("entity_primary_address"): [
            "address", "entity_address", "registered_office_address", "registered address",
            "address (registered office)", "entity_primary_address", "head_office_address",
        ],

        # --- Industry / classification
        _norm_key_for_match("sic_codes"): [
            "sic_codes", "sic", "sic code", "sic codes", "sic code(s)",
            "industry_codes", "industry", "industry_classification",
            "primary_sic", "sic_1", "sic_2", "sic_3", "sic_4",
        ],

        # --- Convenient counts
        _norm_key_for_match("officer_count"): ["officer_count", "directors_count", "number_of_officers"],
        _norm_key_for_match("psc_count"):     ["psc_count", "number_of_pscs", "persons_with_significant_control_count"],
        _norm_key_for_match("charge_count"):  ["charge_count", "mortgage_count", "charges_count"],
    }

    return _aliasify(values, alias_map)

def _map_from_ccew(bundle: dict) -> dict:
    """
    Authoritative values for common headers from a Charity Commission bundle,
    projected onto *your* upload schema names (plus sensible aliases).
    """
    prof = (bundle or {}).get("profile") or {}
    trustees = (bundle or {}).get("trustees") or []

    # address may be dict or str
    addr = prof.get("address")
    if isinstance(addr, dict):
        addr_str = ", ".join([
            addr.get("addressLine1") or addr.get("address_line_1") or "",
            addr.get("addressLine2") or addr.get("address_line_2") or "",
            addr.get("addressLine3") or "",
            addr.get("town") or addr.get("locality") or "",
            addr.get("postcode") or "",
            addr.get("country") or "",
        ]).strip(", ").replace(",,", ",")
    else:
        addr_str = str(addr) if addr else None

    trustee_names = ", ".join([
        str(t.get("name") or t.get("displayName") or "")
        for t in trustees if (t.get("name") or t.get("displayName"))
    ]) or None

    values = {
        "entity_name":                       prof.get("name"),
        "charity_number":                    prof.get("charity_number"),
        "company_status":                    prof.get("status"),
        "entity_primary_address_postcode":   prof.get("postcode"),
        "entity_primary_address":            addr_str,
        "trustee_names":                     trustee_names,
        "trustee_count":                     len(trustees),
        "type":                              prof.get("type") or prof.get("organisationType"),
    }

    alias_map = {
        # --- Identity
        _norm_key_for_match("entity_name"): [
            "Entity_name", "name", "charity_name", "registered_charity_name",
            "organisation_name", "organization_name",
        ],
        _norm_key_for_match("charity_number"): [
            "Entity_registration_number", "charity_registration_number",
            "registered_charity_number", "charity no", "charity_no", "rcn",
            "ccew_number", "registration_number",
        ],

        # --- Status & type
        _norm_key_for_match("company_status"): [
            "Entity_status (active/dissolved etc)", "entity_status", "status",
            "charity_status", "current_status",
        ],
        _norm_key_for_match("type"): [
            "Entity_type", "entitytype", "organisation_type", "organization_type",
            "charity_type", "org_type",
        ],

        # --- Address
        _norm_key_for_match("entity_primary_address_postcode"): [
            "postcode", "postal_code", "post_code", "zip", "zip_code",
            "entity_primary_address_postcode", "entity_address_postcode",
            "registered_address_postcode",
        ],
        _norm_key_for_match("entity_primary_address"): [
            "address", "entity_address", "entity_primary_address",
            "registered_address", "principal_office_address",
        ],

        # --- Trustees
        _norm_key_for_match("trustee_names"): [
            "trustee_names", "trustees", "board_members", "trustee names (csv)",
            "list_of_trustees",
        ],
        _norm_key_for_match("trustee_count"): [
            "trustee_count", "number_of_trustees", "trustees_count",
            "board_size",
        ],
    }

    return _aliasify(values, alias_map)

def _authoritative_for_header(header: str, bundle: dict, *, is_ch: bool, is_cc: bool):
    """
    If we recognise the schema header, return a reliable value from the bundle;
    otherwise None and the caller can fall back to generic flatten matching.
    """
    key_norm = _norm_key_for_match(header)
    if is_ch:
        mapped = _map_from_ch(bundle)
        return mapped.get(key_norm)
    if is_cc:
        mapped = _map_from_ccew(bundle)
        return mapped.get(key_norm)
    return None

def _best_charity_number_for_name(name: str) -> Optional[str]:
    """
    Quick lookup to grab a Charity Commission number for a given name.
    Prefers an exact canonicalised name match; otherwise returns the top candidate.
    """
    try:
        cands, exact, _ = ccew_candidates(name, limit=10)
        if exact and (exact.get("charity_number") or exact.get("charityNumber")):
            return str(exact.get("charity_number") or exact.get("charityNumber"))
        # fall back to first candidate that carries a charity number
        for c in cands or []:
            num = c.get("charity_number") or c.get("charityNumber") or c.get("registrationNumber")
            if num:
                return str(num)
    except Exception:
        pass
    return None

def enqueue_for_registry(item_id: int, registry: Optional[str], company_number: Optional[str], charity_number: Optional[str]):
    registry = (registry or "").strip()
    if registry == "Companies House" and (company_number or "").strip():
        enqueue_enrich(item_id)
    elif registry == "Charity Commission" and (charity_number or "").strip():
        enqueue_enrich_charity(item_id)
    else:
        # nothing to do (leave as pending/queued resolver will tidy later)
        with db() as conn:
            conn.execute("UPDATE items SET enrich_status='skipped' WHERE id=?", (item_id,))

def canonical_registry_name(reg: Optional[str]) -> Optional[str]:
    if not reg:
        return None
    r = str(reg).strip().lower().replace("-", "_").replace(" ", "")
    if r in {"companieshouse", "companies_house", "ch"}:
        return "Companies House"
    if r in {"charitycommission", "charity_commission", "cc", "ccew"}:
        return "Charity Commission"
    return reg

def is_companies_house(reg: Optional[str]) -> bool:
    return canonical_registry_name(reg) == "Companies House"

def extract_all_schema_fields_from_row(row) -> dict:
    """Pull every EXACT client-upload field (26 + 50×10 = 526) from a pandas row."""
    return {h: _norm_cell(row.get(h)) for h in ALL_SCHEMA_FIELDS}

def _infer_registry_from_company_number(n: str) -> Optional[str]:
    """
    Very small heuristic: return 'companies_house' for CH-looking numbers.
    CH numbers: 8 digits or 2 letters + 5–6 digits (SC, NI, OC, SO, LP, SL, FC, SE, GE, ES, NL).
    """
    if not n:
        return None
    n = n.strip().upper()
    if re.fullmatch(r"\d{8}", n):
        return "companies_house"
    if re.fullmatch(r"(SC|NI|OC|SO|LP|SL|FC|SE|GE|ES|NL)\d{5,6}", n):
        return "companies_house"
    return None

# ---- Full flat schema headers: 26 entity + 50× linked party (10 attrs) ----
def get_all_schema_fields() -> List[str]:
    linked_cols = []
    for i in range(1, LP_COUNT + 1):  # LP_COUNT = 50
        for _, prefix in LP_PREFIX.items():  # exact prefixes
            linked_cols.append(f"{prefix}{i}")
    return SCHEMA_ENTITY_FIELDS + linked_cols

ALL_SCHEMA_FIELDS = get_all_schema_fields()

def init_db():
    def _q(s: str) -> str:
        # Quote identifiers that contain spaces/special chars for SQLite
        return '"' + s.replace('"', '""') + '"'

    # Use the global ALL_SCHEMA_FIELDS built at import time
    fields_sql = ",\n            ".join(f"{_q(col)} TEXT" for col in ALL_SCHEMA_FIELDS)

    with db() as conn:
        c = conn.cursor()

        # ---------------- Runs
        c.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            upload_filename TEXT
        )""")

        # ---------------- Items (workflow/meta + full flat schema)
        c.execute(f"""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,

            -- matching/workflow meta
            input_name TEXT NOT NULL,
            name_hash TEXT,
            pipeline_status TEXT NOT NULL,  -- 'auto' | 'manual_required' | 'error'
            match_type TEXT,
            company_number TEXT,
            company_status TEXT,
            confidence REAL,
            reason TEXT,
            search_url TEXT,
            source_url TEXT,
            retrieved_at TEXT,
            candidates_json TEXT,
            enrich_status TEXT DEFAULT 'pending',
            enrich_json_path TEXT,
            enrich_xlsx_path TEXT,
            shareholders_json TEXT,
            shareholders_status TEXT,
            ownership_tree_json TEXT,
            out_dir TEXT,
            created_at TEXT NOT NULL,
            resolved_registry TEXT,

            -- ==== BEGIN: EXACT client-upload schema (526 total) ====
            {fields_sql}
            -- ==== END: EXACT client-upload schema ====

            ,
            -- legacy freeform (used by UI and inserts; safe to keep)
            client_ref TEXT,
            client_address TEXT,
            client_address_city TEXT,
            client_address_postcode TEXT,
            client_address_country TEXT,
            client_linked_parties TEXT,
            client_notes TEXT,

            FOREIGN KEY(run_id) REFERENCES runs(id)
        )""")

        # Case-insensitive view of existing columns
        existing_cols_lower = {
            r["name"].lower()
            for r in conn.execute("PRAGMA table_info(items)").fetchall()
        }

        # critical workflow/meta columns (legacy safety if upgrading)
        for col, decl in [
            ("name_hash", "TEXT"),
            ("resolved_registry", "TEXT"),
            ("candidates_json", "TEXT"),
            ("enrich_status", "TEXT"),
            ("enrich_json_path", "TEXT"),
            ("enrich_xlsx_path", "TEXT"),
            ("ownership_tree_json", "TEXT"),
            ("out_dir", "TEXT"),
        ]:
            if col.lower() not in existing_cols_lower:
                conn.execute(f'ALTER TABLE items ADD COLUMN {_q(col)} {decl}')
                existing_cols_lower.add(col.lower())

        # NEW: ensure charity_number exists for CCEW enrichment
        try:
            conn.execute('ALTER TABLE items ADD COLUMN "charity_number" TEXT')
            existing_cols_lower.add("charity_number")
            print("[init_db] Added charity_number column to items table")
        except sqlite3.OperationalError:
            # Column already exists
            pass

        # add any missing exact-schema columns (526)
        for col in ALL_SCHEMA_FIELDS:
            if col.lower() not in existing_cols_lower:
                conn.execute(f'ALTER TABLE items ADD COLUMN {_q(col)} TEXT')
                existing_cols_lower.add(col.lower())

        # add legacy freeform columns if missing (UI/back-compat)
        for col in [
            "client_ref",
            "client_address",
            "client_address_city",
            "client_address_postcode",
            "client_address_country",
            "client_linked_parties",
            "client_notes",
        ]:
            if col.lower() not in existing_cols_lower:
                conn.execute(f'ALTER TABLE items ADD COLUMN {_q(col)} TEXT')
                existing_cols_lower.add(col.lower())

        # Helpful indexes
        c.execute("CREATE INDEX IF NOT EXISTS idx_items_run        ON items(run_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_items_namehash   ON items(name_hash)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_items_status     ON items(pipeline_status)")

        # Add shareholder-related columns if they don't exist (migration)
        try:
            c.execute("ALTER TABLE items ADD COLUMN shareholders_json TEXT")
            print("[init_db] Added shareholders_json column to items table")
        except sqlite3.OperationalError:
            # Column already exists
            pass

        try:
            c.execute("ALTER TABLE items ADD COLUMN shareholders_status TEXT")
            print("[init_db] Added shareholders_status column to items table")
        except sqlite3.OperationalError:
            # Column already exists
            pass

        # ---------------- Users / Roles (unchanged)
        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            full_name TEXT,
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS user_roles (
            user_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, role_id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(role_id) REFERENCES roles(id)
        )""")
        for role in ("admin", "reviewer", "viewer"):
            c.execute("INSERT OR IGNORE INTO roles (name) VALUES (?)", (role,))

def _flatten_enriched(obj, prefix=""):
    """Flatten dict/list -> { 'a.b.c': value } for easy table rendering."""
    flat = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            flat.update(_flatten_enriched(v, key))
    elif isinstance(obj, list):
        # represent lists as CSV (short) else JSON string
        if all(isinstance(x, (str, int, float, type(None))) for x in obj):
            flat[prefix] = ", ".join("" if x is None else str(x) for x in obj)
        else:
            try:
                flat[prefix] = json.dumps(obj, ensure_ascii=False)
            except Exception:
                flat[prefix] = str(obj)
    else:
        flat[prefix] = "" if obj is None else str(obj)
    return flat

# ---------------- Middleware ----------------
@app.middleware("http")
async def no_referrer_everywhere(request: Request, call_next):
    response = await call_next(request)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response

# ---------------- Auth helpers (login-free stubs) ----------------
def hash_password(raw: str) -> str:
    # Simple SHA256 so admin screens can save users without bcrypt
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def verify_password(raw: str, hashed: str) -> bool:
    return hash_password(raw) == hashed

def get_user_by_email(conn: sqlite3.Connection, email: str):
    return conn.execute("SELECT * FROM users WHERE email=? AND is_active=1", (email.lower(),)).fetchone()

def get_roles_for_user(conn: sqlite3.Connection, user_id: int) -> List[str]:
    rows = conn.execute("""
        SELECT r.name FROM roles r
        JOIN user_roles ur ON ur.role_id = r.id
        WHERE ur.user_id = ?
    """, (user_id,)).fetchall()
    return [r["name"] for r in rows]

def require_user(request: Request):
    return {"id": 1, "email": "dev@local", "roles": ["admin"]}

def require_admin(request: Request):
    return {"id": 1, "email": "dev@local", "roles": ["admin"]}

# ---------------- Batch helpers ----------------
def normalize_name(name: str) -> str:
    n = (name or "").strip().lower()
    n = " ".join(n.split())
    return n

def name_to_hash(name: str) -> str:
    return hashlib.sha256(canonicalise_name(name).encode("utf-8")).hexdigest()

def read_inputs(path: str) -> pd.DataFrame:
    """
    Load the client file and normalize only workflow convenience columns.
    DOES NOT rename or alter any of the EXACT schema headers.
    """
    # Load CSV/XLSX
    if path.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)

    # --- Validate presence (non-fatal: warn but continue)
    missing = [h for h in ALL_SCHEMA_FIELDS if h not in df.columns]
    if missing:
        print(f"[upload] WARNING: {len(missing)} of {len(ALL_SCHEMA_FIELDS)} required headers missing.")

    # ---- Convenience: internal 'name' used for resolver/dedupe.
    lower_cols = {c.lower(): c for c in df.columns}
    if "entity_name" in lower_cols:
        df.rename(columns={lower_cols["entity_name"]: "name"}, inplace=True)
    else:
        for pref in ["name", "subject_name", "company", "company_name"]:
            if pref in lower_cols:
                df.rename(columns={lower_cols[pref]: "name"}, inplace=True)
                break
        if "name" not in df.columns:
            df.rename(columns={df.columns[0]: "name"}, inplace=True)

    # ---- Optional convenience columns (do NOT collide with exact schema)
    # Client reference
    for pref in ["client_ref", "reference", "external_id", "client_reference", "ref", "customer_id"]:
        if pref in lower_cols:
            df.rename(columns={lower_cols[pref]: "client_ref"}, inplace=True)
            break
    if "client_ref" not in df.columns:
        df["client_ref"] = None

    # Freeform address + parts for UI
    if "address" in lower_cols:
        df.rename(columns={lower_cols["address"]: "client_address"}, inplace=True)

    for candidates, target in [
        (["city", "town", "locality"], "client_address_city"),
        (["postcode", "zip", "postal_code"], "client_address_postcode"),
        (["country"], "client_address_country"),
    ]:
        placed = False
        for cand in candidates:
            if cand in lower_cols:
                df.rename(columns={lower_cols[cand]: target}, inplace=True)
                placed = True
                break
        if not placed and target not in df.columns:
            df[target] = None

    if "client_address" not in df.columns:
        addr1 = lower_cols.get("addr1") or lower_cols.get("address1")
        if addr1:
            df["client_address"] = df[addr1].fillna("").astype(str)
        else:
            df["client_address"] = ""
        for key in ["addr2", "address2"]:
            if key in lower_cols:
                part = df[lower_cols[key]].fillna("").astype(str)
                df["client_address"] = df["client_address"].astype(str) + (", " + part).where(part != "", "")
        for col in ["client_address_city", "client_address_postcode", "client_address_country"]:
            if col in df.columns:
                part = df[col].fillna("").astype(str)
                df["client_address"] = df["client_address"].astype(str) + (", " + part).where(part != "", "")
        if (df["client_address"] == "").all():
            df["client_address"] = None

    # Legacy one-cell linked-parties (OPTIONAL; freeform)
    for cand in ["linked_parties", "existing_linked_parties", "related_parties", "directors_on_file", "client_linked_parties"]:
        if cand in lower_cols:
            df.rename(columns={lower_cols[cand]: "client_linked_parties"}, inplace=True)
            break
    if "client_linked_parties" not in df.columns:
        df["client_linked_parties"] = None

    # Notes (optional)
    for cand in ["notes", "client_notes", "comment", "comments"]:
        if cand in lower_cols:
            df.rename(columns={lower_cols[cand]: "client_notes"}, inplace=True)
            break
    if "client_notes" not in df.columns:
        df["client_notes"] = None

    # ---- Optional hints (normalize case-insensitively)
    alias_map = {
        "entity_type": ["entity_type", "entitytype", "type", "org_type", "organisation_type", "organization_type"],
        "postcode": ["postcode", "postal_code", "zip", "post_code",
                     "entity_primary_address_postcode", "entity_address_postcode"],
        "incorporation_year": ["incorporation_year", "inc_year", "year_incorporated", "year_of_incorporation"],
    }
    for target, aliases in alias_map.items():
        found = None
        for a in aliases:
            if a in lower_cols:
                found = lower_cols[a]
                break
        if found:
            df.rename(columns={found: target}, inplace=True)
        elif target not in df.columns:
            df[target] = None

    # IMPORTANT: do NOT modify any of the EXACT ALL_SCHEMA_FIELDS.
    return df

def safe_resolve(name: str, top_n: int = 3, hints: Optional[Dict[str, Any]] = None):
    """
    Resolve a name; when the upload hints 'Charity', always include Charity Commission
    candidates and guarantee at least one makes the final slice if any exist.
    """
    name = (name or "").strip()
    if not name:
        return {"input_name": "", "status": "error", "error_message": "empty_name"}, []

    def _norm(s):  return str(s or "").strip()
    def _lname(s): return _norm(s).lower()
    def _conf(v) -> float:
        try: return float(v or 0.0)
        except Exception: return 0.0

    looks_like_charity = False
    kw: Dict[str, Any] = {}
    if hints:
        h = _lname(hints.get("entity_type"))
        looks_like_charity = any(x in h for x in ("charity", "nonprofit", "ngo", "foundation", "trust"))
        for k in ("postcode", "incorporation_year"):
            v = hints.get(k)
            if v is not None and str(v).strip() != "":
                kw[k] = v

    resolver_top = max((top_n or 0) * 4, 12)
    if looks_like_charity:
        resolver_top = max(resolver_top, 50)

    def _call_resolver(**extra_kw):
        try:
            return resolve_company(name, top_n=resolver_top, **{**kw, **extra_kw})
        except TypeError:
            try:
                return resolve_company(name, top_n=resolver_top, **extra_kw)
            except TypeError:
                return resolve_company(name, top_n=resolver_top)

    # ----- 1) Neutral search -----
    result_primary = _call_resolver()
    resolved_primary = (result_primary.get("resolved") or {})
    candidates_primary = list(result_primary.get("candidates") or [])

    # ----- 2) Charity Commission search if hinted -----
    resolved_fallback: Dict[str, Any] = {}
    candidates_fallback: List[Dict[str, Any]] = []
    best_result_container = result_primary

    if looks_like_charity:
        try:
            tmp = _call_resolver(registry_hint="charity_commission")
            resolved_fallback = (tmp.get("resolved") or {})
            candidates_fallback = list(tmp.get("candidates") or [])
            if _conf(resolved_fallback.get("confidence")) > _conf(resolved_primary.get("confidence")):
                best_result_container = tmp
        except Exception:
            pass

        if not candidates_fallback:
            try:
                tmp = _call_resolver(entity_type="charity")
                resolved_fallback2 = (tmp.get("resolved") or {})
                candidates_fallback = list(tmp.get("candidates") or [])
                if _conf(resolved_fallback2.get("confidence")) > _conf((best_result_container.get("resolved") or {}).get("confidence")):
                    best_result_container = tmp
            except Exception:
                pass

    print(f"[DEBUG] safe_resolve for '{name}' | CH={len(candidates_primary)} | CCEW={len(candidates_fallback)} | looks_like_charity={looks_like_charity}")

    resolved = resolved_primary
    if _conf(resolved_fallback.get("confidence")) > _conf(resolved_primary.get("confidence")):
        resolved = resolved_fallback

    # ----- merge + de-dupe -----
    def _ckey(c: Dict[str, Any]) -> tuple:
        nm  = _lname(c.get("entity_name"))
        num = _norm(c.get("company_number")).upper()
        if num:
            return ("num_name", num, nm)
        ok = _lname(c.get("overlap_key") or c.get("overlap") or c.get("cluster"))
        if ok:
            return ("name_overlap", nm, ok)
        pc   = _lname(c.get("postcode") or c.get("postal_code") or c.get("postalCode"))
        addr = _lname(c.get("address")  or c.get("addr")         or c.get("address_line"))
        return ("name_addr", nm, pc, addr) if (pc or addr) else ("name_only", nm)

    merged: Dict[tuple, Dict[str, Any]] = {}
    for c in (candidates_primary + candidates_fallback):
        k = _ckey(c)
        if k in merged:
            if _conf(c.get("confidence")) > _conf(merged[k].get("confidence")):
                merged[k] = c
        else:
            merged[k] = c

    merged_list = list(merged.values())

    # ----- robust charity detection -----
    def _is_charity(c: Dict[str, Any]) -> bool:
        reg = _lname(c.get("registry"))
        url = _norm(c.get("source_url"))
        has_charity_num = any(k in c for k in ("charity_number", "charityNumber"))
        num = _norm(c.get("company_number")).upper()
        return (
            "charity" in reg or
            "charitycommission" in url or
            "register-of-charities" in url or
            has_charity_num or
            num.startswith("CC-")
        )

    # ---- DEBUG PRINT of merged registries ----
    for cand in merged_list:
        print(f"[DEBUG] candidate '{cand.get('entity_name')}' registry={cand.get('registry')} confidence={cand.get('confidence')}")

    # ----- ranking with charity boost -----
    def _aug_score(c: Dict[str, Any]) -> float:
        base = _conf(c.get("confidence"))
        if looks_like_charity and _is_charity(c):
            base = min(0.999, base + 0.20)
        return base

    merged_list.sort(key=lambda c: (-_aug_score(c), _lname(c.get("entity_name"))))

    if top_n and top_n > 0:
        slice_list = merged_list[:top_n]
        if looks_like_charity and not any(_is_charity(x) for x in slice_list):
            best_charity = next((x for x in merged_list if _is_charity(x)), None)
            if best_charity:
                repl = next((i for i, x in reversed(list(enumerate(slice_list))) if not _is_charity(x)), None)
                if repl is not None:
                    slice_list[repl] = best_charity
                elif len(slice_list) < top_n:
                    slice_list.append(best_charity)
                else:
                    slice_list[-1] = best_charity
        merged_list = slice_list

    # ----- base row -----
    base = {
        "input_name": name,
        "status": resolved.get("status"),
        "match_type": resolved.get("match_type"),
        "entity_name": resolved.get("entity_name"),
        "company_number": resolved.get("company_number"),
        "charity_number": resolved.get("charity_number"),
        "company_status": resolved.get("company_status"),
        "confidence": resolved.get("confidence"),
        "reason": resolved.get("reason"),
        "registry": best_result_container.get("registry"),
        "search_url": best_result_container.get("search_url"),
        "source_url": resolved.get("source_url"),
        "retrieved_at": best_result_container.get("retrieved_at"),
        "resolved_registry": resolved.get("registry"),
    }

    # ----- candidate rows for UI -----
    cand_rows: List[Dict[str, Any]] = []
    if (resolved.get("status") or "").lower() != "auto":
        for cand in merged_list:
            reg_lbl = canonical_registry_name(cand.get("registry"))

            charity_num = (
                cand.get("charity_number")
                or cand.get("charityNumber")
                or cand.get("ccew_number")
                or cand.get("registered_charity_number")
                or cand.get("registration_number")
            )

            open_url = cand.get("source_url")
            if not open_url and reg_lbl == "Charity Commission":
                if charity_num and str(charity_num).isdigit():
                    open_url = f"https://register-of-charities.charitycommission.gov.uk/charity-details/?regId={int(charity_num)}&subId=0"
                else:
                    open_url = (
                        "https://register-of-charities.charitycommission.gov.uk/"
                        f"en/charity-search/-/results/page/1/delta/20/keywords/{quote_plus(name)}"
                    )

            conf_val = cand.get("confidence")

            if not reg_lbl and (open_url and ("charitycommission" in open_url or "register-of-charities" in open_url)):
                reg_lbl = "Charity Commission"

            cand_rows.append({
                "input_name": name,
                "candidate_entity_name": cand.get("entity_name"),
                "candidate_company_number": cand.get("company_number"),
                "charity_number": charity_num,
                "candidate_status": cand.get("company_status"),
                "candidate_address": cand.get("address"),
                "candidate_confidence": conf_val,
                "candidate_source_url": open_url,
                "retrieved_at": cand.get("retrieved_at"),
                "candidate_registry": reg_lbl,
            })

        # keep UI slice sorted by confidence desc (None last)
        def _sort_key(x):
            v = x.get("candidate_confidence")
            return (0, -float(v)) if isinstance(v, (int, float)) else (1, 0)
        cand_rows.sort(key=_sort_key)

    return base, cand_rows

def ensure_out_dir() -> str:
    day_folder = date.today().isoformat()
    out_path = os.path.join(RESULTS_BASE, day_folder)
    os.makedirs(out_path, exist_ok=True)
    return out_path

def bundle_to_xlsx(bundle: dict, xlsx_path: str):
    profile = bundle.get("profile") or {}
    # CH-style collections
    officers = (bundle.get("officers") or {}).get("items") or []
    pscs = (bundle.get("pscs") or {}).get("items") or []
    charges = (bundle.get("charges") or {}).get("items") or []
    # Charity-style collections
    trustees = bundle.get("trustees") or []
    filings = bundle.get("filings") or []
    sources = bundle.get("sources") or {}

    prof_rows = []
    for k, v in profile.items():
        if isinstance(v, (dict, list)):
            try:
                v = json.dumps(v, ensure_ascii=False)
            except Exception:
                v = str(v)
        prof_rows.append({"field": k, "value": v})
    df_profile = pd.DataFrame(prof_rows)

    df_officers = json_normalize(officers) if officers else pd.DataFrame()
    df_pscs     = json_normalize(pscs) if pscs else pd.DataFrame()
    df_charges  = json_normalize(charges) if charges else pd.DataFrame()
    df_trustees = pd.DataFrame(trustees) if trustees else pd.DataFrame()
    df_filings  = pd.DataFrame(filings) if filings else pd.DataFrame()
    df_sources  = pd.DataFrame([{"endpoint": k, "url": v} for k, v in sources.items()]) if sources else pd.DataFrame()

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as w:
        if not df_profile.empty:  df_profile.to_excel(w, index=False, sheet_name="Profile")
        if not df_officers.empty: df_officers.to_excel(w, index=False, sheet_name="Officers")
        if not df_pscs.empty:     df_pscs.to_excel(w, index=False, sheet_name="PSCs")
        if not df_charges.empty:  df_charges.to_excel(w, index=False, sheet_name="Charges")
        if not df_trustees.empty: df_trustees.to_excel(w, index=False, sheet_name="Trustees")
        if not df_filings.empty:  df_filings.to_excel(w, index=False, sheet_name="Filings")
        if not df_sources.empty:  df_sources.to_excel(w, index=False, sheet_name="Sources")

# ---------------- Enrichment worker ----------------

# ---------------- Companies House enrichment ----------------
def enrich_one(item_id: int):
    """Fetch CH bundle, write artifacts, and update status with simple lock retries."""
    import time
    start_time = time.time()
    
    try:
        attempts = 0
        while True:
            try:
                with db() as conn:
                    c = conn.cursor()
                    row = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
                    if not row:
                        return
                    status = (row["enrich_status"] or "").lower()
                    if status in ("running", "done", "skipped", "failed"):
                        return
                    company_number = row["company_number"]
                    if not company_number:
                        c.execute("UPDATE items SET enrich_status='skipped' WHERE id=?", (item_id,))
                        return
                    c.execute("UPDATE items SET enrich_status='running' WHERE id=?", (item_id,))
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempts < 5:
                    attempts += 1
                    time.sleep(0.2 * attempts)
                    continue
                raise

        out_dir = row["out_dir"] or ensure_out_dir()
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        bundle = get_company_bundle(company_number)

        # Check company type - companies limited by guarantee don't have share capital
        # so we skip CS01/IN01/AR01 extraction for them, but still use PSC data
        profile = bundle.get("profile", {})
        company_type = (profile.get("type") or "").lower()
        is_company_limited_by_guarantee = "guarant" in company_type
        
        # Initialize total_extracted at function scope
        total_extracted = 0
        shareholders_status = None
        
        if is_company_limited_by_guarantee:
            print(f"[enrich_one] Company type is '{profile.get('type')}' - skipping CS01/shareholder extraction (no share capital)")
            print(f"[enrich_one] Will use PSC register for ownership structure instead")
            bundle["cs01_filings"] = []
            bundle["cs01_documents"] = []
            bundle["total_shares"] = 0
            bundle["regular_shareholders"] = []
            bundle["parent_shareholders"] = []
            bundle["shareholders_status"] = "pending_psc_extraction"
            # total_extracted stays 0 - will trigger PSC logic below
        else:
            # Add CS01 filings and documents to the bundle
            try:
                cs01_filings = get_cs01_filings_for_company(company_number)
                bundle["cs01_filings"] = cs01_filings

                # Download CS01 PDFs if available
                cs01_documents = []
                for filing in cs01_filings:
                    doc_id = filing.get("document_id")
                    if doc_id:
                        try:
                            # Get document metadata
                            doc_metadata = get_document_metadata(doc_id)
                            filing["document_metadata"] = doc_metadata

                            # Download PDF content
                            pdf_content = download_cs01_pdf(doc_id)
                            pdf_filename = f"cs01_{company_number}_{filing['transaction_id']}_{ts}.pdf"
                            pdf_path = os.path.join(out_dir, pdf_filename)

                            with open(pdf_path, "wb") as f:
                                f.write(pdf_content)

                            filing["pdf_path"] = pdf_path
                            filing["pdf_filename"] = pdf_filename
                            cs01_documents.append(filing)

                        except Exception as e:
                            print(f"[enrich_one] Failed to download CS01 PDF {doc_id}: {e}")
                            continue

                    bundle["cs01_documents"] = cs01_documents

            except Exception as e:
                print(f"[enrich_one] Failed to get CS01 filings for {company_number}: {e}")
                bundle["cs01_filings"] = []
                bundle["cs01_documents"] = []

            # Extract shareholder information using intelligent CS01 -> AR01 fallback
            try:
                print(f"[enrich_one] Extracting shareholder information for {company_number}...")
                shareholder_result = extract_shareholders_for_company(company_number)
                bundle["regular_shareholders"] = shareholder_result.get("regular_shareholders", [])
                bundle["parent_shareholders"] = shareholder_result.get("parent_shareholders", [])
                bundle["total_shares"] = shareholder_result.get("total_shares", 0)
                bundle["shareholders_status"] = shareholder_result.get("extraction_status", "")
                shareholders_status = shareholder_result
                print(f"[enrich_one] Shareholder extraction status: {shareholder_result.get('extraction_status')}")
                total_extracted = len(shareholder_result.get("regular_shareholders", [])) + len(shareholder_result.get("parent_shareholders", []))
            
                # Build ownership tree if shareholders found
                if total_extracted > 0:
                    print(f"[enrich_one] Successfully extracted {total_extracted} shareholders")
                
                    # Build recursive corporate ownership tree
                    try:
                        print(f"[enrich_one] Building corporate ownership tree...")
                        company_name = bundle.get("profile", {}).get("company_name", "Unknown")
                    
                        # Combine regular and parent shareholders for tree building
                        all_shareholders = bundle.get("regular_shareholders", []) + bundle.get("parent_shareholders", [])
                    
                        print(f"[enrich_one] DEBUG: Passing {len(all_shareholders)} shareholders to tree builder")
                        print(f"[enrich_one] DEBUG: Shareholders: {[sh.get('name') for sh in all_shareholders]}")
                    
                        ownership_tree = build_ownership_tree(
                            company_number, 
                            company_name,
                            depth=0,
                            max_depth=50,  # Effectively unlimited - will recurse until end of ownership chain (circular refs prevented by visited set)
                            visited=None,
                            initial_shareholders=all_shareholders  # Pass PSC or filing-extracted shareholders
                        )
                    
                        print(f"[enrich_one] DEBUG: Tree returned with {len(ownership_tree.get('shareholders', []))} shareholders")
                    
                        bundle["ownership_tree"] = ownership_tree
                    
                        # Also create flattened view for easier display
                        flattened_chains = flatten_ownership_tree(ownership_tree)
                        bundle["ownership_chains"] = flattened_chains
                    
                        print(f"[enrich_one] ✅ Built ownership tree with {len(flattened_chains)} ultimate ownership chains")
                    except Exception as tree_error:
                        import traceback
                        print(f"[enrich_one] ⚠️  Failed to build ownership tree: {tree_error}")
                        print(f"[enrich_one] Traceback: {traceback.format_exc()}")
                        
                        # Fallback: Create a simple tree with just direct shareholders
                        print(f"[enrich_one] 🔧 Creating fallback tree with direct shareholders only...")
                        company_name = bundle.get("profile", {}).get("company_name", "Unknown")
                        bundle["ownership_tree"] = {
                            "company_number": company_number,
                            "company_name": company_name,
                            "shareholders": [
                                {
                                    "name": sh.get("name"),
                                    "shares_held": sh.get("shares_held"),
                                    "percentage": sh.get("percentage", 0),
                                    "share_class": sh.get("share_class", ""),
                                    "is_company": False,  # Assume individual for safety
                                    "children": []
                                }
                                for sh in all_shareholders
                            ]
                        }
                        bundle["ownership_chains"] = []
                        print(f"[enrich_one] ✅ Fallback tree created with {len(all_shareholders)} direct shareholders")
                    
            except Exception as e:
                print(f"[enrich_one] Failed to extract shareholders for {company_number}: {e}")
                bundle["regular_shareholders"] = []
                bundle["parent_shareholders"] = []
                bundle["total_shares"] = 0
                bundle["shareholders_status"] = "extraction_error"
                bundle["ownership_tree"] = None
                bundle["ownership_chains"] = []
                total_extracted = 0

        # For companies limited by guarantee OR if no shareholders found in filings, use PSC data
        if total_extracted == 0:
            print(f"[enrich_one] No shareholders found, checking PSC register...")
            try:
                psc_data = bundle.get("pscs", {})
                if psc_data and psc_data.get("items"):
                    print(f"[enrich_one] Found {len(psc_data['items'])} PSCs, converting to ownership structure...")
                    psc_shareholders = []
                    
                    for psc in psc_data['items']:
                        # Skip ceased PSCs
                        if psc.get("ceased_on"):
                            print(f"[enrich_one] ⏭️  Skipping ceased PSC: {psc.get('name')} (ceased: {psc.get('ceased_on')})")
                            continue
                            
                        psc_name = psc.get("name", "Unknown")
                        psc_kind = psc.get("kind", "")
                        natures = psc.get("natures_of_control", [])
                        
                        # Determine if it's a company or individual
                        is_company = "corporate" in psc_kind or "legal" in psc_kind
                        
                        # Extract ownership percentage from PSC natures
                        percentage = None
                        percentage_band = None
                        
                        # Look for ownership-of-shares first
                        if any("ownership-of-shares-75-to-100" in n for n in natures):
                            percentage_band = "75-100%"
                            percentage = 87.5
                        elif any("ownership-of-shares-50-to-75" in n for n in natures):
                            percentage_band = "50-75%"
                            percentage = 62.5
                        elif any("ownership-of-shares-25-to-50" in n for n in natures):
                            percentage_band = "25-50%"
                            percentage = 37.5
                        # Fallback to voting rights
                        elif any("voting-rights-75-to-100" in n for n in natures):
                            percentage_band = "75-100% (voting rights)"
                            percentage = 87.5
                        elif any("voting-rights-50-to-75" in n for n in natures):
                            percentage_band = "50-75% (voting rights)"
                            percentage = 62.5
                        elif any("voting-rights-25-to-50" in n for n in natures):
                            percentage_band = "25-50% (voting rights)"
                            percentage = 37.5
                        # For guarantee companies, also check for "right to appoint and remove directors"
                        elif any("right-to-appoint-and-remove-directors" in n for n in natures):
                            percentage_band = "Control (right to appoint directors)"
                            percentage = 100  # Control = 100% for tree purposes
                        else:
                            percentage_band = "Significant control"
                            percentage = 50  # Default for PSCs
                        
                        shareholder = {
                            "name": psc_name,
                            "shares_held": "N/A (PSC Register)" if is_company_limited_by_guarantee else "Unknown (from PSC)",
                            "percentage": percentage,
                            "percentage_band": percentage_band,
                            "share_class": "N/A" if is_company_limited_by_guarantee else "Ordinary",
                            "source": "PSC Register",
                            "psc_natures": natures
                        }
                        
                        psc_shareholders.append(shareholder)
                    
                    # Separate into regular and parent companies
                    from shareholder_information import identify_parent_companies
                    regular, parent = identify_parent_companies(psc_shareholders)
                    
                    bundle["regular_shareholders"] = regular
                    bundle["parent_shareholders"] = parent
                    bundle["shareholders_status"] = "found_via_psc" if not is_company_limited_by_guarantee else "company_limited_by_guarantee_used_psc"
                    
                    total_extracted = len(psc_shareholders)
                    print(f"[enrich_one] ✅ Converted {total_extracted} PSCs to ownership structure")
                    
                    # Build ownership tree from PSC data
                    if total_extracted > 0:
                        try:
                            print(f"[enrich_one] Building ownership tree from PSC data...")
                            company_name = bundle.get("profile", {}).get("company_name", "Unknown")
                            
                            all_shareholders = bundle.get("regular_shareholders", []) + bundle.get("parent_shareholders", [])
                            
                            # build_ownership_tree already imported at module level (line 37)
                            ownership_tree = build_ownership_tree(
                                company_number, 
                                company_name,
                                depth=0,
                                max_depth=50,
                                visited=None,
                                initial_shareholders=all_shareholders
                            )
                            
                            bundle["ownership_tree"] = ownership_tree
                            
                            flattened_chains = flatten_ownership_tree(ownership_tree)
                            bundle["ownership_chains"] = flattened_chains
                            
                            print(f"[enrich_one] ✅ Built ownership tree with {len(flattened_chains)} ownership chains")
                        except Exception as tree_error:
                            import traceback
                            print(f"[enrich_one] ⚠️  Failed to build ownership tree from PSC: {tree_error}")
                            print(f"[enrich_one] Traceback: {traceback.format_exc()}")
                            
                            # Fallback: Create a simple tree with PSC data
                            print(f"[enrich_one] 🔧 Creating fallback tree from PSC data...")
                            company_name = bundle.get("profile", {}).get("company_name", "Unknown")
                            bundle["ownership_tree"] = {
                                "company_number": company_number,
                                "company_name": company_name,
                                "shareholders": [
                                    {
                                        "name": sh.get("name"),
                                        "shares_held": sh.get("shares_held"),
                                        "percentage": sh.get("percentage", 0),
                                        "share_class": sh.get("share_class", ""),
                                        "is_company": "corporate" in sh.get("source", "").lower() or "ltd" in sh.get("name", "").lower(),
                                        "children": []
                                    }
                                    for sh in all_shareholders
                                ]
                            }
                            bundle["ownership_chains"] = []
                            print(f"[enrich_one] ✅ Fallback PSC tree created with {len(all_shareholders)} controllers")
            except Exception as psc_error:
                print(f"[enrich_one] ⚠️  Failed to convert PSC data: {psc_error}")
                # Set defaults if PSC processing fails
                if not bundle.get("regular_shareholders"):
                    bundle["regular_shareholders"] = []
                if not bundle.get("parent_shareholders"):
                    bundle["parent_shareholders"] = []
                if not bundle.get("shareholders_status"):
                    bundle["shareholders_status"] = "no_data_found"

        json_path = os.path.join(out_dir, f"enriched_{company_number}_{ts}.json")
        xlsx_path = os.path.join(out_dir, f"enriched_{company_number}_{ts}.xlsx")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, ensure_ascii=False, indent=2)
        bundle_to_xlsx(bundle, xlsx_path)

        # Calculate enrichment metrics
        enrichment_duration = time.time() - start_time
        
        # Calculate tree depth and entity count
        def calculate_tree_metrics(node, current_depth=0):
            if not node:
                return (0, 0)
            max_depth = current_depth
            entity_count = 1 if node.get('company_number') or node.get('is_company') else 0
            
            for shareholder in node.get('shareholders', []):
                child_depth, child_count = calculate_tree_metrics(shareholder, current_depth + 1)
                max_depth = max(max_depth, child_depth)
                entity_count += child_count
                
            for child in node.get('children', []):
                child_depth, child_count = calculate_tree_metrics(child, current_depth + 1)
                max_depth = max(max_depth, child_depth)
                entity_count += child_count
                
            return (max_depth, entity_count)
        
        tree_depth, total_entities = calculate_tree_metrics(bundle.get('ownership_tree'))
        
        enrichment_metadata = {
            "enrichment_duration_seconds": round(enrichment_duration, 2),
            "tree_depth": tree_depth,
            "total_entities_in_tree": total_entities,
            "completed_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        print(f"[enrich_one] Completed in {enrichment_duration:.2f}s - Tree depth: {tree_depth}, Entities: {total_entities}")
        
        # Serialize shareholders data for database storage (preserve structure)
        shareholders_data = {
            "regular_shareholders": bundle.get("regular_shareholders", []),
            "parent_shareholders": bundle.get("parent_shareholders", []),
            "total_shares": bundle.get("total_shares", 0),
            "enrichment_metadata": enrichment_metadata
        }
        shareholders_json = json.dumps(shareholders_data, ensure_ascii=False)
        shareholders_status = bundle.get("shareholders_status", "")
        
        # Serialize ownership tree for database storage (to survive Railway redeployments)
        ownership_tree_json = json.dumps(bundle.get("ownership_tree"), ensure_ascii=False) if bundle.get("ownership_tree") else None

        with db() as conn:
            conn.execute(
                "UPDATE items SET enrich_status='done', enrich_json_path=?, enrich_xlsx_path=?, shareholders_json=?, shareholders_status=?, ownership_tree_json=? WHERE id=?",
                (json_path, xlsx_path, shareholders_json, shareholders_status, ownership_tree_json, item_id),
            )

    except Exception as e:
        try:
            with db() as conn:
                conn.execute("UPDATE items SET enrich_status='failed' WHERE id=?", (item_id,))
        except Exception:
            pass
        print(f"[enrich_one] failed for item {item_id}: {e}")

def enqueue_enrich(item_id: int):
    """
    Enqueue enrichment task using worker pool to prevent memory exhaustion.
    Uses ThreadPoolExecutor with MAX_CONCURRENT_WORKERS limit.
    """
    enrichment_executor.submit(enrich_one, item_id)

def _canon_person_name(s: str) -> str:
    if not s:
        return ""
    s = str(s).lower()
    # remove punctuation, extra spaces
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _token_overlap_score(a: str, b: str) -> float:
    """Simple symmetric Jaccard-ish token overlap for names."""
    ta = {t for t in _canon_person_name(a).split() if t}
    tb = {t for t in _canon_person_name(b).split() if t}
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union

def _best_officer_for_name(officers: list, name: str):
    """Return (officer_dict, score) for best match on 'name'."""
    best = (None, 0.0)
    nm = (name or "").strip()
    if not nm:
        return best
    for o in officers or []:
        oname = o.get("name") or ""
        score = 0.0
        if _canon_person_name(oname) == _canon_person_name(nm):
            score = 1.0
        else:
            score = _token_overlap_score(oname, nm)
        if score > best[1]:
            best = (o, score)
    return best

def _addr_to_str(addr) -> str:
    if not addr:
        return ""
    if isinstance(addr, dict):
        parts = []
        for k in ("address_line_1","address_line_2","premises","locality","region","postal_code","country"):
            v = addr.get(k)
            if v: parts.append(str(v))
        return ", ".join(parts)
    return str(addr)

# field map from your LP schema -> officer fields
_LP_TO_OFFICER_FIELD = {
    "full_name":        ("name",),
    "role":             ("officer_role",),
    "dob":              ("date_of_birth","dob","dateOfBirth"),
    "nationality":      ("nationality",),
    "country_of_residence": ("country_of_residence","countryOfResidence"),
    "correspondence_address": ("address",),  # render to string
}

_LP_HEADER_RE = re.compile(r"^linked_party_(?P<field>.+)_(?P<idx>\d+)$", re.IGNORECASE)

def _derive_linked_party_value(header: str, uploaded_map: dict, officers: list):
    """
    If header looks like 'Linked_party_<field>_<n>', find the best officer for the
    uploaded Linked_party_full_name_<n> and return the officer field value.
    """
    m = _LP_HEADER_RE.match(header)
    if not m:
        return None
    field = m.group("field").lower()
    idx   = m.group("idx")

    # Uploaded name for that slot drives the match
    up_name_key = f"Linked_party_full_name_{idx}"
    up_name = uploaded_map.get(_norm_key_for_match(up_name_key))  # normalised lookup

    if not up_name:
        return None

    officer, score = _best_officer_for_name(officers, up_name)
    if not officer or score < 0.4:  # threshold; tune if needed
        return None

    # map requested field to officer fields
    for lp_field, officer_keys in _LP_TO_OFFICER_FIELD.items():
        if field == lp_field:
            for k in officer_keys:
                val = officer.get(k)
                if val:
                    if lp_field == "correspondence_address":
                        return _addr_to_str(val)
                    return val
            return None

    # Unknown LP field → try a generic pull by best guess
    val = officer.get(field)
    if val:
        return _addr_to_str(val) if isinstance(val, dict) else val
    return None

# ---------------- Charity Commission enrichment (trustees etc.) ----------------
def enrich_charity_one(item_id: int):
    try:
        with db() as conn:
            row = conn.execute("""
                SELECT id, entity_name, charity_number, company_number, resolved_registry, out_dir, enrich_status
                FROM items WHERE id=?
            """, (item_id,)).fetchone()
            if not row:
                return
            status = (row["enrich_status"] or "").lower()
            if status in ("running", "done", "failed", "skipped"):
                return

            reg = canonical_registry_name(row["resolved_registry"])
            chnum = (row["charity_number"] or "").strip()

            # Fallback: some old rows might have the CCEW number stored in company_number
            if not chnum and reg == "Charity Commission":
                maybe = (row["company_number"] or "").strip()
                if maybe.isdigit():
                    chnum = maybe

            if not chnum:
                conn.execute("UPDATE items SET enrich_status='failed' WHERE id=?", (item_id,))
                return

            conn.execute("UPDATE items SET enrich_status='running' WHERE id=?", (item_id,))

        out_dir = row["out_dir"] or ensure_out_dir()
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

        bundle = get_charity_bundle_cc(chnum)

        json_path = os.path.join(out_dir, f"enriched_CC_{chnum}_{ts}.json")
        xlsx_path = os.path.join(out_dir, f"enriched_CC_{chnum}_{ts}.xlsx")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, ensure_ascii=False, indent=2)
        bundle_to_xlsx(bundle, xlsx_path)

        with db() as conn:
            conn.execute(
                "UPDATE items SET enrich_status='done', enrich_json_path=?, enrich_xlsx_path=? WHERE id=?",
                (json_path, xlsx_path, item_id),
            )
    except Exception as e:
        try:
            with db() as conn:
                conn.execute("UPDATE items SET enrich_status='failed' WHERE id=?", (item_id,))
        except Exception:
            pass
        print(f"[enrich_charity_one] failed for item {item_id}: {e}")

def enqueue_enrich_charity(item_id: int):
    """
    Enqueue charity enrichment task using worker pool to prevent memory exhaustion.
    Uses ThreadPoolExecutor with MAX_CONCURRENT_WORKERS limit.
    """
    enrichment_executor.submit(enrich_charity_one, item_id)

# ---------------- Admin: Users CRUD (login-free) ----------------
@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request):
    require_admin(request)
    with db() as conn:
        users = conn.execute("SELECT id, email, full_name, is_active, created_at FROM users ORDER BY created_at DESC").fetchall()
        roles = conn.execute("SELECT id, name FROM roles ORDER BY name").fetchall()
        user_roles = {}
        for u in users:
            names = [r["name"] for r in conn.execute("""
                SELECT r.name FROM roles r
                JOIN user_roles ur ON ur.role_id = r.id
                WHERE ur.user_id = ?
            """, (u["id"],))]
            user_roles[u["id"]] = names
    return templates.TemplateResponse("admin_users.html", {
        "request": request,
        "users": users,
        "all_roles": roles,
        "user_roles": user_roles
    })

@app.get("/admin/users/new", response_class=HTMLResponse)
def admin_user_new(request: Request):
    require_admin(request)
    with db() as conn:
        roles = conn.execute("SELECT id, name FROM roles ORDER BY name").fetchall()
    return templates.TemplateResponse("admin_user_edit.html", {
        "request": request,
        "user": None,
        "all_roles": roles,
        "user_role_ids": []
    })

@app.post("/admin/users/new", response_class=HTMLResponse)
def admin_user_create(
    request: Request,
    email: str = Form(...),
    full_name: Optional[str] = Form(None),
    password: str = Form(...),
    roles: Optional[List[int]] = Form(None)
):
    require_admin(request)
    if roles is None: roles = []
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (email, full_name, password_hash, is_active, created_at)
            VALUES (?,?,?,?,?)
        """, (email.lower().strip(), (full_name or "").strip(), hash_password(password), 1, datetime.utcnow().isoformat() + "Z"))
        user_id = cur.lastrowid
        for rid in roles:
            cur.execute("INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?,?)", (user_id, int(rid)))
    return RedirectResponse(url="/admin/users", status_code=HTTP_302_FOUND)

@app.get("/admin/users/{user_id}/edit", response_class=HTMLResponse)
def admin_user_edit(request: Request, user_id: int):
    require_admin(request)
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return RedirectResponse(url="/admin/users", status_code=HTTP_302_FOUND)
        roles = conn.execute("SELECT id, name FROM roles ORDER BY name").fetchall()
        my_roles = [r["id"] for r in conn.execute("""
            SELECT r.id FROM roles r
            JOIN user_roles ur ON ur.role_id = r.id
            WHERE ur.user_id = ?
        """, (user_id,))]
    return templates.TemplateResponse("admin_user_edit.html", {
        "request": request,
        "user": user,
        "all_roles": roles,
        "user_role_ids": my_roles
    })

@app.post("/admin/users/{user_id}/edit", response_class=HTMLResponse)
def admin_user_update(
    request: Request,
    user_id: int,
    email: str = Form(...),
    full_name: Optional[str] = Form(None),
    new_password: Optional[str] = Form(None),
    roles: Optional[List[int]] = Form(None),
    is_active: Optional[int] = Form(1)
):
    require_admin(request)
    if roles is None: roles = []
    with db() as conn:
        cur = conn.cursor()
        if new_password:
            cur.execute("""
                UPDATE users SET email=?, full_name=?, password_hash=?, is_active=?
                WHERE id=?
            """, (email.lower().strip(), (full_name or "").strip(), hash_password(new_password), int(bool(is_active)), user_id))
        else:
            cur.execute("""
                UPDATE users SET email=?, full_name=?, is_active=?
                WHERE id=?
            """, (email.lower().strip(), (full_name or "").strip(), int(bool(is_active)), user_id))
        cur.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
        for rid in roles:
            cur.execute("INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?,?)", (user_id, int(rid)))
    return RedirectResponse(url="/admin/users", status_code=HTTP_302_FOUND)

# ---------------- Upload, Queues & Items ----------------
@app.get("/", response_class=HTMLResponse)
def upload_page(request: Request):
    return templates.TemplateResponse("batchupload.html", {"request": request})

@app.post("/batch-upload", response_class=HTMLResponse)
async def batch_upload(request: Request, file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename)[-1].lower()
    if suffix not in [".csv", ".xlsx", ".xls"]:
        return templates.TemplateResponse(
            "batchupload.html",
            {"request": request, "error": "Please upload CSV/XLSX."}
        )

    # save upload to a temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        contents = await file.read()
        tmp.write(contents)
        tmp_path = tmp.name

    out_dir = ensure_out_dir()
    run_id = None
    try:
        # create run
        with db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO runs (created_at, upload_filename) VALUES (?,?)",
                (datetime.utcnow().isoformat() + "Z", file.filename),
            )
            run_id = cur.lastrowid

        # read uploaded file
        df = read_inputs(tmp_path)

        # build a set of existing name hashes for dedupe (across history)
        with db() as conn:
            existing = {
                r["name_hash"]
                for r in conn.execute(
                    "SELECT name_hash FROM items WHERE name_hash IS NOT NULL"
                ).fetchall()
            }

        seen_hashes = set(existing)

        results_all: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            input_name = str(row.get("name") or "").strip()
            if not input_name:
                continue
            nh = name_to_hash(input_name)
            if nh in seen_hashes:
                continue
            seen_hashes.add(nh)

            base, candidate_rows = safe_resolve(
                input_name,
                top_n=3,
                hints={
                    "entity_type": row.get("entity_type"),
                    "postcode": row.get("postcode"),
                    "incorporation_year": row.get("incorporation_year"),
                },
            )

            # resolve registry (normalised) + derive charity_number if applicable
            resolved_reg = canonical_registry_name(base.get("resolved_registry"))
            charity_number = None
            if resolved_reg == "Charity Commission":
                charity_number = _extract_charity_number(base, candidate_rows)

            # client convenience fields (unchanged)
            client_ref = (row.get("client_ref") or None)
            client_address = (row.get("client_address") or None)
            client_city = (row.get("client_address_city") or None)
            client_postcode = (row.get("client_address_postcode") or None)
            client_country = (row.get("client_address_country") or None)
            client_notes = (row.get("client_notes") or None)

            lp_raw = row.get("client_linked_parties")
            client_lp_json = None
            if pd.notna(lp_raw) and str(lp_raw).strip():
                txt = str(lp_raw).strip()
                try:
                    parsed = json.loads(txt)
                    client_lp_json = json.dumps(parsed, ensure_ascii=False)
                except Exception:
                    parts = [p.strip() for p in txt.replace("\n", ";").split(";")]
                    parts = [p for p in parts if p]
                    client_lp_json = json.dumps(parts, ensure_ascii=False) if parts else None

            results_all.append({
                "base": base,
                "candidates": candidate_rows,
                "name_hash": nh,
                "row": row,
                "resolved_registry": resolved_reg,
                "charity_number": charity_number,
                "client": {
                    "ref": client_ref,
                    "address": client_address,
                    "city": client_city,
                    "postcode": client_postcode,
                    "country": client_country,
                    "linked_parties_json": client_lp_json,
                    "notes": client_notes,
                },
            })

        # ---- persist (explicit transaction for speed) ----
        to_enqueue_ch: List[int] = []
        to_enqueue_cc: List[int] = []

        with db() as conn:
            cur = conn.cursor()
            cur.execute("BEGIN")
            try:
                for pack in results_all:
                    base = pack["base"]
                    candidate_rows = pack["candidates"]
                    pipeline_status = base.get("status") or "error"
                    candidates_json = json.dumps(candidate_rows, ensure_ascii=False) if candidate_rows else None

                    # exact client-upload schema (526)
                    schema_fields = extract_all_schema_fields_from_row(pack["row"])
                    schema_cols_sql = ",".join(_q_ident(h) for h in ALL_SCHEMA_FIELDS)
                    schema_vals_tuple = tuple(schema_fields[h] for h in ALL_SCHEMA_FIELDS)

                    # core columns now include charity_number + resolved_registry
                    core_columns = (
                        "run_id,input_name,name_hash,pipeline_status,match_type,"
                        "entity_name,company_number,company_status,charity_number,resolved_registry,"
                        "confidence,reason,search_url,source_url,retrieved_at,"
                        "candidates_json,out_dir,created_at,"
                        "client_ref,client_address,client_address_city,"
                        "client_address_postcode,client_address_country,"
                        "client_linked_parties,client_notes"
                    )

                    core_values = (
                        run_id, base.get("input_name"), pack["name_hash"], pipeline_status,
                        base.get("match_type"), base.get("entity_name"),
                        base.get("company_number"), base.get("company_status"),
                        (pack["charity_number"] or None), pack["resolved_registry"],
                        base.get("confidence"), base.get("reason"),
                        base.get("search_url"), base.get("source_url"),
                        base.get("retrieved_at"),
                        candidates_json, out_dir, datetime.utcnow().isoformat() + "Z",
                        pack["client"]["ref"], pack["client"]["address"],
                        pack["client"]["city"], pack["client"]["postcode"],
                        pack["client"]["country"], pack["client"]["linked_parties_json"],
                        pack["client"]["notes"],
                    )

                    columns_sql = f"{core_columns},{schema_cols_sql}"
                    values_tuple = core_values + schema_vals_tuple
                    placeholders = ",".join(["?"] * len(values_tuple))

                    cur.execute(
                        f"INSERT INTO items ({columns_sql}) VALUES ({placeholders})",
                        values_tuple,
                    )
                    item_id = cur.lastrowid

                    # queue enrichment for either registry
                    if pipeline_status == "auto":
                        if base.get("company_number"):
                            cur.execute("UPDATE items SET enrich_status='queued' WHERE id=?", (item_id,))
                            to_enqueue_ch.append(item_id)
                        elif (pack["resolved_registry"] == "Charity Commission") and pack["charity_number"]:
                            cur.execute("UPDATE items SET enrich_status='queued' WHERE id=?", (item_id,))
                            to_enqueue_cc.append(item_id)
                        else:
                            cur.execute("UPDATE items SET enrich_status='skipped' WHERE id=?", (item_id,))

            except Exception:
                conn.execute("ROLLBACK")
                raise
            # commit by context manager

        # fire workers after commit
        for iid in to_enqueue_ch:
            enqueue_enrich(iid)
        for iid in to_enqueue_cc:
            enqueue_enrich_charity(iid)

    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    return RedirectResponse(url=f"/queue/manual?run_id={run_id}", status_code=303)

# API endpoint for JSON response (for Cloudflare frontend)
@app.post("/api/batch/upload")
async def api_batch_upload(file: UploadFile = File(...)):
    """JSON API endpoint for batch upload - used by Cloudflare frontend"""
    suffix = os.path.splitext(file.filename)[-1].lower()
    if suffix not in [".csv", ".xlsx", ".xls"]:
        return JSONResponse(
            content={"error": "Invalid file format. Please upload CSV, XLSX, or XLS file."},
            status_code=400
        )

    # save upload to a temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        contents = await file.read()
        tmp.write(contents)
        tmp_path = tmp.name

    out_dir = ensure_out_dir()
    run_id = None
    try:
        # create run
        with db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO runs (created_at, upload_filename) VALUES (?,?)",
                (datetime.utcnow().isoformat() + "Z", file.filename),
            )
            run_id = cur.lastrowid

        # read uploaded file
        df = read_inputs(tmp_path)

        # build a set of existing name hashes for dedupe (across history)
        with db() as conn:
            existing = {
                r["name_hash"]
                for r in conn.execute(
                    "SELECT name_hash FROM items WHERE name_hash IS NOT NULL"
                ).fetchall()
            }

        seen_hashes = set(existing)

        results_all: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            input_name = str(row.get("name") or "").strip()
            if not input_name:
                continue
            nh = name_to_hash(input_name)
            if nh in seen_hashes:
                continue
            seen_hashes.add(nh)

            base, candidate_rows = safe_resolve(
                input_name,
                top_n=3,
                hints={
                    "entity_type": row.get("entity_type"),
                    "postcode": row.get("postcode"),
                    "incorporation_year": row.get("incorporation_year"),
                },
            )

            # resolve registry (normalised) + derive charity_number if applicable
            resolved_reg = canonical_registry_name(base.get("resolved_registry"))
            charity_number = None
            if resolved_reg == "Charity Commission":
                charity_number = _extract_charity_number(base, candidate_rows)

            # client convenience fields (unchanged)
            client_ref = (row.get("client_ref") or None)
            client_address = (row.get("client_address") or None)
            client_city = (row.get("client_address_city") or None)
            client_postcode = (row.get("client_address_postcode") or None)
            client_country = (row.get("client_address_country") or None)
            client_notes = (row.get("client_notes") or None)

            lp_raw = row.get("client_linked_parties")
            client_lp_json = None
            if pd.notna(lp_raw) and str(lp_raw).strip():
                txt = str(lp_raw).strip()
                try:
                    parsed = json.loads(txt)
                    client_lp_json = json.dumps(parsed, ensure_ascii=False)
                except Exception:
                    parts = [p.strip() for p in txt.replace("\n", ";").split(";")]
                    parts = [p for p in parts if p]
                    client_lp_json = json.dumps(parts, ensure_ascii=False) if parts else None

            results_all.append({
                "base": base,
                "candidates": candidate_rows,
                "name_hash": nh,
                "row": row,
                "resolved_registry": resolved_reg,
                "charity_number": charity_number,
                "client": {
                    "ref": client_ref,
                    "address": client_address,
                    "city": client_city,
                    "postcode": client_postcode,
                    "country": client_country,
                    "linked_parties_json": client_lp_json,
                    "notes": client_notes,
                },
            })

        # ---- persist (explicit transaction for speed) ----
        to_enqueue_ch: List[int] = []
        to_enqueue_cc: List[int] = []

        with db() as conn:
            cur = conn.cursor()
            cur.execute("BEGIN")
            try:
                for pack in results_all:
                    base = pack["base"]
                    candidate_rows = pack["candidates"]
                    pipeline_status = base.get("status") or "error"
                    candidates_json = json.dumps(candidate_rows, ensure_ascii=False) if candidate_rows else None

                    # exact client-upload schema (526)
                    schema_fields = extract_all_schema_fields_from_row(pack["row"])
                    schema_cols_sql = ",".join(_q_ident(h) for h in ALL_SCHEMA_FIELDS)
                    schema_vals_tuple = tuple(schema_fields[h] for h in ALL_SCHEMA_FIELDS)

                    # core columns now include charity_number + resolved_registry
                    core_columns = (
                        "run_id,input_name,name_hash,pipeline_status,match_type,"
                        "entity_name,company_number,company_status,charity_number,resolved_registry,"
                        "confidence,reason,search_url,source_url,retrieved_at,"
                        "candidates_json,out_dir,created_at,"
                        "client_ref,client_address,client_address_city,"
                        "client_address_postcode,client_address_country,"
                        "client_linked_parties,client_notes"
                    )

                    core_values = (
                        run_id, base.get("input_name"), pack["name_hash"], pipeline_status,
                        base.get("match_type"), base.get("entity_name"),
                        base.get("company_number"), base.get("company_status"),
                        (pack["charity_number"] or None), pack["resolved_registry"],
                        base.get("confidence"), base.get("reason"),
                        base.get("search_url"), base.get("source_url"),
                        base.get("retrieved_at"),
                        candidates_json, out_dir, datetime.utcnow().isoformat() + "Z",
                        pack["client"]["ref"], pack["client"]["address"],
                        pack["client"]["city"], pack["client"]["postcode"],
                        pack["client"]["country"], pack["client"]["linked_parties_json"],
                        pack["client"]["notes"],
                    )

                    columns_sql = f"{core_columns},{schema_cols_sql}"
                    values_tuple = core_values + schema_vals_tuple
                    placeholders = ",".join(["?"] * len(values_tuple))

                    cur.execute(
                        f"INSERT INTO items ({columns_sql}) VALUES ({placeholders})",
                        values_tuple,
                    )
                    item_id = cur.lastrowid

                    # queue enrichment for either registry
                    if pipeline_status == "auto":
                        if base.get("company_number"):
                            cur.execute("UPDATE items SET enrich_status='queued' WHERE id=?", (item_id,))
                            to_enqueue_ch.append(item_id)
                        elif (pack["resolved_registry"] == "Charity Commission") and pack["charity_number"]:
                            cur.execute("UPDATE items SET enrich_status='queued' WHERE id=?", (item_id,))
                            to_enqueue_cc.append(item_id)
                        else:
                            cur.execute("UPDATE items SET enrich_status='skipped' WHERE id=?", (item_id,))

            except Exception:
                conn.execute("ROLLBACK")
                raise
            # commit by context manager

        # fire workers after commit
        for iid in to_enqueue_ch:
            enqueue_enrich(iid)
        for iid in to_enqueue_cc:
            enqueue_enrich_charity(iid)

        # Get stats for response
        with db() as conn:
            stats = conn.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN pipeline_status='auto' THEN 1 ELSE 0 END) as auto_matched,
                    SUM(CASE WHEN pipeline_status='manual' THEN 1 ELSE 0 END) as manual_review
                FROM items WHERE run_id=?
            """, (run_id,)).fetchone()

        return JSONResponse(content={
            "success": True,
            "run_id": run_id,
            "filename": file.filename,
            "total_entities": stats["total"],
            "auto_matched": stats["auto_matched"],
            "manual_review": stats["manual_review"],
            "message": f"Successfully processed {stats['total']} entities"
        })

    except Exception as e:
        return JSONResponse(
            content={"error": f"Upload processing failed: {str(e)}"},
            status_code=500
        )
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

@app.get("/api/batches")
async def api_get_batches():
    """Get all batch runs with statistics"""
    try:
        with db() as conn:
            runs = conn.execute("""
                SELECT 
                    r.id,
                    r.created_at,
                    r.upload_filename,
                    COUNT(i.id) as total_entities,
                    SUM(CASE WHEN i.pipeline_status='auto' THEN 1 ELSE 0 END) as auto_matched,
                    SUM(CASE WHEN i.pipeline_status='manual' THEN 1 ELSE 0 END) as manual_review,
                    SUM(CASE WHEN i.pipeline_status='error' THEN 1 ELSE 0 END) as errors,
                    SUM(CASE WHEN i.enrich_status='done' THEN 1 ELSE 0 END) as enriched,
                    SUM(CASE WHEN i.enrich_status='queued' OR i.enrich_status='pending' THEN 1 ELSE 0 END) as in_progress
                FROM runs r
                LEFT JOIN items i ON r.id = i.run_id
                GROUP BY r.id
                ORDER BY r.created_at DESC
                LIMIT 50
            """).fetchall()
            
            batches = []
            for run in runs:
                batches.append({
                    "id": run["id"],
                    "created_at": run["created_at"],
                    "filename": run["upload_filename"],
                    "stats": {
                        "total": run["total_entities"] or 0,
                        "auto_matched": run["auto_matched"] or 0,
                        "manual_review": run["manual_review"] or 0,
                        "errors": run["errors"] or 0,
                        "enriched": run["enriched"] or 0,
                        "in_progress": run["in_progress"] or 0
                    }
                })
            
            return JSONResponse(content={"batches": batches})
    except Exception as e:
        return JSONResponse(
            content={"error": f"Failed to fetch batches: {str(e)}"},
            status_code=500
        )

@app.get("/api/batch/{batch_id}/status")
async def api_get_batch_status(batch_id: int):
    """Get status for a specific batch"""
    try:
        with db() as conn:
            run = conn.execute("""
                SELECT 
                    r.id,
                    r.created_at,
                    r.upload_filename,
                    COUNT(i.id) as total_entities,
                    SUM(CASE WHEN i.pipeline_status='auto' THEN 1 ELSE 0 END) as auto_matched,
                    SUM(CASE WHEN i.pipeline_status='manual' THEN 1 ELSE 0 END) as manual_review,
                    SUM(CASE WHEN i.pipeline_status='error' THEN 1 ELSE 0 END) as errors,
                    SUM(CASE WHEN i.enrich_status='done' THEN 1 ELSE 0 END) as enriched,
                    SUM(CASE WHEN i.enrich_status='queued' OR i.enrich_status='pending' THEN 1 ELSE 0 END) as in_progress
                FROM runs r
                LEFT JOIN items i ON r.id = i.run_id
                WHERE r.id = ?
                GROUP BY r.id
            """, (batch_id,)).fetchone()
            
            if not run:
                return JSONResponse(
                    content={"error": "Batch not found"},
                    status_code=404
                )
            
            return JSONResponse(content={
                "id": run["id"],
                "created_at": run["created_at"],
                "filename": run["upload_filename"],
                "stats": {
                    "total": run["total_entities"] or 0,
                    "auto_matched": run["auto_matched"] or 0,
                    "manual_review": run["manual_review"] or 0,
                    "errors": run["errors"] or 0,
                    "enriched": run["enriched"] or 0,
                    "in_progress": run["in_progress"] or 0
                }
            })
    except Exception as e:
        return JSONResponse(
            content={"error": f"Failed to fetch batch status: {str(e)}"},
            status_code=500
        )

def build_screening_list(bundle: dict, shareholders: list, item: dict) -> dict:
    """
    Build KYC/AML screening list based on regulatory requirements.
    Returns categorized list of persons/entities requiring screening.
    
    Based on UK AML/KYC requirements:
    - Directors, Company Secretary, PSCs
    - Direct shareholders ≥10%
    - Corporate shareholders ≥10% (screen entire entity)
    - Parent companies, grandparent companies
    - Ultimate parent companies
    - UBOs (individuals ≥10% indirect ownership)
    - Trust-related parties (settlors, trustees, protectors, beneficiaries)
    - Guarantee company members
    - Associated persons (authorized signatories, introducers, SMF holders)
    - Subsidiaries (≥10% ownership or effective control)
    """
    screening = {
        "entity": [],
        "governance_and_control": [],
        "ownership_chain": [],
        "ubos": [],
        "trusts": [],
        "guarantee_companies": [],
        "associated_persons": [],
        "subsidiaries": []
    }
    
    profile = bundle.get("profile", {})
    officers_data = bundle.get("officers", {})
    pscs_data = bundle.get("pscs", {})
    ownership_tree = bundle.get("ownership_tree", {})
    
    # 1. ENTITY - The legal entity itself
    screening["entity"].append({
        "name": item.get("input_name") or profile.get("company_name", "Unknown"),
        "type": "Company/Charity/Association/Trust",
        "company_number": item.get("company_number"),
        "charity_number": item.get("charity_number"),
        "status": profile.get("company_status", "Unknown"),
        "category": "Legal Entity"
    })
    
    # 2. GOVERNANCE & CONTROL
    # Directors
    officers_items = officers_data.get("items", [])
    for officer in officers_items:
        if officer.get("officer_role", "").lower() in ["director", "corporate-director", "shadow-director"]:
            screening["governance_and_control"].append({
                "name": officer.get("name", "Unknown"),
                "role": officer.get("officer_role", "Director"),
                "appointed_on": officer.get("appointed_on"),
                "resigned_on": officer.get("resigned_on"),
                "nationality": officer.get("nationality"),
                "dob": f"{officer.get('date_of_birth', {}).get('month')}/{officer.get('date_of_birth', {}).get('year')}" if officer.get("date_of_birth") else None,
                "category": "Directors",
                "description": "All current directors including shadow directors"
            })
    
    # Company Secretary
    for officer in officers_items:
        if officer.get("officer_role", "").lower() in ["secretary", "corporate-secretary"]:
            screening["governance_and_control"].append({
                "name": officer.get("name", "Unknown"),
                "role": officer.get("officer_role", "Secretary"),
                "appointed_on": officer.get("appointed_on"),
                "category": "Company Secretary",
                "description": "If appointed"
            })
    
    # PSCs
    psc_items = pscs_data.get("items", [])
    for psc in psc_items:
        if not psc.get("ceased", False):
            natures = psc.get("natures_of_control", [])
            screening["governance_and_control"].append({
                "name": psc.get("name", "Unknown"),
                "role": "Person with Significant Control",
                "kind": psc.get("kind", "Unknown"),
                "natures_of_control": natures,
                "notified_on": psc.get("notified_on"),
                "category": "PSCs",
                "description": "Anyone meeting UK PSC criteria (>10% shares/votes or significant influence)"
            })
    
    # 3. OWNERSHIP CHAIN - Extract from ownership tree
    # First, add the target company's officers and PSCs
    target_company_name = item.get("input_name") or profile.get("company_name", "Unknown")
    target_company_number = item.get("company_number")
    
    # Add target company itself
    if target_company_number:
        screening["ownership_chain"].append({
            "name": target_company_name,
            "role": "Target Entity",
            "shareholding": "100%",
            "is_company": True,
            "company_number": target_company_number,
            "category": "Target Company",
            "depth": -1  # Use -1 to indicate root/target
        })
        
        # Add target company's directors
        for officer in officers_items:
            if officer.get("officer_role", "").lower() in ["director", "corporate-director", "shadow-director"]:
                if not officer.get("resigned_on"):  # Only active directors
                    screening["ownership_chain"].append({
                        "name": officer.get("name", "Unknown"),
                        "role": "Director",
                        "shareholding": "-",
                        "is_company": False,
                        "company_number": target_company_number,
                        "category": f"Directors of {target_company_name}",
                        "depth": -1,
                        "appointed_on": officer.get("appointed_on")
                    })
        
        # Add target company's secretaries
        for officer in officers_items:
            if officer.get("officer_role", "").lower() in ["secretary", "corporate-secretary"]:
                if not officer.get("resigned_on"):
                    screening["ownership_chain"].append({
                        "name": officer.get("name", "Unknown"),
                        "role": "Company Secretary",
                        "shareholding": "-",
                        "is_company": False,
                        "company_number": target_company_number,
                        "category": f"Company Secretaries of {target_company_name}",
                        "depth": -1,
                        "appointed_on": officer.get("appointed_on")
                    })
        
        # Add target company's PSCs
        for psc in psc_items:
            if not psc.get("ceased", False):
                natures = psc.get("natures_of_control", [])
                natures_str = ", ".join(natures) if natures else "Significant control"
                
                screening["ownership_chain"].append({
                    "name": psc.get("name", "Unknown"),
                    "role": "PSC",
                    "shareholding": natures_str,
                    "is_company": psc.get("kind") == "corporate-entity-person-with-significant-control",
                    "company_number": target_company_number,
                    "category": f"PSCs of {target_company_name}",
                    "depth": -1,
                    "natures_of_control": natures
                })
    
    def extract_ownership_chain(tree_node, depth=0):
        """Recursively extract shareholders, directors, officers, and PSCs from ownership tree"""
        if not tree_node or depth > 10:  # Prevent infinite loops
            return
        
        shareholders_in_node = tree_node.get("shareholders", [])
        for sh in shareholders_in_node:
            sh_name = sh.get("name", "Unknown")
            sh_percentage = sh.get("percentage", 0)
            sh_shares = sh.get("shares_held", 0)
            is_company = sh.get("is_company", False)
            company_number = sh.get("company_number")
            
            # Add the entity itself to ownership_chain
            if is_company and company_number:
                # Determine category based on depth
                if depth == 0:
                    category = "Corporate Shareholders"
                    role = "Shareholder"
                elif depth == 1:
                    category = "Parent Companies"
                    role = "Parent Company"
                elif depth == 2:
                    category = "Grandparent Companies"
                    role = "Grandparent Company"
                else:
                    category = "Ultimate Parent Companies"
                    role = "Ultimate Parent Company"
                
                screening["ownership_chain"].append({
                    "name": sh_name,
                    "role": role,
                    "shareholding": f"{sh_percentage}%",
                    "shares_held": sh_shares,
                    "is_company": True,
                    "company_number": company_number,
                    "category": category,
                    "depth": depth
                })
                
                # Fetch officers and PSCs for this company
                try:
                    from resolver import get_company_bundle
                    entity_bundle = get_company_bundle(company_number)
                    
                    # Extract officers (directors, secretaries, etc.)
                    officers_data = entity_bundle.get("officers", {})
                    officers_items = officers_data.get("items", [])
                    
                    for officer in officers_items:
                        officer_name = officer.get("name", "Unknown")
                        officer_role = officer.get("officer_role", "officer")
                        appointed_on = officer.get("appointed_on", "")
                        resigned_on = officer.get("resigned_on")
                        
                        # Skip resigned officers
                        if resigned_on:
                            continue
                        
                        # Categorize by role
                        role_lower = officer_role.lower()
                        if "director" in role_lower:
                            category = f"Directors of {sh_name}"
                            display_role = "Director"
                        elif "secretary" in role_lower:
                            category = f"Company Secretaries of {sh_name}"
                            display_role = "Company Secretary"
                        else:
                            category = f"Officers of {sh_name}"
                            display_role = officer_role.title()
                        
                        screening["ownership_chain"].append({
                            "name": officer_name,
                            "role": display_role,
                            "shareholding": "-",
                            "is_company": False,
                            "company_number": company_number,
                            "category": category,
                            "depth": depth,
                            "appointed_on": appointed_on
                        })
                    
                    # Extract PSCs (Persons with Significant Control)
                    pscs_data = entity_bundle.get("pscs", {})
                    pscs_items = pscs_data.get("items", [])
                    
                    for psc in pscs_items:
                        psc_name = psc.get("name", "Unknown")
                        natures = psc.get("natures_of_control", [])
                        ceased_on = psc.get("ceased_on")
                        
                        # Skip ceased PSCs
                        if ceased_on:
                            continue
                        
                        # Build natures description
                        natures_str = ", ".join(natures) if natures else "Significant control"
                        
                        screening["ownership_chain"].append({
                            "name": psc_name,
                            "role": "PSC",
                            "shareholding": natures_str,
                            "is_company": psc.get("kind") == "corporate-entity-person-with-significant-control",
                            "company_number": company_number,
                            "category": f"PSCs of {sh_name}",
                            "depth": depth,
                            "natures_of_control": natures
                        })
                    
                except Exception as e:
                    # Log error but continue processing
                    print(f"Error fetching officers/PSCs for {company_number}: {e}")
            
            # Individual shareholders with ≥10%
            elif not is_company and sh_percentage >= 10:
                screening["ownership_chain"].append({
                    "name": sh_name,
                    "role": "Individual Shareholder",
                    "shareholding": f"{sh_percentage}%",
                    "shares_held": sh_shares,
                    "is_company": False,
                    "company_number": None,
                    "category": "Individual Shareholders ≥10%",
                    "depth": depth
                })
            
            # UBOs - Individuals with ≥10% indirect ownership
            if not is_company and sh_percentage >= 10:
                screening["ubos"].append({
                    "name": sh_name,
                    "role": "Ultimate Beneficial Owner",
                    "shareholding": f"{sh_percentage}%",
                    "shares_held": sh_shares,
                    "indirect_ownership": True,
                    "category": "Individuals ≥10% indirect ownership",
                    "description": "Multiply percentages across layers to compute indirect control",
                    "depth": depth
                })
            
            # UBOs with control but no ownership (golden shares, veto rights, etc.)
            if not is_company and "control" in str(sh.get("psc_natures", [])).lower():
                screening["ubos"].append({
                    "name": sh_name,
                    "role": "Individual with Control",
                    "shareholding": "No ownership disclosed",
                    "category": "Individuals with control but no ownership",
                    "description": "Golden share, veto rights, dominant creditor",
                    "depth": depth
                })
            
            # Recurse into children
            if sh.get("children"):
                extract_ownership_chain(sh, depth + 1)
    
    # Start extraction from root
    if ownership_tree:
        extract_ownership_chain(ownership_tree)
    
    # 4. TRUSTS - Detect trust-related entities
    for sh in shareholders:
        sh_name = sh.get("name", "").lower()
        # Detect trustees
        if "trustee" in sh_name or "trust" in sh_name:
            screening["trusts"].append({
                "name": sh.get("name"),
                "role": "Trustee",
                "shareholding": f"{sh.get('percentage', 0)}%",
                "category": "Trustees",
                "description": "Always screen",
                "trust_type": "Detected from name"
            })
    
    # Check PSCs for trusts
    for psc in psc_items:
        if "trust" in psc.get("kind", "").lower():
            screening["trusts"].append({
                "name": psc.get("name"),
                "role": "Settlor/Beneficiary",
                "category": "Trust Parties",
                "description": "Settlor(s), Trustees, Protector(s), Beneficiaries",
                "kind": psc.get("kind")
            })
    
    # 5. GUARANTEE COMPANIES - Members for companies limited by guarantee
    company_type = profile.get("type", "").lower()
    if "guarant" in company_type:
        # Note: Member information not typically in public data
        screening["guarantee_companies"].append({
            "name": "Guarantee Members",
            "category": "Guarantee Company Members",
            "description": "Company is limited by guarantee - member information required",
            "company_type": company_type,
            "note": "Member information not available in public registers"
        })
    
    # 6. ASSOCIATED PERSONS
    # Note: This data is typically not in public registers
    # Would need to be collected separately via client questionnaire
    screening["associated_persons"].append({
        "category": "Associated Persons",
        "description": "Authorized Signatories, Introducers/Brokers, SMF Holders",
        "note": "This information must be collected via client questionnaire - not available in public registers",
        "required": [
            "Anyone with authority to move funds",
            "If involved in onboarding or decision influence",
            "Senior Management Functions in regulated firms"
        ]
    })
    
    # 7. SUBSIDIARIES
    # Note: Subsidiary information not readily available in bundle
    # Would need to fetch filing history or use separate API
    screening["subsidiaries"].append({
        "category": "Controlled Subsidiaries / Joint Ventures",
        "description": "≥10% ownership or effective control / If entity has control or sanctioned exposure risk",
        "note": "Subsidiary data requires additional API calls or filing analysis"
    })
    
    return screening

@app.get("/api/batch/{batch_id}/items")
async def api_get_batch_items(batch_id: int, limit: int = 100, offset: int = 0):
    """Get items for a specific batch"""
    try:
        with db() as conn:
            items = conn.execute("""
                SELECT 
                    id,
                    input_name,
                    pipeline_status,
                    match_type,
                    company_number,
                    charity_number,
                    company_status,
                    confidence,
                    reason,
                    enrich_status,
                    resolved_registry,
                    created_at
                FROM items
                WHERE run_id = ?
                ORDER BY id ASC
                LIMIT ? OFFSET ?
            """, (batch_id, limit, offset)).fetchall()
            
            result_items = []
            for item in items:
                result_items.append({
                    "id": item["id"],
                    "input_name": item["input_name"],
                    "pipeline_status": item["pipeline_status"],
                    "match_type": item["match_type"],
                    "company_number": item["company_number"],
                    "charity_number": item["charity_number"],
                    "company_status": item["company_status"],
                    "confidence": item["confidence"],
                    "reason": item["reason"],
                    "enrich_status": item["enrich_status"],
                    "resolved_registry": item["resolved_registry"],
                    "created_at": item["created_at"]
                })
            
            return JSONResponse(content={"items": result_items})
    except Exception as e:
        return JSONResponse(
            content={"error": f"Failed to fetch batch items: {str(e)}"},
            status_code=500
        )

@app.get("/api/item/{item_id}/test-tree")
def test_ownership_tree(item_id: int):
    """Test ownership tree building for debugging"""
    try:
        with db() as conn:
            item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        
        if not item:
            return JSONResponse(content={"error": "Item not found"}, status_code=404)
        
        # Get shareholders from database
        shareholders_json = item["shareholders_json"]
        if not shareholders_json:
            return JSONResponse(content={"error": "No shareholders data"}, status_code=400)
        
        shareholders_data = json.loads(shareholders_json)
        all_shareholders = []
        
        if isinstance(shareholders_data, dict):
            all_shareholders = shareholders_data.get("regular_shareholders", []) + shareholders_data.get("parent_shareholders", [])
        elif isinstance(shareholders_data, list):
            all_shareholders = shareholders_data
        
        company_number = item["company_number"]
        company_name = item["input_name"]
        
        # Test tree building
        print(f"[TEST] Building tree for {company_name} ({company_number})")
        print(f"[TEST] Shareholders to pass: {len(all_shareholders)}")
        
        ownership_tree = build_ownership_tree(
            company_number,
            company_name,
            depth=0,
            max_depth=50,  # Effectively unlimited - will recurse until end of ownership chain (circular refs prevented by visited set)
            visited=None,
            initial_shareholders=all_shareholders
        )
        
        return JSONResponse(content={
            "input_shareholders": len(all_shareholders),
            "shareholder_names": [sh.get("name") for sh in all_shareholders],
            "tree_shareholders": len(ownership_tree.get("shareholders", [])),
            "tree": ownership_tree
        })
        
    except Exception as e:
        import traceback
        return JSONResponse(
            content={"error": str(e), "traceback": traceback.format_exc()},
            status_code=500
        )

@app.post("/api/item/{item_id}/reset")
def reset_item_enrichment(item_id: int):
    """Reset an item's enrichment status from 'running' to 'pending' to retry"""
    try:
        with db() as conn:
            # Check if item exists and is stuck
            row = conn.execute("SELECT id, enrich_status FROM items WHERE id=?", (item_id,)).fetchone()
            if not row:
                return JSONResponse(
                    content={"error": "Item not found"},
                    status_code=404
                )
            
            old_status = row["enrich_status"]
            
            # Reset status to pending and re-enqueue
            conn.execute("UPDATE items SET enrich_status='pending' WHERE id=?", (item_id,))
            
            # Re-enqueue enrichment
            registry = conn.execute("SELECT resolved_registry FROM items WHERE id=?", (item_id,)).fetchone()
            if registry and registry["resolved_registry"]:
                reg = canonical_registry_name(registry["resolved_registry"])
                if reg == "Companies House":
                    enqueue_enrich(item_id)
                elif reg == "Charity Commission":
                    enqueue_enrich_charity(item_id)
            
            return JSONResponse(content={
                "success": True,
                "item_id": item_id,
                "old_status": old_status,
                "new_status": "pending",
                "message": "Item reset and re-enqueued for enrichment"
            })
    except Exception as e:
        return JSONResponse(
            content={"error": f"Failed to reset item: {str(e)}"},
            status_code=500
        )

@app.get("/api/item/{item_id}")
async def api_get_item_details(item_id: int):
    """Get full details for a specific item including enriched data and shareholders"""
    try:
        with db() as conn:
            item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            
        if not item:
            return JSONResponse(
                content={"error": "Item not found"},
                status_code=404
            )
        
        # Parse shareholders JSON (handle both old flat array and new structured format)
        shareholders = []
        if item["shareholders_json"]:
            try:
                shareholders_data = json.loads(item["shareholders_json"])
                # New format: object with regular_shareholders and parent_shareholders
                if isinstance(shareholders_data, dict):
                    shareholders = shareholders_data.get("regular_shareholders", []) + shareholders_data.get("parent_shareholders", [])
                # Old format: flat array
                elif isinstance(shareholders_data, list):
                    shareholders = shareholders_data
            except Exception as e:
                print(f"[api_get_item_details] Failed to parse shareholders_json: {e}")
        
        # Read enriched bundle if available
        bundle = {}
        if item["enrich_json_path"]:
            try:
                with open(item["enrich_json_path"], 'r') as f:
                    bundle = json.load(f)
            except Exception:
                pass
        
        # Read ownership tree from database (preferred, survives Railway redeployments)
        ownership_tree = None
        try:
            # Use dict() to safely access columns that might not exist yet
            item_dict = dict(item)
            if item_dict.get("ownership_tree_json"):
                ownership_tree = json.loads(item_dict["ownership_tree_json"])
        except Exception as e:
            print(f"[api_get_item_details] Failed to read ownership_tree_json (column may not exist yet): {e}")
        
        # Fallback to bundle if database doesn't have it
        if not ownership_tree and bundle:
            ownership_tree = bundle.get("ownership_tree")
        
        # Build KYC/AML screening list
        # Convert sqlite3.Row to dict for screening list function
        item_dict = dict(item) if item else {}
        screening_list = build_screening_list(bundle, shareholders, item_dict)
        
        # Build response
        result = {
            "id": item["id"],
            "input_name": item["input_name"],
            "company_number": item["company_number"],
            "charity_number": item["charity_number"],
            "resolved_registry": item["resolved_registry"],
            "pipeline_status": item["pipeline_status"],
            "enrich_status": item["enrich_status"],
            "match_type": item["match_type"],
            "confidence": item["confidence"],
            "company_status": item["company_status"],
            "created_at": item["created_at"],
            "shareholders": shareholders,
            "shareholders_status": item["shareholders_status"],
            "ownership_tree": ownership_tree,
            "ownership_chains": bundle.get("ownership_chains", []),
            "profile": bundle.get("profile", {}),
            "officers": bundle.get("officers", {}),
            "pscs": bundle.get("pscs", {}),
            "filings": bundle.get("filings", []),
            "sources": bundle.get("sources", {}),
            "screening_list": screening_list  # KYC/AML screening requirements
        }
        
        return JSONResponse(content=result)
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"[api_get_item_details] Error: {e}")
        print(f"[api_get_item_details] Traceback: {error_details}")
        return JSONResponse(
            content={"error": f"Failed to fetch item details: {str(e)}"},
            status_code=500
        )

@app.get("/api/item/{item_id}/screening-export.csv")
async def export_screening_list_csv(item_id: int):
    """Export KYC/AML screening list as CSV for ingestion into screening engines"""
    try:
        from fastapi.responses import StreamingResponse
        import io
        import csv
        
        with db() as conn:
            item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            
        if not item:
            return JSONResponse(content={"error": "Item not found"}, status_code=404)
        
        # Get shareholders and bundle
        shareholders = []
        if item["shareholders_json"]:
            try:
                shareholders_data = json.loads(item["shareholders_json"])
                if isinstance(shareholders_data, dict):
                    shareholders = shareholders_data.get("regular_shareholders", []) + shareholders_data.get("parent_shareholders", [])
                elif isinstance(shareholders_data, list):
                    shareholders = shareholders_data
            except Exception:
                pass
        
        bundle = {}
        if item["enrich_json_path"]:
            try:
                with open(item["enrich_json_path"], 'r') as f:
                    bundle = json.load(f)
            except Exception:
                pass
        
        # Build screening list
        item_dict = dict(item) if item else {}
        screening_list = build_screening_list(bundle, shareholders, item_dict)
        
        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header
        writer.writerow([
            'Category',
            'Name',
            'Type',
            'Role',
            'Company Number',
            'Shareholding',
            'Date of Birth',
            'Nationality',
            'Appointed On',
            'Description',
            'Notes'
        ])
        
        # Flatten screening list into CSV rows
        all_entries = []
        
        # Entity
        for entity in screening_list.get('entity', []):
            all_entries.append({
                'Category': 'Entity',
                'Name': entity.get('name'),
                'Type': entity.get('type'),
                'Role': 'Subject Entity',
                'Company Number': entity.get('company_number'),
                'Shareholding': '',
                'Date of Birth': '',
                'Nationality': '',
                'Appointed On': '',
                'Description': entity.get('category'),
                'Notes': f"Status: {entity.get('status')}"
            })
        
        # Governance & Control
        for person in screening_list.get('governance_and_control', []):
            all_entries.append({
                'Category': 'Governance & Control',
                'Name': person.get('name'),
                'Type': 'Corporate Entity' if person.get('kind') == 'corporate-entity-person-with-significant-control' else 'Individual',
                'Role': person.get('role', '').title(),
                'Company Number': person.get('company_number', ''),
                'Shareholding': '',
                'Date of Birth': person.get('dob', ''),
                'Nationality': person.get('nationality', ''),
                'Appointed On': person.get('appointed_on', ''),
                'Description': person.get('description'),
                'Notes': ''
            })
        
        # Ownership Chain
        for owner in screening_list.get('ownership_chain', []):
            all_entries.append({
                'Category': 'Ownership Chain',
                'Name': owner.get('name'),
                'Type': 'Company' if owner.get('is_company') else 'Individual',
                'Role': owner.get('role', 'Shareholder'),
                'Company Number': owner.get('company_number', ''),
                'Shareholding': owner.get('shareholding', ''),
                'Date of Birth': '',
                'Nationality': '',
                'Appointed On': '',
                'Description': owner.get('description'),
                'Notes': f"Depth: {owner.get('depth', 0)}, Shares: {owner.get('shares_held', 'Unknown')}"
            })
        
        # UBOs
        for ubo in screening_list.get('ubos', []):
            all_entries.append({
                'Category': 'UBO',
                'Name': ubo.get('name'),
                'Type': 'Company' if ubo.get('is_company') else 'Individual',
                'Role': 'Ultimate Beneficial Owner',
                'Company Number': ubo.get('company_number', ''),
                'Shareholding': ubo.get('indirect_ownership', ''),
                'Date of Birth': '',
                'Nationality': '',
                'Appointed On': '',
                'Description': ubo.get('description'),
                'Notes': f"Chain: {' → '.join(ubo.get('chain', []))}"
            })
        
        # Trusts
        for trust in screening_list.get('trusts', []):
            all_entries.append({
                'Category': 'Trust',
                'Name': trust.get('name'),
                'Type': 'Trust Entity',
                'Role': trust.get('role', 'Trustee'),
                'Company Number': trust.get('company_number', ''),
                'Shareholding': trust.get('shareholding', ''),
                'Date of Birth': '',
                'Nationality': '',
                'Appointed On': '',
                'Description': trust.get('description'),
                'Notes': trust.get('note', '')
            })
        
        # Deduplicate entries by name and category to avoid duplicate screening
        seen = set()
        unique_entries = []
        for entry in all_entries:
            # Create unique key based on name and category
            key = (entry.get('Name', '').strip().lower(), entry.get('Category', ''))
            if key not in seen:
                seen.add(key)
                unique_entries.append(entry)
        
        # Write rows
        for entry in unique_entries:
            writer.writerow([
                entry.get('Category', ''),
                entry.get('Name', ''),
                entry.get('Type', ''),
                entry.get('Role', ''),
                entry.get('Company Number', ''),
                entry.get('Shareholding', ''),
                entry.get('Date of Birth', ''),
                entry.get('Nationality', ''),
                entry.get('Appointed On', ''),
                entry.get('Description', ''),
                entry.get('Notes', '')
            ])
        
        # Return CSV
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=screening_list_{item_id}_{item['company_number']}.csv"
            }
        )
        
    except Exception as e:
        return JSONResponse(
            content={"error": f"Failed to export screening list: {str(e)}"},
            status_code=500
        )

@app.get("/auto/{item_id}/compare", response_class=HTMLResponse)
def auto_compare(request: Request, item_id: int):
    # ---------- load the item ----------
    with db() as conn:
        item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not item:
        return RedirectResponse(url="/queue/auto", status_code=303)

    # ---------- build uploaded payload (every exact schema field, including empty) ----------
    uploaded = []
    uploaded_map = {}  # normalized-header -> uploaded string (for LP name lookups)
    for h in ALL_SCHEMA_FIELDS:
        try:
            v = item[h]
        except Exception:
            v = None
        sval = None
        if v is not None:
            sval = str(v).strip()
        uploaded.append({"header": h, "value": sval if sval else None})
        if sval:
            uploaded_map[_norm_key_for_match(h)] = sval

    # ---------- read enriched bundle + handy slices ----------
    bundle = {}
    if item["enrich_json_path"]:
        bundle = _read_json(item["enrich_json_path"]) or {}

    # keep the focused sections for flattening
    enriched_focus = {}
    if bundle:
        for root in ("profile", "officers", "pscs", "charges", "trustees", "filings", "sources"):
            if root in bundle:
                enriched_focus[root] = bundle[root]
        try:
            enriched_focus.setdefault("_derived", {})
            enriched_focus["_derived"]["counts.officers"] = len((bundle.get("officers") or {}).get("items") or [])
            enriched_focus["_derived"]["counts.pscs"] = len((bundle.get("pscs") or {}).get("items") or [])
            enriched_focus["_derived"]["counts.charges"] = len((bundle.get("charges") or {}).get("items") or [])
            enriched_focus["_derived"]["counts.trustees"] = len(bundle.get("trustees") or [])
            enriched_focus["_derived"]["counts.filings"] = len(bundle.get("filings") or [])
        except Exception:
            pass

    enriched_flat = _flatten_enriched(enriched_focus) if enriched_focus else {}

    # ---------- officer / PSC helpers ----------
    def _canon_person_name(s: str) -> str:
        if not s:
            return ""
        s = str(s).lower()
        s = re.sub(r"[^a-z0-9\s]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _token_overlap_score(a: str, b: str) -> float:
        ta = {t for t in _canon_person_name(a).split() if t}
        tb = {t for t in _canon_person_name(b).split() if t}
        if not ta or not tb:
            return 0.0
        inter = len(ta & tb)
        union = len(ta | tb)
        return inter / union

    def _names_equivalent(a: str, b: str) -> bool:
        """Robust person-name match: case/format-insensitive, 'SURNAME, Forename' vs 'Forename Surname'."""
        ca = _canon_person_name(a)
        cb = _canon_person_name(b)
        if not ca or not cb:
            return False
        if ca == cb:
            return True
        # token set equality (orderless)
        sa = {t for t in ca.split() if t}
        sb = {t for t in cb.split() if t}
        return bool(sa and sb and sa == sb)

    def _addr_to_str(addr) -> str:
        if not addr:
            return ""
        if isinstance(addr, dict):
            parts = []
            for k in ("address_line_1", "address_line_2", "premises", "locality", "region", "postal_code", "country"):
                v = addr.get(k)
                if v: parts.append(str(v))
            return ", ".join(parts)
        return str(addr)

    officers_list = (_get_in(bundle, "officers", "items") or [])
    pscs_list     = (_get_in(bundle, "pscs", "items") or [])

    LP_HEADER_RE = re.compile(r"^linked_party_(?P<field>.+)_(?P<idx>\d+)$", re.IGNORECASE)

    def _psc_display_name(psc: dict) -> str:
        if psc.get("name"):
            return str(psc.get("name"))
        ne = psc.get("name_elements") or {}
        parts = [ne.get("title"), ne.get("forename"), ne.get("middle_name"), ne.get("surname")]
        parts = [p for p in parts if p]
        return " ".join(parts).strip()

    def _best_person_for_name(name: str):
        best_kind, best_obj, best_score = (None, None, 0.0)
        nm = (name or "").strip()
        if not nm:
            return (None, None, 0.0)

        for o in officers_list:
            score = 1.0 if _canon_person_name(o.get("name") or "") == _canon_person_name(nm) else _token_overlap_score(o.get("name") or "", nm)
            if score > best_score:
                best_kind, best_obj, best_score = ("officer", o, score)

        for p in pscs_list:
            pname = _psc_display_name(p)
            score = 1.0 if _canon_person_name(pname) == _canon_person_name(nm) else _token_overlap_score(pname, nm)
            if score > best_score:
                best_kind, best_obj, best_score = ("psc", p, score)

        return (best_kind, best_obj, best_score)

    def _derive_linked_party_value(header: str):
        m = LP_HEADER_RE.match(header)
        if not m:
            return (None, None)
        lp_field = m.group("field").lower()
        idx      = m.group("idx")

        up_name_key_norm = _norm_key_for_match(f"Linked_party_full_name_{idx}")
        up_name = uploaded_map.get(up_name_key_norm)

        def _person_by_index(n: int):
            i = max(0, n - 1)
            if i < len(pscs_list):
                return ("psc", pscs_list[i])
            if i < len(officers_list):
                return ("officer", officers_list[i])
            return (None, None)

        if up_name:
            kind, person, score = _best_person_for_name(up_name)
            if not person or score < 0.40:
                kind, person = _person_by_index(int(idx))
        else:
            kind, person = _person_by_index(int(idx))

        if not person:
            return (None, None)

        def _fmt_dob(dob):
            if isinstance(dob, dict):
                y, m = dob.get("year"), dob.get("month")
                try:
                    if y and m:
                        return f"{int(y):04d}-{int(m):02d}"
                except Exception:
                    pass
                return json.dumps(dob, ensure_ascii=False)
            return dob

        if kind == "officer":
            if lp_field == "full_name":
                return (person.get("name"), "(officer)")
            if lp_field == "role":
                return (person.get("officer_role"), "(officer)")
            if lp_field in ("dob", "date_of_birth"):
                return (_fmt_dob(person.get("date_of_birth") or person.get("dob") or person.get("dateOfBirth")), "(officer)")
            if lp_field == "nationality":
                return (person.get("nationality"), "(officer)")
            if lp_field == "country_of_residence":
                return (person.get("country_of_residence") or person.get("countryOfResidence"), "(officer)")
            if lp_field == "correspondence_address":
                return (_addr_to_str(person.get("address")), "(officer)")
            if lp_field in ("appointed_on", "appointed_date"):
                return (person.get("appointed_on"), "(officer)")
            return (person.get(lp_field), "(officer)")

        if kind == "psc":
            if lp_field == "full_name":
                return (_psc_display_name(person), "(psc)")
            if lp_field in ("role", "position"):
                return (person.get("kind"), "(psc)")
            if lp_field in ("dob", "date_of_birth"):
                return ((person.get("date_of_birth")), "(psc)")
            if lp_field == "nationality":
                return (person.get("nationality"), "(psc)")
            if lp_field == "country_of_residence":
                return (person.get("country_of_residence") or person.get("countryOfResidence"), "(psc)")
            if lp_field == "correspondence_address":
                addr = person.get("address") or person.get("principal_address")
                return (_addr_to_str(addr) if addr else None, "(psc)")
            if lp_field in ("appointed_on", "notified_on"):
                return (person.get("notified_on"), "(psc)")
            return (person.get(lp_field), "(psc)")

        return (None, None)

    # ---------- authoritative map (CH / CCEW) ----------
    is_ch = (item["resolved_registry"] or "").startswith("Companies House")
    is_cc = "Charity Commission" in (item["resolved_registry"] or "")
    auth_map, consumed_paths = _authoritative_map(bundle, is_ch=is_ch, is_cc=is_cc)
    auth_map_norm = { _norm_key_for_match(k): v for k, v in auth_map.items() }

    # ---------- build index of flattened keys ----------
    enriched_index = {}
    for k, v in enriched_flat.items():
        norm_full = _norm_key_for_match(k)
        leaf = k.split(".")[-1]
        norm_leaf = _norm_key_for_match(leaf)
        enriched_index.setdefault(norm_full, []).append((k, v))
        if norm_leaf != norm_full:
            enriched_index.setdefault(norm_leaf, []).append((k, v))

    # ---------- helper: fields that should never appear ----------
    NEVER_ENRICH_NORMS = {
        _norm_key_for_match(x) for x in [
            "Customer_id",
            "Entity_primary_phone",
            "Entity_primary_email",
            "Entity_Industry_sector",
            "Entity_nature_&_purpose",
            "Existing_accounts_balance",
            "Expected_annual_revenue",
            "Expected_money_into_account",
            "Expected_money_out_of_account",
            "Expected_revenue_sources",
            "Expected_transaction_jurisdictions",
            "Products_held",
            "Source_Of_Funds",
            "Source_Of_Wealth",
        ]
    }
    # LP block status patterns (match any index)
    def _is_never_enriched(header: str) -> bool:
        n = _norm_key_for_match(header)
        if n in NEVER_ENRICH_NORMS:
            return True
        # linked party status blocks
        return (
            n.startswith("linked_party_pep_rca_status_")
            or n.startswith("linked_party_sanction_status_")
            or n.startswith("linked_party_adverse_media_status_")
        )

    # ---------- helper: smart equality per field ----------
    def _strip_time(s: str) -> str:
        # "YYYY-MM-DD 00:00:00" -> "YYYY-MM-DD"
        return re.sub(r"\s+\d{2}:\d{2}:\d{2}$", "", s.strip())

    def _eq_uploaded_enriched(header: str, uploaded_val: Optional[str], enriched_val: Optional[str]) -> bool:
        if uploaded_val is None or enriched_val is None:
            return False
        u = str(uploaded_val).strip()
        e = str(enriched_val).strip()
        if not u and not e:
            return True
        # case-insensitive exact
        if u.lower() == e.lower():
            return True

        hn = _norm_key_for_match(header)

        # Person names: linked_party_full_name_*
        if re.match(r"^linked_party_full_name_\d+$", hn):
            return _names_equivalent(u, e)

        # Roles: case-insensitive only
        if re.match(r"^linked_party_role_\d+$", hn):
            return u.lower() == e.lower()

        # Country / Nationality: case-insensitive (common variations like 'UK' vs 'United Kingdom' are NOT folded here)
        if re.match(r"^linked_party_country_of_residence_\d+$", hn) or re.match(r"^linked_party_nationality_\d+$", hn):
            return u.lower() == e.lower()

        # DoB: uploaded can be YYYY-MM-DD (maybe with time), CH often YYYY-MM
        if re.match(r"^linked_party_dob_\d+$", hn) or re.match(r"^linked_party_date_of_birth_\d+$", hn):
            u_no_time = _strip_time(u)
            # accept prefix match YYYY-MM
            if re.match(r"^\d{4}-\d{2}$", e) and u_no_time.startswith(e):
                return True
            if re.match(r"^\d{4}-\d{2}$", u_no_time) and e.startswith(u_no_time):
                return True
            # final exact (case-insensitive) after stripping time
            return u_no_time.lower() == e.lower()

        return False  # default to strict (already checked case-insensitive above)

    # ---------- construct comparison rows ----------
    rows = []
    seen_norm_upload = set()

    for rec in uploaded:
        header = rec["header"]
        if _is_never_enriched(header):
            # Hide lines that will never be enriched
            continue

        uval = rec["value"]
        key_norm = _norm_key_for_match(header)
        seen_norm_upload.add(key_norm)

        eval_, ekey = (None, None)

        # 1) Authoritative mapping
        mapped_val = auth_map_norm.get(key_norm)
        if mapped_val is not None:
            eval_, ekey = mapped_val, "(mapped)"

        # 2) Linked Party derivation
        if eval_ is None and (officers_list or pscs_list):
            eval_, ekey = _derive_linked_party_value(header)

        # 3) Fallback: flattened enriched
        if eval_ is None:
            e_candidates = enriched_index.get(key_norm) or []
            if e_candidates:
                ekey, eval_ = min(e_candidates, key=lambda t: len(t[0]))

        # status + outcome
        is_same = (eval_ is not None and uval is not None and _eq_uploaded_enriched(header, uval, eval_))
        if eval_ is None and uval is None:
            status = "same"
            outcome = "missing"
        elif is_same:
            status = "same"
            outcome = "matched"
        elif eval_ is None and uval is not None:
            status = "missing_enriched"
            outcome = "missing"
        elif eval_ is not None and (uval is None or uval == ""):
            status = "diff"  # visually yellow; indicates CH filled it
            outcome = "enriched"
        else:
            status = "diff"
            outcome = "mismatch"

        rows.append({
            "field": header,
            "uploaded": uval,
            "enriched": eval_,
            "enriched_key": ekey,
            "status": status,
            "outcome": outcome,
        })

    # ---------- extras ----------
    for k, v in enriched_flat.items():
        if k in consumed_paths:
            continue
        n_leaf = _norm_key_for_match(k.split(".")[-1])
        n_full = _norm_key_for_match(k)
        if n_leaf not in seen_norm_upload and n_full not in seen_norm_upload:
            rows.append({
                "field": "(extra) " + k,
                "uploaded": None,
                "enriched": v,
                "enriched_key": k,
                "status": "extra_enriched",
                "outcome": "enriched",
            })

    # ---- Hide empty Linked Party blocks (keep full block if any field has data) ----
    HIDE_EMPTY_LP_BLOCKS = True
    if HIDE_EMPTY_LP_BLOCKS:
        # Match: Linked_party_<anything>_<index>, case-insensitive
        LP_BLOCK_RE_HIDE = re.compile(r"^linked_party_(?P<field>.+)_(?P<idx>\d+)$", re.IGNORECASE)

        # group row indices by LP block index
        block_to_rowidxs = {}
        for i, r in enumerate(rows):
            fld = r.get("field") or ""
            m = LP_BLOCK_RE_HIDE.match(_norm_key_for_match(fld.replace("(extra) ", "")))
            if not m:
                continue
            idx = int(m.group("idx"))
            block_to_rowidxs.setdefault(idx, []).append(i)

        # decide which block rows to keep
        keep = [True] * len(rows)
        for idx, idxs in block_to_rowidxs.items():
            # any field in the block has data (uploaded or enriched)?
            has_any_data = any(
                (rows[i].get("uploaded") not in (None, "")) or
                (rows[i].get("enriched") not in (None, ""))
                for i in idxs
            )
            # if no data anywhere, hide the whole block
            if not has_any_data:
                for i in idxs:
                    keep[i] = False

        rows = [r for r, k in zip(rows, keep) if k]

    # ---------- split main vs extra for template ----------
    main_rows = [r for r in rows if not str(r.get("field") or "").startswith("(extra)")]
    extra_rows = [r for r in rows if     str(r.get("field") or "").startswith("(extra)")]

    return templates.TemplateResponse(
        "auto_compare.html",
        {
            "request": request,
            "item": item,
            "rows": main_rows,         # main comparison table
            "extra_rows": extra_rows,  # collapsible 'Additional Enriched Fields'
            "is_ch": is_ch,
            "is_cc": is_cc,
        },
    )

@app.get("/queue/auto", response_class=HTMLResponse)
def queue_auto(request: Request, run_id: Optional[int] = None):
    with db() as conn:
        if run_id:
            rows = conn.execute("SELECT * FROM items WHERE pipeline_status='auto' AND run_id=? ORDER BY created_at ASC", (run_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM items WHERE pipeline_status='auto' ORDER BY created_at DESC").fetchall()
    return templates.TemplateResponse("queue_auto.html", {"request": request, "rows": rows, "run_id": run_id})

@app.get("/queue/manual", response_class=HTMLResponse)
def queue_manual(request: Request, run_id: Optional[int] = None):
    with db() as conn:
        if run_id:
            rows = conn.execute("SELECT id,input_name,created_at FROM items WHERE pipeline_status='manual_required' AND run_id=? ORDER BY created_at ASC", (run_id,)).fetchall()
        else:
            rows = conn.execute("SELECT id,input_name,created_at FROM items WHERE pipeline_status='manual_required' ORDER BY created_at DESC").fetchall()
    return templates.TemplateResponse("queue_manual.html", {"request": request, "rows": rows, "run_id": run_id})

# --- helper to safely read JSON bundles ---
def _read_json(path: str):
    try:
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[auto_detail] failed to read {path}: {e}")
    return {}

def _q_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'

def _norm_cell(v):
    import pandas as pd
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    s = str(v).strip()
    return s if s else None

# ---------------- Auto item detail page ----------------
@app.get("/auto/{item_id}", response_class=HTMLResponse)
def auto_detail(request: Request, item_id: int):
    with db() as conn:
        item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not item:
        return RedirectResponse(url="/queue/auto", status_code=303)
    if item["pipeline_status"] not in ("auto",):
        return RedirectResponse(url="/queue/auto", status_code=303)

    bundle = {}
    if item["enrich_json_path"]:
        bundle = _read_json(item["enrich_json_path"])

    # Load shareholders data from database if available
    if item["shareholders_json"]:
        try:
            all_shareholders = json.loads(item["shareholders_json"])
            # Separate regular and parent shareholders based on name suffixes
            from shareholder_information import identify_parent_companies
            bundle["regular_shareholders"], bundle["parent_shareholders"] = identify_parent_companies(all_shareholders)
            bundle["total_shares"] = sum(int(s.get("shares_held", 0)) for s in all_shareholders if s.get("shares_held"))
        except (json.JSONDecodeError, TypeError):
            bundle["regular_shareholders"] = []
            bundle["parent_shareholders"] = []
            bundle["total_shares"] = 0

    # Load shareholders status from database if available
    if item["shareholders_status"]:
        bundle["shareholders_status"] = item["shareholders_status"]

    enrich_status = item["enrich_status"] or "pending"

    # safe field extractor
    def _nz_field(row, key):
        try:
            v = row[key]
            if v is None:
                return None
            s = str(v).strip()
            return s if s else None
        except Exception:
            return None

    ref_no = _nz_field(item, "company_number") or _nz_field(item, "charity_number")

    return templates.TemplateResponse(
        "auto_detail.html",
        {
            "request": request,
            "item": item,
            "bundle": bundle,
            "enrich_status": enrich_status,
            "ref_no": ref_no,
            "registry": item["resolved_registry"],
            "is_ch": (item["resolved_registry"] or "").startswith("Companies House"),
            "is_cc": "Charity Commission" in (item["resolved_registry"] or ""),
            "source_profile_url": item["source_url"],
        },
    )

@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request):
    # Basic metrics from DB
    with db() as conn:
        cur = conn.cursor()
        batches = cur.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
        total_records = cur.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"]

        # What happened on upload:
        auto_on_upload = cur.execute("SELECT COUNT(*) AS c FROM items WHERE pipeline_status='auto'").fetchone()["c"]
        manual_on_upload = cur.execute("SELECT COUNT(*) AS c FROM items WHERE pipeline_status='manual_required'").fetchone()["c"]

        # Post review transitions:
        moved_to_auto_after_manual = cur.execute("""
            SELECT COUNT(*) AS c FROM items
            WHERE pipeline_status='auto' AND match_type='Manual confirm'
        """).fetchone()["c"]

        unable_to_match = cur.execute("""
            SELECT COUNT(*) AS c FROM items
            WHERE pipeline_status='error' AND reason='Unable to match'
        """).fetchone()["c"]

        pending_manual = manual_on_upload  # current snapshot

        # Enriched items to scan for compare-derived tallies
        enriched_rows = cur.execute("""
            SELECT * FROM items
            WHERE pipeline_status='auto' AND enrich_status='done' AND enrich_json_path IS NOT NULL
        """).fetchall()

    mismatch_records = 0
    enriched_records = 0
    potential_risks = 0

    for r in enriched_rows:
        roll = _record_compare_rollup(r)
        if roll["has_mismatch"]:
            mismatch_records += 1
        if roll["has_enrichment"]:
            enriched_records += 1
        if roll["potential_risk"]:
            potential_risks += 1

    metrics = {
        "batches": batches,
        "total_records": total_records,
        "auto_on_upload": auto_on_upload,
        "manual_on_upload": manual_on_upload,
        "moved_to_auto_after_manual": moved_to_auto_after_manual,
        "unable_to_match": unable_to_match,
        "pending_manual": pending_manual,
        "potential_screening_risks": potential_risks,
    }
    charts = {
        "data_quality": {
            "mismatch_records": mismatch_records,
            "enriched_records": enriched_records,
        },
        "outcome_split": {
            "auto_on_upload": auto_on_upload,
            "manual_on_upload": manual_on_upload,
        }
    }
    return templates.TemplateResponse("reports.html", {
        "request": request,
        "metrics": metrics,
        "charts": charts,
    })

# --- EXPORT: Enriched report (Excel) ---
from fastapi import Query
from fastapi.responses import FileResponse
import pandas as pd
import tempfile
import os
from datetime import datetime

def _compare_impacts(row):
    """
    Returns:
      mismatch_fields: list[str]  # keys where upload present & != enriched
      enriched_fields: list[str]  # keys enriched OR present only in bundle
    Uses the same visible-compare logic as _record_compare_rollup.
    """
    # local safe getter
    def _rg(r, k, default=None):
        try:
            return r[k]
        except Exception:
            try:
                return r.get(k, default)  # type: ignore[attr-defined]
            except Exception:
                return default

    # ---- uploaded map (cleaned)
    uploaded_map = {}
    for h in ALL_SCHEMA_FIELDS:
        try:
            v = row[h]
        except Exception:
            v = None
        cv = _clean_cell(v)
        if cv is not None:
            uploaded_map[_norm_key_for_match(h)] = cv

    # seeds
    in_name = _clean_cell(_rg(row, "input_name"))
    if "entity_name" not in uploaded_map and in_name:
        uploaded_map["entity_name"] = in_name
    client_pc = _clean_cell(_rg(row, "client_address_postcode"))
    if "entity_primary_address_postcode" not in uploaded_map and client_pc:
        uploaded_map["entity_primary_address_postcode"] = client_pc
    client_ctry = _clean_cell(_rg(row, "client_address_country"))
    if "entity_primary_address_country" not in uploaded_map and client_ctry:
        uploaded_map["entity_primary_address_country"] = client_ctry

    # ---- bundle
    enrich_path = (
        _rg(row, "enrich_json_path")
        or _rg(row, "enriched_json_path")
        or _rg(row, "bundle_path")
        or _rg(row, "auto_detail_path")
    )
    bundle = _safe_read_json(enrich_path) if enrich_path else {}

    # if no readable bundle, signal to caller
    if not bundle:
        return None, None, None  # (mismatch_fields, enriched_fields, bundle_present=False)

    # focus + flatten (same as rollup)
    enriched_focus = {}
    for root in ("profile", "officers", "pscs", "charges", "trustees", "filings", "sources"):
        if root in bundle:
            enriched_focus[root] = bundle[root]
    try:
        enriched_focus.setdefault("_derived", {})
        enriched_focus["_derived"]["counts.officers"] = len((bundle.get("officers") or {}).get("items") or [])
        enriched_focus["_derived"]["counts.pscs"] = len((bundle.get("pscs") or {}).get("items") or [])
        enriched_focus["_derived"]["counts.charges"] = len((bundle.get("charges") or {}).get("items") or [])
        enriched_focus["_derived"]["counts.trustees"] = len(bundle.get("trustees") or [])
        enriched_focus["_derived"]["counts.filings"] = len(bundle.get("filings") or [])
    except Exception:
        pass

    enriched_flat = _flatten_enriched(enriched_focus) if enriched_focus else {}

    # authoritative lookup
    reg = _rg(row, "resolved_registry") or ""
    is_ch = reg.startswith("Companies House")
    is_cc = "Charity Commission" in reg
    auth_map, _ = _authoritative_map(bundle, is_ch=is_ch, is_cc=is_cc)
    auth_map_norm = { _norm_key_for_match(k): v for k, v in auth_map.items() }

    # quick index
    enriched_index = {}
    for k, v in (enriched_flat or {}).items():
        nf = _norm_key_for_match(k)
        lf = _norm_key_for_match(k.split(".")[-1])
        enriched_index.setdefault(nf, []).append(v)
        if lf != nf:
            enriched_index.setdefault(lf, []).append(v)

    def _first_enriched_for(norm_key):
        if norm_key in auth_map_norm and auth_map_norm[norm_key] not in (None, ""):
            return auth_map_norm[norm_key]
        for v in enriched_index.get(norm_key, []):
            if v not in (None, ""):
                return v
        return None

    mismatch_fields = []
    enriched_fields = []

    # mismatches based only on uploaded+seeded keys
    for norm_key in set(uploaded_map.keys()):
        up_val = uploaded_map.get(norm_key)
        ev = _first_enriched_for(norm_key)

        if (up_val in (None, "")) and (ev in (None, "")):
            continue

        same = False
        if ev is not None and up_val is not None:
            same = _smart_equal(norm_key, str(up_val), str(ev))

        if not same:
            if ev is None and up_val:
                # enriched missing -> ignore for impacts
                pass
            elif ev is not None and (up_val is None or up_val == ""):
                enriched_fields.append(norm_key)
            else:
                mismatch_fields.append(norm_key)

    # LP-only enrichment where upload had nothing
    for k in list(enriched_index.keys()):
        if k.startswith("linked_party_full_name_") or "dob" in k:
            if k not in uploaded_map and _first_enriched_for(k) not in (None, ""):
                enriched_fields.append(k)

    # Generic enrichment: any meaningful bundle field not uploaded (ignore boilerplate)
    for k, v in auth_map_norm.items():
        if k not in uploaded_map and k not in _ENRICH_IGNORE and _is_meaningful(v):
            enriched_fields.append(k)

    # De-dup & sort for neatness
    mismatch_fields = sorted(set(mismatch_fields))
    enriched_fields = sorted(set(enriched_fields))

    return mismatch_fields, enriched_fields, True

def _compare_impacts_detailed(row):
    """
    Returns:
      mismatch_pairs: list[(field, uploaded_value, enriched_value)]
      enriched_pairs: list[(field, uploaded_value, enriched_value)]
      bundle_ok: bool
    Uses the same visible-compare logic as _record_compare_rollup, but captures values.
    """
    # local safe getter
    def _rg(r, k, default=None):
        try:
            return r[k]
        except Exception:
            try:
                return r.get(k, default)  # type: ignore[attr-defined]
            except Exception:
                return default

    # ---- uploaded map (cleaned)
    uploaded_map = {}
    for h in ALL_SCHEMA_FIELDS:
        try:
            v = row[h]
        except Exception:
            v = None
        cv = _clean_cell(v)
        if cv is not None:
            uploaded_map[_norm_key_for_match(h)] = cv

    # seeds
    in_name = _clean_cell(_rg(row, "input_name"))
    if "entity_name" not in uploaded_map and in_name:
        uploaded_map["entity_name"] = in_name
    client_pc = _clean_cell(_rg(row, "client_address_postcode"))
    if "entity_primary_address_postcode" not in uploaded_map and client_pc:
        uploaded_map["entity_primary_address_postcode"] = client_pc
    client_ctry = _clean_cell(_rg(row, "client_address_country"))
    if "entity_primary_address_country" not in uploaded_map and client_ctry:
        uploaded_map["entity_primary_address_country"] = client_ctry

    # ---- bundle
    enrich_path = (
        _rg(row, "enrich_json_path")
        or _rg(row, "enriched_json_path")
        or _rg(row, "bundle_path")
        or _rg(row, "auto_detail_path")
    )
    bundle = _safe_read_json(enrich_path) if enrich_path else {}

    # if no readable bundle, signal to caller
    if not bundle:
        return [], [], False

    # focus + flatten (same as rollup)
    enriched_focus = {}
    for root in ("profile", "officers", "pscs", "charges", "trustees", "filings", "sources"):
        if root in bundle:
            enriched_focus[root] = bundle[root]
    try:
        enriched_focus.setdefault("_derived", {})
        enriched_focus["_derived"]["counts.officers"] = len((bundle.get("officers") or {}).get("items") or [])
        enriched_focus["_derived"]["counts.pscs"] = len((bundle.get("pscs") or {}).get("items") or [])
        enriched_focus["_derived"]["counts.charges"] = len((bundle.get("charges") or {}).get("items") or [])
        enriched_focus["_derived"]["counts.trustees"] = len(bundle.get("trustees") or [])
        enriched_focus["_derived"]["counts.filings"] = len(bundle.get("filings") or [])
    except Exception:
        pass

    enriched_flat = _flatten_enriched(enriched_focus) if enriched_focus else {}

    # authoritative lookup
    reg = _rg(row, "resolved_registry") or ""
    is_ch = reg.startswith("Companies House")
    is_cc = "Charity Commission" in reg
    auth_map, _ = _authoritative_map(bundle, is_ch=is_ch, is_cc=is_cc)
    auth_map_norm = { _norm_key_for_match(k): v for k, v in auth_map.items() }

    # quick index
    enriched_index = {}
    for k, v in (enriched_flat or {}).items():
        nf = _norm_key_for_match(k)
        lf = _norm_key_for_match(k.split(".")[-1])
        enriched_index.setdefault(nf, []).append(v)
        if lf != nf:
            enriched_index.setdefault(lf, []).append(v)

    def _first_enriched_for(norm_key):
        if norm_key in auth_map_norm and auth_map_norm[norm_key] not in (None, ""):
            return auth_map_norm[norm_key]
        for v in enriched_index.get(norm_key, []):
            if v not in (None, ""):
                return v
        return None

    mismatch_pairs = []
    enriched_pairs = []

    # mismatches based only on uploaded+seeded keys
    for norm_key in set(uploaded_map.keys()):
        up_val = uploaded_map.get(norm_key)
        ev_raw = _first_enriched_for(norm_key)
        ev = _clean_cell(ev_raw)  # normalise enriched side too

        if (up_val in (None, "")) and (ev in (None, "")):
            continue

        same = False
        if ev is not None and up_val is not None:
            same = _smart_equal(norm_key, str(up_val), str(ev))

        if not same:
            if ev is None and up_val:
                # enriched missing -> ignore for impacts
                pass
            elif ev is not None and (up_val is None or up_val == ""):
                enriched_pairs.append((norm_key, up_val or "", ev))
            else:
                mismatch_pairs.append((norm_key, up_val or "", ev or ""))

    # LP-only enrichment where upload had nothing
    for k in list(enriched_index.keys()):
        if k.startswith("linked_party_full_name_") or "dob" in k:
            if k not in uploaded_map:
                ev_raw = _first_enriched_for(k)
                ev = _clean_cell(ev_raw)
                if ev not in (None, ""):
                    enriched_pairs.append((k, "", ev))

    # Generic enrichment: any meaningful bundle field not uploaded (ignore boilerplate)
    for k, v in auth_map_norm.items():
        if k not in uploaded_map and k not in _ENRICH_IGNORE:
            ev = _clean_cell(v)
            if ev not in (None, ""):
                enriched_pairs.append((k, "", ev))

    # De-dup while preserving first occurrence; then sort by field for neatness
    def _dedup_pairs(pairs):
        seen = set()
        out = []
        for f, a, b in pairs:
            key = (f, a, b)
            if key not in seen:
                seen.add(key)
                out.append((f, a, b))
        return sorted(out, key=lambda x: x[0])

    mismatch_pairs = _dedup_pairs(mismatch_pairs)
    enriched_pairs = _dedup_pairs(enriched_pairs)

    return mismatch_pairs, enriched_pairs, True

from typing import Optional, Any, List, Dict
from fastapi import Query
from fastapi.responses import FileResponse
import pandas as pd
import tempfile, os
from datetime import datetime


@app.get("/reports/export")
def export_report(
    request: Request,
    q: str = Query("", description="free-text filter on input/entity name"),
    registry: Optional[str] = Query(None, description="e.g. 'Companies House'"),
    only_flagged: int = Query(0, description="1 = only rows with mismatch or enrichment"),
):
    # WHERE clause
    where_sql = "1=1"
    params: List[Any] = []
    if q:
        where_sql += " AND (COALESCE(input_name,'') LIKE ? OR COALESCE(entity_name,'') LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if registry:
        where_sql += " AND COALESCE(resolved_registry,'') = ?"
        params += [registry]

    # Read ALL columns
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM items
            WHERE {where_sql}
            ORDER BY created_at ASC
            """,
            tuple(params),
        ).fetchall()

    out_rows: List[Dict[str, Any]] = []

    for r in rows:
        row = dict(r)

        # compute flags (existing roll-up)
        roll = _record_compare_rollup(row)

        # compute detailed impacts and ensure bundle exists
        mismatch_pairs, enriched_pairs, bundle_ok = _compare_impacts_detailed(row)
        if not bundle_ok:
            continue

        if only_flagged and not (roll.get("has_mismatch") or roll.get("has_enrichment") or roll.get("potential_risk")):
            continue

        # format details for Excel
        def fmt_pairs(pairs):
            # field: "uploaded" → "enriched"
            parts = []
            for f, a, b in (pairs or []):
                a_s = str(a) if a is not None else ""
                b_s = str(b) if b is not None else ""
                parts.append(f'{f}: "{a_s}" → "{b_s}"')
            return "; ".join(parts)

        # Process shareholder information
        shareholder_info = ""
        parent_company_identified = "N"
        shareholders_json = row.get("shareholders_json")
        if shareholders_json:
            try:
                all_shareholders = json.loads(shareholders_json)
                regular_shareholders, parent_shareholders = identify_parent_companies(all_shareholders)

                # Format shareholder information for display
                shareholder_details = []

                def format_shareholder_info(shareholders, category):
                    """Format shareholder info including name, shares, and percentage"""
                    formatted_list = []
                    for s in shareholders:
                        name = s.get("name", "")
                        shares = s.get("shares_held", "")
                        percentage = s.get("percentage", "")

                        if name:
                            parts = [name]
                            if shares:
                                parts.append(f"{shares} shares")
                            if percentage:
                                parts.append(f"{percentage}%")
                            formatted_list.append(" - ".join(parts))

                    return f"{category}: {', '.join(formatted_list)}" if formatted_list else ""

                if regular_shareholders:
                    regular_info = format_shareholder_info(regular_shareholders, "Regular")
                    if regular_info:
                        shareholder_details.append(regular_info)

                if parent_shareholders:
                    parent_info = format_shareholder_info(parent_shareholders, "Parent")
                    if parent_info:
                        shareholder_details.append(parent_info)

                shareholder_info = "; ".join(shareholder_details)
                parent_company_identified = "Y" if parent_shareholders else "N"
            except Exception as e:
                shareholder_info = f"Error parsing shareholders: {str(e)}"
                parent_company_identified = "N"

        out_rows.append({
            # core identification
            "id": row.get("id"),
            "created": row.get("created_at"),
            "input_name": row.get("input_name"),
            "entity_name": row.get("entity_name"),
            "registry": row.get("resolved_registry"),
            "reference_number": row.get("company_number") or row.get("charity_number") or "",
            # NEW: human-readable diffs your client can act on
            "mismatch_details": fmt_pairs(mismatch_pairs),
            "enriched_details": fmt_pairs(enriched_pairs),
            # NEW: shareholder information
            "shareholder_info": shareholder_info,
            "parent_company_identified": parent_company_identified,
            # flags LAST for easy filtering
            "has_mismatch": "Y" if roll.get("has_mismatch") else "N",
            "has_enrichment": "Y" if roll.get("has_enrichment") else "N",
            "potential_screening_risk": "Y" if roll.get("potential_risk") else "N",
        })

    if not out_rows:
        out_rows = [{
            "id": None, "created": None, "input_name": None, "entity_name": None,
            "registry": None, "reference_number": None,
            "mismatch_details": None, "enriched_details": None,
            "shareholder_info": None, "parent_company_identified": "N",
            "has_mismatch": "N", "has_enrichment": "N", "potential_screening_risk": "N"
        }]

    df = pd.DataFrame(out_rows)

    fname = f"scrutinise_enriched_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    tmpdir = tempfile.mkdtemp(prefix="export_")
    fpath = os.path.join(tmpdir, fname)

    with pd.ExcelWriter(fpath, engine="xlsxwriter") as writer:
        df.to_excel(writer, sheet_name="Sheet 1", index=False)

    return FileResponse(
        fpath,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=fname,
    )

@app.get("/item/{item_id}", response_class=HTMLResponse)
def review_item(request: Request, item_id: int):
    with db() as conn:
        item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not item:
        return RedirectResponse(url="/queue/manual", status_code=303)

    candidates = json.loads(item["candidates_json"] or "[]")
    print("[DEBUG UI] item", item_id, "candidates:", len(candidates), "sample:", (candidates[0] if candidates else None))

    # ---------- helpers ----------
    def _nz(v):
        return v is not None and str(v).strip() != ""

    def _guess_ccew_url(c: dict) -> Optional[str]:
        for k in ("candidate_source_url", "source_url", "url", "link", "href"):
            v = c.get(k)
            if _nz(v):
                return str(v)
        num = (
            c.get("charity_number")
            or c.get("candidate_charity_number")
            or c.get("registered_charity_number")
            or c.get("registeredCharityNumber")
        )
        if _nz(num):
            try:
                return f"https://register-of-charities.charitycommission.gov.uk/charity-details/?regId={int(str(num))}&subId=0"
            except Exception:
                pass
        return None

    # ---------- Build UI-friendly candidate dicts ----------
    ui_candidates = []
    for c in candidates:
        ref = (
            c.get("charity_number")
            or c.get("candidate_charity_number")
            or c.get("registered_charity_number")
            or c.get("registeredCharityNumber")
            or c.get("candidate_company_number")
            or c.get("company_number")
        )

        reg_raw = c.get("candidate_registry") or c.get("registry")
        reg = canonical_registry_name(reg_raw)

        looks_charity = (
            reg == "Charity Commission"
            or str(ref or "").upper().startswith("CC-")
            or _nz(c.get("charity_number"))
            or _nz(c.get("registered_charity_number"))
            or _nz(c.get("registeredCharityNumber"))
        )
        source_label = "Charity Commission" if looks_charity else "Companies House"

        open_url = (
            c.get("candidate_source_url")
            or c.get("source_url")
            or (_guess_ccew_url(c) if looks_charity else None)
        )

        ui_candidates.append({
            **c,
            "ui_ref": ref,
            "ui_ref_label": "Ref/No." if ref else None,
            "ui_source_label": source_label,
            "ui_open_url": open_url,
            "ui_registry": reg or ("Charity Commission" if looks_charity else None),
        })

    # sort for display (confidence desc, None last)
    def _ui_sort_key(x):
        v = x.get("candidate_confidence")
        return (0, -float(v)) if isinstance(v, (int, float)) else (1, 0)
    ui_candidates.sort(key=_ui_sort_key)

    # ---------- Client Provided: headline fields ----------
    client_info = []
    if _nz(item["client_ref"]):
        client_info.append({"label": "Reference", "value": item["client_ref"]})
    if _nz(item["client_address"]):
        client_info.append({"label": "Address on file", "value": item["client_address"]})
    if _nz(item["client_address_city"]):
        client_info.append({"label": "City", "value": item["client_address_city"]})
    if _nz(item["client_address_postcode"]):
        client_info.append({"label": "Postcode", "value": item["client_address_postcode"]})
    if _nz(item["client_address_country"]):
        client_info.append({"label": "Country", "value": item["client_address_country"]})

    # Linked parties (stored as JSON string)
    linked_parties_list = []
    if _nz(item["client_linked_parties"]):
        try:
            parsed = json.loads(item["client_linked_parties"])
            if isinstance(parsed, list):
                linked_parties_list = [str(p) for p in parsed if _nz(p)]
            else:
                linked_parties_list = [str(parsed)]
        except Exception:
            linked_parties_list = [str(item["client_linked_parties"])]
    if linked_parties_list:
        client_info.append({"label": "Linked Parties", "value": " · ".join(linked_parties_list)})

    if _nz(item["client_notes"]):
        client_info.append({"label": "Notes", "value": item["client_notes"]})

    # ---------- Full exact upload payload (non-empty only) ----------
    schema_fields = []
    for header in ALL_SCHEMA_FIELDS:
        try:
            val = item[header]
        except Exception:
            continue
        if _nz(val):
            schema_fields.append({"header": header, "value": str(val)})

    return templates.TemplateResponse(
        "item_review.html",
        {
            "request": request,
            "item": item,
            "candidates": ui_candidates,
            "client_info": client_info,
            "schema_fields": schema_fields,
        },
    )

@app.post("/item/{item_id}/confirm", response_class=HTMLResponse)
def confirm_item(request: Request, item_id: int, selection: str = Form(...)):
    if selection == "UNABLE|UNABLE":
        with db() as conn:
            conn.execute(
                "UPDATE items SET pipeline_status='error', match_type='Manual review', reason=? WHERE id=?",
                ("Unable to match", item_id),
            )
        return RedirectResponse(url="/queue/manual", status_code=303)

    try:
        company_number, entity_name = selection.split("|", 1)
    except ValueError:
        return RedirectResponse(url=f"/item/{item_id}", status_code=303)

    inferred = _infer_registry_from_company_number(company_number)  # 'companies_house' or None

    with db() as conn:
        row = conn.execute("SELECT resolved_registry, candidates_json, source_url FROM items WHERE id=?", (item_id,)).fetchone()
        current_registry = row["resolved_registry"] if row else None
        new_registry = canonical_registry_name(inferred) or canonical_registry_name(current_registry)

        # discover charity number if CC
        charity_number = None
        if new_registry == "Charity Commission":
            try:
                cands = json.loads(row["candidates_json"] or "[]")
            except Exception:
                cands = []
            charity_number = _extract_charity_number(
                {"source_url": row["source_url"]},  # base-ish
                cands
            )

        should_queue_ch = (new_registry == "Companies House" and (company_number or "").strip() != "")
        should_queue_cc = (new_registry == "Charity Commission" and (charity_number or "").strip() != "")

        enrich_status = "queued" if (should_queue_ch or should_queue_cc) else "skipped"

        conn.execute(
            """
            UPDATE items
               SET pipeline_status='auto',
                   match_type='Manual confirm',
                   entity_name=?,
                   company_number=?,
                   charity_number=?,
                   resolved_registry=?,
                   enrich_status=?
             WHERE id=?
            """,
            (entity_name, company_number or None, (str(charity_number).strip() or None),
             new_registry, enrich_status, item_id),
        )

    if should_queue_ch:
        enqueue_enrich(item_id)
    elif should_queue_cc:
        enqueue_enrich_charity(item_id)

    return RedirectResponse(url="/queue/auto", status_code=303)

# ---------------- No-referrer redirectors for CH links ----------------
@app.get("/go/ch/{company_number}", response_class=HTMLResponse)
def go_ch_company(company_number: str):
    dest = ch_company_url(company_number)
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="referrer" content="no-referrer">
  <meta http-equiv="refresh" content="0;url={dest}">
  <title>Redirecting…</title>
</head>
<body>
  <p>Redirecting to Companies House… If not redirected, <a href="{dest}">click here</a>.</p>
  <script>location.replace("{dest}");</script>
</body>
</html>"""
    return HTMLResponse(content=html, headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})

@app.get("/go/url", response_class=HTMLResponse)
def go_url(path: str):
    path = path if path.startswith("/") else f"/{path}"
    dest = f"https://{CH_HOST}{path}"
    html = f"""<!doctype html>
<meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<title>Redirecting…</title>
<script>location.replace("{dest}");</script>
<p>Redirecting… <a href="{dest}">continue</a></p>"""
    return HTMLResponse(content=html, headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})

@app.get("/shareholders", response_class=HTMLResponse)
def shareholder_test_page():
    """Shareholder extraction test page."""
    return templates.TemplateResponse("shareholder_test.html", {"request": {}})

# ---------------- Filing History & Document APIs ----------------
@app.get("/api/company/{company_number}/filing-history")
def get_filing_history(company_number: str, category: str = None, items_per_page: int = None, start_index: int = None):
    """Get filing history for a company with optional filters."""
    try:
        category = "confirmation-statement"
        result = get_company_filing_history(company_number, category=category, items_per_page=items_per_page, start_index=start_index)
        return result
    except Exception as e:
        return {"error": str(e), "company_number": company_number}

@app.get("/api/company/{company_number}/filing-history/{transaction_id}")
def get_filing_detail_endpoint(company_number: str, transaction_id: str):
    """Get detailed metadata for a specific filing."""
    try:
        result = get_filing_detail(company_number, transaction_id)
        return result
    except Exception as e:
        return {"error": str(e), "company_number": company_number, "transaction_id": transaction_id}

@app.get("/api/company/{company_number}/shareholders")
def get_shareholders_endpoint(company_number: str):
    """Extract shareholder information for a company using intelligent CS01 -> AR01 fallback."""
    try:
        result = extract_shareholders_for_company(company_number)
        return {
            "company_number": company_number,
            "shareholders": result.get("shareholders", []),
            "count": len(result.get("shareholders", [])),
            "extraction_status": result.get("extraction_status", ""),
            "cs01_found": result.get("cs01_found", False),
            "cs01_has_shareholders": result.get("cs01_has_shareholders", False),
            "ar01_found": result.get("ar01_found", False),
            "ar01_has_shareholders": result.get("ar01_has_shareholders", False)
        }
    except Exception as e:
        return {
            "error": str(e),
            "company_number": company_number,
            "shareholders": [],
            "count": 0,
            "extraction_status": "extraction_error"
        }

@app.get("/api/document/{document_id}/metadata")
def get_document_metadata_endpoint(document_id: str):
    """Get metadata for a document."""
    try:
        result = get_document_metadata(document_id)
        return result
    except Exception as e:
        return {"error": str(e), "document_id": document_id}

@app.get("/api/document/{document_id}/content")
def download_document_content(document_id: str):
    """Download the actual PDF content."""
    try:
        pdf_content = download_cs01_pdf(document_id)
        return StreamingResponse(
            io.BytesIO(pdf_content),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={document_id}.pdf"}
        )
    except Exception as e:
        return {"error": str(e), "document_id": document_id}

@app.get("/api/company/{company_number}/cs01-filings")
def get_cs01_filings(company_number: str):
    """Get all CS01 filings for a company with document IDs."""
    try:
        result = get_cs01_filings_for_company(company_number)
        return {"company_number": company_number, "cs01_filings": result}
    except Exception as e:
        return {"error": str(e), "company_number": company_number}

@app.get("/api/company/{company_number}/psc-data")
def get_psc_data(company_number: str):
    """Get PSC data for a company to debug PSC fallback."""
    try:
        from resolver import get_company_bundle
        bundle = get_company_bundle(company_number)
        psc_data = bundle.get("pscs", {})
        psc_items = psc_data.get("items", [])
        
        return {
            "company_number": company_number,
            "psc_count": len(psc_items),
            "psc_data": psc_data,
            "bundle_keys": list(bundle.keys()),
            "debug": {
                "has_pscs_key": "pscs" in bundle,
                "has_items": bool(psc_items),
                "first_psc": psc_items[0] if psc_items else None
            }
        }
    except Exception as e:
        import traceback
        return {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "company_number": company_number
        }

@app.get("/api/search/companies")
def search_companies(q: str, items_per_page: int = 5):
    """Search Companies House by company name - for debugging recursive lookup."""
    try:
        from resolver import search_companies_house
        results = search_companies_house(q, items_per_page)
        
        return {
            "query": q,
            "result_count": len(results) if results else 0,
            "results": results,
            "debug": {
                "query_length": len(q),
                "items_requested": items_per_page
            }
        }
    except Exception as e:
        import traceback
        return {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "query": q
        }

# ---------------- Downloads & health ----------------
@app.get("/download")
def download(path: str):
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(os.path.abspath(RESULTS_BASE)) or not os.path.isfile(abs_path):
        return RedirectResponse(url="/")
    return FileResponse(abs_path, filename=os.path.basename(abs_path))

@app.get("/health")
def health():
    return {"status": "healthy"}