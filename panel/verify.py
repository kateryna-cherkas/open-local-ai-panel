"""Post-resolution verification (protocol 4.5 / 4.6, added v0.1.3).

1. Phones. For every resolved mention whose AI-stated phone differs from the Google
   profile phone, fetch the business's own website (Google profile URL, plus the
   AI-stated URL when it is the same domain) and collect every US phone number on the
   home page and a contact page. Outcome per mention (column phone_check):
     site_match      - AI phone appears on the business's own site (an official
                       alternate number, e.g. call tracking or a second office)
     site_no_match   - site fetched, AI phone not found on it (likely wrong number)
     site_unavailable- site could not be fetched; stays unverified
2. Unresolved mentions. Re-read the cached Places candidates (no new API calls) and
   label why the mention failed to resolve (column unresolved_reason):
     same_name_outside_metro - a candidate with name similarity >= threshold exists
                               but lies outside the metro radius
     low_name_similarity     - no candidate passed the name threshold
   The ghost decision itself stays manual (ghost_manual_check in resolved.csv).

Network: plain HTTPS GETs to business websites only, one request per page, cached in
data/cache/sites/. No paid APIs.
"""
import argparse
import hashlib
import re
import sys
from typing import Dict, List, Optional, Set

import pandas as pd
import requests

from panel.names import digits_only, extract_domain, same_domain, token_set_ratio
from panel.paths import CACHE_DIR, DATA_DIR

UA = "Mozilla/5.0 (compatible; OpenLocalAIPanel/0.1; research; +https://webhunterstudio.com)"
PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?\(?([2-9]\d{2})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})\b")
TEL_RE = re.compile(r'href=["\']tel:([^"\']+)["\']', re.I)
CONTACT_RE = re.compile(r'href=["\']([^"\']*contact[^"\']*)["\']', re.I)
SITE_CACHE = CACHE_DIR / "sites"


def fetch(url: str) -> Optional[str]:
    SITE_CACHE.mkdir(parents=True, exist_ok=True)
    path = SITE_CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".html")
    if path.exists():
        txt = path.read_text(encoding="utf-8", errors="ignore")
        return txt or None
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15, allow_redirects=True)
        txt = r.text if r.ok else ""
    except requests.RequestException:
        txt = ""
    path.write_text(txt, encoding="utf-8")
    return txt or None


def phones_in(html: str) -> Set[str]:
    out = set()
    for m in TEL_RE.findall(html):
        d = digits_only(m)
        if len(d) == 10:
            out.add(d)
    for a, b, c in PHONE_RE.findall(html):
        out.add(a + b + c)
    return out


def site_phones(url: str) -> Optional[Set[str]]:
    if not url:
        return None
    dom = extract_domain(url)
    if not dom:
        return None
    root = "https://" + dom + "/"
    pages = [root]
    # Location pages (franchises, multi-office firms) carry their own number: fetch the
    # exact profile URL too, without tracking parameters.
    exact = url.split("?")[0].split("#")[0]
    if exact.startswith("http") and exact.rstrip("/") != root.rstrip("/"):
        pages.append(exact)
    found = set()  # type: Set[str]
    html = None
    for pg in pages:
        h = fetch(pg)
        if h is not None:
            found |= phones_in(h)
            html = html or h
    if html is None:
        return None
    m = CONTACT_RE.search(html)
    if m:
        href = m.group(1)
        if href.startswith("/"):
            href = "https://" + dom + href
        if href.startswith("http") and same_domain(href, root):
            sub = fetch(href.split("#")[0])
            if sub:
                found |= phones_in(sub)
    return found


def check_phones(df: pd.DataFrame) -> pd.DataFrame:
    df["phone_check"] = ""
    mism = df[df["fact_error_phone"].astype(str) == "1"]
    cache = {}  # type: Dict[str, Optional[Set[str]]]
    for idx, row in mism.iterrows():
        urls = [row.get("place_website") or ""]
        sw = row.get("stated_website") or ""
        if sw and extract_domain(sw) and not same_domain(sw, urls[0]):
            urls.append(sw)
        phones = set()  # type: Set[str]
        fetched = False
        for u in urls:
            if not u or not extract_domain(u):
                continue
            key = u.split("?")[0]
            if key not in cache:
                cache[key] = site_phones(u)
            if cache[key] is not None:
                fetched = True
                phones |= cache[key]
        if not fetched:
            df.at[idx, "phone_check"] = "site_unavailable"
        else:
            stated = {a + b + c for a, b, c in PHONE_RE.findall(row["stated_phone"])}
            df.at[idx, "phone_check"] = "site_match" if stated & phones else "site_no_match"
    return df


