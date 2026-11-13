"""Places resolution and classification (protocol 3.5, 4.4, 4.5).

    python -m panel.resolve --wave 2026-11 [--mock] [--limit N]

Input : data/processed/{wave}/mentions.csv
Output: data/processed/{wave}/resolved.csv   (mentions + place fields + classification)
        data/processed/{wave}/businesses.csv (one row per distinct resolved/unresolved business per city)
Cache : data/cache/places/{sha1}.json per (normalised name, city) so repeated
        names across engines/repeats/waves cost one Places call.

Rule (4.4): send "name + city" to Places Text Search; accept the top candidate
within the metro area (haversine <= resolution.metro_radius_km from the city
centre) only if token-set ratio of the suffix-stripped names >= 0.85; otherwise
`unresolved`. Candidates are scanned in API order and the first that passes both
checks is taken.

Classification (4.5): closed = business_status CLOSED_PERMANENTLY;
fact_error_* = stated phone / website domain / street address disagree with
Places; ghost = unresolved AND manual-search column says not found (the manual
step is a human task: see `ghost_manual_check` column, default blank -> ghost
is counted as "unresolved_pending_manual" until filled).
"""
import argparse
import csv
import re
import sys
from typing import Any, Dict, List, Optional

from panel.config import City, load_cities, load_panel
from panel.engines.places import PlacesClient, cache_key
from panel.geo import haversine_km
from panel.areacodes import in_metro
from panel.names import canonical_key, digits_only, normalize_name, normalize_street, same_domain, extract_domain, token_set_ratio
from panel.paths import CACHE_DIR, processed_wave_dir
from panel.storage import dump_json, load_json

RESOLVED_EXTRA = [
    "resolution_status", "match_similarity", "place_id", "place_name", "place_address", "place_phone", "place_website",
    "place_rating", "place_user_rating_count", "place_business_status", "place_types", "place_distance_km",
    "business_key", "is_closed", "fact_error_phone", "fact_error_website", "fact_error_address", "fact_error",
    "ghost_manual_check", "is_ghost",
]


class Resolver:
    def __init__(self, panel: Dict[str, Any], cities: List[City], mock: bool):
        self.places = PlacesClient(panel["places"], panel.get("retry"), mock=mock)
        self.threshold = float(panel["resolution"]["name_similarity_threshold"])
        self.radius_km = float(panel["resolution"]["metro_radius_km"])
        self.cities = {c.key: c for c in cities}
        self.cache_dir = CACHE_DIR / "places"
        self.niche_keywords = panel["resolution"].get("niche_keywords", {})
        self.calls = 0

    def legacy_cached(self, name: str, city: City) -> Optional[Dict[str, Any]]:
        """Pre-v0.2 cache entry (searched without service-area businesses), if any."""
        path = self.cache_dir / (cache_key(name, city) + ".json")
        return load_json(path) if path.exists() else None

    def lookup(self, name: str, city: City) -> Dict[str, Any]:
        path = self.cache_dir / (cache_key(name, city) + "_sab.json")
        if path.exists():
            return load_json(path)
        raw = self.places.search(name, city)
        self.calls += 1
        dump_json(path, raw)
        return raw

    def resolve(self, name: str, city: City, niche: str = "") -> Dict[str, Any]:
        """Protocol 4.4 (v0.2): query 1 = name + city. If that fails, query 2 = cleaned name
        (location suffixes in brackets / after a dash removed) + niche keyword + city.
        Matching rules (name similarity, metro radius) are the same for both queries."""
        legacy = self.legacy_cached(name, city)
        if legacy is not None:
            # A match found without service-area businesses stays valid; only failures are re-queried.
            res = self._match(legacy, name, city)
            if res["resolution_status"] == "resolved":
                return res
        res = self._try(name, name, city)
        if res["resolution_status"] == "resolved" or not niche:
            return res
        clean = clean_name(name)
        kw = self.niche_keywords.get(niche, niche.replace("_", " "))
        query2 = clean if kw.lower() in clean.lower() else "%s %s" % (clean, kw)
        res2 = self._try(query2, clean, city)
        if res2["resolution_status"] == "resolved":
            res2["resolution_query"] = "fallback"
            return res2
        res["match_similarity"] = max(res.get("match_similarity", 0), res2.get("match_similarity", 0))
        return res

    def _try(self, query: str, name: str, city: City) -> Dict[str, Any]:
        return self._match(self.lookup(query, city), name, city)

    def _match(self, raw: Dict[str, Any], name: str, city: City) -> Dict[str, Any]:
        best_sim = 0.0
        for cand in self.places.candidates(raw):
            sim = token_set_ratio(name, cand.get("name") or "")
            if compact(name) and compact(name) == compact(cand.get("name") or ""):
                sim = 1.0  # "ResCom" vs "Res Com", "Lily White" vs "Lily-White"
            best_sim = max(best_sim, sim)
            dist = None
            if cand.get("latitude") is not None and cand.get("longitude") is not None:
                dist = haversine_km(city.latitude, city.longitude, cand["latitude"], cand["longitude"])
                if dist > self.radius_km:
                    continue
            elif not in_metro(cand.get("phone") or "", city.key):
                # Service-area business without coordinates: place it by phone area code.
                continue
            if sim >= self.threshold:
                out = dict(cand)
                out.update({"resolution_status": "resolved", "match_similarity": round(sim, 3),
                            "place_distance_km": round(dist, 1) if dist is not None else None})
                return out
        return {"resolution_status": "unresolved", "match_similarity": round(best_sim, 3)}


