"""Anthropic Messages API with the server-side web search tool.

Doc checked 2026-10-06:
  https://platform.claude.com/docs/en/docs/agents-and-tools/tool-use/web-search-tool
Request:  POST https://api.anthropic.com/v1/messages
          headers x-api-key, anthropic-version: 2023-06-01
          {"model", "max_tokens", "messages": [{"role": "user", "content": prompt}],
           "tools": [{"type": "web_search_20260209", "name": "web_search",
                      "max_uses": 5, "allowed_callers": ["direct"],
                      "user_location": {"type": "approximate", "city", "region",
                                        "country": "US", "timezone"}}]}
          Tool type is config-driven: web_search_20250305 (basic, all models),
          web_search_20260209 (dynamic filtering, Claude 4.6+), web_search_20260318.
          allowed_callers ["direct"] disables dynamic filtering so result blocks
          are flat; the parser also walks nested blocks in case it is enabled.
Response: content[] blocks: "text" (with citations[] of type
          web_search_result_location {url, title}), "server_tool_use"
          (input.query), "web_search_tool_result" (content[] of
          web_search_result {url, title} or a single error object); model.
"""
from typing import Any, Dict, List

from panel.engines.base import CallContext, Engine, ParsedResponse, user_location_approximate
from panel.env import require_secret
from panel.mockdata import recommended_for, render_answer


class ClaudeEngine(Engine):
    id = "claude"
    secret_names = ["ANTHROPIC_API_KEY"]

    def build_request(self, ctx: CallContext):
        tool = {
            "type": self.cfg.get("web_search_tool_type", "web_search_20260209"),
            "name": "web_search",
            "max_uses": int(self.cfg.get("max_uses", 5)),
            "user_location": user_location_approximate(ctx.city),
        }
        callers = self.cfg.get("allowed_callers")
        if callers:
            tool["allowed_callers"] = list(callers)
        payload = {
            "model": self.model,
            "max_tokens": int(self.cfg.get("max_tokens", 4096)),
            "messages": [{"role": "user", "content": ctx.prompt}],
            "tools": [tool],
        }
        headers = {
            "x-api-key": require_secret("ANTHROPIC_API_KEY"),
            "anthropic-version": self.cfg.get("anthropic_version", "2023-06-01"),
        }
        return self.cfg["endpoint"], payload, headers, None

    def parse_response(self, raw: Dict[str, Any]) -> ParsedResponse:
        texts = []  # type: List[str]
        cites = []  # type: List[str]
        queries = []  # type: List[str]
        search_errors = []  # type: List[str]

        def walk(blocks):
            for b in blocks or []:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text":
                    texts.append(b.get("text", ""))
                    for c in b.get("citations") or []:
                        if c.get("url"):
                            cites.append(c["url"])
                elif bt == "server_tool_use":
                    q = (b.get("input") or {}).get("query")
                    if q:
                        queries.append(q)
                elif bt == "web_search_tool_result":
                    content = b.get("content")
                    if isinstance(content, dict):  # error object
                        search_errors.append(str(content.get("error_code")))
                    else:
                        for r in content or []:
                            if isinstance(r, dict) and r.get("type") == "web_search_result" and r.get("url"):
                                # results seen by the model but not necessarily cited; kept in extra
                                seen_results.append(r["url"])
                elif "content" in b and isinstance(b.get("content"), list):
                    walk(b["content"])  # nested (dynamic filtering) blocks

        seen_results = []  # type: List[str]
        walk(raw.get("content"))
        return ParsedResponse("".join(texts).strip(), cites, raw.get("model"), {
            "search_queries": queries, "search_result_urls": seen_results,
            "search_errors": search_errors, "stop_reason": raw.get("stop_reason"), "usage": raw.get("usage"),
        })

    def mock_raw(self, ctx: CallContext) -> Dict[str, Any]:
        fx = self.load_fixture("claude_response.json")
        recs = recommended_for(self.id, ctx.city, ctx.niche, ctx.template_id, ctx.repeat)
        text = render_answer(self.id, ctx.city, ctx.niche, recs)
        fx["model"] = self.model
        cited = [{"type": "web_search_result_location", "url": r["website"], "title": r["display_name"],
                  "encrypted_index": "mock", "cited_text": r["display_name"]} for r in recs if r["state_site"]]
        cited.append({"type": "web_search_result_location", "url": "https://www.angi.com/companylist/%s/%s.htm" % (ctx.city.city.lower(), ctx.niche),
                      "title": "Angi", "encrypted_index": "mock", "cited_text": "Angi"})
        new_content = []
        for block in fx["content"]:
            if block["type"] == "text" and block.get("citations") is not None:
                block["text"] = text
                block["citations"] = cited
            elif block["type"] == "text":
                block["text"] = "I'll search for %s companies in %s." % (ctx.niche, ctx.city.label)
            elif block["type"] == "server_tool_use":
                block["input"] = {"query": "best %s %s %s" % (ctx.niche, ctx.city.city, ctx.city.state)}
            elif block["type"] == "web_search_tool_result":
                block["content"] = [{"type": "web_search_result", "url": c["url"], "title": c["title"],
                                     "encrypted_content": "mock", "page_age": None} for c in cited]
            new_content.append(block)
        fx["content"] = new_content
        return fx
