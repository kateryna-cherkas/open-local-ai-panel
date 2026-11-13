"""Deterministic synthetic businesses for --mock mode.

Every mock engine draws from the same per-(city, niche) pool so that overlap,
stability and resolution metrics are non-trivial but reproducible. Names are
obviously synthetic ("Mock" is embedded) and never refer to real businesses.
"""
import hashlib
import random
from typing import Dict, List

from panel.config import City

_NICHE_WORDS = {
    "roofing": ["Roofing", "Roof Co", "Exteriors", "Roofing & Siding"],
    "hvac": ["Heating & Air", "HVAC", "Air Conditioning", "Climate Services"],
    "plumbing": ["Plumbing", "Plumbing & Drain", "Plumbers", "Pipe Works"],
    "construction": ["Construction", "Builders", "Remodeling", "Contracting"],
    "appliance_repair": ["Appliance Repair", "Appliance Service", "Appliance Pros", "Repair Co"],
    "cleaning": ["Cleaning", "Maids", "Cleaning Services", "Home Cleaning"],
}
_PREFIXES = ["Summit", "Blue Sky", "Evergreen", "Pioneer", "Northstar", "Granite", "Riverbend",
             "Cedar", "Ironclad", "Sunrise", "Heritage", "Lakeside", "Prairie", "Redwood", "Harbor"]
_SUFFIX_FORMS = ["{}", "{} LLC", "{}, Inc.", "{} Co.", "The {}"]


def _rng(*parts) -> random.Random:
    seed = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return random.Random(int(seed[:16], 16))


def city_pool(city: City, niche: str, size: int = 12) -> List[Dict[str, str]]:
    """Stable pool of synthetic businesses for a city x niche."""
    rng = _rng("pool", city.key, niche)
    words = _NICHE_WORDS.get(niche, ["Services"])
    pool = []
    prefixes = rng.sample(_PREFIXES, k=min(size, len(_PREFIXES)))
    for i, pref in enumerate(prefixes):
        base = "%s %s" % (pref, rng.choice(words))
        slug = base.lower().replace("&", "and").replace(",", "").replace(".", "").replace(" ", "")
        area = str(200 + int(hashlib.md5(city.key.encode("utf-8")).hexdigest()[:6], 16) % 700).zfill(3)
        pool.append({
            "id": "%s-%s-%02d" % (city.key.lower(), niche, i),
            "base_name": base,
            "phone": "(%s) 555-%04d" % (area, 1000 + i * 37 % 9000),
            "website": "https://www.%s-mock.example.com" % slug,
            "address": "%d Mock %s St, %s, %s" % (100 + i * 13, pref.split()[0], city.city, city.state),
            # Deterministic quality flags used by the mock Places adapter
            "ghost": i == size - 1,                 # last one does not exist in Places
            "closed": i == size - 2,                # second-to-last is CLOSED_PERMANENTLY
            "rating": "%.1f" % (3.6 + (i % 7) * 0.2),
            "reviews": str(15 + (i * 53) % 900),
        })
    return pool


def recommended_for(engine: str, city: City, niche: str, template_id: str, repeat: int, n: int = 5) -> List[Dict[str, str]]:
    """Which pool members an engine 'recommends' on a given call. Engines share
    a bias toward the first few pool members (so lists overlap and are fairly
    stable), with per-repeat jitter."""
    pool = city_pool(city, niche)
    rng = _rng("rec", engine, city.key, niche, template_id, repeat)
    weights = [max(1, 12 - i) for i in range(len(pool))]
    chosen = []  # type: List[Dict[str, str]]
    candidates = list(range(len(pool)))
    while len(chosen) < n and candidates:
        pick = rng.choices(candidates, weights=[weights[c] for c in candidates], k=1)[0]
        candidates.remove(pick)
        chosen.append(pool[pick])
    out = []
    for i, b in enumerate(chosen):
        form = rng.choice(_SUFFIX_FORMS)
        rec = dict(b)
        rec["display_name"] = form.format(b["base_name"])
        # Occasionally the mock engine states a wrong phone (fact error)
        if rng.random() < 0.15:
            rec["stated_phone"] = rec["phone"][:-1] + ("0" if rec["phone"][-1] != "0" else "1")
        else:
            rec["stated_phone"] = rec["phone"]
        rec["state_phone"] = rng.random() < 0.7
        rec["state_site"] = rng.random() < 0.6
        out.append(rec)
    return out


def local_pack_for(city: City, niche: str, template_id: str, n: int = 3) -> List[Dict[str, str]]:
    pool = city_pool(city, niche)
    rng = _rng("pack", city.key, niche, template_id)
    idx = sorted(rng.sample(range(0, 6), k=n))
    return [pool[i] for i in idx]


def render_answer(engine: str, city: City, niche: str, recs: List[Dict[str, str]]) -> str:
    """Produce a plausible answer text with a Mock marker."""
    lines = ["Here are some well-reviewed %s options in %s, %s (mock %s answer):" % (niche, city.city, city.state, engine), ""]
    for i, r in enumerate(recs, 1):
        bits = ["%d. **%s**" % (i, r["display_name"])]
        if r["state_phone"]:
            bits.append("Phone: %s." % r["stated_phone"])
        if r["state_site"]:
            bits.append("Website: %s" % r["website"])
        lines.append(" - ".join(bits))
    lines.append("")
    lines.append("You can also compare reviews on Yelp or Angi before deciding.")
    return "\n".join(lines)
