"""Common engine interface.

Each adapter implements:
  build_request(ctx)      -> (url, payload, headers, auth)
  parse_response(raw)     -> ParsedResponse(text, citations, returned_model)
  mock_raw(ctx)           -> a raw payload shaped like the real API, built from
                             tests/fixtures/<engine>_response.json

`call(ctx)` wires these together with retries/rate limiting and returns a
normalised EngineResult. In mock mode the fixture payload still goes through
parse_response, so the parser is exercised by the tests.
"""
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from panel.config import City
from panel.http import HTTPError, post_json
from panel.paths import FIXTURES_DIR


@dataclass
class CallContext:
    engine: str
    wave: str
    niche: str
    template_id: str
    prompt: str
    city: City
    repeat: int
    requested_model: str

    @property
    def key(self) -> str:
        return "%s|%s|%s|%s|%d" % (self.engine, self.niche, self.template_id, self.city.key, self.repeat)


@dataclass
class ParsedResponse:
    text: str
    citations: List[str]
    returned_model: Optional[str]
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineResult:
    text: str
    citations: List[str]
    returned_model: Optional[str]
    raw: Any
    latency_s: Optional[float]
    error: Optional[str]
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Engine:
    id = "base"
    secret_names = []  # type: List[str]

    def __init__(self, cfg: Dict[str, Any], retry_cfg: Optional[Dict[str, Any]] = None, mock: bool = False):
        self.cfg = cfg
        self.retry_cfg = retry_cfg or {}
        self.mock = mock

    # ---- to implement -------------------------------------------------
    def build_request(self, ctx: CallContext) -> Tuple[str, Dict[str, Any], Dict[str, str], Optional[Tuple[str, str]]]:
        raise NotImplementedError

    def parse_response(self, raw: Dict[str, Any]) -> ParsedResponse:
        raise NotImplementedError

    def mock_raw(self, ctx: CallContext) -> Dict[str, Any]:
        raise NotImplementedError

    # ---- shared -------------------------------------------------------
    @property
    def model(self) -> str:
        return str(self.cfg.get("model", ""))

    def load_fixture(self, name: str) -> Dict[str, Any]:
        with open(FIXTURES_DIR / name, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def call(self, ctx: CallContext) -> EngineResult:
        t0 = time.monotonic()
        try:
            if self.mock:
                raw = self.mock_raw(ctx)
                latency = time.monotonic() - t0
            else:
                url, payload, headers, auth = self.build_request(ctx)
                raw, latency = post_json(
                    url, payload, headers=headers, auth=auth,
                    retry=self.retry_cfg, rate_key=self.id,
                    rpm=self.cfg.get("requests_per_minute"),
                )
            parsed = self.parse_response(raw)
            return EngineResult(
                text=parsed.text, citations=dedupe(parsed.citations),
                returned_model=parsed.returned_model, raw=raw,
                latency_s=round(latency, 3), error=None, extra=parsed.extra,
            )
        except HTTPError as exc:
            return EngineResult("", [], None, {"status": exc.status, "body": exc.body[:2000]},
                                round(time.monotonic() - t0, 3), "http_%d" % exc.status)
        except Exception as exc:  # noqa: BLE001 - recorded, not raised, per protocol 4.1
            return EngineResult("", [], None, None, round(time.monotonic() - t0, 3),
                                "%s: %s" % (type(exc).__name__, str(exc)[:500]))


def dedupe(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def user_location_approximate(city: City, with_timezone: bool = True) -> Dict[str, str]:
    loc = {"type": "approximate", "country": "US", "city": city.city, "region": city.state_name}
    if with_timezone and city.timezone:
        loc["timezone"] = city.timezone
    return loc
