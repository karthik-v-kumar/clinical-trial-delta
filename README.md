# Clinical Trial Delta

Tracks clinical trials in GLP-1/obesity, type 2 diabetes, and cholesterol.
ClinicalTrials.gov only shows a trial's *current* record, so Clinical Trial Delta
snapshots the registry every day and surfaces what changed: new trials, status
flips, enrollment, endpoint, and site changes.

Public registry data only. Information and links, not medical advice.

**Status:** milestone M1 (data pipeline) is built. The site (M2) and the eval
suite (M3) come next. See [BUILD_BRIEF.md](BUILD_BRIEF.md) for the full spec and
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
`data/` back to `main`. It uses only the repository's own token: no API keys or
secrets, and nothing runs at page-view time. Timestamps are America/Los_Angeles.

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
| `data/trials/<NCT>.json` | One trial: the normalized record, its eligibility checklist, its collections, and its full change history |
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
docs/                design and ops notes
.github/workflows/   refresh.yml (daily), tests.yml
```

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
