# Build notes (v0.1, 2026-10-06)

Implementation decisions, API verification, and points where the protocol was
ambiguous or not implementable as written. Nothing here changes prompts,
cities, engines, metrics or thresholds; where an interpretation was needed it
is stated so it can be reviewed before the first wave.

## 1. API documentation checked (all on 2026-10-06, via the live docs)

| Engine | Doc URL | Request shape used |
|---|---|---|
| openai | https://developers.openai.com/api/docs/guides/tools-web-search (redirect from platform.openai.com) ; models/pricing at https://developers.openai.com/api/docs/models and /pricing | `POST /v1/responses` `{model, input, tools:[{type:"web_search", search_context_size, user_location:{type:"approximate", country, city, region, timezone}}]}`. Text in `output[].type=="message"` -> `content[].output_text.text`; citations in `annotations[].type=="url_citation".url`. Web search priced at $10 / 1k calls plus tokens. |
| gemini | https://ai.google.dev/gemini-api/docs/google-search ; https://ai.google.dev/api/generate-content ; https://ai.google.dev/gemini-api/docs/models | `POST /v1beta/models/{model}:generateContent` header `x-goog-api-key`, `{contents:[{role:"user",parts:[{text}]}], tools:[{google_search:{}}]}`. Text in `candidates[0].content.parts[].text`; citations in `groundingMetadata.groundingChunks[].web.uri`; model string in `modelVersion`. Google also documents a newer `/v1beta/interactions` surface with `tools:[{type:"google_search"}]`; the protocol names generateContent, so that is what is implemented. |
| perplexity | https://docs.perplexity.ai/api-reference/chat-completions-post ; https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/how-to ; https://docs.perplexity.ai/guides/user-location-filter-guide | `POST /chat/completions` `{model:"sonar", messages, web_search_options:{user_location:{latitude, longitude, country:"US", city, region}}}`; text in `choices[0].message.content`, citations in `citations[]` and `search_results[].url`. **Important:** the docs state "Sonar Chat Completions support ended on September 27, 2026. Synchronous and streaming requests keep working: they are being reformulated as Agent API requests." The adapter therefore also implements the Agent API (`POST /v1/agent` with `preset` and `tools:[{type:"web_search", user_location:{...}}]`), selectable with `engines.perplexity.mode = "agent"`. Default stays `chat_completions` to match protocol 3.3 wording; the returned `model` string is logged either way so the series can be flagged if Perplexity changes the backing model. Decide before wave 1 and record the choice in CHANGELOG. |
| claude | https://platform.claude.com/docs/en/docs/agents-and-tools/tool-use/web-search-tool (redirect from docs.anthropic.com) | `POST /v1/messages` headers `x-api-key`, `anthropic-version: 2023-06-01`; `{model, max_tokens, messages:[{role:"user",content}], tools:[{type:"web_search_20260209", name:"web_search", max_uses:5, allowed_callers:["direct"], user_location:{type:"approximate", city, region, country:"US", timezone}}]}`. Three tool versions exist (`web_search_20250305`, `_20260209` with dynamic filtering, `_20260318`); `_20260209+` defaults to running inside code execution, so `allowed_callers:["direct"]` is sent to keep result blocks flat. Text in `content[].type=="text".text`; citations in `citations[].type=="web_search_result_location".url`. Tool errors arrive as HTTP 200 with `web_search_tool_result.content` being an object with `error_code`; recorded in `extra.search_errors`. Web search $10 / 1k searches plus tokens. |
| google_aio | https://docs.dataforseo.com/v3/serp/google/organic/live/advanced/ | `POST /v3/serp/google/organic/live/advanced`, HTTP Basic auth, body is an **array** of tasks `[{keyword, location_name:"Houston,Texas,United States", language_code:"en", device:"desktop", depth:10, load_async_ai_overview:true}]`. Items: `ai_overview` (`items[].text`, `references[].url`, `asynchronous_ai_overview`) and `local_pack` (`title, phone, url, domain, rating{value, votes_count}, rank_absolute, cid`). `load_async_ai_overview` adds $0.002 per request. `tasks[0].cost` is logged. |
| places | https://developers.google.com/maps/documentation/places/web-service/text-search | `POST https://places.googleapis.com/v1/places:searchText` headers `X-Goog-Api-Key`, `X-Goog-FieldMask` (required, no default); body `{textQuery:"<name> <city>, <state>", pageSize:5, locationBias:{circle:{center:{latitude, longitude}, radius<=50000}}}`. Response `places[]` with `id, displayName.text, formattedAddress, nationalPhoneNumber, websiteUri, rating, userRatingCount, businessStatus, types, location`. Rating/phone/website put the call in the Enterprise SKU. |

Model defaults (all config-driven in `config/panel.json`): `gpt-6.1-sol`,
`gemini-3.5-flash`, `sonar`, `claude-opus-5-5`, extractor `claude-haiku-4-5`.
The brief suggested `claude-haiku-4-5-20251001`; Anthropic's current docs list
the model id without a date suffix (`claude-haiku-4-5`), so that is used.

## 2. Interpretations of the protocol

- **Cell definition (5).** "Per engine x niche x city x wave" is implemented
  literally; a cell holds the 3 templates x k = 5 repeats = 15 responses.
  Recommendation Rate for business b is the share of those responses (that
  returned without API error) that name b.
- **Within-wave stability (5).** Computed per template (the k = 5 repeats of
  the *same* prompt), as mean pairwise Jaccard of the recommended-business
  sets, then averaged over the 3 templates to the cell level. Resolved
  businesses are keyed by `place_id`; unresolved ones by normalised name, so
  spelling variants of an unresolved name still count as the same business.
