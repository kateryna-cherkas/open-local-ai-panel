# Open Local AI Panel: Study Protocol v1.0

**Status:** pre-registered, frozen before the first full wave (December 2026) · **Version date:** 2026-11-13 (drafts: v0.1 2026-10-06, v0.2 2026-11-12) · **Author:** Kateryna Cherkas
**Any change after this date opens a new protocol version and is recorded in `CHANGELOG.md` with a reason.**

## 1. Purpose

This panel measures, in the open and on a fixed monthly schedule, **which independent local
home-service businesses AI assistants recommend for non-branded local queries** in the United States. It also records how stable those recommendations are, how they compare with Google's local results, and how accurate the facts in them are.

The work exists because the published studies in this area have gaps that make them hard to build on:
- they are produced by vendors as marketing;
- they are one-off rather than repeated;
- their data, prompts and code are not released;
- they cover multi-location chains (SOCi LVI 2026), or leave non-branded prompts out of their findings (BrightLocal 2026), or measure only branded factual accuracy (Insites).

**Out of scope for v0.1.** Causal claims. Any association between business attributes and being recommended is reported as correlation.

## 2. Research questions

- **RQ1. Concentration:** how many distinct businesses does each engine recommend per city × niche, and what share goes to the top 3?
- **RQ2. Stability:** how similar are the lists across 5 identical runs, and how much carries over month to month?
- **RQ3. Overlap:** how much do AI recommendations overlap with the Google local pack for the same query?
- **RQ4. Integrity:** how often do recommendations name businesses that do not exist or are closed? How often are the phone, website and address wrong compared with Google Places?
- **RQ5. Sources:** which domains are cited (business sites, Yelp, BBB, Angi, Thumbtack, Reddit, news, etc.)?
- **RQ6. Correlates (exploratory):** which observable attributes are associated with being recommended? Candidates: rating, review count, website present, years listed, and presence on directories.

## 3. Design

### 3.1 Niches and prompts (4 niches × 3 prompts = 12 templates)

| Niche | P1: generic "best" | P2: specific job | P3: urgent problem |
|---|---|---|---|
| construction | What are the best general contractors in {city}, {state}? | I want to remodel my kitchen in {city}, {state}. Which contractors should I call? | I need a contractor to repair water damage in my house in {city}, {state}. Who can start soon? |
| appliance_repair | What are the best appliance repair companies in {city}, {state}? | My dishwasher is not draining in {city}, {state}. Which appliance repair companies should I call? | My refrigerator stopped cooling in {city}, {state}. Who can come out today? |
| roofing | What are the best roofing companies in {city}, {state}? | I need a roof replacement in {city}, {state}. Which companies should I call? | My roof is leaking after a storm in {city}, {state}. Who can I call to fix it fast? |
| cleaning | What are the best house cleaning services in {city}, {state}? | I need a deep cleaning for my house in {city}, {state}. Which companies should I call? | I'm moving out this week in {city}, {state}. Who can do a move-out cleaning on short notice? |

Prompts are sent verbatim, with no system prompt and no conversation history.

Protocol v0.2 (2026-11-12) replaced HVAC and plumbing with construction (general contractor), appliance repair and house cleaning, before the first full wave. Roofing prompts are unchanged from v0.1, so the November 2026 pilot remains comparable for roofing.

### 3.2 Cities (26, three size tiers)

Tiers are based on city (incorporated place) population from the US Census Bureau Vintage 2025 estimates (July 1, 2025; file `sub-est2025.csv`, checked 2026-11-13), recorded in `config/cities.csv`: **large ≥ 500,000; mid 150,000–499,999; small < 150,000.** City population, not metro population, is used because the prompts name the city.

- **Large (10):** New-state picks: Los Angeles CA; Jacksonville FL; Boston MA. Kept: Houston TX; Phoenix AZ; Philadelphia PA; Seattle WA; Denver CO; Atlanta GA; Chicago IL
- **Mid (8):** New-state picks: Bakersfield CA; Tampa FL; Newark NJ; Worcester MA. Kept: Reno NV; Boise ID; Tacoma WA; Knoxville TN
- **Small (8):** New-state picks: Lakeland FL; Bellingham WA; Redding CA; Trenton NJ. Kept: Bend OR; Asheville NC; Flagstaff AZ; Greenville SC

