"""Seeded streams, deterministic UUIDs, and small helpers shared by every generator module."""
from __future__ import annotations

import datetime as dt
import hashlib
import time

import numpy as np

from . import config as C


def _key(name: str) -> tuple[int, ...]:
    h = hashlib.sha256(name.encode("utf-8")).digest()
    return tuple(int.from_bytes(h[i:i + 4], "little") for i in range(0, 16, 4))


def rng(*names: object) -> np.random.Generator:
    """An independent stream per name: rng('orders', 2025, 7). Same names, same numbers, any run order."""
    key = "/".join(str(n) for n in names)
    return np.random.default_rng(np.random.SeedSequence(entropy=C.SEED, spawn_key=_key(key)))


def uuids(r: np.random.Generator, n: int) -> np.ndarray:
    """n version-4 UUID strings drawn from r (deterministic, unlike uuid.uuid4())."""
    if n == 0:
        return np.array([], dtype=object)
    b = np.frombuffer(r.bytes(16 * n), dtype=np.uint8).reshape(n, 16).copy()
    b[:, 6] = (b[:, 6] & 0x0F) | 0x40
    b[:, 8] = (b[:, 8] & 0x3F) | 0x80
    allhex = b.tobytes().hex()
    return np.array([f"{allhex[i:i+8]}-{allhex[i+8:i+12]}-{allhex[i+12:i+16]}-{allhex[i+16:i+20]}-{allhex[i+20:i+32]}"
                     for i in range(0, 32 * n, 32)], dtype=object)


def stable_hash(s: str, n: int = 16) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]


def pretty_price(x: np.ndarray | float) -> np.ndarray:
    """Retail-style prices ending in .49 or .99, never below 0.99."""
    x = np.asarray(x, dtype=float)
    return np.maximum(0.99, np.round(np.round(x * 2) / 2 - 0.01, 2))


def days(start: dt.date, end: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(d) for d in range((end - start).days + 1)]


def date_index(d: dt.date) -> int:
    """Days since config.START (0 = Jan 1, 2024)."""
    return (d - C.START).days


def idx_date(i: int) -> dt.date:
    return C.START + dt.timedelta(int(i))


EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
START_US = int((dt.datetime.combine(C.START, dt.time(), dt.timezone.utc) - EPOCH).total_seconds()) * 1_000_000


class Log:
    def __init__(self) -> None:
        self.t0 = time.time()

    def __call__(self, msg: str) -> None:
        print(f"[{time.time() - self.t0:7.1f}s] {msg}", flush=True)


log = Log()


def weighted_choice(r: np.random.Generator, weights: np.ndarray, n: int) -> np.ndarray:
    w = np.asarray(weights, dtype=float)
    s = w.sum()
    if n == 0 or s <= 0:
        return np.zeros(0, dtype=np.int64)
    cdf = np.cumsum(w / s)
    cdf[-1] = 1.0
    return np.searchsorted(cdf, r.random(n), side="right").clip(0, len(w) - 1)


def utc64(s) -> np.ndarray:
    """A tz-aware datetime Series as a naive-UTC datetime64[ns] array.

    pandas 3 gives datetime64 from .to_numpy() on tz-aware columns; pandas 2 gives an object array of
    Timestamps, which numpy can't do arithmetic on. Use this wherever a timestamp column goes to numpy."""
    import pandas as pd
    s = pd.Series(s) if not isinstance(s, pd.Series) else s
    if getattr(s.dt, "tz", None) is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return s.to_numpy(dtype="datetime64[ns]")
