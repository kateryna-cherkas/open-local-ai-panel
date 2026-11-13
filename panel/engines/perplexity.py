"""Perplexity Sonar.

Docs checked 2026-10-06:
  https://docs.perplexity.ai/api-reference/chat-completions-post
  https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/how-to
  https://docs.perplexity.ai/guides/user-location-filter-guide

Two modes (config `engines.perplexity.mode`):

chat_completions (default, matches protocol 3.3 wording "Sonar chat completions")
  POST https://api.perplexity.ai/chat/completions
  {"model": "sonar", "messages": [{"role": "user", "content": prompt}],
   "web_search_options": {"user_location": {"latitude", "longitude",
   "country": "US", "city", "region"}}}
  Response: choices[0].message.content; citations[] (URLs);
  search_results[] {title, url, date}; model.
  NOTE: Perplexity states Sonar chat-completions support ended 2026-09-27;
  requests keep working but are reformulated as Agent API requests server
  side. See BUILD_NOTES.md.

agent
  POST https://api.perplexity.ai/v1/agent
  {"preset": "medium", "input": prompt, "tools": [{"type": "web_search",
   "user_location": {...same fields...}}]}
  Response: output_text; output[] items of type "message" (content[].text)
  and "search_results" (results[].url); model.
"""
from typing import Any, Dict, List

from panel.engines.base import CallContext, Engine, ParsedResponse
from panel.env import require_secret
from panel.mockdata import recommended_for, render_answer


class PerplexityEngine(Engine):
    id = "perplexity"
    secret_names = ["PERPLEXITY_API_KEY"]

    @property
    def mode(self) -> str:
        return str(self.cfg.get("mode", "chat_completions"))

    def _user_location(self, ctx: CallContext) -> Dict[str, Any]:
        return {
            "latitude": ctx.city.latitude,
            "longitude": ctx.city.longitude,
            "country": "US",
            "city": ctx.city.city,
            "region": ctx.city.state_name,
        }

    def build_request(self, ctx: CallContext):
        headers = {"Authorization": "Bearer " + require_secret("PERPLEXITY_API_KEY")}
        if self.mode == "agent":
            payload = {
                "preset": self.cfg.get("agent_preset", "medium"),
                "input": ctx.prompt,
                "tools": [{"type": "web_search", "user_location": self._user_location(ctx)}],
            }
            return self.cfg["agent_endpoint"], payload, headers, None
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": ctx.prompt}],
            "web_search_options": {"user_location": self._user_location(ctx)},
        }
        return self.cfg["endpoint"], payload, headers, None

    def parse_response(self, raw: Dict[str, Any]) -> ParsedResponse:
        if "choices" in raw:  # chat completions shape
            text = ""
            choices = raw.get("choices") or []
            if choices:
                text = (choices[0].get("message") or {}).get("content", "") or ""
            cites = list(raw.get("citations") or [])
            for sr in raw.get("search_results") or []:
                if sr.get("url"):
                    cites.append(sr["url"])
            return ParsedResponse(text.strip(), cites, raw.get("model"),
                                  {"search_results": raw.get("search_results"), "usage": raw.get("usage")})
        # agent API shape
        texts = []  # type: List[str]
        cites = []  # type: List[str]
        for item in raw.get("output") or []:
            if item.get("type") == "message":
                for part in item.get("content") or []:
                    if part.get("type") in ("output_text", "text") and part.get("text"):
                        texts.append(part["text"])
            elif item.get("type") == "search_results":
                for r in item.get("results") or []:
                    if r.get("url"):
                        cites.append(r["url"])
        text = "\n".join(texts) if texts else (raw.get("output_text") or "")
        return ParsedResponse(text.strip(), cites, raw.get("model"), {"usage": raw.get("usage")})

    def mock_raw(self, ctx: CallContext) -> Dict[str, Any]:
        recs = recommended_for(self.id, ctx.city, ctx.niche, ctx.template_id, ctx.repeat)
        text = render_answer(self.id, ctx.city, ctx.niche, recs)
        urls = [r["website"] for r in recs if r["state_site"]] + ["https://www.reddit.com/r/%s/" % ctx.city.city.replace(" ", "")]
        if self.mode == "agent":
            fx = self.load_fixture("perplexity_agent_response.json")
            fx["output_text"] = text
            for item in fx["output"]:
                if item["type"] == "message":
                    item["content"] = [{"type": "output_text", "text": text}]
                elif item["type"] == "search_results":
                    item["results"] = [{"id": i + 1, "url": u, "title": u, "snippet": ""} for i, u in enumerate(urls)]
            return fx
        fx = self.load_fixture("perplexity_response.json")
        fx["model"] = self.model
        fx["choices"][0]["message"]["content"] = text
        fx["citations"] = urls
        fx["search_results"] = [{"title": u, "url": u, "date": None} for u in urls]
        return fx