def compact(name: str) -> str:
    """Name with legal suffixes, spaces and punctuation removed (spacing/hyphen variants)."""
    n = normalize_name(name)
    n = re.sub(r"\b(llc|inc|co|corp|company|ltd)\b", " ", n)
    return re.sub(r"[^a-z0-9]", "", n)


def clean_name(name: str) -> str:
    """Drop trailing location qualifiers: 'X (Houston)', 'X — Houston', 'X - Houston TX'."""
    n = re.sub(r"\s*\([^)]*\)\s*", " ", name)
    n = re.split(r"\s+[\u2014\u2013-]\s+", n)[0]
    return re.sub(r"\s+", " ", n).strip() or name


def classify(mention: Dict[str, str], res: Dict[str, Any]) -> Dict[str, Any]:
    resolved = res.get("resolution_status") == "resolved"
    out = {
        "is_closed": int(resolved and res.get("business_status") == "CLOSED_PERMANENTLY"),
        "fact_error_phone": "", "fact_error_website": "", "fact_error_address": "", "fact_error": 0,
        "ghost_manual_check": "", "is_ghost": "",
    }
    if resolved:
        sp, pp = mention.get("stated_phone") or "", res.get("phone") or ""
        if sp and pp:
            out["fact_error_phone"] = int(digits_only(sp) != digits_only(pp))
        sw, pw = mention.get("stated_website") or "", res.get("website") or ""
        # Only compare when the stated value is actually a URL/domain (extractor sometimes
        # copies the business name into the website field).
        if sw and pw and extract_domain(sw) and extract_domain(pw):
            out["fact_error_website"] = int(not same_domain(sw, pw))
        sa, pa = mention.get("stated_address") or "", res.get("formatted_address") or ""
        # Only compare street addresses (a house number is present); a bare city/area
        # name is not a fact claim we can falsify. Match on house number + one street word.
        if sa and pa and re.search(r"\b\d{2,6}\b", sa):
            s_num = re.search(r"\b(\d{2,6})\b", sa).group(1)
            s_tok, p_tok = set(normalize_street(sa)), set(normalize_street(pa))
            same = s_num in re.findall(r"\b\d{2,6}\b", pa) and bool((s_tok & p_tok) - {s_num})
            out["fact_error_address"] = int(not same)
        # Phone is reported separately as "differs from Google profile": many businesses
        # put a call-tracking number on Google, so a mismatch is not necessarily wrong.
        out["fact_error"] = int(any(v == 1 for v in (out["fact_error_website"], out["fact_error_address"])))
    else:
        # Ghost requires a manual search (protocol 4.6). Blank until a human fills ghost_manual_check
        # with "not_found" (-> ghost) or "found" (-> exists but unresolved).
        out["is_ghost"] = ""
    return out


