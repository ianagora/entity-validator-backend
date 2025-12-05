# batch_resolver.py
# Batch entity resolution across multiple registries (CH, Charity Commission, etc.)
# Uses your updated resolver.py (resolve_company) which is registry-aware.

from resolver import resolve_company  # <- must return {'resolved': {...}, 'candidates': [...], 'registry': ...}

import os
import sys
import json
import time
import argparse
import pandas as pd
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed


# -----------------------
# Helpers
# -----------------------
def now_utc() -> str:
    return datetime.utcnow().isoformat() + "Z"


def read_inputs(path: str) -> pd.DataFrame:
    """Read CSV/XLSX and normalize to have a 'name' column (+ optional hints)."""
    if path.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)

    # Standardize the name column
    for pref in ["name", "entity_name", "subject_name", "company", "company_name"]:
        if pref in df.columns:
            df.rename(columns={pref: "name"}, inplace=True)
            break
    if "name" not in df.columns:
        # Fallback: first column is the name
        df.rename(columns={df.columns[0]: "name"}, inplace=True)

    # Optional hints (kept for future extensions)
    for optional in ["entity_type_hint", "postcode", "incorporation_year"]:
        if optional not in df.columns:
            df[optional] = None

    return df


def safe_resolve(row: pd.Series, top_n: int = 3):
    """Call resolve_company() safely and return (base_row, candidate_rows)."""
    name = str(row.get("name") or "").strip()
    if not name:
        return (
            {
                "input_name": "",
                "status": "error",
                "error_message": "empty_name",
                "retrieved_at": now_utc(),
            },
            [],
        )

    try:
        result = resolve_company(name, top_n=top_n) or {}
        resolved = result.get("resolved") or {}

        base = {
            "input_name": name,
            "registry": result.get("registry"),        # e.g. 'companies_house' or 'charity_commission'
            "search_url": result.get("search_url"),
            "retrieved_at": result.get("retrieved_at"),
            "status": resolved.get("status"),          # 'auto' | 'manual_required' | 'error'
            "match_type": resolved.get("match_type"),
            "entity_name": resolved.get("entity_name"),
            "company_number": resolved.get("company_number"),   # may include registry prefix (e.g. CC-123456)
            "company_status": resolved.get("company_status"),
            "confidence": resolved.get("confidence"),
            "reason": resolved.get("reason"),
            "source_url": resolved.get("source_url"),
        }

        # Candidates for manual review
        cand_rows = []
        if (resolved.get("status") or "").lower() != "auto":
            for cand in result.get("candidates") or []:
                cand_rows.append(
                    {
                        "input_name": name,
                        "candidate_registry": cand.get("registry") or result.get("registry"),
                        "candidate_entity_name": cand.get("entity_name"),
                        "candidate_company_number": cand.get("company_number"),
                        "candidate_status": cand.get("company_status") or cand.get("status"),
                        "candidate_address": cand.get("address"),
                        "candidate_confidence": cand.get("confidence"),
                        "candidate_source_url": cand.get("source_url"),
                        "retrieved_at": cand.get("retrieved_at") or result.get("retrieved_at"),
                    }
                )

        return base, cand_rows

    except Exception as e:
        return (
            {
                "input_name": name,
                "registry": None,
                "status": "error",
                "reason": str(e),
                "retrieved_at": now_utc(),
            },
            [],
        )


def run_batch(input_path: str, out_dir: str, workers: int, top_n: int, throttle_per_sec: float):
    """Resolve all names from input file, write CSV + JSONL into results/<YYYY-MM-DD>/."""
    df = read_inputs(input_path)

    jobs = []
    results = []
    candidates = []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for _, row in df.iterrows():
            jobs.append(ex.submit(safe_resolve, row, top_n))

        # Optional gentle throttle (helps with external rate limits if you raise workers)
        last_emit = time.time()
        for fut in as_completed(jobs):
            base, cand_rows = fut.result()
            results.append(base)
            if cand_rows:
                candidates.extend(cand_rows)

            if throttle_per_sec > 0:
                elapsed = time.time() - last_emit
                to_sleep = max(0.0, (1.0 / throttle_per_sec) - elapsed)
                if to_sleep > 0:
                    time.sleep(to_sleep)
                last_emit = time.time()

    # --- ensure results/<YYYY-MM-DD>/ exists ---
    date_folder = date.today().isoformat()  # e.g., 2025-08-28
    out_path = os.path.join(out_dir, date_folder)
    os.makedirs(out_path, exist_ok=True)

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    res_path = os.path.join(out_path, f"resolutions_{ts}.csv")
    cand_path = os.path.join(out_path, f"candidates_{ts}.csv")
    jsonl_path = os.path.join(out_path, f"resolutions_{ts}.jsonl")

    # Write primary results CSV
    if results:
        pd.DataFrame(results).to_csv(res_path, index=False)
    else:
        # still create an empty file with headers for consistency
        pd.DataFrame(
            columns=[
                "input_name",
                "registry",
                "search_url",
                "retrieved_at",
                "status",
                "match_type",
                "entity_name",
                "company_number",
                "company_status",
                "confidence",
                "reason",
                "source_url",
            ]
        ).to_csv(res_path, index=False)

    # Write candidates CSV (only if there are any)
    if candidates:
        pd.DataFrame(candidates).to_csv(cand_path, index=False)
    else:
        cand_path = None

    # Write JSONL (one line per result)
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    return res_path, cand_path, jsonl_path


# -----------------------
# CLI
# -----------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Batch entity resolution using a multi-registry resolver (Companies House, Charity Commission, etc.)"
    )
    ap.add_argument("input_path", help="CSV/XLSX with a 'name' column (or first column used as name)")
    ap.add_argument("--out-dir", default="results", help="Base output directory (default: results/)")
    ap.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    ap.add_argument("--top", type=int, default=3, help="Top-N candidates to keep for manual review (default: 3)")
    ap.add_argument(
        "--throttle",
        type=float,
        default=2.0,
        help="Approx results/sec (simple throttle). Use 0 to disable (default: 2.0).",
    )
    args = ap.parse_args()

    res_path, cand_path, jsonl_path = run_batch(
        args.input_path, args.out_dir, args.workers, args.top, args.throttle
    )
    print(f"Wrote: {res_path}")
    if cand_path:
        print(f"Wrote: {cand_path}")
    print(f"Wrote: {jsonl_path}")