# M1 acceptance log — pipeline

Run on 2026-11-09 against the live registry (ClinicalTrials.gov API 2.0.5, registry
data timestamp `2026-11-09T10:00:05`). This was a verification run in the build
session; its output was not committed. The committed baseline will come from the
first GitHub Actions run. After adopting the REFERENCE_FRH.md conventions, the
run was repeated (`python -m pipeline.run daily` on Python 3.11): identical
counts, and byte-identical trial files.

## Count reconciliation

For each seed query: the trials the pipeline stored, the count the
clinicaltrials.gov search page displays for the same search (Condition /
Intervention / Sponsor boxes plus the *Study type: Interventional* filter), and the
API v2 `totalCount`. The website counts were read in two independent ways: from the
search page's own backend (`python -m pipeline.reconcile`), and by rendering each
search URL in headless Chromium and reading the page text ("Viewing 1-10 out of N
studies").

| Query | Pipeline | Website (rendered page) | API v2 totalCount | Delta |
|---|---:|---:|---:|---:|
| glp1-obesity-drugs | 890 | 890 | 890 | 0 |
| glp1-obesity-sponsors | 442 | 442 | 442 | 0 |
| t2d-drugs | 3,729 | 3,729 | 3,729 | 0 |
| t2d-sponsors | 1,242 | 1,242 | 1,242 | 0 |
| lipids-drugs | 1,053 | 1,053 | 1,053 | 0 |
| lipids-sponsors | 188 | 188 | 188 | 0 |

The search URLs are in `data/collections/<id>.json` (`queries[].web_url`) and in
`data/reconciliation.json`.

Collections are the de-duplicated union of their queries:

| Collection | Trials | Sum of query counts | Trials in both queries |
|---|---:|---:|---:|
| glp1-obesity | 1,091 | 1,332 | 241 |
| type-2-diabetes | 4,182 | 4,971 | 789 |
| cholesterol-lipids | 1,081 | 1,241 | 160 |
| **All collections** | **6,189 unique** | | |

(Collections overlap one another too: a semaglutide trial in people with obesity
and type 2 diabetes appears in both.)

The daily workflow repeats this check after every refresh (website backend + API).
It writes `data/reconciliation.json`, adds the table to the run summary, and raises
a warning annotation on any mismatch.

## No false changes on real data

The pipeline fetched the same registry version a second time and ran as if it were
the next day (`--date 2026-11-10`). Result: 0 events and 0 changed records across
6,189 trials. Normalization is deterministic, so array-ordering noise from the API
does not show up as changes.

## Differ tests

`python -m pytest`, on Python 3.11 (the version CI uses): **89 passed**. 26 of
them are differ tests against the two fixture snapshots in `pipeline/fixtures/`. The fixtures produce exactly 15
known events, covering all 8 event types, and also check cases that must *not*
produce events: whitespace- or case-only endpoint edits, a date's type flipping
without the date moving, site status or coordinate changes, reordered lists,
bookkeeping-only updates, and trials that match only because the watchlist was
edited.

| Test file (`pipeline/`) | Tests |
|---|---:|
| test_diff.py | 26 |
| test_watchlist.py | 14 |
| test_run.py | 13 |
| test_writers.py | 11 |
| test_ctgov.py | 11 |
| test_snapshot.py | 8 |
| test_normalize.py | 6 |

test_run.py also covers the partial-run rules: a failed query is carried
forward and its collection marked partial; a query with nothing to carry
forward re-baselines its collection; a bad record keeps its previous version;
only when every query fails is nothing written.

## Size and cost

- Each run makes 13 API requests and takes about 22 seconds.
- Day-one data: trial files 55.9 MB, collection files 4.2 MB, snapshot store
  46.7 MB (about 311 KB of that is the daily manifest). Everything is plain
  JSON: 12,385 files, none compressed.
- Each later day adds a manifest of about 311 KB plus record files only for trials
  that changed.

## Writer allowlists (REFERENCE_FRH.md: name them in every milestone report)

All in `pipeline/writers.py`, the only path from the pipeline into `data/`:

| Allowlist | Governs |
|---|---|
| `TRIAL_FIELDS`, `TRIAL_DERIVED` (order: `TRIAL_FILE_KEYS`) | `data/trials/<NCT>.json` |
| `TRIAL_EXCLUDED` | normalized fields deliberately not published (empty) |
| `HISTORY_FIELDS` | each trial file's `history` entries |
| `EVENT_FIELDS` | events in `data/changes.json` |
| `COLLECTION_FIELDS`, `COLLECTION_QUERY_FIELDS`, `ROW_FIELDS` | `data/collections/<id>.json` |
| `META_FIELDS`, `META_RUN_FIELDS`, `META_COLLECTION_FIELDS`, `META_QUERY_FIELDS` | `data/meta.json` |

Guard tests that fail when a produced key isn't allowlisted:
`test_every_normalized_field_is_allowlisted_or_excluded`,
`test_every_differ_event_key_is_allowlisted` (`pipeline/test_writers.py`) and
`test_every_run_info_key_reaches_meta` (`pipeline/test_run.py`).