17 states. **Selection rationale (v1.0):** (a) every size tier includes a city from each of the states where the author has hands-on client work with local businesses (California, Florida, Washington) and from New Jersey and Massachusetts, so that findings can be checked against field experience; (b) the remaining cities spread the panel across US regions (South, Southwest, Mountain West, Midwest, Northeast, Pacific Northwest). The five pilot cities (Houston, Denver, Boise, Knoxville, Bend) are kept for pilot-to-wave comparison. Results are not population-weighted estimates for the US; they describe this fixed panel of cities, and comparisons are made between tiers and engines.

Changes from v0.1 (made before the first full wave, without looking at full-wave data): removed Tulsa OK, Des Moines IA, Dubuque IA, Bozeman MT, Lynchburg VA; added the 11 new-state picks above; Greenville SC (75,310) moved from mid to small after the Census check.

### 3.3 Engines (official APIs only, no scraping of consumer apps)

| Engine id | Access | Location handling |
|---|---|---|
| `openai` | Responses API with the web search tool | approximate `user_location` (city, region, country=US) |
| `gemini` | generateContent with Google Search grounding | city and state appear in the prompt text only |
| `perplexity` | Perplexity API, agent mode with web search (Sonar chat completions were retired on 2026-09-27) | `user_location` (lat/lng, city, region, country) |
| `claude` | Messages API with the web search tool | approximate `user_location` |
| `google_aio` | SERP API (DataForSEO): Google AI Overview block | SERP `location_name` = city |

The model version is set in config, and the exact model string returned by each API is logged with every response.

**Known limitation:** API answers may differ from what users see in the consumer apps. Each quarter, a manual check of at least 30 prompts per engine is run in the consumer apps, and the agreement with the API answers is reported.

### 3.4 Repetition and schedule

- **k = 5 repeats** of every prompt × city × engine per wave, sent as independent calls.
- **One wave per month**, starting on the first Monday and completed within 72 hours.
- Volume per wave: 12 templates × 26 cities × 5 engines × 5 repeats = **7,800 AI responses**, plus 312 SERP local-pack pulls. The local pack is pulled once per prompt × city per wave.

### 3.5 Reference data

- **Google local pack:** the same query text sent through the SERP API with location set to the city. All local-pack entries are stored.
- **Google Places API (Text Search):** each extracted business name is resolved to a place, and the following are stored: place_id, name, formatted address, phone, website, rating, user rating count, business status and types.

## 4. Processing

1. **Raw storage.** Each AI response is saved unmodified as JSONL. Each record includes: wave id, timestamp (UTC), engine, requested model, returned model, prompt template id, city, repeat index, full response text, citations or URLs returned by the API, latency, and error if any.
2. **Entity extraction.** Business names recommended in each response are extracted into a fixed JSON schema: name, rank in answer, phone/website/address if stated. Extraction uses an LLM, with the extractor model and its prompt versioned in `extract/`. Any business that is only mentioned, and not recommended, is flagged separately.
3. **Extraction validation.** Each wave, 100 random responses are hand-coded by a person. Agreement with the extractor (precision and recall at the business-name level) is reported. If recall falls below 0.9, the wave is re-extracted after the extractor is fixed.
4. **Resolution.** Each name + city is sent to Places Text Search (service-area businesses without a public address included). The first candidate is accepted only if name similarity ≥ 0.85, measured as token-set ratio after legal suffixes (LLC, Inc, etc.) are removed, or if the names are identical after spaces and punctuation are removed; and if it lies in the city's metro area: within the metro radius when Places gives coordinates, or, for service-area businesses without coordinates, with a phone area code of that metro (`panel/areacodes.py`; toll-free or missing phone → not accepted). If the first query fails, a second query is sent with the cleaned name (location suffixes removed) plus the niche keyword, under the same rules. Otherwise the business is marked `unresolved`. A sample of resolutions is hand-checked. (Rules as of protocol v1.0.)
5. **Classification.**
   - **Ghost:** the business is unresolved and also not found by a manual search (see 4.6).
   - **Closed:** Places returns business_status CLOSED_PERMANENTLY.
   - **Fact error:** the website domain or street address stated in the AI answer differs from what Places returns. A phone that differs from the Google profile is reported separately ("phone differs"), because many businesses use several official numbers; differing phones are checked against the business's own site and, if not found there, by manual search.
