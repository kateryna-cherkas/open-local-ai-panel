# Open Local AI Panel

A monthly, open, reproducible panel that measures **which independent local
home-service businesses AI assistants recommend** for non-branded local queries
in the United States, how stable those recommendations are, how they compare
with Google's local pack, and how accurate their facts are.

The study design is fixed in [PROTOCOL.md](PROTOCOL.md) (v1.0, pre-registered before the first full wave).
This repository is the data pipeline that implements it. Build
decisions, API-doc verification and known gaps are in [BUILD_NOTES.md](BUILD_NOTES.md).

- 4 niches (construction, appliance repair, roofing, cleaning) x 3 prompt templates = 12 prompts
- 26 US cities in 17 states, three size tiers (Census 2025)
- 5 engines: OpenAI (Responses API + web search), Gemini (Google Search
  grounding), Perplexity Sonar, Claude (web search tool), Google AI Overview
  (via DataForSEO SERP API)
- k = 5 repeats, one wave per month: **7,800 AI responses + 312 local-pack
  pulls per wave**

## Requirements

- macOS/Linux, Python 3.9+ (no 3.10 syntax is used)
- `requests`, `pandas` (see `requirements.txt`). No vendor SDKs: all APIs are
  called with plain HTTP so the request shapes are visible and auditable.

## Setting keys

Copy `.env.example` to `.env` and fill in the values. `.env` is git-ignored.
Variables can also be set in the shell environment, which takes precedence.

| Variable | Used by | Where to get it |
|---|---|---|
| `OPENAI_API_KEY` | engine `openai` | platform.openai.com (billing enabled) |
| `GEMINI_API_KEY` | engine `gemini` | aistudio.google.com / Google Cloud |
| `PERPLEXITY_API_KEY` | engine `perplexity` | perplexity.ai API settings |
| `ANTHROPIC_API_KEY` | engine `claude` and the extractor | platform.claude.com |
| `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | engine `google_aio` and local pack | app.dataforseo.com (prepaid balance) |
| `GOOGLE_PLACES_API_KEY` | resolution (Places API New) | Google Cloud project with Places API (New) enabled and billing |

The code never prints secrets. Everything can be exercised without any key
using `--mock`.

## Running a wave

Wave ids are `YYYY-MM`. Every step is resumable: re-running skips work that is
already on disk.

```bash
# 0. Smoke test without network
python -m panel.collect --wave 2026-11 --mock --limit 20
python -m panel.extract --wave 2026-11 --mock
python -m panel.resolve --wave 2026-11 --mock
python -m panel.metrics --wave 2026-11
python -m panel.report  --wave 2026-11
rm -rf data/raw/2026-11 data/processed/2026-11   # clean up the mock run

# 1. Collect (prints the planned volume and cost, asks for confirmation; --yes skips)
python -m panel.collect --wave 2026-11                      # all engines, all cities
python -m panel.collect --wave 2026-11 --engines openai claude --cities Houston Bend --limit 50

# 2. Extract business names with the LLM extractor (config: extractor.model)
python -m panel.extract --wave 2026-11

# 3. Validation sample (protocol 4.3): hand-code 100 responses, then score
python -m panel.validate sample --wave 2026-11               # -> data/processed/2026-11/validation_sample.csv
python -m panel.validate score  --wave 2026-11 --filled data/processed/2026-11/validation_sample.csv

# 4. Resolve names against Google Places, classify closed / fact errors
python -m panel.resolve --wave 2026-11

# 5. Metrics (+ between-wave persistence once a previous wave exists) and draft report
python -m panel.metrics --wave 2026-11 --previous-wave 2026-10
python -m panel.report  --wave 2026-11                       # -> reports/2026-11_draft.md
```

Human steps per wave:
1. Hand-code the validation sample (`human_recommended_names`, names separated
   by ` | `; write `none` in `coder_notes` when an answer names no business).
   If recall < 0.9, fix `extract/prompt_vN.txt` (new version), bump
   `extractor.prompt_version`, delete `data/processed/<wave>/extractions.jsonl`
   and re-run `panel.extract`.
2. For unresolved businesses, do the manual search (protocol 4.5) and fill
   `ghost_manual_check` in `resolved.csv` with `not_found` or `found`, then set
   `is_ghost` to `1`/`0` accordingly (or re-run metrics after editing).
3. Hand-check a sample of resolutions (protocol 4.4).
4. Quarterly: manual consumer-app check, 30 prompts per engine (protocol 3.3).

## Tests

```bash
python -m unittest            # includes an end-to-end mock run
```

## Cost estimate

Per wave, with k = 5, 12 prompts, 26 cities:

```
AI calls per engine       = 12 x 26 x 5          = 1,560
AI calls total (5 engines) = 1,560 x 5           = 7,800
Local-pack SERP calls     = 12 x 26              = 312
Extractor calls           = successful AI responses with text  (<= 7,800)
Places calls              = distinct (normalised name, city) pairs (cached across waves; ~10-30% of recommended mentions)

Cost = sum_e 1,560 x P_e  +  312 x P_serp  +  N_extract x P_extract  +  N_places x P_places
```

Fill the per-call prices in `config/panel.json` (`price_per_call_usd` per
engine, `local_pack`, `extractor`, `places`) and `panel.collect` prints the
estimate before running. Placeholders to fill from the vendors' pricing pages:

| Item | Price driver (as of docs checked 2026-10-06) | `price_per_call_usd` |
|---|---|---|
| openai | tokens at model rate + web search $10 / 1k calls | fill |
| gemini | tokens at model rate + Google Search grounding per-request fee | fill |
| perplexity | request fee by model/search context + tokens | fill |
| claude | tokens at model rate + web search $10 / 1k searches (up to `max_uses` per call) | fill |
| google_aio (DataForSEO) | live advanced SERP price + $0.002 for `load_async_ai_overview` | fill |
| local pack (DataForSEO) | live advanced SERP price | fill |
| extractor (claude-haiku-4-5) | ~1k input + ~0.2k output tokens per response | fill |
| Places Text Search | Enterprise SKU because phone/website/rating are requested | fill |

## Folder layout

```
PROTOCOL.md                 study protocol (the spec)
BUILD_NOTES.md              API verification log, interpretations, infeasible points
config/
  panel.json                niches, 9 prompt templates, engines + model names, k, thresholds
  cities.csv                26 cities: tier, lat/lng, population (Census 2025), DataForSEO location_name
  domain_categories.json    editable domain -> category map for the source-mix metric
extract/
  prompt_v1.txt, schema_v1.json   versioned extractor prompt and JSON schema
panel/
  collect.py   extract.py   validate.py   resolve.py   metrics.py   report.py
  engines/     one adapter per engine + places.py (Google Places)
  config.py env.py http.py storage.py names.py geo.py mockdata.py paths.py
data/
  raw/{wave}/{engine}.jsonl, local_pack.jsonl      unmodified API responses
  processed/{wave}/mentions.csv, extractions.jsonl, resolved.csv, businesses.csv,
                   metrics_cells.csv, metrics_rr.csv, metrics_summary.csv,
                   source_mix.csv, source_domains.csv, persistence.csv,
                   validation_sample.csv
  cache/places/   Places lookups keyed by (normalised name, city)
reports/{wave}_draft.md
tests/           unit tests + end-to-end mock run; fixtures/ hold API response shapes
```

## Licence

Code and prompts: MIT (see LICENSE). Released data: CC BY 4.0, per protocol
section 7. The panel collects data only about businesses; any private
individual's name is redacted before public release.
