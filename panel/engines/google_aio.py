"""Google AI Overview and Google local pack via DataForSEO SERP API.

Doc checked 2026-10-06: https://docs.dataforseo.com/v3/serp/google/organic/live/advanced/
Request:  POST https://api.dataforseo.com/v3/serp/google/organic/live/advanced
          HTTP Basic auth (DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD).
          Body is a JSON array of task objects:
          [{"keyword", "location_name": "Houston,Texas,United States",
            "language_code": "en", "device": "desktop", "depth": 10,
            "load_async_ai_overview": true}]
Response: tasks[0].result[0].items[] with "type":
          ai_overview  -> items[] {type: ai_overview_element, text},
                          references[] {url, domain, title, source},
                          asynchronous_ai_overview (bool)
          local_pack   -> title, phone, url, domain, rating{value,votes_count},
                          rank_absolute, rank_group, cid, is_paid
          tasks[0].cost is the charge for the request.
The same SERP call returns both the AI Overview block and the local pack; the
collector uses one adapter class with two entry points.
"""
from typing import Any, Dict, List, Optional

from panel.engines.base import CallContext, Engine, ParsedResponse
from panel.env import require_secret
from panel.mockdata import local_pack_for, recommended_for

NO_AIO_MARKER = "[no_ai_overview]"


class GoogleAIOEngine(Engine):
    id = "google_aio"
    secret_names = ["DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD"]

    def _auth(self):
        return (require_secret("DATAFORSEO_LOGIN"), require_secret("DATAFORSEO_PASSWORD"))

    def _task(self, ctx: CallContext, load_aio: bool) -> Dict[str, Any]:
        task = {
            "keyword": ctx.prompt,
            "location_name": ctx.city.dataforseo_location_name,
            "language_code": "en",
            "device": self.cfg.get("device", "desktop"),
            "depth": int(self.cfg.get("depth", 10)),
        }
        if load_aio:
            task["load_async_ai_overview"] = bool(self.cfg.get("load_async_ai_overview", True))
        return task

    def build_request(self, ctx: CallContext):
        # DataForSEO expects a JSON array body; post_json serialises whatever it gets.
        return self.cfg["endpoint"], [self._task(ctx, load_aio=True)], {}, self._auth()

    def build_local_pack_request(self, ctx: CallContext):
        return self.cfg["endpoint"], [self._task(ctx, load_aio=False)], {}, self._auth()

    # ---- parsing ------------------------------------------------------
    @staticmethod
    def _items(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        tasks = raw.get("tasks") or []
        if not tasks:
            return []
        task = tasks[0]
        if task.get("status_code") and task["status_code"] != 20000:
            raise RuntimeError("DataForSEO task error %s: %s" % (task.get("status_code"), task.get("status_message")))
        results = task.get("result") or []
        if not results:
            return []
        return results[0].get("items") or []

    def parse_response(self, raw: Dict[str, Any]) -> ParsedResponse:
        texts = []  # type: List[str]
        cites = []  # type: List[str]
        found = False
        asynchronous = None  # type: Optional[bool]
        for item in self._items(raw):
            if item.get("type") != "ai_overview":
                continue
            found = True
            asynchronous = item.get("asynchronous_ai_overview")
            if item.get("text"):
                texts.append(item["text"])
            for el in item.get("items") or []:
                if el.get("text"):
                    texts.append(el["text"])
                for ref in el.get("references") or []:
                    if ref.get("url"):
                        cites.append(ref["url"])
            for ref in item.get("references") or []:
                if ref.get("url"):
                    cites.append(ref["url"])
        text = "\n".join(texts).strip() if found else NO_AIO_MARKER
        tasks = raw.get("tasks") or [{}]
        return ParsedResponse(text, cites, self.model, {
            "ai_overview_present": found, "asynchronous_ai_overview": asynchronous,
            "cost_usd": tasks[0].get("cost"), "check_url": ((tasks[0].get("result") or [{}])[0]).get("check_url"),
        })

    def parse_local_pack(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for item in self._items(raw):
            if item.get("type") != "local_pack":
                continue
            rating = item.get("rating") or {}
            out.append({
                "title": item.get("title"),
                "phone": item.get("phone"),
                "url": item.get("url"),
                "domain": item.get("domain"),
                "description": item.get("description"),
                "rating": rating.get("value"),
                "votes_count": rating.get("votes_count"),
                "rank_group": item.get("rank_group"),
                "rank_absolute": item.get("rank_absolute"),
                "cid": item.get("cid"),
                "is_paid": item.get("is_paid"),
            })
        return out

    # ---- mock ---------------------------------------------------------
    def mock_raw(self, ctx: CallContext) -> Dict[str, Any]:
        fx = self.load_fixture("dataforseo_response.json")
        recs = recommended_for(self.id, ctx.city, ctx.niche, ctx.template_id, ctx.repeat, n=4)
        result = fx["tasks"][0]["result"][0]
        result["keyword"] = ctx.prompt
        items = []
        # Every 7th repeat has no AI Overview, to exercise the missing-AIO path.
        if (ctx.repeat + len(ctx.city.city)) % 7 != 3:
            items.append({
                "type": "ai_overview", "rank_group": 1, "rank_absolute": 1, "position": "left",
                "asynchronous_ai_overview": True, "text": None,
                "items": [{"type": "ai_overview_element",
                           "text": "Mock AI Overview: well-reviewed %s options in %s include %s." % (
                               ctx.niche, ctx.city.label, "; ".join(r["display_name"] for r in recs)),
                           "references": None}],
                "references": [{"type": "ai_overview_reference", "source": r["display_name"],
                                "domain": r["website"].split("//")[1], "url": r["website"], "title": r["display_name"], "text": ""}
                               for r in recs if r["state_site"]]
                + [{"type": "ai_overview_reference", "source": "Yelp", "domain": "www.yelp.com",
                    "url": "https://www.yelp.com/search?find_desc=%s&find_loc=%s" % (ctx.niche, ctx.city.city), "title": "Yelp", "text": ""}],
            })
        items.extend(self._mock_local_pack_items(ctx))
        result["items"] = items
        result["item_types"] = sorted({i["type"] for i in items})
        return fx

    def mock_local_pack_raw(self, ctx: CallContext) -> Dict[str, Any]:
        fx = self.load_fixture("dataforseo_response.json")
        result = fx["tasks"][0]["result"][0]
        result["keyword"] = ctx.prompt
        items = self._mock_local_pack_items(ctx)
        result["items"] = items
        result["item_types"] = ["local_pack"]
        return fx

    def _mock_local_pack_items(self, ctx: CallContext) -> List[Dict[str, Any]]:
        pack = local_pack_for(ctx.city, ctx.niche, ctx.template_id)
        return [{
            "type": "local_pack", "rank_group": i + 1, "rank_absolute": i + 2, "position": "left",
            "title": b["base_name"], "description": "Mock local pack entry", "domain": b["website"].split("//")[1],
            "phone": b["phone"], "url": b["website"], "is_paid": False,
            "rating": {"rating_type": "Max5", "value": float(b["rating"]), "votes_count": int(b["reviews"]), "rating_max": 5},
            "cid": "mock-cid-%s" % b["id"],
        } for i, b in enumerate(pack)]