6. **Manual check of unresolved businesses (sample).** Checking every unresolved name by hand is not feasible at full scale, so each wave a random sample is checked:
   - Unit: a unique (business name, city) pair that is unresolved after step 4.
   - Sample: simple random sample of **50 pairs per niche** (all pairs if a niche has fewer than 50), drawn with a fixed seed equal to the wave id, before anyone looks at the names. The sample file is saved as `data/manual/<wave>/unresolved_sample.csv`.
   - Procedure: a person searches Google Maps and Google Search, the state contractor-licence register, BBB and the business's own site. Each pair gets one label: `found_local` (operates in that metro), `found_elsewhere` (real, but based in another region), `found_closed`, `category_mismatch` (real, but not this kind of business), `not_found` (no trace). The evidence URL is recorded for every label except `not_found`.
   - Estimate: ghost rate per niche = (share of recommended mentions that are unresolved) × (mention-weighted share of `not_found` in the sample), reported with a 95% bootstrap confidence interval over sampled pairs. The same estimate is given per engine within each niche; with small samples these intervals are wide and are reported as such. "Wrong city" (`found_elsewhere`) is estimated the same way and reported separately.
   - Labels from earlier waves are reused for the same (name, city) pair and do not count toward the 50.

## 5. Metrics (per engine × niche × city × wave unless noted)

- **Recommendation Rate (RR)** for business b: the share of the k × 3 responses that name b.
- **Distinct count:** the number of unique resolved businesses named.
- **Top-3 share:** the share of all mentions that go to the three most-mentioned businesses.
- **Within-wave stability:** mean pairwise Jaccard similarity of the name sets across the k repeats.
- **Between-wave persistence:** the share of businesses with RR ≥ 0.4 in wave t that also have RR ≥ 0.4 in wave t+1.
- **Pack overlap:** the share of AI-recommended resolved businesses that also appear in the Google local pack for the same query and city.
- **Ghost rate, closed rate, fact error rate:** each as a share of all recommended business mentions. Ghost rate is the sample-based estimate from 4.6.
- **Source mix:** cited URLs grouped by domain category (business site, directory, review platform, marketplace, forum, media, government, other).

## 6. Analysis plan (fixed before the first wave)

- **Descriptive statistics.** Every metric above, by engine, niche and city tier, with bootstrap 95% confidence intervals (resampling over cities).
- **RQ6, exploratory.** A logistic model of P(business named ≥ 1 time). The candidate set is the union of local-pack entries and AI-named businesses for each city × niche. Predictors are log(review count), rating, website present, and engine. Standard errors are clustered by city. The analysis is labelled exploratory and correlational.
- **No outcome-driven changes.** Prompts, cities and engines do not change mid-series. Any change opens a new protocol version and is logged in the CHANGELOG.

## 7. Release

- **Code and prompts:** public GitHub repository, MIT licence.
- **Data:** each wave is released as CSV/Parquet. The dataset is deposited on Zenodo with a DOI and a CC BY 4.0 licence. Released: the AI responses (prompt, engine, returned model, answer text, cited URLs), the extracted business mentions, the Google `place_id` of each resolved business, and all derived flags and metrics.
- **Third-party content is not redistributed.** Google Places fields (name, address, phone, website, rating, review count) and SERP / local-pack pages are used for resolution and metrics but are not included in the public files, in line with the Google Maps Platform and DataForSEO terms. Anyone can re-fetch them from the released `place_id`. Places content is kept in the local cache for at most 30 days; `place_id` may be kept indefinitely.
- **Report:** a quarterly report that states limitations. Monthly updates are short and data-only.
- **No personal data:** the panel collects data only about businesses. If an AI answer contains a private individual's name that is not part of a business name, that name is redacted in the public release.

## 8. Limitations (stated up front)

- API answers are not the same as consumer app answers (see §3.3).
- Model updates between waves confound the time trend. Model strings are logged so these updates can be flagged.
- Four niches and 26 cities are not a representative sample of all US local businesses; city choice partly follows the author's field experience (see 3.2).
- Entity extraction and resolution both introduce measurement error. That error is quantified in §4.3 and §4.4.
- Correlates in RQ6 are associations, not causes.

## 9. Ethics and terms of service

The panel uses only official APIs, within their terms of service. It does not contact businesses and does not simulate customers. Businesses are identified only by public information.
