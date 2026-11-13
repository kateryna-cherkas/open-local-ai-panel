"""Load panel configuration (config/panel.json, config/cities.csv)."""
import csv
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from panel.paths import CONFIG_DIR

PANEL_JSON = CONFIG_DIR / "panel.json"
CITIES_CSV = CONFIG_DIR / "cities.csv"
DOMAIN_CATEGORIES_JSON = CONFIG_DIR / "domain_categories.json"

ENGINE_IDS = ["openai", "gemini", "perplexity", "claude", "google_aio"]
TEMPLATE_IDS = ["P1", "P2", "P3"]


@dataclass(frozen=True)
class City:
    city: str
    state: str
    state_name: str
    tier: str
    latitude: float
    longitude: float
    population: Optional[int]
    dataforseo_location_name: str
    timezone: str

    @property
    def key(self) -> str:
        return "%s_%s" % (self.city.replace(" ", "_"), self.state)

    @property
    def label(self) -> str:
        return "%s, %s" % (self.city, self.state)


def load_panel(path=PANEL_JSON) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_cities(path=CITIES_CSV) -> List[City]:
    cities = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            pop = row.get("population", "").strip()
            cities.append(
                City(
                    city=row["city"].strip(),
                    state=row["state"].strip(),
                    state_name=row["state_name"].strip(),
                    tier=row["tier"].strip(),
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                    population=int(pop) if pop else None,
                    dataforseo_location_name=row["dataforseo_location_name"].strip(),
                    timezone=row.get("timezone", "").strip(),
                )
            )
    return cities


def load_domain_categories(path=DOMAIN_CATEGORIES_JSON) -> Dict[str, str]:
    """Return a flat domain -> category map."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    out = {}  # type: Dict[str, str]
    for category, domains in raw.items():
        if category.startswith("_"):
            continue
        for d in domains:
            out[d.lower()] = category
    return out


def prompt_text(panel: Dict[str, Any], niche: str, template_id: str, city: City) -> str:
    """Render a prompt template verbatim from protocol section 3.1."""
    template = panel["prompt_templates"][niche][template_id]
    return template.format(city=city.city, state=city.state)


def iter_prompts(panel: Dict[str, Any]):
    """Yield (niche, template_id) for all 9 templates in protocol order."""
    for niche in panel["niches"]:
        for template_id in TEMPLATE_IDS:
            yield niche, template_id


def select_cities(cities: List[City], wanted: Optional[List[str]]) -> List[City]:
    if not wanted:
        return cities
    wanted_norm = {w.strip().lower() for w in wanted}
    out = []
    for c in cities:
        if c.city.lower() in wanted_norm or c.key.lower() in wanted_norm or c.label.lower() in wanted_norm:
            out.append(c)
    missing = wanted_norm - {c.city.lower() for c in out} - {c.key.lower() for c in out} - {c.label.lower() for c in out}
    if missing:
        raise SystemExit("Unknown cities: %s" % ", ".join(sorted(missing)))
    return out
