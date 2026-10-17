"""Offline stand-in for google-genai, for testing notebook 11 with CF_FAKE_GEMINI=1. Never used in a real run.

It writes plausible JSON from the fact block in the prompt (enough to pass QA most of the time), puts a
banned brand into about 4% of first drafts so the repair path runs, and fails about 1% of calls with a
retryable 503. Embeddings are deterministic pseudo-random unit vectors.
"""
from __future__ import annotations

import hashlib
import json
import re

import numpy as np


class _Err(Exception):
    def __init__(self, code):
        super().__init__(f"{code} UNAVAILABLE (fake)")
        self.code = code


class _Usage:
    def __init__(self, ti, to):
        self.prompt_token_count, self.candidates_token_count, self.thoughts_token_count = ti, to, to // 3


class _Cand:
    finish_reason = "STOP"


class _Resp:
    def __init__(self, text, ti):
        self.text, self.candidates = text, [_Cand()]
        self.usage_metadata = _Usage(ti, len(text) // 4)


class _Emb:
    def __init__(self, v):
        self.values = v


class _EmbResp:
    def __init__(self, v):
        self.embeddings = [_Emb(v)]


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)


def _field(block: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}: (.*)$", block, re.M)
    return m.group(1).strip() if m else ""


def _words(n: int, seed: int) -> str:
    pool = ("good fresh bright warm deep the jar pantry kitchen weeknight dinner balance aroma color pinch finish "
            "roasted simmer sauce rub dough batch season toast bloom gentle layered round earthy").split()
    return " ".join(pool[(seed + i * 7) % len(pool)] for i in range(n))


THEME_WORDS = {"clumped": "it had clumped into hard lumps", "stale": "it tasted stale and weak", "cinnamon": "the cinnamon was not the kind I expected",
               "hotter": "it was much hotter than I expected", "value": "great value for the amount",
               "leaked": "the bottle leaked and was sticky", "musty": "it had a musty smell when opened",
               "Ceylon": "the cinnamon was not the kind I expected"}


def _review(block: str, seed: int) -> dict:
    must = _field(block, "Points to make")
    must = "" if must.startswith("none") else must
    bits = [v for k, v in THEME_WORDS.items() if k.lower() in must.lower()]
    n = int(re.search(r"about (\d+)", _field(block, "Length")).group(1)) if _field(block, "Length") else 40
    body = (". ".join(bits) + ". " if bits else "") + _words(max(8, n - 8 * len(bits)), seed) + "."
    return {"title": "Worth a try" if not bits else "Not what I hoped", "body": body[0].upper() + body[1:]}


def _text(doc: str, prompt: str, seed: int) -> dict:
    n = int(re.search(r"about (\d+)", prompt).group(1)) if re.search(r"Length: about (\d+)", prompt) else 110
    if doc == "description":
        name = _field(prompt, "Product")
        heat = _field(prompt, "Heat")
        extra = " It is smoky." if re.search(r"smok", prompt, re.I) else ""
        hot = " It brings real heat." if re.match(r"(hot|very hot|extremely hot)", heat) else ""
        return {"description": f"{name.split(',')[0]} for the kitchen.{extra}{hot} " + _words(100, seed) + "."}
    if doc == "recipe":
        names = re.findall(r"^- (.+) \(", prompt, re.M)
        ing = "\n".join(f"1 tsp {x}" for x in names) + "\n2 cups water\n1 onion"
        steps = "\n".join(f"{i}. " + _words(22, seed + i) + "." for i in range(1, 8))
        return {"title": "Weeknight Skillet Supper", "ingredients_text": ing, "instructions": steps}
    if doc == "report":
        lot = _field(prompt, "Lot")
        res = _field(prompt, "Result")
        vals = re.findall(r"^- (.+)$", prompt, re.M)
        why = " Customers reported a musty smell." if "musty" in prompt else ""
        verdict = {"fail": "The lot failed and was rejected or held.", "conditional": "The lot was released with a condition.",
                   "pass": "All results were within specification and the lot was released."}.get(res, "")
        return {"report_text": (f"Lot {lot} was inspected.{why} " if lot else "The supplier was audited. ") +
                "; ".join(vals) + f". {verdict} " + _words(max(45, n - 40), seed) + "."}
    pref = _field(prompt, "Preference")
    sub = _field(prompt, "Substituted")
    musty = " Chef says it smells musty, wants a replacement." if "musty" in prompt else ""
    return {"note_text": f"Talked with the chef.{musty}" + (f" Noted: {pref}." if pref else "") +
            (f" Substituted {sub}." if sub else "") + " " + _words(max(20, n - 20), seed) + "."}


_FAILED_ONCE = set()


class _Models:
    def generate_content(self, model, contents, config):
        seed = _h(contents)
        repair = "previous draft was rejected" in contents
        contents = contents.split("\n\nA previous draft was rejected")[0]
        if seed % 100 == 0 and seed not in _FAILED_ONCE:      # a retryable error, once per prompt
            _FAILED_ONCE.add(seed)
            raise _Err(503)
        system = (config or {}).get("system", "")
        if "customer reviews" in system:
            blocks = re.split(r"^REVIEW r\d+\n", contents, flags=re.M)[1:]
            out = [{"id": f"r{i + 1}", **_review(b, seed + i)} for i, b in enumerate(blocks)]
            if not repair and seed % 25 == 3 and out:
                out[0]["body"] += " Better than McCormick."
            text = json.dumps(out)
        else:
            doc = ("description" if "product descriptions" in system else "recipe" if "recipe developer" in system
                   else "report" if "quality analyst" in system else "note")
            d = _text(doc, contents, seed)
            if not repair and seed % 25 == 3:
                k = next(iter(d))
                d[k] += " Better than McCormick."
            text = json.dumps(d)
        return _Resp(text, len(contents) // 4)

    def embed_content(self, model, contents, config):
        r = np.random.default_rng(_h(contents))
        v = r.standard_normal(3072).astype(np.float32)
        return _EmbResp((v / np.linalg.norm(v)).tolist())


class Client:
    def __init__(self, *a, **k):
        self.models = _Models()
