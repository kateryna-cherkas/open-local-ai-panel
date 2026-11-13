"""Google Places API (New) Text Search, used for resolution (protocol 3.5, 4.4).

Doc checked 2026-10-06:
  https://developers.google.com/maps/documentation/places/web-service/text-search
Request:  POST https://places.googleapis.com/v1/places:searchText
          headers X-Goog-Api-Key, X-Goog-FieldMask (required; no default fields)
          {"textQuery": "<name> <city>, <state>", "pageSize": 5,
           "locationBias": {"circle": {"center": {"latitude", "longitude"}, "radius": <=50000}}}
Response: places[] {id, displayName.text, formattedAddress, nationalPhoneNumber,
          internationalPhoneNumber, websiteUri, rating, userRatingCount,
          businessStatus (OPERATIONAL | CLOSED_TEMPORARILY | CLOSED_PERMANENTLY),
          types[], location{latitude, longitude}}
Billing:  field mask tiers: rating / phone / website are "Enterprise" SKU fields.
"""
import hashlib
import json
from typing import Any, Dict, List, Optional

from panel.config import City
from panel.env import require_secret
from panel.http import post_json
from panel.mockdata import _NICHE_WORDS, city_pool
from panel.names import normalize_name


class PlacesClient:
    def __init__(self, cfg: Dict[str, Any], retry_cfg: Optional[Dict[str, Any]] = None, mock: bool = False):
        self.cfg = cfg
        self.retry_cfg = retry_cfg or {}
        self.mock = mock

    def build_request(self, name: str, city: City):
        payload = {
            "textQuery": "%s %s, %s" % (name, city.city, city.state),
            "pageSize": int(self.cfg.get("page_size", 5)),
            "locationBias": {"circle": {
                "center": {"latitude": city.latitude, "longitude": city.longitude},
                "radius": float(min(50000, int(self.cfg.get("location_bias_radius_m", 50000)))),
            }},
        }
        # Service-area businesses (no public address: most cleaners, appliance repair,
        # many roofers) are excluded by Text Search unless this flag is set.
        if self.cfg.get("include_pure_service_area_businesses", True):
            payload["includePureServiceAreaBusinesses"] = True
        headers = {
            "X-Goog-Api-Key": require_secret("GOOGLE_PLACES_API_KEY"),
            "X-Goog-FieldMask": self.cfg["field_mask"],
        }
        return self.cfg["endpoint"], payload, headers

    def search(self, name: str, city: City) -> Dict[str, Any]:
        """Return the raw searchText response (mock or live)."""
        if self.mock:
            return self.mock_raw(name, city)
        url, payload, headers = self.build_request(name, city)
        raw, _ = post_json(url, payload, headers=headers, retry=self.retry_cfg,
                           rate_key="places", rpm=self.cfg.get("requests_per_minute"))
        return raw

    @staticmethod
    def candidates(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for p in raw.get("places") or []:
            loc = p.get("location") or {}
            out.append({
                "place_id": p.get("id"),
                "name": (p.get("displayName") or {}).get("text"),
                "formatted_address": p.get("formattedAddress"),
                "phone": p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber"),
                "website": p.get("websiteUri"),
                "rating": p.get("rating"),
                "user_rating_count": p.get("userRatingCount"),
                "business_status": p.get("businessStatus"),
                "types": p.get("types") or [],
                "latitude": loc.get("latitude"),
                "longitude": loc.get("longitude"),
            })
        return out

    # ---- mock ---------------------------------------------------------
    def mock_raw(self, name: str, city: City) -> Dict[str, Any]:
        """Match the query against the synthetic pools for all three niches.
        'ghost' pool members return no places; 'closed' members return
        CLOSED_PERMANENTLY; unknown names return an unrelated place so the
        similarity threshold is exercised."""
        target = normalize_name(name)
        for niche in _NICHE_WORDS:
            for b in city_pool(city, niche):
                if normalize_name(b["base_name"]) == target:
                    if b["ghost"]:
                        return {}
                    return {"places": [self._place(b, city, niche)]}
        # Unknown name -> a distractor with low similarity
        pool = city_pool(city, "plumbing")
        return {"places": [self._place(pool[0], city, "plumbing")]}

    @staticmethod
    def _place(b: Dict[str, str], city: City, niche: str) -> Dict[str, Any]:
        pid = "ChIJmock" + hashlib.sha1(b["id"].encode("utf-8")).hexdigest()[:20]
        types = {"roofing": ["roofing_contractor"], "hvac": ["hvac_contractor"], "plumbing": ["plumber"],
                 "construction": ["general_contractor"]}.get(niche, ["service"])
        return {
            "id": pid,
            "displayName": {"text": b["base_name"], "languageCode": "en"},
            "formattedAddress": b["address"] + " 00000, USA",
            "nationalPhoneNumber": b["phone"],
            "websiteUri": b["website"],
            "rating": float(b["rating"]),
            "userRatingCount": int(b["reviews"]),
            "businessStatus": "CLOSED_PERMANENTLY" if b["closed"] else "OPERATIONAL",
            "types": types + ["point_of_interest", "establishment"],
            "location": {"latitude": city.latitude + 0.01, "longitude": city.longitude - 0.01},
        }


def cache_key(name: str, city: City) -> str:
    return hashlib.sha1(json.dumps([normalize_name(name), city.key]).encode("utf-8")).hexdigest()
