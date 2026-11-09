# BUILD BRIEF — Clinical Trial Delta

Name (chosen Nov 9, 2026): **Clinical Trial Delta**. The name states the product:
ClinicalTrials.gov only serves a trial's current record; this app snapshots the
registry daily and shows the *delta* — new trials, status flips, enrollment,
endpoint, and site changes. Repo: `clinical-trial-delta` (public). The in-app
display name is "Clinical Trial Delta" everywhere (title tag, header, README).

Repo description (GitHub About): "Tracks clinical trials in GLP-1/obesity, type 2
diabetes, and cholesterol — ClinicalTrials.gov only shows a trial's current record,
so this snapshots the registry daily and surfaces what changed: new trials, status
flips, enrollment, endpoint, and site changes. Includes a built-in AI evaluation
suite." Topics: clinical-trials, clinicaltrials-gov, health-data, ai-evals,
data-pipeline.

Naming checks run Nov 9, 2026: the earlier working title "Cohort" collides inside
clinical trials (Citeline's Cohort SmartBuilder, a Norstella tool; Cohorta AI;
Cohortix), and the obvious Trial* compounds are all taken by real products —
TrialScope (Citeline), TrialLens (Clinical Ink), TrialTrack / eTrialTrack,
Trialwatch (an existing ClinicalTrials.gov change monitor), TrialPulse, TrialWire,
Trialwise (a CRO since 1999). No clinical-trials product was found using "Delta"
in this form; treat it as a portfolio-grade name, not a cleared trademark.

## What this is

A clinical-trial intelligence hub: the Beacon pattern (pipeline + static site + agent
layer) applied to public trial data instead of market data. It tracks a watchlist of
therapeutic areas, shows the trial landscape per area, and — the differentiator —
**diffs the registry against its own previous snapshots** to surface what changed:
new trials, status flips, enrollment changes, endpoint edits, sites added or removed.

Two audiences, in this order:
1. Portfolio piece for Karthik's applications to biotech / health-tech AI startups
   (life-sciences wedge). It must demo well live in an interview on a phone.
