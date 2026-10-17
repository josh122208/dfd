"""Shared helpers: HTTP with retries and an on-disk cache, name/team normalization."""
import hashlib
import io
import json
import logging
import os
import re
import time
import unicodedata

import requests

log = logging.getLogger("pipeline")

CACHE_DIR = os.environ.get("PIPELINE_CACHE", ".cache")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"

_session = requests.Session()
_session.headers.update({"User-Agent": UA})


def _cache_path(key):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode()).hexdigest())


def http_get(url, params=None, headers=None, cookies=None, max_age=3600, retries=3, timeout=60, binary=False):
    """GET with retries. Responses are cached on disk for `max_age` seconds so reruns are cheap."""
    key = url + json.dumps(params or {}, sort_keys=True) + json.dumps(headers or {}, sort_keys=True)
    path = _cache_path(key)
    if max_age and os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        with open(path, "rb") as f:
            data = f.read()
        return data if binary else data.decode("utf-8")
    last = None
    for attempt in range(retries):
        try:
            r = _session.get(url, params=params, headers=headers, cookies=cookies, timeout=timeout)
            if r.status_code == 404:
                raise FileNotFoundError(url)
            if r.status_code in (401, 403):
                raise PermissionError(f"{r.status_code} for {url}")
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
            return r.content if binary else r.text
        except (FileNotFoundError, PermissionError):
            raise
        except Exception as e:  # network hiccup: back off and retry
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GET failed after {retries} tries: {url}: {last}")


def get_json(url, **kw):
    return json.loads(http_get(url, **kw))


def get_csv(url, **kw):
    import pandas as pd

    raw = http_get(url, binary=True, **kw)
    compression = "gzip" if url.endswith(".gz") else None
    return pd.read_csv(io.BytesIO(raw), compression=compression, low_memory=False)


def safe(fn, default=None, label=None):
    """Run a data-source call; log and return `default` instead of killing the whole build."""
    try:
        return fn()
    except Exception as e:
        log.warning("source failed (%s): %s", label or getattr(fn, "__name__", "?"), e)
        return default


_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm_name(name):
    if not isinstance(name, str):
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'\-]", "", s)
    s = _SUFFIX.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


# Every source spells a few teams differently; nflverse abbreviations are canonical here.
TEAM_FIX = {"LAR": "LA", "WSH": "WAS", "JAC": "JAX", "LVR": "LV", "OAK": "LV", "SD": "LAC", "STL": "LA",
            "GBP": "GB", "KCC": "KC", "NEP": "NE", "NOS": "NO", "SFO": "SF", "TBB": "TB", "ARZ": "ARI",
            "BLT": "BAL", "CLV": "CLE", "HST": "HOU"}


def norm_team(t):
    if not isinstance(t, str) or not t:
        return None
    t = t.upper().strip()
    return TEAM_FIX.get(t, t)


def r1(x, nd=1):
    try:
        if x is None or x != x:
            return None
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None
