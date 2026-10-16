"""Cut named slices (datagen/slices.yaml) from the Parquet master copy into staged CSVs and one manifest per slice.

A slice is groups × an optional date window × an optional consumer cap. Rows a kept row points at are always
kept (the batches and lots behind every kept order line, the customer behind every kept order), so every slice is
referentially whole. Foreign keys are listed in the manifest with the tables they need; the loader applies only
those whose tables are present.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml

from . import config as C
from .schemas import BY_NAME, EXTENSION_ORDER, GROUP_EXTENSIONS, GROUP_NEEDS, LOAD_ORDER, T, constraints
from .util import log
from .writer import write_csv_gz

SLICES_YAML = Path(__file__).resolve().parent.parent / "slices.yaml"


def load_specs(path: Path = SLICES_YAML) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["slices"]


def read_table(parquet_dir: Path, name: str) -> pd.DataFrame | None:
    d = parquet_dir / name
    if not d.exists():
        return None
    parts = sorted(d.glob("*.parquet"))
    return pd.concat([pq.read_table(p).to_pandas() for p in parts], ignore_index=True)


def _groups(spec: dict) -> list[str]:
    want, out = list(spec["groups"]), []
    while want:
        g = want.pop(0)
        if g not in out:
            out.append(g)
            want += GROUP_NEEDS[g]
    return out


def _et_date(ts: pd.Series) -> pd.Series:
    return pd.to_datetime(ts, utc=True).dt.tz_convert("America/New_York").dt.date


def cut(spec_name: str, spec: dict, parquet_dir: Path, out_dir: Path, stage: int = 1) -> dict:
    groups = _groups(spec)
    tables = [t.name for t in T if t.group in groups and t.stage <= stage]
    data = {n: read_table(parquet_dir, n) for n in tables}
    data = {n: df for n, df in data.items() if df is not None}
    w = spec.get("window")
    w0 = dt.date.fromisoformat(str(w[0])) if w else None
    w1 = dt.date.fromisoformat(str(w[1])) if w else None

    def in_win(df: pd.DataFrame, col: str) -> pd.Series:
        if not w:
            return pd.Series(True, index=df.index)
        v = df[col]
        d = _et_date(v) if pd.api.types.is_datetime64_any_dtype(v) else pd.to_datetime(v).dt.date
        return (d >= w0) & (d <= w1)

    # consumers: optional cap by a seeded hash rank
    cap = (spec.get("cap") or {}).get("consumers")
    if cap and "customers" in data:
        c = data["customers"]
        rank = c.customer_id.map(lambda x: hashlib.sha256(f"{C.SEED}|{x}".encode()).hexdigest())
        data["customers"] = c.loc[rank.sort_values(kind="stable").index[: int(cap)]].sort_index()
    if "orders" in data:
        o = data["orders"]
        keep = in_win(o, "ordered_at")
        if "customers" in data:
            keep &= o.customer_id.isna() | o.customer_id.isin(data["customers"].customer_id)
        data["orders"] = o[keep]
        if "order_lines" in data:
            data["order_lines"] = data["order_lines"][data["order_lines"].order_id.isin(data["orders"].order_id)]
    if "reviews" in data:
        rv = data["reviews"]
        k = in_win(rv, "submitted_at")
        if "orders" in data:
            k &= rv.order_id.isin(data["orders"].order_id)
        data["reviews"] = rv[k]
    if "production_batches" in data:
        b = data["production_batches"]
        need = set()
        if "order_lines" in data:
            need |= set(data["order_lines"].batch_id.dropna())
        if "inventory_positions" in data:
            need |= set(data["inventory_positions"].batch_id)
        b = b[in_win(b, "produced_at") | b.batch_id.isin(need)]
        data["production_batches"] = b
        bi = data["batch_inputs"][data["batch_inputs"].batch_id.isin(b.batch_id)]
        data["batch_inputs"] = bi
        lt = data["inbound_lots"]
        need_l = set(bi.lot_id)
        if "supplier_quality_reports" in data:
            q = data["supplier_quality_reports"]
            q = q[in_win(q, "inspected_at")]
            data["supplier_quality_reports"] = q
            need_l |= set(q.lot_id.dropna())
        lt = lt[in_win(lt, "received_at") | lt.lot_id.isin(need_l)]
        data["inbound_lots"] = lt
        po = data["purchase_orders"]
        po = po[in_win(po, "ordered_on") | po.po_id.isin(lt.po_id)]
        data["purchase_orders"] = po
        data["po_lines"] = data["po_lines"][data["po_lines"].po_id.isin(po.po_id)]
    if "account_notes" in data:
        an = data["account_notes"]
        data["account_notes"] = an[in_win(an, "noted_at")]

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_tables, total_rows, total_vec = {}, 0, 0
    for name in LOAD_ORDER:
        if name not in data:
            continue
        df = data[name].reset_index(drop=True)
        t = BY_NAME[name]
        files = []
        if t.year_split and len(df):
            key = df["ordered_at"] if "ordered_at" in df else (df["submitted_at"] if "submitted_at" in df else None)
            if key is None:          # order_lines follow their orders' year
                yr = data["orders"].set_index("order_id").ordered_at
                key = df.order_id.map(yr)
            yrs = _et_date(key).map(lambda d: d.year)
            for y in sorted(yrs.unique()):
                files.append(write_csv_gz(df[yrs == y], name, out_dir / f"{name}_{y}.csv.gz"))
        else:
            files.append(write_csv_gz(df, name, out_dir / f"{name}.csv.gz"))
        vec = any(c[1].startswith("vector") for c in t.columns)
        manifest_tables[name] = dict(group=t.group, column_order=[c[0] for c in t.columns], rows=int(len(df)),
                                     files=files, has_vectors=vec)
        total_rows += len(df)
        total_vec += len(df) if vec else 0
    present = set(manifest_tables)
    cons = [c for c in constraints() if set(c["requires"]) <= present]
    exts = sorted({e for g in groups for e in GROUP_EXTENSIONS.get(g, [])}, key=EXTENSION_ORDER.index)
    # rough load estimate, calibrated on CymbalGoal (1.66M rows + 38k vectors: ~3 min COPY, ~7.5 min import)
    nfiles = sum(len(v["files"]) for v in manifest_tables.values())
    copy_s = total_rows / 11_000 + total_vec / 900
    manifest = dict(dataset="cymbalfoods", version="v1", scale=C.SCALE, seed=C.SEED, slice=spec_name,
                    description=spec.get("description", ""), spec=dict(groups=groups, window=[str(w0), str(w1)] if w else None, cap=spec.get("cap")),
                    extensions=exts,
                    schema_files=["../../sql/schema.sql"] + (["../../sql/schema-corpus.sql"] if any(g.startswith("corpus") for g in groups) else []),
                    load_order=[n for n in LOAD_ORDER if n in manifest_tables], tables=manifest_tables,
                    constraints=cons, index_files=[f"../../sql/{f}" for f in spec.get("index_files", [])],
                    totals=dict(rows=int(total_rows), vectors=int(total_vec), files=nfiles,
                                bytes=int(sum(f["bytes"] for v in manifest_tables.values() for f in v["files"]))),
                    estimates=dict(copy_seconds=int(copy_s), import_seconds=int(copy_s * 2.5 + 30 * nfiles),
                                   note="Rough, calibrated on CymbalGoal; stage 4 measures."))
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str) + "\n", encoding="utf-8")
    log(f"  slice {spec_name}: {total_rows:,} rows in {nfiles} files, {manifest['totals']['bytes'] / 1e6:,.1f} MB")
    return manifest
