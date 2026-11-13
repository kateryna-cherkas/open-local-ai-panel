"""HTTP helpers: retries with exponential backoff, per-engine rate limiting.

All vendor calls go through `post_json` so retry and rate-limit policy live in
one place. `requests` is imported lazily so the package stays importable in
environments without it (tests in --mock mode never hit the network).
"""
import json
import random
import threading
import time
from typing import Any, Dict, Optional, Tuple

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class HTTPError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__("HTTP %d: %s" % (status, body[:500]))
        self.status = status
        self.body = body


class RateLimiter:
    """Simple fixed-interval limiter: at most `rpm` calls per minute per key."""

    def __init__(self):
        self._lock = threading.Lock()
        self._next_ok = {}  # type: Dict[str, float]

    def wait(self, key: str, rpm: Optional[int]) -> None:
        if not rpm or rpm <= 0:
            return
        interval = 60.0 / float(rpm)
        with self._lock:
            now = time.monotonic()
            next_ok = self._next_ok.get(key, 0.0)
            sleep_for = max(0.0, next_ok - now)
            self._next_ok[key] = max(now, next_ok) + interval
        if sleep_for > 0:
            time.sleep(sleep_for)


RATE_LIMITER = RateLimiter()


def post_json(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    auth: Optional[Tuple[str, str]] = None,
    timeout: float = 120.0,
    retry: Optional[Dict[str, Any]] = None,
    rate_key: Optional[str] = None,
    rpm: Optional[int] = None,
) -> Tuple[Dict[str, Any], float]:
    """POST JSON, return (parsed_json, latency_seconds). Retries on transient
    errors with exponential backoff + jitter. Raises HTTPError otherwise."""
    import requests  # local import: keeps module importable without requests

    retry = retry or {}
    max_attempts = int(retry.get("max_attempts", 5))
    base = float(retry.get("base_delay_s", 2.0))
    cap = float(retry.get("max_delay_s", 60.0))
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)

    last_exc = None  # type: Optional[Exception]
    for attempt in range(max_attempts):
        RATE_LIMITER.wait(rate_key or url, rpm)
        t0 = time.monotonic()
        try:
            resp = requests.post(url, data=json.dumps(payload), headers=hdrs, auth=auth, timeout=timeout)
            latency = time.monotonic() - t0
            if resp.status_code in RETRYABLE_STATUS:
                last_exc = HTTPError(resp.status_code, resp.text)
                retry_after = resp.headers.get("retry-after")
                delay = _backoff(attempt, base, cap)
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                time.sleep(delay)
                continue
            if resp.status_code >= 400:
                raise HTTPError(resp.status_code, resp.text)
            try:
                return resp.json(), latency
            except ValueError:
                raise HTTPError(resp.status_code, "non-JSON body: " + resp.text[:200])
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            time.sleep(_backoff(attempt, base, cap))
    raise last_exc if last_exc else RuntimeError("post_json failed without exception")


def _backoff(attempt: int, base: float, cap: float) -> float:
    return min(cap, base * (2 ** attempt)) + random.uniform(0, 1)