- **Top-3 share and ghost/closed/fact-error rates (5)** use all *recommended*
  mentions as denominator (mentions flagged "mentioned, not recommended" by
  the extractor are excluded, per 4.2).
- **Distinct count (5)** counts unique *resolved* businesses only, as written.
  `unresolved_rate` is reported alongside so the two can be read together.
- **Pack overlap (5).** Local-pack entries carry no `place_id`, only
  `title`, `cid`, phone and URL. Overlap is matched by name using the same
  token-set ratio >= 0.85 as resolution, against the pack for the same
  template x city. A stricter cid->place_id join would need an extra Places
  lookup per pack entry; left out of v0.1 and noted here.
- **Between-wave persistence (5).** Needs two processed waves; computed by
  `panel.metrics --previous-wave`. Keys are engine x niche x city x business.
- **Ghost (4.5)** requires a manual search. `resolve.py` leaves `is_ghost`
  blank for unresolved businesses until a human fills `ghost_manual_check`
  (`not_found` / `found`). Until then the ghost rate reads 0 and the report
  says so. `unresolved_rate` is the upper bound.
- **Fact error (4.5)** is evaluated only where the AI answer *stated* the
  field (phone digits; website registrable domain; street line tokens).
  Address comparison is a containment test on the street line after
  abbreviation normalisation; it is deliberately lenient and a sample should
  be hand-checked.
- **Bootstrap CIs (6)** resample cities with replacement (1,000 draws, fixed
  seed); the per-city value is first averaged within the city so each city
  counts once at every level (engine, engine x niche, engine x tier).
- **Source mix (5).** Domain categories live in `config/domain_categories.json`.
  Unknown domains default to `business_site` (an uncategorised cited domain is
  most often the business's own site) with `.gov/.edu` -> government and a
  small news-word heuristic -> media. Review the top of
  `source_domains.csv` after each wave and extend the map.
- **Gemini location (3.3).** No location parameter is sent; city/state appear
  in the prompt only, as specified.
- **"No system prompt, no history" (3.1)** holds for all engines; tests assert
  that no `system`/`systemInstruction` field is sent.
- **Local pack once per prompt x city (3.4)** is a separate DataForSEO call
  (180/wave). Note that every google_aio call also returns the local pack, so
  the 180 extra calls could be avoided by reading the pack from repeat 0 of
  google_aio; kept separate for fidelity to 3.4, and the raw google_aio
  records retain the pack items anyway.

## 3. Points that are infeasible or need a human decision

1. **Perplexity Sonar deprecation** (see table): the protocol names "Sonar
   chat completions"; the vendor has ended support and serves the endpoint
   via the Agent API. Choose `chat_completions` (legacy, reformulated) or
   `agent` before wave 1. Either way, API answers may not match the
   consumer app (protocol limitation already stated).
2. **Google AI Overview is not an LLM API.** Whether an AI Overview appears
   for a query is decided by Google; the record is stored with
   `response_text = "[no_ai_overview]"` and `extra.ai_overview_present=false`,
   and `no_answer_rate` reports the share. k = 5 repeats are still sent as
   independent SERP calls as the protocol requires, but repeats of a SERP are
   not "independent samples" in the same sense as LLM calls.
3. **Census populations.** `config/cities.csv` has an empty `population`
   column (TODO per protocol 3.2: fill from current Census estimates before
   wave 1 and record in CHANGELOG). Coordinates are approximate city centres.
4. **Model drift.** Model ids on all vendors change often (OpenAI and Gemini
   docs listed several generations on the day of checking). The requested
   and returned model strings are logged per response as required; pick the
   defaults consciously before wave 1.
5. **DataForSEO `location_name` strings** follow the documented
   "City,State,United States" format; DataForSEO's locations list should be
   checked for the exact spelling of each of the 20 names (e.g. "Des Moines")
   before wave 1. An unknown name returns a task error, which is recorded
   per response rather than raised.
6. **Places billing tier.** The field mask requests phone, website, rating,
   which bills at the Enterprise SKU. Dropping those would break the
   fact-error metric, so the cost is accepted and cached.
7. **RQ6 logistic model (6)** is not implemented in v0.1; `businesses.csv`
   provides the candidate set (union of AI-named and, after a join step,
   local-pack businesses) with rating, review count and website fields.
   A `statsmodels` script can be added without touching the protocol.
8. **Redaction of private individuals' names (7)** before public release is
   not automated; it should be done on the public export, not on raw data.

## 4. Mock mode

`--mock` builds responses from `tests/fixtures/*_response.json` (shapes copied
from the vendor docs) filled with deterministic synthetic businesses
(`panel/mockdata.py`; names contain "Mock" and example.com domains). The
same raw payload then goes through the real parser. The mock extractor is a
rule parser aligned with the synthetic text; the mock Places client returns
one ghost and one CLOSED_PERMANENTLY business per city x niche and a ~15%
wrong-phone rate so every classification path is exercised. No network calls
are made in mock mode.

## 5. Estimated API calls per wave

| Step | Calls |
|---|---|
| AI responses | 5 engines x 9 prompts x 20 cities x 5 repeats = 4,500 |
| Local pack | 9 x 20 = 180 |
| Extractor | <= 4,500 (one per non-empty response; google_aio without an overview is skipped) |
| Places | distinct (normalised name, city) pairs; in the mock run ~8% of recommended mentions, cached across waves |
| Validation | 0 API calls (human) |
