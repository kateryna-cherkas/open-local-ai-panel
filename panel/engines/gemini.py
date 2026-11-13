"""Gemini generateContent with Google Search grounding.

Doc checked 2026-10-06:
  https://ai.google.dev/gemini-api/docs/google-search
  https://ai.google.dev/api/generate-content
Request:  POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
          header x-goog-api-key; body {"contents": [{"parts": [{"text": prompt}]}],
          "tools": [{"google_search": {}}]}
Response: candidates[0].content.parts[].text; candidates[0].groundingMetadata
          .groundingChunks[].web.{uri,title}, .webSearchQueries[]; modelVersion.
Location: protocol 3.3 says city/state appear in the prompt text only; no
          location parameter is sent.
"""
from typing import Any, Dict, List

import re

from panel.engines.base import CallContext, Engine, ParsedResponse
from panel.env import require_secret
from panel.mockdata import recommended_for, render_answer


DOMAIN_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$", re.I)


class GeminiEngine(Engine):
    id = "gemini"
    secret_names = ["GEMINI_API_KEY"]

    def build_request(self, ctx: CallContext):
        url = self.cfg["endpoint"].format(model=self.model)
        payload = {
            "contents": [{"role": "user", "parts": [{"text": ctx.prompt}]}],
            "tools": [{"google_search": {}}],
        }
        headers = {"x-goog-api-key": require_secret("GEMINI_API_KEY")}
        return url, payload, headers, None

    def parse_response(self, raw: Dict[str, Any]) -> ParsedResponse:
        texts = []  # type: List[str]
        cites = []  # type: List[str]
        queries = []  # type: List[str]
        redirects = []  # type: List[str]
        cands = raw.get("candidates") or []
        if cands:
            cand = cands[0]
            for part in (cand.get("content") or {}).get("parts", []) or []:
                if "text" in part:
                    texts.append(part["text"])
            gm = cand.get("groundingMetadata") or {}
            for chunk in gm.get("groundingChunks", []) or []:
                web = chunk.get("web") or {}
                uri, title = web.get("uri"), (web.get("title") or "").strip()
                if not uri:
                    continue
                redirects.append(uri)
                # Live API returns opaque vertexaisearch redirect URIs; the source
                # domain is only exposed in `title`. Use it so source-mix works.
                if "grounding-api-redirect" in uri and DOMAIN_RE.match(title):
                    cites.append("https://%s/" % title.lower())
                else:
                    cites.append(uri)
            queries = list(gm.get("webSearchQueries", []) or [])
        return ParsedResponse("\n".join(texts).strip(), cites, raw.get("modelVersion"),
                              {"search_queries": queries, "usage": raw.get("usageMetadata"),
                               "grounding_redirects": redirects})

    def mock_raw(self, ctx: CallContext) -> Dict[str, Any]:
        fx = self.load_fixture("gemini_response.json")
        recs = recommended_for(self.id, ctx.city, ctx.niche, ctx.template_id, ctx.repeat)
        fx["modelVersion"] = self.model
        cand = fx["candidates"][0]
        cand["content"]["parts"] = [{"text": render_answer(self.id, ctx.city, ctx.niche, recs)}]
        cand["groundingMetadata"]["groundingChunks"] = [
            {"web": {"uri": r["website"], "title": r["display_name"]}} for r in recs if r["state_site"]
        ] + [{"web": {"uri": "https://www.bbb.org/us/%s/%s" % (ctx.city.state.lower(), ctx.city.city.lower()), "title": "BBB"}}]
        cand["groundingMetadata"]["webSearchQueries"] = ["best %s %s %s" % (ctx.niche, ctx.city.city, ctx.city.state)]
        return fx