2. A genuinely useful tool for Karthik and family ("what trials exist for X, where,
   and what changed lately").

## Hard rules (do not violate)

- **Public data only.** No accounts, no logins, no PHI, no personal data anywhere.
  No medical advice anywhere in copy — information and links only, with a visible
  "summaries are generated from the official registry record; verify on
  ClinicalTrials.gov" note wherever an AI summary appears.
- **No recurring compute on Karthik's Muse tokens.** Refresh runs on GitHub
  Actions (daily cron) like the financial-research-hub pipeline. Nothing in this
  project may depend on a personal agent allowance.
- **Charts always have numbered axis labels** (both axes). No exceptions — this is
  a standing design rule across all of Karthik's apps.
- **Plain-language first.** Every piece of trial jargon (phase, primary endpoint,
  enrollment, arm, masking) gets a one-line plain-English explanation at first use
  or via tooltip/glossary. Audience includes non-expert family members.
- Cache aggressively. LLM calls happen **only** for records that changed since the
  last run, and results are committed to the repo as data. Never call an LLM at
  page-view time.
- Mobile-first layout; Karthik reviews everything on his iPhone.

## Data source

**ClinicalTrials.gov API v2** — free, no API key, CORS-open.
- Search: `GET https://clinicaltrials.gov/api/v2/studies` with `query.cond`,
  `query.intr`, `query.spons`, `query.term`, `filter.advanced`
  (phase filtering is `filter.advanced=AREA[Phase]PHASE3` — there is no
  `filter.phase`), `pageSize` up to 1000, follow `nextPageToken` until absent.
- Single study: `GET /api/v2/studies/{nctId}`.
- Pace requests (~1/sec is plenty); the registry is ~600K studies total, our
  watchlist is a few thousand.
- **Known limitation that shapes the design:** the API serves only the *current*
  record — there is no history endpoint. Our daily snapshots are therefore the
  only history that exists. Store one snapshot per day; git history of the data
  directory doubles as the archive. Never overwrite a snapshot in place.

## The watchlist (v1 seed — config file, editable without code changes)

Three collections in `pipeline/watchlist.json`. Each collection = a set of
ClinicalTrials.gov queries (conditions + intervention terms + notable sponsors)
plus display metadata (name, one-line why-it-matters, accent color).

1. **GLP-1 & obesity** — cond: Obesity, Overweight; intr: semaglutide, tirzepatide,
   retatrutide, orforglipron, "GLP-1" class terms; sponsors of note: Novo Nordisk,
   Eli Lilly, Amgen, Viking, Roche.
2. **Type 2 diabetes** — cond: Diabetes Mellitus, Type 2; intr: metformin, insulin,
   empagliflozin, dapagliflozin (SGLT2 class), DPP-4 class terms; sponsors of note:
   Novo Nordisk, Eli Lilly, Boehringer Ingelheim, AstraZeneca.
   *Why it matters line:* South Asians develop type 2 diabetes earlier and at lower
   body weights than other groups — this collection exists for the family as much
   as the portfolio.
3. **Cholesterol & lipid disorders** — cond: Hypercholesterolemia, Dyslipidemias,
   Hypertriglyceridemia; intr: statin class terms, evolocumab, alirocumab (PCSK9
   class), inclisiran, bempedoic acid, evinacumab; sponsors of note: Amgen,
   Novartis, Regeneron, Esperion.

## Architecture (mirror financial-research-hub)

- `pipeline/` — Python. Fetch → normalize → snapshot → diff → write JSON.
  Writers copy only allowlisted keys into the site data files (the Beacon lesson:
  new pipeline outputs are silently dropped unless the writer allowlist is updated
  in the same change).
- `data/` — committed JSON: per-collection trial files, per-trial records,
  `changes.json` (the diff feed), `meta.json` (run timestamps, counts).
- Frontend — static, reads `data/` JSON only. Same stack and conventions as
  financial-research-hub. Deployed on Vercel from this repo.
- `.github/workflows/` — daily refresh (fetch + diff + commit), plus
  refresh-on-open staleness badge in the UI ("data as of <date>").
- Agent layer (later, not v1): the same JSON is the API; a small CLI mirrors the
  finresearch CLI's shape. Do not build it in v1.

## Pages

- **Home:** the three collections as cards (trial count, recruiting count, changes
  this week), then the global "what changed" feed (latest 20 changes across all
  collections, each linking to the trial).
- **Collection page:** landscape header (trials by phase bar chart, trials by
  status, recruiting-near-a-city search), "changed this week" section, trial table
  (sortable: phase, status, enrollment, start date) linking to trial pages.
- **Trial page:** official title, NCT number (linked to ClinicalTrials.gov),
  sponsor, phase, status, enrollment (target vs actual as reported), start /
  primary completion / completion dates, interventions, conditions, plain-language
  AI summary (cached; only regenerated when the record changes), eligibility
  criteria shown as a structured checklist parsed from the registry text,
  sites list (city/state, recruiting flag; a map is v1.1, not v1),
  results-posted flag, and a "history" section listing every change our snapshots
  have ever recorded for this trial.
- **Evals page:** see Eval module below.

## Change detection (the core feature)

Diff today's normalized record against yesterday's snapshot per trial. Emit typed
change events: `new_trial`, `status_change`, `enrollment_change`,
`endpoint_change` (primary outcome measure text differs), `site_added`,
`site_removed`, `date_change` (primary completion or completion date moved),
`results_posted`. Each event: trial NCT, collection, field, old value, new value,
detected date. Events append to `changes.json` (keep the full history; the UI
windows it). Unit-test the differ against two fixture snapshots with known diffs.

## Eval module (v1 — not a later phase)

This module exists because health-AI job descriptions ask for shipped AI
evaluation by name. It evaluates the AI summary layer and the eligibility parser.

- **Golden set:** `evals/golden.json`, hand-written, starts at ~30 cases and
  grows. Three case types:
  1. *Summary faithfulness* — a trial record + questions whose answers are
     verifiable from the record (phase, sponsor, enrollment, status).
  2. *Eligibility parsing* — a criteria block + the correct structured checklist.
  3. *Refusal / grounding* — questions the record cannot answer; the correct
     behavior is to say so, not to invent.
- **Assertions (free, always run in CI):** every cited NCT is well-formed and
  resolves to a record in our data; every summary's stated phase/sponsor/enrollment
  matches the registry record exactly; refusal cases contain no fabricated
  specifics (checked against a blocklist of the record's absent fields).
- **Judge scoring (only when `LLM_API_KEY` is present in env):** LLM-as-a-judge
  grades faithfulness and clarity 1–5 per case with a one-line rationale, stored
  per run. **If no key is present, the dashboard must show judge scores as
  "not run" — never estimate, interpolate, or display a placeholder number.**
- **Dashboard (Evals page):** pass rate by case type (assertions), mean judge
  score by category when available, run history (date, model, prompt version),
  and a failure gallery: every failing case with the record, the output, and the
  expected answer side by side.

## Milestones (each independently shippable)

- **M1 — Pipeline:** watchlist config, fetch + normalize + snapshot + differ,
  GitHub Actions daily run, counts reconcile against ClinicalTrials.gov web
  counts for the seed queries (acceptance check, log the numbers).
- **M2 — Site:** home, collection, and trial pages reading committed JSON;
  staleness badge; glossary tooltips; Vercel deploy.
- **M3 — Evals:** golden set v1 (30 cases), assertion runner in CI, Evals page
  with assertions live and judge section honestly marked "not run" until a key
  is wired.
- **M4 — Polish:** print-friendly collection digest ("what changed this week"),
  glossary completeness pass, empty states, share/OG metadata, link the project
  from karthikvkumar.com once the portfolio site exists.
- **M5 — Company pages:** a page per watchlist sponsor (pipeline across our
  collections, phase/status mix, recent changes, posted results, linked FDA
  actions via openFDA). This merges the biotech-radar idea into this product
  instead of building a second app.

## Explicitly out of scope for v1

- Patient matching UI (the eligibility checklist on trial pages is the v1
  surface; a person-profile matcher is phase 2).
- Accounts, alerts by email/push, any data collection from visitors.
- openFDA / PubMed joins (M5 uses openFDA for FDA actions only; adverse-event
  and literature joins belong to later projects).
- Any claim of clinical or investment advice.

## Acceptance checks before calling a milestone done

- Pipeline counts match ClinicalTrials.gov's own counts for each seed query
  (same filters) within a small explainable delta; log both numbers.
- Differ unit tests pass on fixture snapshots.
- Eval assertions run in CI on every data refresh; a failing assertion opens a
  visible failure on the Evals page, not a silent log line.
- Every chart on the site has numbered labels on both axes (visual check).
- No page makes an LLM call at view time (grep the frontend for fetch targets;
  only `data/` JSON and static assets are allowed).
