"""Business-name normalisation and similarity (protocol section 4.4).

- Legal suffixes (LLC, Inc, Co, Corp, Ltd, ...) are removed.
- Similarity is the token-set ratio (as popularised by fuzzywuzzy/rapidfuzz),
  implemented here without external libraries using difflib.SequenceMatcher.
"""
import re
from difflib import SequenceMatcher
from typing import List, Set

LEGAL_SUFFIXES = {
    "llc", "l.l.c", "inc", "inc.", "incorporated", "co", "co.", "company", "corp", "corp.",
    "corporation", "ltd", "ltd.", "limited", "llp", "lp", "pllc", "pc", "p.c", "plc",
    "dba", "d/b/a", "the",
}

_PUNCT_RE = re.compile(r"[^\w\s&]")
_WS_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation and legal suffixes, collapse whitespace."""
    s = (name or "").lower().replace("&", " and ")
    s = _PUNCT_RE.sub(" ", s)
    tokens = [t for t in _WS_RE.split(s) if t]
    tokens = [t for t in tokens if t.rstrip(".") not in LEGAL_SUFFIXES and t not in LEGAL_SUFFIXES]
    return " ".join(tokens)


def tokens(name: str) -> Set[str]:
    return set(normalize_name(name).split())


def _ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def token_set_ratio(a: str, b: str) -> float:
    """Token-set ratio in [0, 1].

    Follows the fuzzywuzzy definition: with intersection I and differences
    A-B, B-A (each sorted and joined), compare
      t0 = I,  t1 = I + (A-B),  t2 = I + (B-A)
    and return max(ratio(t0,t1), ratio(t0,t2), ratio(t1,t2)).
    """
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 1.0 if ta == tb else 0.0
    inter = " ".join(sorted(ta & tb))
    diff_ab = " ".join(sorted(ta - tb))
    diff_ba = " ".join(sorted(tb - ta))
    t0 = inter.strip()
    t1 = (inter + " " + diff_ab).strip()
    t2 = (inter + " " + diff_ba).strip()
    return max(_ratio(t0, t1), _ratio(t0, t2), _ratio(t1, t2))


def canonical_key(name: str) -> str:
    """Key for grouping unresolved mentions of the same business."""
    return normalize_name(name)


def extract_domain(url: str) -> str:
    """Registrable-ish domain: strip scheme, path, 'www.'. Not a full PSL
    implementation; good enough for grouping citations."""
    if not url:
        return ""
    u = url.strip().lower()
    u = re.sub(r"^[a-z][a-z0-9+.-]*://", "", u)
    host = u.split("/")[0].split("?")[0].split("#")[0].split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def same_domain(a: str, b: str) -> bool:
    da, db = extract_domain(a), extract_domain(b)
    if not da or not db:
        return False
    return da == db or da.endswith("." + db) or db.endswith("." + da)


def digits_only(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d


def normalize_street(address: str) -> List[str]:
    """Return tokens of the street line (before the first comma) with common
    abbreviations harmonised, for fact-error comparison."""
    first = (address or "").split(",")[0].lower()
    first = _PUNCT_RE.sub(" ", first)
    repl = {
        "street": "st", "avenue": "ave", "road": "rd", "boulevard": "blvd", "drive": "dr",
        "lane": "ln", "court": "ct", "parkway": "pkwy", "highway": "hwy", "suite": "ste",
        "north": "n", "south": "s", "east": "e", "west": "w",
    }
    return [repl.get(t, t) for t in first.split() if t]
