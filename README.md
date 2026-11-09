# Clinical Trial Delta

Tracks clinical trials in GLP-1/obesity, type 2 diabetes, and cholesterol.
ClinicalTrials.gov only shows a trial's *current* record, so Clinical Trial Delta
snapshots the registry every day and surfaces what changed: new trials, status
flips, enrollment, endpoint, and site changes.

Public registry data only. Information and links, not medical advice.

**Status:** milestone M1 (data pipeline) is built. The site (M2) and the eval
suite (M3) come next. See [BUILD_BRIEF.md](BUILD_BRIEF.md) for the full spec.

## How it works

```
watchlist.json ──► fetch ──► normalize ──► snapshot ──► diff ──► data/*.json
   (what to track)  (API v2)   (stable record)  (one per day)  (typed events)
```

- **Fetch:** ClinicalTrials.gov API v2, about one request per second, following
  `nextPageToken` to the end. A run aborts, writing nothing, if any query's
  fetched trials don't add up to the API's own `totalCount`.
- **Normalize:** turns each study into a deterministic record. Site contacts,
  investigator names, phone numbers and e-mails are never requested from the
  API at all.
- **Snapshot:** `data/snapshots/YYYY-MM-DD.json.gz` is written once a day and
  never overwritten. It points to content-addressed records in
  `data/snapshots/records/`, so an unchanged trial is stored only once. The API
  has no history endpoint, so these snapshots are the only history there is.
- **Diff:** compares today's snapshot with the previous one and emits change
  events of these types: `new_trial`, `status_change`, `enrollment_change`,
  `endpoint_change`, `site_added`, `site_removed`, `date_change`,
  `results_posted`.
- **Write:** writers copy only allowlisted keys into the site's data files. A new
  field that nobody has allowlisted fails the run instead of disappearing
  silently.

The refresh runs once a day on GitHub Actions
([`.github/workflows/refresh.yml`](.github/workflows/refresh.yml)) using only the
repository's own token. No API keys or secrets are involved, and nothing runs at
page-view time.

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
| `data/trials/<NCT>.json` | One trial: the normalized record, its collections, and its full change history |
| `data/reconciliation.json` | Latest count check against ClinicalTrials.gov |
| `data/snapshots/` | Daily manifests and content-addressed records (pipeline state; not read by the site) |

## Running it

Requires Python 3.11 or newer. The pipeline uses the standard library only.

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest
```

```bash
python -m pipeline.run --dry-run
```

```bash
python -m pipeline.run
```

```bash
python -m pipeline.reconcile
```

`--dry-run` fetches and diffs without writing anything. A plain
`pipeline.run` writes today's snapshot; if one already exists for today, it does
nothing. `pipeline.reconcile` compares the latest snapshot's counts with
ClinicalTrials.gov's.
