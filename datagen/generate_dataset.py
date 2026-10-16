#!/usr/bin/env python3
"""
Generate, validate and slice the Cymbal Foods dataset.

    python3 generate_dataset.py                       # everything at lab scale into ./out
    python3 generate_dataset.py --cut-only            # re-cut slices.yaml from the existing Parquet (no regeneration)
    python3 generate_dataset.py --scale 10 --out out-demo --no-ga4

Deterministic: the same seed (cfgen/config.py) and the same content/ give the same rows, Parquet and CSV bytes,
given the same library versions (recorded in out/manifest.json). See DESIGN.md for what everything means.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--scale", type=float, default=None, help="1 = lab, 10 = demo, 100 = Spanner")
    ap.add_argument("--no-ga4", action="store_true", help="skip the GA4 clickstream")
    ap.add_argument("--cut-only", action="store_true", help="only re-cut slices from the existing Parquet")
    ap.add_argument("--slices", default="all", help="comma-separated slice names, or all")
    ap.add_argument("--skip-validate", action="store_true")
    a = ap.parse_args()
    if a.scale is not None:
        os.environ["CF_SCALE"] = str(a.scale)
    sys.path.insert(0, str(ROOT))
    import numpy as np
    import pandas as pd
    import pyarrow
    import duckdb
    from cfgen import config as C
    from cfgen import (catalog, clickstream, content, demand, facts, fraud, inventory, orders, people, schemas,
                       slicer, supply, validate, writer)
    from cfgen.util import log

    out = Path(a.out)
    pq_dir = out / "parquet"
    t0 = time.time()
    timings, counts, sizes, extra = {}, {}, {}, {}

    def tick(name: str, t: float) -> float:
        timings[name] = round(time.time() - t, 1)
        return time.time()

    if not a.cut_only:
        if pq_dir.exists():
            shutil.rmtree(pq_dir)
        t = time.time()
        log(f"Cymbal Foods dataset, scale {C.SCALE}, seed {C.SEED}")
        c = content.load()
        probs = content.check(c)
        if probs:
            for p in probs:
                print("CONTENT PROBLEM:", p)
            return 2
        cat = catalog.build(c)
        names = people.Names(c)
        places = people.build_places(c, names)
        log(f"catalog: {len(cat['products']):,} products, {len(cat['skus']):,} SKUs")
        cons = people.build_consumers(c, names, places)
        acc, acc_extra = people.build_accounts(c, names, places, cat)
        cp = people.build_contract_prices(acc, acc_extra, cat)
        cons, ring = fraud.make_ring(cons, cat)
        t = tick("catalog_people", t)
        log(f"people: {len(cons):,} consumers, {len(acc):,} accounts, {len(cp):,} contract prices")
        dem = demand.Demand(c, cat)
        O, L = orders.build(c, cat, places, cons, acc, acc_extra, cp, dem, ring)
        # a customer record can't be younger than its first order: accounts made at checkout get created then
        first = O[O.customer_id.notna()].groupby("customer_id").ordered_at.min()
        ca = cons.set_index("customer_id").created_at
        late = first[first < ca.reindex(first.index)]
        from cfgen.util import rng as _rng
        rr = _rng("customers", "created_at_checkout")
        newc = late - pd.to_timedelta(rr.integers(120, 2 * 86400, len(late)), "s")
        cons.loc[cons.customer_id.isin(late.index), "created_at"] = cons.loc[cons.customer_id.isin(late.index), "customer_id"].map(newc).to_numpy()
        extra["customers_created_at_checkout"] = int(len(late))
        t = tick("orders", t)
        log(f"orders: {len(O):,} orders, {len(L):,} lines")
        sups, sup_ing = supply.build_suppliers(c, cat)
        extra["stockout"] = supply.apply_stockout(O, L, cat)
        extra["fraud"] = fraud.score(O, L, cons, ring)
        res = supply.build(c, cat, O, L, sups, sup_ing)
        t = tick("supply", t)
        inv, rp = inventory.build(cat, places, O, L, res)
        t = tick("inventory", t)
        log(f"inventory: {len(inv):,} positions, {len(rp):,} reorder points")
        f_reviews = facts.reviews(cat, cons, O, L, res)
        f_quality = facts.quality_reports(cat, places, sups, res)
        f_notes = facts.account_notes(cat, acc, O, L, cp, res)
        f_recipes, f_recipe_products = facts.recipes(cat)
        f_desc = facts.product_descriptions(cat)
        t = tick("facts", t)
        extra["shared_business_names"] = len(acc_extra["shared_names"])

        # ---- assemble the tables in their final shape --------------------------------------------------
        P = cat["products"]
        tables = {
            "regions": places["regions"], "locations": places["locations"], "staff": places["staff"],
            "categories": cat["categories"], "uom_conversions": cat["uom_conversions"], "ingredients": cat["ingredients"],
            "suppliers": sups, "supplier_ingredients": sup_ing, "products": P, "blend_components": cat["blend_components"],
            "gift_set_items": cat["gift_set_items"], "skus": cat["skus"], "price_history": cat["price_history"],
            "customers": cons, "restaurant_accounts": acc, "contract_prices": cp, "orders": O, "order_lines": L,
            "purchase_orders": res["purchase_orders"], "po_lines": res["po_lines"], "inbound_lots": res["lots"],
            "production_batches": res["batches"], "batch_inputs": res["batch_inputs"], "inventory_positions": inv,
            "reorder_points": rp,
        }
        log("writing Parquet")
        for name, df in tables.items():
            tdef = schemas.BY_NAME[name]
            if tdef.year_split:
                key = df["ordered_at"] if name == "orders" else df["order_id"].map(O.set_index("order_id").ordered_at)
                yrs = pd.to_datetime(key, utc=True).dt.tz_convert("America/New_York").dt.year
                n_rows, n_bytes = 0, 0
                for y in sorted(yrs.unique()):
                    rr, bb = writer.write_parquet(df[yrs.to_numpy() == y], name, pq_dir / name / f"part-{y}.parquet")
                    n_rows, n_bytes = n_rows + rr, n_bytes + bb
            else:
                n_rows, n_bytes = writer.write_parquet(df, name, pq_dir / name / "part-000.parquet")
            counts[name], sizes[name] = n_rows, n_bytes
        for name, df in (("review_facts", f_reviews), ("quality_report_facts", f_quality), ("account_note_facts", f_notes),
                         ("recipe_facts", f_recipes), ("recipe_product_facts", f_recipe_products), ("product_description_facts", f_desc)):
            sizes[name] = writer.write_parquet_frame(df, pq_dir / "_facts" / f"{name}.parquet")
            counts[name] = len(df)
        schemas.write_all(out / "sql", out / "bigquery" / "schemas")
        t = tick("write_parquet", t)
        ga4_dir = out / "ga4"
        if not a.no_ga4:
            if ga4_dir.exists():
                shutil.rmtree(ga4_dir)
            log("GA4 clickstream for 2026")
            extra["ga4"] = clickstream.build(cat, cons, O, L, dem, ga4_dir)
            t = tick("ga4", t)
        # validation and slicing read the Parquet back through DuckDB; free the in-memory frames first
        # (with pandas 2 they hold ~3 GB at full scale, enough to push an 8 GB machine over)
        del tables, O, L, cons, acc, cp, res, inv, rp, sups, sup_ing, dem, cat, P, places, names
        del f_reviews, f_quality, f_notes, f_recipes, f_recipe_products, f_desc, df, acc_extra, ring
        del first, ca, late, newc
        import gc
        gc.collect()
        if not a.skip_validate:
            log("validating")
            rep = validate.run(out, ga4_dir if not a.no_ga4 else None, extra)
            t = tick("validate", t)
            shutil.rmtree(out / ".duckdb_tmp", ignore_errors=True)
            extra["problems"] = rep["problems"]
            log(f"validation: {len(rep['problems'])} problems")
            for p in rep["problems"]:
                print("  PROBLEM:", p)

    # ---- slices ---------------------------------------------------------------------------------------
    t = time.time()
    specs = slicer.load_specs()
    want = list(specs) if a.slices == "all" else [s.strip() for s in a.slices.split(",")]
    slice_manifests = {}
    for name in want:
        d = out / "slices" / name
        if d.exists():
            shutil.rmtree(d)
        m = slicer.cut(name, specs[name], pq_dir, d, stage=1)
        slice_manifests[name] = dict(rows=m["totals"]["rows"], files=m["totals"]["files"], bytes=m["totals"]["bytes"],
                                     estimates=m["estimates"])
    timings["slices"] = round(time.time() - t, 1)

    mpath = out / "manifest.json"
    prev = json.loads(mpath.read_text()) if (a.cut_only and mpath.exists()) else {}
    manifest = dict(prev) if prev else dict(
        dataset="cymbalfoods", version="v1", seed=C.SEED, scale=C.SCALE,
        window=[C.START.isoformat(), C.END.isoformat()], now=C.NOW_UTC.isoformat(),
        generated_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        versions=dict(python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__,
                      pyarrow=pyarrow.__version__, duckdb=duckdb.__version__),
        tables=counts, parquet_bytes=sizes, timings=timings, extra=extra)
    manifest.setdefault("slices", {}).update(slice_manifests)      # a partial re-cut keeps the other slices
    manifest.setdefault("timings", {})["slices"] = timings["slices"]
    manifest["elapsed_seconds"] = round(time.time() - t0, 1)
    mpath.write_text(json.dumps(manifest, indent=1, default=str) + "\n", encoding="utf-8")
    log(f"done in {manifest['elapsed_seconds']}s → {out}")
    return 3 if extra.get("problems") else 0          # 3 = validation problems; a crash exits 1, bad content 2


if __name__ == "__main__":
    sys.exit(main())
