"""Gemini calls for the Stage 2 corpus: the CymbalGoal notebook 01 machinery, as a module.

- Meter counts tokens, calls, retries, failures and 429s across threads. On Gemini 3.x the output count
  already includes reasoning, so reasoning is reported but never added on top.
- Throttle sizes the pool every 30 s: over 15% of a window's calls rate-limited cuts the workers to 60%;
  under 3% lets them grow, up to the ceiling.
- backoff() is exponential with jitter, uses the server's "retry in N s" hint, and never waits less than
  20 s after a 429. Each request has a 120 s retry budget.
- run_pool() is a continuous pool: a slot is refilled the moment a call returns, so one request sitting out
  a 429 holds one slot, not a batch of finished ones. It calls on_save() every N results so the notebook
  can checkpoint.

The notebook passes in the client factory, so CF_FAKE_GEMINI=1 swaps in cfgen/fake_gemini.py for offline tests.
"""
from __future__ import annotations

import os
import random
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor
from concurrent.futures import wait as fut_wait

import numpy as np

RETRYABLE_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
MAX_RETRIES = 8
REQUEST_TIMEOUT_S = 90.0
RETRY_BUDGET_S = 120.0
BACKOFF_BASE, BACKOFF_CAP_S, RATE_LIMIT_MIN_WAIT_S = 2.0, 60.0, 20.0
THROTTLE_WINDOW_S, THROTTLE_TRIGGER_RATE, THROTTLE_GROW_BELOW = 30.0, 0.15, 0.03
_RETRY_AFTER_RE = re.compile(r"(?:retry|try again)\s+(?:in|after)\s+([\d.]+)\s*(s|sec|second)", re.I)

# USD per 1M tokens, online (no batch discount): arithmetic only, for estimates
PRICE_IN_PER_M, PRICE_OUT_PER_M, PRICE_EMBED_PER_M = 0.75, 3.75, 0.15


class Fatal(Exception):
    """A failure retrying can't fix: cut off at max tokens, or blocked by the model."""


