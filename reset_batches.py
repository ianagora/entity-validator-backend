#!/usr/bin/env python3
"""
Reset batch imports for the Scrutinise MVP.

Deletes rows from `items` and `runs` tables in entity_workflow.db.
Optionally purges the results/ directory on disk.

Usage examples:
  python3 reset_batches.py --all
  python3 reset_batches.py --all --purge-results
  python3 reset_batches.py --run-id 12
  python3 reset_batches.py --before 2025-08-28
  python3 reset_batches.py --dry-run --all
"""

import os
import sys
import argparse
import sqlite3
from datetime import datetime
from contextlib import contextmanager
from shutil import rmtree

DEFAULT_DB = "entity_workflow.db"
DEFAULT_RESULTS_DIR = "results"

@contextmanager
def db_conn(path: str):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        # Ensure FK behaviour if you add ON DELETE in future
        conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
        conn.commit()
    finally:
        conn.close()

def count_rows(conn, table: str, where: str = "", params: tuple = ()):
    q = f"SELECT COUNT(*) AS c FROM {table} {('WHERE ' + where) if where else ''}"
    return conn.execute(q, params).fetchone()["c"]

def delete_by_run_id(conn, run_id: int) -> tuple[int, int]:
    items_before = count_rows(conn, "items", "run_id = ?", (run_id,))
    conn.execute("DELETE FROM items WHERE run_id = ?", (run_id,))
    runs_before = count_rows(conn, "runs", "id = ?", (run_id,))
    conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
    return items_before, runs_before

def delete_before_date(conn, cutoff_iso: str) -> tuple[int, int]:
    # cutoff_iso should be YYYY-MM-DD (we match prefix)
    run_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM runs WHERE substr(created_at,1,10) <= ?", (cutoff_iso,)
    ).fetchall()]
    items_deleted = 0
    runs_deleted = 0
    for rid in run_ids:
        i, r = delete_by_run_id(conn, rid)
        items_deleted += i
        runs_deleted += r
    return items_deleted, runs_deleted

def delete_all(conn) -> tuple[int, int]:
    items_before = count_rows(conn, "items")
    runs_before = count_rows(conn, "runs")
    conn.execute("DELETE FROM items")
    conn.execute("DELETE FROM runs")
    return items_before, runs_before

def purge_results_dir(results_dir: str):
    if os.path.isdir(results_dir):
        rmtree(results_dir)
        os.makedirs(results_dir, exist_ok=True)

def main():
    ap = argparse.ArgumentParser(description="Reset/Delete batch imports from the local SQLite DB.")
    ap.add_argument("--db", default=os.getenv("DB_PATH", DEFAULT_DB), help=f"Path to SQLite DB (default: {DEFAULT_DB})")
    ap.add_argument("--results-dir", default=DEFAULT_RESULTS_DIR, help=f"Results directory to purge (default: {DEFAULT_RESULTS_DIR})")

    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="Delete ALL runs and items")
    g.add_argument("--run-id", type=int, help="Delete a specific run and its items")
    g.add_argument("--before", type=str, metavar="YYYY-MM-DD", help="Delete runs with created_at on or before this date")

    ap.add_argument("--purge-results", action="store_true", help="Also purge the results/ directory")
    ap.add_argument("--dry-run", action="store_true", help="Show what would be deleted without making changes")

    args = ap.parse_args()

    if not os.path.isfile(args.db):
        print(f"DB not found at: {args.db}")
        sys.exit(1)

    with db_conn(args.db) as conn:
        total_items = count_rows(conn, "items")
        total_runs = count_rows(conn, "runs")

        print(f"Current DB state: items={total_items}, runs={total_runs}")

        items_del = runs_del = 0

        if args.all:
            if args.dry_run:
                print("[DRY-RUN] Would delete ALL rows from items and runs.")
            else:
                items_del, runs_del = delete_all(conn)
        elif args.run_id is not None:
            if args.dry_run:
                i = count_rows(conn, "items", "run_id = ?", (args.run_id,))
                r = count_rows(conn, "runs", "id = ?", (args.run_id,))
                print(f"[DRY-RUN] Would delete: items={i} (run_id={args.run_id}), runs={r} (id={args.run_id})")
            else:
                items_del, runs_del = delete_by_run_id(conn, args.run_id)
        elif args.before:
            # Validate date format
            try:
                datetime.strptime(args.before, "%Y-%m-%d")
            except ValueError:
                print("Error: --before must be in YYYY-MM-DD format.")
                sys.exit(2)
            if args.dry_run:
                run_ids = [r["id"] for r in conn.execute(
                    "SELECT id FROM runs WHERE substr(created_at,1,10) <= ?", (args.before,)
                ).fetchall()]
                items_count = 0
                for rid in run_ids:
                    items_count += count_rows(conn, "items", "run_id = ?", (rid,))
                print(f"[DRY-RUN] Would delete: items={items_count}, runs={len(run_ids)} (cutoff {args.before})")
            else:
                items_del, runs_del = delete_before_date(conn, args.before)

        if not args.dry_run and args.purge_results:
            print(f"Purging results directory: {args.results_dir}")
            purge_results_dir(args.results_dir)

        # Final state
        final_items = count_rows(conn, "items")
        final_runs = count_rows(conn, "runs")

        if args.dry_run:
            print("No changes applied (dry run).")
        else:
            print(f"Deleted: items={items_del}, runs={runs_del}")
            print(f"Now: items={final_items}, runs={final_runs}")

if __name__ == "__main__":
    main()