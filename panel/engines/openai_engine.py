"""OpenAI Responses API with the `web_search` tool.

Doc checked 2026-10-06: https://developers.openai.com/api/docs/guides/tools-web-search
Request:  POST https://api.openai.com/v1/responses
          {"model", "input": <prompt>, "tools": [{"type": "web_search",
            "search_context_size", "user_location": {"type": "approximate",
            "country", "city", "region", "timezone"}}]}
Response: output[] items; type "web_search_call" (action.query) and type
          "message" whose content[] has {"type": "output_text", "text",
          "annotations": [{"type": "url_citation", "url", "title", ...}]}.
"""
from typing import Any, Dict, List, Optional, Tuple

from panel.engines.base import CallContext, Engine, ParsedResponse, user_location_approximate
from panel.env import require_secret
from panel.mockdata import recommended_for, render_answer


class OpenAIEngine(Engine):
    id = "openai"
    secret_names = ["OPENAI_API_KEY"]

    def build_request(self, ctx: CallContext):
        tool = {
            "type": "web_search",
            "search_context_size": self.cfg.get("search_context_size", "medium"),
            "user_location": user_location_approximate(ctx.city),
        }
        payload = {"model": self.model, "input": ctx.prompt, "tools": [tool]}
        headers = {"Authorization": "Bearer " + require_secret("OPENAI_API_KEY")}
        return self.cfg["endpoint"], payload, headers, None

    def parse_response(self, raw: Dict[str, Any]) -> ParsedResponse:
        texts = []  # type: List[str]
        cites = []  # type: List[str]
        queries = []  # type: List[str]
        for item in raw.get("output", []) or []:
            itype = item.get("type")
            if itype == "web_search_call":
                q = (item.get("action") or {}).get("query")
                if q:
                    queries.append(q)
            elif itype == "message":
                for part in item.get("content", []) or []:
                    if part.get("type") == "output_text":
                        texts.append(part.get("text", ""))
                        for ann in part.get("annotations", []) or []:
                            if ann.get("type") == "url_citation" and ann.get("url"):
                                cites.append(ann["url"])
        if not texts and raw.get("output_text"):
            texts.append(raw["output_text"])
        return ParsedResponse("\n".join(texts).strip(), cites, raw.get("model"),
                              {"search_queries": queries, "usage": raw.get("usage")})

    def mock_raw(self, ctx: CallContext) -> Dict[str, Any]:
        fx = self.load_fixture("openai_response.json")
        recs = recommended_for(self.id, ctx.city, ctx.niche, ctx.template_id, ctx.repeat)
        text = render_answer(self.id, ctx.city, ctx.niche, recs)
        fx["model"] = self.model
        msg = [i for i in fx["output"] if i.get("type") == "message"][0]
        part = msg["content"][0]
        part["text"] = text
        part["annotations"] = [
            {"type": "url_citation", "url": r["website"], "title": r["display_name"], "start_index": 0, "end_index": 1}
            for r in recs if r["state_site"]
        ] + [{"type": "url_citation", "url": "https://www.yelp.com/search?find_desc=%s" % ctx.niche,
              "title": "Yelp", "start_index": 0, "end_index": 1}]
        return fx