def status_code(exc) -> int | None:
    for attr in ("code", "status_code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    m = re.search(r"\b(4\d\d|5\d\d)\b", str(exc))
    return int(m.group(1)) if m else None


def _suggested_delay(exc) -> float | None:
    for attr in ("response", "_response"):
        hdrs = getattr(getattr(exc, attr, None), "headers", None)
        if hdrs and hasattr(hdrs, "get"):
            for key in ("retry-after", "Retry-After", "x-retry-after"):
                v = hdrs.get(key)
                if v:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
    m = _RETRY_AFTER_RE.search(str(exc))
    return float(m.group(1)) if m else None


def backoff(attempt: int, code: int | None = None, exc=None) -> float:
    jitter = 0.5 + random.random()
    if exc is not None:
        hinted = _suggested_delay(exc)
        if hinted:
            return min(BACKOFF_CAP_S, hinted * jitter + hinted * 0.5)
    wait = (BACKOFF_BASE ** attempt) * jitter
    if code == 429:
        wait = max(RATE_LIMIT_MIN_WAIT_S * jitter, wait)
    return min(BACKOFF_CAP_S, wait)


class Meter:
    def __init__(self):
        self._lock = threading.Lock()
        self.in_tokens = self.out_tokens = self.thinking_tokens = 0
        self.calls = self.retries = self.failures = self.rate_limited = 0

    def add(self, *, ti=0, to=0, tt=0, retries=0, failed=False, rate_limited=0):
        with self._lock:
            self.in_tokens += ti; self.out_tokens += to; self.thinking_tokens += tt
            self.calls += 1; self.retries += retries; self.failures += int(failed)
            self.rate_limited += rate_limited

    def cost(self) -> float:
        return (self.in_tokens * PRICE_IN_PER_M + self.out_tokens * PRICE_OUT_PER_M) / 1e6


class Throttle:
    def __init__(self, start: int, floor: int = 4, ceiling: int | None = None):
        self.floor, self.workers, self.ceiling = floor, start, ceiling or start
        self._seen_calls = self._seen_429 = 0

    def adjust(self, meter: Meter) -> int:
        calls, limited = meter.calls - self._seen_calls, meter.rate_limited - self._seen_429
        self._seen_calls, self._seen_429 = meter.calls, meter.rate_limited
        if calls <= 0:
            return self.workers
        rate, before = limited / calls, self.workers
        if rate > THROTTLE_TRIGGER_RATE:
            self.workers = max(self.floor, int(self.workers * 0.6))
        elif rate < THROTTLE_GROW_BELOW and self.workers < self.ceiling:
            self.workers = min(self.ceiling, int(self.workers * 1.5) + 1)
        if self.workers != before:
            print(f"    throttle: {rate:.0%} rate-limited -> workers {before} -> {self.workers}")
        return self.workers


def client_factory(project: str, location: str):
    """Per-thread genai clients (or the offline fake), with a per-request timeout."""
    tls = threading.local()
    if os.environ.get("CF_FAKE_GEMINI"):
        from . import fake_gemini
        return lambda: fake_gemini.Client()
    from google import genai
    from google.genai import types as gtypes

    def get():
        c = getattr(tls, "c", None)
        if c is None:
            try:
                c = genai.Client(vertexai=True, project=project, location=location,
                                 http_options=gtypes.HttpOptions(timeout=int(REQUEST_TIMEOUT_S * 1000)))
            except (TypeError, AttributeError):
                c = genai.Client(vertexai=True, project=project, location=location)
            tls.c = c
        return c
    return get


def gen_config(system: str, schema: dict, temperature: float, seed: int | None, max_output_tokens: int = 8192,
               thinking_level: str = "LOW"):
    if os.environ.get("CF_FAKE_GEMINI"):
        return dict(system=system, schema=schema, temperature=temperature, seed=seed)
    from google.genai import types as gtypes
    kw = dict(temperature=temperature, max_output_tokens=max_output_tokens, system_instruction=system,
              response_mime_type="application/json", response_schema=schema)
    if seed is not None:
        kw["seed"] = seed
    fields = set(getattr(gtypes.GenerateContentConfig, "model_fields", None) or {})
    if "automatic_function_calling" in fields:
        kw["automatic_function_calling"] = gtypes.AutomaticFunctionCallingConfig(disable=True)
    try:
        kw["thinking_config"] = gtypes.ThinkingConfig(thinking_level=thinking_level)
    except Exception:  # noqa: BLE001
        print("⚠️  this google-genai build rejects thinking_level; upgrade the SDK")
    return gtypes.GenerateContentConfig(**kw)


def generate_one(req: dict, *, get_client, model: str, config, parse, meter: Meter, repair_config=None) -> dict:
    """One request → {request_id, job_ids, outputs {job_id: fields} | None, tokens, attempts, error}."""
    last, n_429 = "", 0
    deadline = time.time() + RETRY_BUDGET_S
    for attempt in range(MAX_RETRIES):
        try:
            cfg = repair_config() if (req.get("repair") and repair_config) else config
            client = get_client()      # held for the whole call: a google-genai Client closes its connection when collected
            resp = client.models.generate_content(model=model, contents=req["prompt"], config=cfg)
            cand = (getattr(resp, "candidates", None) or [None])[0]
            finish = str(getattr(cand, "finish_reason", "") or "").upper()
            if "MAX_TOKENS" in finish:
                raise Fatal("hit max_output_tokens")
            if "SAFETY" in finish or "RECITATION" in finish or "PROHIBITED" in finish:
                raise Fatal(f"blocked by the model, finish_reason={finish}")
            text = (resp.text or "").strip()
            if not text:
                raise RuntimeError(f"empty response body (finish_reason={finish or 'unknown'})")
            outputs = parse(text, req["job_ids"])          # malformed JSON is retried like any other error
            um = getattr(resp, "usage_metadata", None)
            ti = int(getattr(um, "prompt_token_count", 0) or 0)
            to = int(getattr(um, "candidates_token_count", 0) or 0)
            tt = int(getattr(um, "thoughts_token_count", 0) or 0)
            meter.add(ti=ti, to=to, tt=tt, retries=attempt, rate_limited=n_429)
            return dict(request_id=req["request_id"], job_ids=req["job_ids"], outputs=outputs, in_tok=ti, out_tok=to,
                        think_tok=tt, attempts=attempt + 1, error="")
        except Fatal as exc:
            last = f"Fatal: {exc}"
            break
        except Exception as exc:  # noqa: BLE001
            code = status_code(exc)
            last = f"{type(exc).__name__} {code or ''}: {str(exc)[:200]}"
            if code == 429:
                n_429 += 1
            if code is not None and code not in RETRYABLE_CODES and 400 <= code < 500:
                break
            if attempt < MAX_RETRIES - 1:
                wait = backoff(attempt, code, exc)
                if time.time() + wait > deadline:
                    last += f" | gave up: {RETRY_BUDGET_S:.0f}s retry budget exhausted"
                    break
                time.sleep(wait)
    meter.add(retries=MAX_RETRIES, failed=True, rate_limited=n_429)
    return dict(request_id=req["request_id"], job_ids=req["job_ids"], outputs=None, in_tok=0, out_tok=0, think_tok=0,
                attempts=MAX_RETRIES, error=last or "failed")


def run_pool(items: list, fn, *, workers: int = 50, ceiling: int = 150, floor: int = 4, label: str = "generate",
             on_result=None, save_every: int = 100, on_save=None, meter: Meter | None = None, unit: str = "request") -> Meter:
    """Run fn(item, meter) over items in a continuous, throttled pool. on_result(result) per finished item;
    on_save() every save_every results and once at the end (also when the cell is stopped)."""
    meter = meter or Meter()
    if not items:
        return meter
    throttle = Throttle(workers, floor=floor, ceiling=ceiling)
    queue = list(reversed(items))
    inflight = set()
    t0 = last_adj = time.time()
    done = since = 0
    pool = ThreadPoolExecutor(max_workers=ceiling)

    def status():
        el = max(time.time() - t0, 1)
        eta = (len(items) - done) / max(done / el, 1e-9) / 60
        return (f"{done:,}/{len(items):,} {unit}s, {done * 60 / el:,.0f}/min, ETA {eta:,.0f} min, workers {throttle.workers}, "
                f"429s {meter.rate_limited}, failures {meter.failures}, ≈${meter.cost():,.2f}")
    try:
        while queue or inflight:
            while queue and len(inflight) < throttle.workers:
                inflight.add(pool.submit(fn, queue.pop(), meter))
            finished, inflight = fut_wait(inflight, timeout=5, return_when=FIRST_COMPLETED)
            for f in finished:
                if on_result:
                    on_result(f.result())
                done += 1; since += 1
            now = time.time()
            if now - last_adj >= THROTTLE_WINDOW_S:
                throttle.adjust(meter)
                last_adj = now
                print(f"  {label}: {status()}")
            if on_save and since >= save_every and (queue or inflight):
                on_save()
                since = 0
    except KeyboardInterrupt:
        print(f"\n⏹  stopped: {done:,} finished in this run are being saved; Run all again to resume")
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        if on_save and done:
            on_save()
    el = time.time() - t0
    print(f"{label}: {len(items):,} {unit}s in {el / 60:.1f} min; tokens in {meter.in_tokens:,} out {meter.out_tokens:,} "
          f"(reasoning {meter.thinking_tokens:,}); retries {meter.retries:,}, failures {meter.failures:,}, "
          f"429s {meter.rate_limited:,}; ≈${meter.cost():,.2f}")
    return meter


def embed_one(item: tuple, meter: Meter, *, get_client, model: str, dim: int, task_type: str = "RETRIEVAL_DOCUMENT"):
    """(key, text) → (key, float32 vector | None, error)."""
    key, text = item
    if os.environ.get("CF_FAKE_GEMINI"):
        cfg = None
    else:
        from google.genai import types as gtypes
        cfg = gtypes.EmbedContentConfig(output_dimensionality=dim, task_type=task_type)
    last, n_429 = "", 0
    deadline = time.time() + RETRY_BUDGET_S
    for attempt in range(MAX_RETRIES):
        try:
            client = get_client()
            r = client.models.embed_content(model=model, contents=text, config=cfg)
            v = np.asarray(r.embeddings[0].values, dtype=np.float32)
            if v.shape[0] != dim:
                raise Fatal(f"{v.shape[0]} dims, expected {dim}")
            meter.add(ti=max(1, len(text) // 4), retries=attempt, rate_limited=n_429)
            return key, v, ""
        except Fatal as exc:
            last = f"Fatal: {exc}"
            break
        except Exception as exc:  # noqa: BLE001
            code = status_code(exc)
            last = f"{type(exc).__name__} {code or ''}: {str(exc)[:120]}"
            if code == 429:
                n_429 += 1
            if code is not None and code not in RETRYABLE_CODES and 400 <= code < 500:
                break
            if attempt < MAX_RETRIES - 1:
                wait = backoff(attempt, code, exc)
                if time.time() + wait > deadline:
                    break
                time.sleep(wait)
    meter.add(retries=MAX_RETRIES, failed=True, rate_limited=n_429)
    return key, None, last
