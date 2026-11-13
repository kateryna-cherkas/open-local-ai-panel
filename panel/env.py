"""Minimal .env loader (no python-dotenv dependency).

Keys are read at call time from the process environment, falling back to a
`.env` file at the project root. Values are never logged.
"""
import os
from typing import Dict, Optional

from panel.paths import PROJECT_ROOT

_ENV_FILE = PROJECT_ROOT / ".env"
_cache = None  # type: Optional[Dict[str, str]]


def _parse_env_file(path) -> Dict[str, str]:
    out = {}  # type: Dict[str, str]
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key.strip()] = value
    return out


def get_secret(name: str) -> Optional[str]:
    """Return a secret by variable name, or None if not set. Never print it."""
    global _cache
    value = os.environ.get(name)
    if value:
        return value
    if _cache is None:
        _cache = _parse_env_file(_ENV_FILE)
    value = _cache.get(name)
    return value or None


def require_secret(name: str) -> str:
    value = get_secret(name)
    if not value:
        raise RuntimeError(
            "Missing credential %s. Set it in the environment or in %s "
            "(see .env.example), or run with --mock." % (name, _ENV_FILE)
        )
    return value
