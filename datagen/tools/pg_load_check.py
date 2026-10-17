#!/usr/bin/env python3
"""
Load a staged slice into a local PostgreSQL exactly the way the setup VM will (schema files, extensions, COPY of each
headerless CSV, constraints whose tables are present, index files, ANALYZE), time every step, and run the plant
queries in PostgreSQL SQL. A developer check; the real timing on AlloyDB is Stage 4.

    python3 tools/pg_load_check.py out/slices/aldb003_agents --db cf_check --psql "psql -U postgres -h localhost"
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path


def run(psql: list[str], db: str, sql: str) -> str:
    r = subprocess.run(psql + ["-d", db, "-v", "ON_ERROR_STOP=1", "-q", "-At", "-c", sql], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:2000])
    return r.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slice_dir")
    ap.add_argument("--db", default="cf_check")
    ap.add_argument("--psql", default="psql -U postgres -h localhost")
    ap.add_argument("--skip-corpus", action="store_true", help="skip vector tables (no pgvector locally)")
    a = ap.parse_args()
    psql = shlex.split(a.psql)
    sd = Path(a.slice_dir)
    m = json.loads((sd / "manifest.json").read_text())
    times = {}
    run(psql, "postgres", f"DROP DATABASE IF EXISTS {a.db}")
    run(psql, "postgres", f"CREATE DATABASE {a.db}")
    t = time.time()
    avail = set(run(psql, a.db, "SELECT name FROM pg_available_extensions").split())
    for e in m["extensions"]:
        if a.skip_corpus and e != "pg_trgm":
            continue
        if e not in avail:          # alloydb_scann, google_ml_integration: AlloyDB only
            print(f"extension {e}: not available locally, skipped (AlloyDB only)")
            continue
        run(psql, a.db, f"CREATE EXTENSION IF NOT EXISTS {e} CASCADE")
    for f in m["schema_files"]:
        if a.skip_corpus and "corpus" in f:
            continue
        r = subprocess.run(psql + ["-d", a.db, "-v", "ON_ERROR_STOP=1", "-q", "-f", str((sd / f).resolve())], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr[:2000])
    times["schema"] = round(time.time() - t, 2)
    total = time.time()
    for name in m["load_order"]:
        tm = m["tables"][name]
        if a.skip_corpus and tm["has_vectors"]:
            continue
        t = time.time()
        cols = ", ".join(tm["column_order"])
        for f in tm["files"]:
            p = (sd / f["file"]).resolve()
            run(psql, a.db, f"\\copy {name} ({cols}) FROM PROGRAM 'gzip -dc {p}' WITH (FORMAT csv)")
        n = int(run(psql, a.db, f"SELECT count(*) FROM {name}"))
        assert n == tm["rows"], f"{name}: loaded {n}, manifest says {tm['rows']}"
        times[f"copy {name}"] = round(time.time() - t, 2)
    times["copy total"] = round(time.time() - total, 1)
    t = time.time()
    for c in m["constraints"]:
        run(psql, a.db, c["sql"])
    times["constraints"] = round(time.time() - t, 1)
    t = time.time()
    for f in m["index_files"]:
        r = subprocess.run(psql + ["-d", a.db, "-v", "ON_ERROR_STOP=1", "-q", "-f", str((sd / f).resolve())], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr[:2000])
    run(psql, a.db, "ANALYZE")
    times["indexes + analyze"] = round(time.time() - t, 1)
    # a re-run must be safe: apply constraints and indexes again
    for c in m["constraints"]:
        run(psql, a.db, c["sql"])
    for f in m["index_files"]:
        subprocess.run(psql + ["-d", a.db, "-q", "-f", str((sd / f).resolve())], capture_output=True, text=True, check=True)
    checks = {}
    tables = set(m["tables"])
    if {"inbound_lots", "batch_inputs", "order_lines", "orders"} <= tables:
        checks["P8 trace"] = run(psql, a.db, """
            WITH RECURSIVE lot AS (SELECT lot_id FROM inbound_lots WHERE lot_code = 'L-ES-2608-0117'),
            b AS (SELECT DISTINCT batch_id FROM batch_inputs JOIN lot USING (lot_id))
            SELECT count(DISTINCT o.account_id) || ' restaurants, ' || count(DISTINCT o.customer_id) || ' consumers, '
                   || count(DISTINCT o.location_id) FILTER (WHERE o.channel = 'store') || ' stores'
            FROM order_lines l JOIN b USING (batch_id) JOIN orders o USING (order_id)""")
    if {"orders"} <= tables:
        checks["P15 peak day"] = run(psql, a.db, """SELECT d || ': ' || n FROM (SELECT (ordered_at AT TIME ZONE 'America/New_York')::date d, count(*) n
            FROM orders WHERE channel = 'online' GROUP BY 1) x ORDER BY n DESC, d LIMIT 1""")
        checks["F1 ring"] = run(psql, a.db, """SELECT count(*) FROM (SELECT ship_address_hash FROM orders WHERE channel = 'online'
            AND ordered_at >= '2026-11-01' GROUP BY 1 HAVING count(DISTINCT customer_id) >= 5) x""")
    if {"inventory_positions", "skus", "products"} <= tables:
        checks["P13 Saigon at DC-CE"] = run(psql, a.db, """SELECT coalesce(sum(on_hand), 0) FROM inventory_positions ip JOIN skus USING (sku_id)
            JOIN products p USING (product_id) WHERE p.ingredient_id = 'cinnamon_saigon' AND ip.location_id = 'DC-CE'""")
    checks["array literal round trip"] = run(psql, a.db, "SELECT aliases FROM ingredients WHERE ingredient_id = 'cinnamon_saigon'")
    checks["comment on a column"] = run(psql, a.db, "SELECT col_description('contract_prices'::regclass, 1)")
    checks["db size"] = run(psql, a.db, "SELECT pg_size_pretty(pg_database_size(current_database()))")
    print(json.dumps(dict(slice=m["slice"], rows=m["totals"]["rows"], times=times, checks=checks), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
