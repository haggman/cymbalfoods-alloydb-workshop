"""Deterministic output: Parquet (the master copy) and headerless CSV in PostgreSQL COPY ... (FORMAT csv) form."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .schemas import BY_NAME


def _arrow_type(pg: str) -> pa.DataType:
    if pg.endswith("[]"):
        return pa.list_(pa.string())
    if pg.startswith("vector"):
        return pa.list_(pa.float32())
    if pg in ("integer", "smallint"):
        return pa.int64()
    if pg == "boolean":
        return pa.bool_()
    if pg == "timestamptz":
        return pa.timestamp("us", tz="UTC")
    if pg == "date":
        return pa.date32()
    if pg.startswith("numeric"):
        return pa.float64()
    return pa.string()


def arrow_schema(table: str) -> pa.Schema:
    return pa.schema([pa.field(n, _arrow_type(t), nullable=nul) for n, t, nul, _ in BY_NAME[table].columns])


def _clean(df: pd.DataFrame, table: str) -> pd.DataFrame:
    """Final column order and types; no empty strings (NULL instead), numerics rounded to their scale."""
    cols = [c[0] for c in BY_NAME[table].columns]
    missing = [c for c in cols if c not in df.columns]
    assert not missing, f"{table}: missing {missing}"
    out = df[cols].copy()
    for n, t, nul, _ in BY_NAME[table].columns:
        s = out[n]
        if t in ("text", "uuid"):
            out[n] = s.astype(object).where(s.notna() & (s.astype(str) != ""), None)
        elif t.startswith("numeric"):
            sc = int(re.search(r",(\d+)\)", t).group(1))
            out[n] = pd.to_numeric(s).round(sc)
        elif t == "timestamptz":
            out[n] = pd.to_datetime(s, utc=True).dt.floor("us")
        elif t == "date":
            out[n] = pd.to_datetime(s).dt.date.where(s.notna(), None)
        elif t.endswith("[]"):
            out[n] = s.map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else [str(x) for x in v])
        if not nul:
            assert out[n].notna().all(), f"{table}.{n} has NULLs"
    return out.reset_index(drop=True)


def write_parquet(df: pd.DataFrame, table: str, path: Path) -> tuple[int, int]:
    df = _clean(df, table)
    path.parent.mkdir(parents=True, exist_ok=True)
    tbl = pa.Table.from_pandas(df, schema=arrow_schema(table), preserve_index=False).replace_schema_metadata(None)
    pq.write_table(tbl, path, compression="zstd", row_group_size=250_000, use_dictionary=True, write_statistics=True)
    return len(df), path.stat().st_size


def write_parquet_frame(df: pd.DataFrame, path: Path) -> int:
    """For non-schema tables (facts): lists and dicts are stored as JSON text."""
    df = df.copy()
    for c in df.columns:
        if df[c].map(lambda v: isinstance(v, (list, dict))).any():
            df[c] = df[c].map(lambda v: json.dumps(v, sort_keys=True, default=str) if isinstance(v, (list, dict)) else v)
    path.parent.mkdir(parents=True, exist_ok=True)
    tbl = pa.Table.from_pandas(df, preserve_index=False).replace_schema_metadata(None)
    pq.write_table(tbl, path, compression="zstd")
    return path.stat().st_size


def _pg_array(v) -> str | None:
    if v is None:
        return None
    parts = []
    for x in v:
        x = str(x).replace("\\", "\\\\").replace('"', '\\"')
        parts.append(f'"{x}"')
    return "{" + ",".join(parts) + "}"


def to_pg_csv(df: pd.DataFrame, table: str) -> bytes:
    """Headerless CSV for COPY ... (FORMAT csv): NULL = unquoted empty field, booleans t/f, arrays as {..} literals."""
    out = pd.DataFrame(index=df.index)
    for n, t, nul, _ in BY_NAME[table].columns:
        s = df[n]
        if t.endswith("[]"):
            out[n] = s.map(_pg_array)
        elif t.startswith("vector"):
            out[n] = s.map(lambda v: None if v is None else "[" + ",".join(f"{x:.6g}" for x in v) + "]")
        elif t == "boolean":
            out[n] = s.map({True: "t", False: "f"})
        elif t == "timestamptz":
            ts = pd.to_datetime(s, utc=True)
            out[n] = ts.dt.strftime("%Y-%m-%d %H:%M:%S+00").where(ts.notna(), None)
        elif t == "date":
            out[n] = s.map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else str(v))
        elif t.startswith("numeric"):
            sc = int(re.search(r",(\d+)\)", t).group(1))
            out[n] = s.map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{sc}f}")
        elif t in ("integer", "smallint"):
            out[n] = s.map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else str(int(v)))
        else:
            out[n] = s
    buf = io.StringIO()
    out.to_csv(buf, header=False, index=False, na_rep="", lineterminator="\n")
    return buf.getvalue().encode("utf-8")


def write_csv_gz(df: pd.DataFrame, table: str, path: Path) -> dict:
    """Written in chunks, so a table of 3,072-dim vectors never sits in memory as one CSV string (the bytes are
    the same as a single write: gzip is fed one continuous stream)."""
    vec = any(t.startswith("vector") for _, t, _, _ in BY_NAME[table].columns)
    step = 1_000 if vec else 250_000
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0, compresslevel=6) as gz:
            for i in range(0, len(df), step):
                gz.write(to_pg_csv(df.iloc[i:i + step], table))
    return dict(file=path.name, rows=int(len(df)), bytes=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())
