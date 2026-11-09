# M2 acceptance log — site

Checked on 2026-11-09 against the data committed by the daily refresh
(snapshot 2026-11-09, 6,189 trials), on the built site at iPhone 14 size
(390 x 664), in light and dark mode, and at 1280 x 900.

## Brief's checks

| Check | Result |
|---|---|
| Home, collection and trial pages read committed JSON | Yes. The only network code is `web/src/data.ts`, which requests `/data/*.json` on the same origin. |
| Staleness badge ("data as of …") | In the header on every page. Re-read from `meta.json` on open and when the tab regains focus after five minutes. It turns to a warning when the data is two or more days old or the last run was partial. |
| Glossary tooltips | 42 terms in `web/src/glossary.ts`. Every use is a tappable word with a one-line explanation, and `/glossary` lists them all. |
| Every chart has numbered labels on both axes | Trials by phase: phase numbers on one axis (Early 1, 1, 1/2, 2, 2/3, 3, 4, None), numbered trial counts on the other. Trials by status: numbered trial counts on the value axis; the other axis names each status, because statuses have no numbers. Both axes have titles and every bar prints its count. |
| No page makes an LLM call at view time | `npm run check` (CI) fails if `fetch` is used outside `data.ts`, if `data.ts` requests anything but `/data/…`, or if any other network API or unexpected host appears. In a headless run of all four page types the browser made no request to any other host. |
| Mobile first | No horizontal scrolling on any page at 390 px. Buttons, inputs and menus are at least 44 px tall (inline glossary words and chips are smaller), and inputs use 16 px text so iOS does not zoom. |

## What was exercised

- Navigation: home to collection to trial and back, a direct load of each
  address, and an unknown trial (a not-found page, no crash).
- Charts: bar read-out on tap, the table view, and re-drawing at phone and desktop
  widths.
- Near-a-city search: typing "bost", choosing Boston, 19 recruiting trials within
  50 miles, changing the distance.
- Trial table: search, status and phase filters, all four sorts in both
  directions, "show more".
- Change feed and trial history: rendered with made-up events of all eight types,
  placed in the built copy only. The committed data has no changes yet because
  tracking started today.
- No console errors in any of the above.

## Pipeline additions made for the site

All published through allowlists in `pipeline/writers.py`:

| Addition | Allowlist | File |
|---|---|---|
| Eligibility checklist | `CHECKLIST_FIELDS`, `CHECKLIST_ITEM_FIELDS`; `eligibility_checklist` in `TRIAL_DERIVED` | `data/trials/<NCT>.json` |
| Recruiting-sites index | `SITES_FIELDS`, `SITES_CITY_FIELDS`, `SITES_TRIAL_FIELDS` | `data/sites/<id>.json` |
| `rebuild` and `tracking_since` | `META_FIELDS`, `META_REBUILD_FIELDS` | `data/meta.json` |

The eligibility parser (`pipeline/eligibility.py`) is deterministic. On the
6,189 records: 6,155 parse into inclusion and exclusion lists, 34 are marked
unparsed and shown as the registry wrote them, and no criterion line is dropped.

Tests: `python -m pytest` 125 passed (Python 3.11).

## Size

- JavaScript 48 KB (18 KB compressed), CSS 16 KB (4 KB compressed), no runtime
  dependencies.
- The built site carries 6,197 data files, about 57 MB uncompressed. A page loads
  only the files it needs: `meta.json` and `changes.json`, plus one collection
  file or one trial file.

## Deploying on Vercel

Not done yet: creating the Vercel project needs the owner's approval.

1. In Vercel, add a new project and import `karthik-v-kumar/clinical-trial-delta`.
2. Set **Root Directory** to `web`. The framework (Vite), build command and output
   directory come from `web/vercel.json`.
3. Leave "Include files outside the Root Directory in the Build Step" on (the
   default). The build copies the committed JSON from `../data`.
4. Deploy. No environment variables are needed.

After that, every push to `main`, including the daily data commit, redeploys the
site.
