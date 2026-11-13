# Clinical Trial Delta

Tracks clinical trials in GLP-1/obesity, type 2 diabetes, and cholesterol.
ClinicalTrials.gov only shows a trial's *current* record, so Clinical Trial Delta
snapshots the registry every day and surfaces what changed: new trials, status
flips, enrollment, endpoint, and site changes.

Public registry data only. Information and links, not medical advice.

It also has a **Find a trial** page for patients and caregivers: the trials in
these three areas that are recruiting now or listed as not yet recruiting,
searched by distance from a city or the visitor's own location, by the age and
sex limits each record lists, and by what is being tested. Recruiting trials
come first; trials and sites that have not opened are listed separately, with
the planned start date. It flags trials that started recruiting or added a site
recently. The search runs entirely in the browser from `data/finder.json`;
the place and age a visitor enters are never sent anywhere or saved.

**Status:** milestones M1 (data pipeline), M2 (site) and M3 (AI summaries and
evals) are built. See [BUILD_BRIEF.md](BUILD_BRIEF.md) for the full spec and
[REFERENCE_FRH.md](REFERENCE_FRH.md) for the financial-research-hub conventions
this repo mirrors.

## How it works

```
watchlist.json ──► fetch ──► normalize ──► snapshot ──► diff ──► data/*.json
   (what to track)  (API v2)   (stable record)  (one per day)  (typed events)
```

- **Fetch** (`pipeline/fetch/ctgov.py`): ClinicalTrials.gov API v2, about one
  request per second, following `nextPageToken` to the end. Every query's trials
  must add up to the API's own `totalCount`.
- **Normalize:** turns each study into a deterministic record. Site contacts,
  investigator names, phone numbers and e-mails are never requested from the
  API at all.
- **Snapshot:** `data/snapshots/YYYY-MM-DD.json` is written once a day and
  never overwritten. It points to content-addressed records in
  `data/snapshots/records/`, so an unchanged trial is stored only once. The API
  has no history endpoint, so these snapshots are the only history there is.
- **Diff:** compares today's snapshot with the previous one and emits change
  events of these types: `new_trial`, `status_change`, `enrollment_change`,
  `endpoint_change`, `site_added`, `site_removed`, `date_change`,
  `results_posted`.
- **Write** (`pipeline/writers.py`, the only path into `data/`): each data file
  gets an explicit allowlist of keys, and everything else is dropped. A field
  that nobody allowlisted fails the tests instead of quietly never reaching the
  site.

A failure in one place never stops the whole run. A query that can't be fetched
keeps yesterday's trial list, and its collection is marked partial. A record
that can't be read keeps its previous version. Both are recorded in `meta.json`
and shown as a warning on the Actions run.

The refresh runs once a day on GitHub Actions
([`.github/workflows/refresh.yml`](.github/workflows/refresh.yml)). It checks out
`main`, runs the tests, runs the `daily` mode, reconciles counts, and commits
`data/` back to `main`. The data pipeline uses only the repository's own token;
the one secret is the optional AI provider key below. Nothing runs at page-view
time. Timestamps are America/Los_Angeles.

## AI summaries and evals

Trial pages show a short plain-language summary written by an AI model from the
registry record only, next to the note "Summaries are generated from the official
registry record; verify on ClinicalTrials.gov".

- **Where it runs:** only in the daily GitHub Actions refresh, never when a page
  is viewed.
- **When it runs:** only for trials whose record is new or changed since its
  summary was written, or whose prompt changed, within a daily request budget
  (default 100), open and running trials first. If the provider's daily quota
  runs out mid-run, the run stops cleanly and the next day carries on.
- **Checks before publishing:** the summary's phase, sponsor, enrollment and
  status must match the record exactly, any phase or head count in the prose must
  agree, and any NCT number cited must exist. Failures are not published; they
  show on the Evals page.
- **Evals** (`python -m pipeline.evals`, every refresh): 30 hand-written cases in
  `evals/golden.json` (summary faithfulness, eligibility parsing, refusal and
  grounding), free automatic checks, and an AI judge that scores faithfulness and
  clarity from 1 to 5. Without a provider the judge shows "not run", never a
  number. Results: `data/evals/`, shown at `/evals`.

### Setting up the AI provider

Any provider with an OpenAI-compatible chat-completions API works. Nothing is
built in: until these settings exist, the AI steps report "not run" and the site
says so. In the repository, open **Settings → Secrets and variables → Actions**:

| Kind | Name | Value |
|---|---|---|
| Secret | `LLM_API_KEY` | The provider's API key (never commit it) |
| Variable | `LLM_BASE_URL` | The provider's OpenAI-compatible base URL, without `/chat/completions` |
| Variable | `LLM_MODEL` | Model that writes summaries |
| Variable (optional) | `JUDGE_MODEL` | Model that grades them; defaults to `LLM_MODEL` |
| Variable (optional) | `LLM_PROVIDER` | Name shown on the site; defaults to the URL's host |
| Variable (optional) | `LLM_MIN_INTERVAL` | Seconds between requests (default 6, i.e. 10 a minute) |
| Variable (optional) | `LLM_REASONING_EFFORT` | Sent as `reasoning_effort`, for "thinking" models |
| Variable (optional) | `SUMMARY_BUDGET`, `EVAL_GENERATION_BUDGET`, `EVAL_JUDGE_BUDGET` | Requests per run (defaults 100, 40, 30) |