def label_unresolved(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    df["unresolved_reason"] = ""
    un = df["resolution_status"] == "unresolved"
    sim = pd.to_numeric(df["match_similarity"], errors="coerce").fillna(0)
    df.loc[un & (sim >= threshold), "unresolved_reason"] = "same_name_outside_metro"
    df.loc[un & (sim < threshold), "unresolved_reason"] = "low_name_similarity"
    return df


def apply_manual(df: pd.DataFrame, wave: str) -> pd.DataFrame:
    """Merge human/web-search verification files from data/manual/<wave>/ (protocol 4.6).

    unresolved_verified.csv (name,city,state,label,...): label not_found -> ghost;
    found_local / found_elsewhere / found_closed / category_mismatch -> exists, not ghost.
    phones_verified.csv (place_name,city,state,stated_phone,label,...): overrides
    phone_check for automated non-matches with the manual label.
    """
    mdir = DATA_DIR / "manual" / wave
    lab = {}  # type: Dict[tuple, str]
    for fname in ("unresolved_verified.csv", "unresolved_sample.csv"):
        up = mdir / fname
        if up.exists():
            uv = pd.read_csv(up, dtype=str, keep_default_na=False)
            lab.update({(r["name"], r["city"], r["state"]): r["label"] for _, r in uv.iterrows() if r.get("label")})
    if lab:
        for idx, row in df[df["resolution_status"] == "unresolved"].iterrows():
            l = lab.get((row["name"], row["city"], row["state"]))
            if l:
                df.at[idx, "ghost_manual_check"] = l
                df.at[idx, "is_ghost"] = "1" if l == "not_found" else "0"
    pp = mdir / "phones_verified.csv"
    if pp.exists():
        pv = pd.read_csv(pp, dtype=str, keep_default_na=False)
        lab = {(r["place_name"], r["city"], r["state"], r["stated_phone"]): r["label"] for _, r in pv.iterrows()}
        for idx, row in df[df["phone_check"].isin(["site_no_match", "site_unavailable"])].iterrows():
            l = lab.get((row["place_name"], row["city"], row["state"], row["stated_phone"]))
            if l:
                df.at[idx, "phone_check"] = "manual_" + l
    return df


SAMPLE_PER_NICHE = 50
LABELS = ("found_local", "found_elsewhere", "found_closed", "category_mismatch", "not_found")


def draw_sample(df: pd.DataFrame, wave: str, per_niche: int = SAMPLE_PER_NICHE) -> pd.DataFrame:
    """Protocol 4.6: random sample of unique unresolved (name, city) pairs per niche,
    seed = wave id. Pairs already labeled (this or earlier waves) are excluded."""
    un = df[(df["resolution_status"] == "unresolved") & (df["ghost_manual_check"] == "")]
    pairs = (un.groupby(["niche", "name", "city", "state"]).size().reset_index(name="mentions"))
    seed = int(hashlib.sha1(wave.encode()).hexdigest()[:8], 16)
    parts = []
    for niche, g in pairs.groupby("niche"):
        parts.append(g.sample(n=min(per_niche, len(g)), random_state=seed))
    out = pd.concat(parts) if parts else pairs.head(0)
    for c in ("label", "real_name", "evidence_url", "note"):
        out[c] = ""
    return out


def estimate_ghost(df: pd.DataFrame, n_boot: int = 2000, seed: int = 7) -> pd.DataFrame:
    """Protocol 4.6: rate = unresolved share x mention-weighted label share in the labeled
    sample, per niche and per engine within niche, with bootstrap CI over labeled pairs."""
    import numpy as np
    rec = df[df["resolution_status"].isin(["resolved", "unresolved"])]
    rows = []
    rng = np.random.default_rng(seed)
    groups = [("all", n, g) for n, g in rec.groupby("niche")] + \
             [(e, n, g) for (n, e), g in rec.groupby(["niche", "engine"])]
    for engine, niche, g in groups:
        un = g[g["resolution_status"] == "unresolved"]
        lab = un[un["ghost_manual_check"].isin(LABELS)]
        u_share = len(un) / float(len(g)) if len(g) else 0.0
        pairs = lab.groupby(["name", "city"])
        w = pairs.size().values.astype(float)
        nf = pairs["ghost_manual_check"].first().eq("not_found").values.astype(float)
        el = pairs["ghost_manual_check"].first().eq("found_elsewhere").values.astype(float)
        row = {"niche": niche, "engine": engine, "mentions": len(g), "unresolved_share": round(u_share, 4),
               "labeled_pairs": len(w)}
        for name, x in (("ghost", nf), ("wrong_city", el)):
            if len(w) == 0:
                row.update({name + "_rate": None, name + "_ci_low": None, name + "_ci_high": None})
                continue
            point = u_share * (w * x).sum() / w.sum()
            idx = rng.integers(0, len(w), size=(n_boot, len(w)))
            boots = u_share * (w[idx] * x[idx]).sum(1) / w[idx].sum(1)
            row.update({name + "_rate": round(point, 4), name + "_ci_low": round(float(np.percentile(boots, 2.5)), 4),
                        name + "_ci_high": round(float(np.percentile(boots, 97.5)), 4)})
        rows.append(row)
    return pd.DataFrame(rows)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wave", required=True)
    ap.add_argument("--sample", action="store_true", help="Draw the protocol 4.6 manual-check sample (does not overwrite an existing one)")
    ap.add_argument("--threshold", type=float, default=None, help="Name similarity threshold (default: from config)")
    args = ap.parse_args(argv)
    from panel.config import load_panel
    thr = args.threshold if args.threshold is not None else float(load_panel()["resolution"]["name_similarity_threshold"])
    path = DATA_DIR / "processed" / args.wave / "resolved.csv"
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = check_phones(df)
    df = label_unresolved(df, thr)
    df = apply_manual(df, args.wave)
    df.to_csv(path, index=False)
    mdir = DATA_DIR / "manual" / args.wave
    if args.sample:
        sp = mdir / "unresolved_sample.csv"
        if sp.exists():
            print("Sample already exists, not redrawn: %s" % sp)
        else:
            mdir.mkdir(parents=True, exist_ok=True)
            draw_sample(df, args.wave).to_csv(sp, index=False)
            print("Sample written: %s" % sp)
    est = estimate_ghost(df)
    est.to_csv(DATA_DIR / "processed" / args.wave / "ghost_estimate.csv", index=False)
    print(df["phone_check"].replace("", pd.NA).dropna().value_counts().to_string())
    print(df["unresolved_reason"].replace("", pd.NA).dropna().value_counts().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