def run(wave: str, mock: bool, limit: Optional[int]) -> Dict[str, int]:
    panel = load_panel()
    cities = load_cities()
    resolver = Resolver(panel, cities, mock)
    in_path = processed_wave_dir(wave) / "mentions.csv"
    out_path = processed_wave_dir(wave) / "resolved.csv"
    with open(in_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        mentions = list(reader)
        fieldnames = list(reader.fieldnames or [])
    stats = {"mentions": len(mentions), "resolved": 0, "unresolved": 0, "skipped_not_recommended": 0, "places_calls": 0}
    rows = []
    memo = {}  # type: Dict[str, Dict[str, Any]]
    for m in mentions:
        row = dict(m)
        for k in RESOLVED_EXTRA:
            row[k] = ""
        if m.get("recommended") not in ("1", "True", "true"):
            row["resolution_status"] = "not_recommended"
            stats["skipped_not_recommended"] += 1
            rows.append(row)
            continue
        city = resolver.cities[m["city_key"]]
        memo_key = canonical_key(m["name"]) + "|" + city.key + "|" + (m.get("niche") or "")
        if memo_key not in memo:
            if limit is not None and resolver.calls >= limit:
                row["resolution_status"] = "not_attempted"
                rows.append(row)
                continue
            memo[memo_key] = resolver.resolve(m["name"], city, m.get("niche") or "")
        res = memo[memo_key]
        row["resolution_status"] = res["resolution_status"]
        row["match_similarity"] = res.get("match_similarity", "")
        if res["resolution_status"] == "resolved":
            stats["resolved"] += 1
            row.update({
                "place_id": res.get("place_id"), "place_name": res.get("name"), "place_address": res.get("formatted_address"),
                "place_phone": res.get("phone") or "", "place_website": res.get("website") or "",
                "place_rating": res.get("rating") if res.get("rating") is not None else "",
                "place_user_rating_count": res.get("user_rating_count") if res.get("user_rating_count") is not None else "",
                "place_business_status": res.get("business_status") or "", "place_types": "|".join(res.get("types") or []),
                "place_distance_km": res.get("place_distance_km") if res.get("place_distance_km") is not None else "",
                "business_key": "place:" + str(res.get("place_id")),
            })
        else:
            stats["unresolved"] += 1
            row["business_key"] = "unresolved:" + canonical_key(m["name"]) + "@" + city.key
        row.update(classify(m, res))
        rows.append(row)
    stats["places_calls"] = resolver.calls
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames + RESOLVED_EXTRA)
        w.writeheader()
        w.writerows(rows)
    write_businesses(wave, rows)
    return stats


def write_businesses(wave: str, rows: List[Dict[str, Any]]) -> None:
    """One row per business_key x city (candidate set for RQ6)."""
    agg = {}  # type: Dict[str, Dict[str, Any]]
    for r in rows:
        bk = r.get("business_key")
        if not bk:
            continue
        a = agg.setdefault(bk, {
            "wave": wave, "business_key": bk, "city_key": r["city_key"], "niche": r["niche"],
            "name": r.get("place_name") or r["name"], "resolution_status": r["resolution_status"],
            "place_id": r.get("place_id", ""), "place_rating": r.get("place_rating", ""),
            "place_user_rating_count": r.get("place_user_rating_count", ""), "place_website": r.get("place_website", ""),
            "place_business_status": r.get("place_business_status", ""), "n_mentions": 0, "engines": set(),
        })
        a["n_mentions"] += 1
        a["engines"].add(r["engine"])
    path = processed_wave_dir(wave) / "businesses.csv"
    cols = ["wave", "business_key", "city_key", "niche", "name", "resolution_status", "place_id", "place_rating",
            "place_user_rating_count", "place_website", "place_business_status", "n_mentions", "engines"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for a in agg.values():
            a["engines"] = "|".join(sorted(a["engines"]))
            w.writerow(a)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Resolve extracted names against Google Places.")
    ap.add_argument("--wave", required=True)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="Max Places calls this run (cache hits are free)")
    args = ap.parse_args(argv)
    stats = run(args.wave, args.mock, args.limit)
    print("resolve: %s -> %s" % (stats, processed_wave_dir(args.wave) / "resolved.csv"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