Then run **Actions → LLM probe → Run workflow**. It lists the models your key
can use (so you can set `LLM_MODEL` after adding just the key and URL), then
sends one tiny request to each chosen model and prints the reply, so a wrong
URL, model name or key shows up before the daily run.

For Google's Gemini API: create a key at aistudio.google.com (Get API key →
Create API key) and use `LLM_BASE_URL` =
`https://generativelanguage.googleapis.com/v1beta/openai`. Free tiers change often; set the budgets below the daily
limits your provider's dashboard shows.

GitHub Models, the first provider used here, was retired by GitHub on
2026-07-30. Its endpoint now answers every request with a plain-text "OK".

## The watchlist

[`pipeline/watchlist.json`](pipeline/watchlist.json) defines three collections.
Each collection is a set of ClinicalTrials.gov searches plus display metadata.
You can edit it without touching any code:

| Collection | Searches (interventional studies only) |
|---|---|
| GLP-1 & obesity | Obesity or overweight **and** a GLP-1 medicine · obesity or overweight **and** a sponsor of note |
| Type 2 diabetes | Type 2 diabetes **and** metformin / insulin / SGLT2 / DPP-4 medicines · type 2 diabetes **and** a sponsor of note |
| Cholesterol & lipid disorders | High cholesterol, dyslipidemia or high triglycerides **and** a statin / PCSK9 / other lipid medicine · the same conditions **and** a sponsor of note |

A collection's trials are all the trials its searches return, with duplicates
removed. Each search maps one-to-one to a search on clinicaltrials.gov/search:
the Condition, Intervention and Sponsor boxes plus the "Interventional" filter.
Every run reconciles our counts against the website's. When you edit a
collection's searches, that collection is re-baselined on the next run, so trials
that match only because of the edit are not reported as "new trials".

## Data files

| File | What it holds |
|---|---|
| `data/meta.json` | Snapshot date, run info, per-collection counts (trials, recruiting, recent changes) |
| `data/changes.json` | Every change event ever detected, oldest first (append-only) |
| `data/collections/<id>.json` | Collection metadata, trials-by-phase and trials-by-status counts, trial table rows |
| `data/sites/<id>.json` | Cities where a collection's trials are recruiting, for the near-a-city search |
| `data/finder.json` | Every trial that is recruiting or not yet recruiting and names a mappable site: cities recruiting now, cities with sites not open yet, planned start, age and sex limits, what is tested, placebo group, and recent news (started recruiting, sites added) for the Find a trial page |
| `data/trials/<NCT>.json` | One trial: the normalized record, its eligibility checklist, its collections, and its full change history |
| `data/summaries/<NCT>.json` | A trial's AI summary, with the record hash it was written from |
| `data/evals/latest.json`, `history.json` | Latest eval run (every case, output and expected answer) and one row per run |
| `data/reconciliation.json` | Latest count check against ClinicalTrials.gov |
| `data/snapshots/` | Daily manifests and content-addressed records (pipeline state; not read by the site) |

## Layout

```
pipeline/            Python package (standard library only)
  run.py             entrypoint: python -m pipeline.run <mode>
  fetch/ctgov.py     ClinicalTrials.gov API v2 client (one module per source)
  normalize.py       raw study -> stable record
  snapshot.py        write-once daily snapshots
  diff.py            typed change events
  eligibility.py     criteria text -> inclusion/exclusion checklist
  writers.py         allowlisted writers; the only path into data/
  reconcile.py       count check against ClinicalTrials.gov
  watchlist.json     what to track
  test_*.py          tests, next to the code (fixtures in pipeline/fixtures/)
data/                committed JSON; the site reads nothing else
web/                 the site: Vite + TypeScript, hand-rolled CSS and SVG charts
  src/data.ts        the only network code: same-origin GETs of /data/*.json
  src/pages/         home, collection, trial, glossary
  src/charts.ts      bar charts with numbered, titled axes
  src/glossary.ts    plain-language explanations for every trial term
  scripts/           copy-data.mjs (build step), check-fetch-targets.mjs (CI)
  vercel.json        Vercel settings; the Vercel project root is web/
docs/                design and ops notes
.github/workflows/   refresh.yml (daily), tests.yml
```

## The site

A static single-page app with three kinds of page: the home page (collections
and the latest changes), a page per collection (charts, a recruiting-near-a-city
search, recent changes, a sortable trial table) and a page per trial. Every trial
term is tappable for a plain-language explanation, and the header shows the date
of the data and warns when it is stale.

The site reads only the committed JSON. `web/scripts/copy-data.mjs` copies it
from `data/` into the build (snapshots are left out). No page calls an LLM, an
API or any third-party host; `npm run check` fails the build if that changes.

```bash
cd web
```

```bash
npm ci
```

```bash
npm run dev
```

```bash
npm run check
```

```bash
npm run build
```

Deployment is on Vercel with the project root set to `web/`. Each data commit to
`main` redeploys the site.

## Running it

Requires Python 3.11 or newer.

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest
```

```bash
python -m pipeline.run daily --dry-run
```

```bash
python -m pipeline.run daily
```

```bash
python -m pipeline.reconcile
```

There are two modes, both documented at the top of `pipeline/run.py` and recorded
in `meta.json`. `daily` writes today's snapshot (Los Angeles date) and the site
files; if a snapshot already exists for today, it does nothing. `rebuild`
re-writes the site files from the latest snapshot without contacting the
registry, for use after changing a writer or the eligibility parser. `--dry-run`
fetches and diffs without writing anything. `pipeline.reconcile` compares the latest snapshot's counts with
ClinicalTrials.gov's.
