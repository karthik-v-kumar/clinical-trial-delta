// Collection page: landscape charts, recruiting-near-a-city search, this
// week's changes, and the sortable trial table.

import { barChart } from "../charts";
import type { Page } from "../config";
import { loadChanges, loadCollection, loadSites } from "../data";
import { clear, external, h, link } from "../dom";
import {
  formatDate,
  formatInt,
  PHASE_ORDER,
  phaseBucket,
  phaseLabel,
  phaseRank,
  phaseShort,
  plural,
  shiftDate,
  STATUS_ORDER,
  statusLabel,
} from "../format";
import { cityName, fold, matchCities, trialsNear, type City } from "../geo";
import { term } from "../glossary";
import type { Collection, Meta, SitesIndex, TrialRow } from "../types";
import { changeItem, emptyState, phaseChip, section, showMoreButton, stat, statusChip } from "../ui";
import { latestChanges } from "./home";

const PAGE_SIZE = 25;

// --- landscape -----------------------------------------------------------------

function landscape(collection: Collection): HTMLElement {
  const phaseCounts = new Map(collection.by_phase.map((p) => [p.phase, p.count]));
  const extraPhases = collection.by_phase.map((p) => p.phase).filter((p) => !PHASE_ORDER.includes(p));
  const phases = [...PHASE_ORDER, ...extraPhases].map((phase) => ({
    key: phase,
    label: phaseLabel(phase),
    axisLabel: phaseShort(phase),
    value: phaseCounts.get(phase) ?? 0,
  }));
  const statuses = collection.by_status.map((s) => ({
    key: s.status,
    label: statusLabel(s.status),
    axisLabel: statusLabel(s.status),
    value: s.count,
  }));
  return h(
    "div",
    { class: "charts", style: `--accent:${collection.accent}` },
    barChart({
      title: ["Trials by ", term("phase", "phase")],
      summary: `Bar chart of ${collection.name} trials by phase.`,
      data: phases,
      categoryTitle: "Phase",
      valueTitle: "Trials",
      unit: ["trial", "trials"],
      orientation: "columns",
    }),
    barChart({
      title: ["Trials by ", term("status", "status")],
      summary: `Bar chart of ${collection.name} trials by status.`,
      data: statuses,
      categoryTitle: "Status",
      valueTitle: "Trials",
      unit: ["trial", "trials"],
      orientation: "bars",
    }),
  );
}

function definition(collection: Collection): HTMLElement {
  const sum = collection.queries.reduce((total, q) => total + q.matched, 0);
  return h(
    "details",
    { class: "definition" },
    h("summary", null, "How this collection is built"),
    h(
      "p",
      null,
      "These trials come from the ClinicalTrials.gov searches below, limited to ",
      term("study-type", "interventional studies"),
      ". The counts are checked against the registry's own numbers on every refresh.",
    ),
    h(
      "ul",
      { class: "queries" },
      collection.queries.map((q) =>
        h(
          "li",
          null,
          h("span", null, q.label),
          h("span", { class: "query-count" }, plural(q.matched, "trial")),
          q.failed ? h("span", { class: "note" }, "Not refreshed on the last run; showing the previous list.") : null,
          external(q.web_url, null, "See this search on ClinicalTrials.gov"),
        ),
      ),
    ),
    sum > collection.trial_count
      ? h(
          "p",
          null,
          `${plural(sum - collection.trial_count, "trial")} ${sum - collection.trial_count === 1 ? "matches" : "match"} more than one search and ${sum - collection.trial_count === 1 ? "is" : "are"} counted once, giving ${formatInt(collection.trial_count)} in total.`,
        )
      : null,
    collection.sponsors_of_note.length
      ? h("p", null, "Sponsors of note: ", collection.sponsors_of_note.join(", "), ".")
      : null,
  );
}

// --- recruiting near a city ------------------------------------------------------

function citySearch(collection: Collection): HTMLElement {
  const rows = new Map(collection.trials.map((row) => [row.nct_id, row]));
  const input = h("input", {
    type: "search",
    id: "city-input",
    class: "input",
    placeholder: "Type a city, for example Boston",
    autocomplete: "off",
    "aria-controls": "city-options",
  });
  const radius = h(
    "select",
    { id: "city-radius", class: "input" },
    [25, 50, 100, 250].map((miles) => h("option", { value: miles, selected: miles === 50 }, `${miles} miles`)),
  );
  const options = h("ul", { class: "city-options", id: "city-options" });
  const results = h("div", { class: "city-results", "aria-live": "polite" });
  let index: SitesIndex | null = null;
  let chosen: City | null = null;
  let shown = PAGE_SIZE;

  const renderResults = () => {
    clear(results);
    if (!index || !chosen) return;
    const origin = chosen;
    const miles = Number(radius.value);
    const nearby = trialsNear(index, origin, miles);
    if (!nearby.length) {
      results.appendChild(
        emptyState(
          `No recruiting sites within ${miles} miles of ${cityName(origin)}`,
          "Try a wider distance. Sites that the registry lists without a location are not included.",
        ),
      );
      return;
    }
    results.appendChild(
      h("p", { class: "section-note" }, `${plural(nearby.length, "recruiting trial")} within ${miles} miles of ${cityName(origin)}, nearest first.`),
    );
    results.appendChild(
      h(
        "ul",
        { class: "near-list" },
        nearby.slice(0, shown).map((hit) => {
          const row = rows.get(hit.nctId);
          const where =
            hit.miles < 1
              ? `Recruiting in ${cityName(hit.nearest)}`
              : `Nearest site: ${cityName(hit.nearest)}, about ${Math.round(hit.miles)} miles away`;
          return h(
            "li",
            { class: "near-item" },
            link(`/t/${hit.nctId}`, { class: "row-title" }, row?.brief_title ?? hit.nctId),
            h("p", { class: "row-meta" }, row ? phaseChip(row.phases) : null, h("span", null, where)),
            hit.citiesInRange > 1
              ? h("p", { class: "row-sub" }, `Recruiting in ${hit.citiesInRange} cities within ${miles} miles.`)
              : null,
          );
        }),
      ),
    );
    if (nearby.length > shown) {
      results.appendChild(
        showMoreButton(nearby.length - shown, PAGE_SIZE, () => {
          shown += PAGE_SIZE;
          renderResults();
        }),
      );
    }
  };

  const choose = (city: City) => {
    chosen = city;
    shown = PAGE_SIZE;
    input.value = cityName(city);
    clear(options);
    renderResults();
  };

  const renderOptions = () => {
    clear(options);
    if (!index) return;
    const typed = input.value;
    if (chosen && fold(cityName(chosen)) === fold(typed)) return;
    const matches = matchCities(index.cities, typed);
    if (fold(typed).length >= 2 && !matches.length) {
      options.appendChild(
        h("li", { class: "city-none" }, "No recruiting site in a city with that name. Try the nearest large city."),
      );
      return;
    }
    for (const city of matches) {
      options.appendChild(
        h("li", null, h("button", { type: "button", class: "city-option", onclick: () => choose(city) }, cityName(city))),
      );
    }
  };

  input.addEventListener("input", () => {
    chosen = null;
    clear(results);
    renderOptions();
  });
  input.addEventListener("keydown", (event) => {
    if ((event as KeyboardEvent).key !== "Enter" || !index) return;
    const first = matchCities(index.cities, input.value, 1)[0];
    if (first) choose(first);
  });
  radius.addEventListener("change", renderResults);

  const note = h("p", { class: "section-note" }, "Loading recruiting sites…");
  void loadSites(collection.id)
    .then((loaded) => {
      index = loaded;
      note.textContent =
        `Search ${formatInt(loaded.cities.length)} cities where ${plural(loaded.trials.length, "trial")} in this collection ` +
        `${loaded.trials.length === 1 ? "is" : "are"} recruiting. Distances are between city centers.`;
      renderOptions();
    })
    .catch(() => {
      note.textContent = "The list of recruiting sites could not be loaded.";
    });

  return h(
    "div",
    { class: "city-search" },
    note,
    h(
      "div",
      { class: "controls" },
      h("label", { class: "field" }, h("span", null, "City"), input),
      h("label", { class: "field field-small" }, h("span", null, "Within"), radius),
    ),
    options,
    results,
  );
}

// --- trial table ---------------------------------------------------------------

type SortKey = "start" | "phase" | "status" | "enrollment";

const SORTS: { key: SortKey; label: string }[] = [
  { key: "start", label: "Start date" },
  { key: "phase", label: "Phase" },
  { key: "status", label: "Status" },
  { key: "enrollment", label: "Enrollment" },
];

function compareRows(key: SortKey, descending: boolean): (a: TrialRow, b: TrialRow) => number {
  const direction = descending ? -1 : 1;
  // Rows with no value always sort last, whichever direction is chosen.
  const withMissingLast = <T>(get: (row: TrialRow) => T | null, compare: (x: T, y: T) => number) => {
    return (a: TrialRow, b: TrialRow) => {
      const x = get(a);
      const y = get(b);
      if (x === null && y === null) return a.nct_id.localeCompare(b.nct_id);
      if (x === null) return 1;
      if (y === null) return -1;
      return compare(x, y) * direction || b.nct_id.localeCompare(a.nct_id);
    };
  };
  switch (key) {
    case "start":
      return withMissingLast((row) => row.start_date, (x, y) => x.localeCompare(y));
    case "enrollment":
      return withMissingLast((row) => row.enrollment_count, (x, y) => x - y);
    case "phase":
      return withMissingLast((row) => phaseRank(phaseBucket(row.phases)), (x, y) => x - y);
    case "status":
      return withMissingLast(
        (row) => (row.overall_status ? STATUS_ORDER.indexOf(row.overall_status) : null),
        (x, y) => x - y,
      );
  }
}

function trialTable(collection: Collection): HTMLElement {
  let sortKey: SortKey = "start";
  let descending = true;
  let shown = PAGE_SIZE;

  const search = h("input", { type: "search", class: "input", placeholder: "Title, sponsor or NCT number", id: "trial-search" });
  const statusFilter = h(
    "select",
    { class: "input", id: "status-filter" },
    h("option", { value: "" }, "Any status"),
    collection.by_status.map((s) => h("option", { value: s.status }, `${statusLabel(s.status)} (${formatInt(s.count)})`)),
  );
  const phaseFilter = h(
    "select",
    { class: "input", id: "phase-filter" },
    h("option", { value: "" }, "Any phase"),
    collection.by_phase.map((p) => h("option", { value: p.phase }, `${phaseLabel(p.phase)} (${formatInt(p.count)})`)),
  );
  const sortSelect = h(
    "select",
    { class: "input", id: "sort-key" },
    SORTS.map((s) => h("option", { value: s.key }, s.label)),
  );
  const directionButton = h("button", { type: "button", class: "button", id: "sort-direction" });
  const count = h("p", { class: "section-note", "aria-live": "polite" });
  const body = h("tbody");
  const more = h("div", { class: "more" });
  const headers = new Map<SortKey, HTMLElement>();

  const sortHeader = (key: SortKey, label: string, termKey: string) => {
    const button = h("button", { type: "button", class: "sort-button", onclick: () => setSort(key) }, label, h("span", { class: "sort-arrow", "aria-hidden": "true" }));
    const cell = h("th", { scope: "col" }, button, term(termKey, "?"));
    cell.querySelector(".term")?.classList.add("term-help");
    cell.querySelector(".term")?.setAttribute("aria-label", `What does ${label.toLowerCase()} mean?`);
    headers.set(key, cell);
    return cell;
  };

  const setSort = (key: SortKey) => {
    if (sortKey === key) descending = !descending;
    else {
      sortKey = key;
      descending = key === "start" || key === "enrollment";
    }
    shown = PAGE_SIZE;
    render();
  };

  const filtered = (): TrialRow[] => {
    const query = fold(search.value);
    const status = statusFilter.value;
    const phase = phaseFilter.value;
    return collection.trials.filter((row) => {
      if (status && row.overall_status !== status) return false;
      if (phase && phaseBucket(row.phases) !== phase) return false;
      if (!query) return true;
      return fold(`${row.brief_title ?? ""} ${row.acronym ?? ""} ${row.lead_sponsor ?? ""} ${row.nct_id}`).includes(query);
    });
  };

  const render = () => {
    const rows = filtered().sort(compareRows(sortKey, descending));
    sortSelect.value = sortKey;
    const directionWords: Record<SortKey, [string, string]> = {
      start: ["Newest first", "Oldest first"],
      enrollment: ["Largest first", "Smallest first"],
      phase: ["Latest phase first", "Earliest phase first"],
      status: ["Finished first", "Not started first"],
    };
    directionButton.textContent = directionWords[sortKey][descending ? 0 : 1];
    for (const [key, cell] of headers) {
      cell.setAttribute("aria-sort", key === sortKey ? (descending ? "descending" : "ascending") : "none");
    }
    count.textContent = rows.length
      ? `Showing ${formatInt(Math.min(shown, rows.length))} of ${plural(rows.length, "trial")}.`
      : "No trials match these filters.";
    clear(body);
    for (const row of rows.slice(0, shown)) {
      body.appendChild(
        h(
          "tr",
          null,
          h(
            "td",
            { class: "cell-trial" },
            link(`/t/${row.nct_id}`, { class: "row-title" }, row.brief_title ?? row.nct_id),
            h(
              "span",
              { class: "row-sub" },
              row.nct_id,
              row.lead_sponsor ? ` · ${row.lead_sponsor}` : "",
              row.has_results ? " · results posted" : "",
            ),
          ),
          h("td", { "data-label": "Phase" }, phaseChip(row.phases)),
          h("td", { "data-label": "Status" }, statusChip(row.overall_status)),
          h(
            "td",
            { class: "num", "data-label": "Enrollment" },
            row.enrollment_count === null
              ? "—"
              : `${formatInt(row.enrollment_count)}${row.enrollment_type === "ESTIMATED" ? " (target)" : ""}`,
          ),
          h("td", { class: "num", "data-label": "Start" }, formatDate(row.start_date)),
        ),
      );
    }
    clear(more);
    if (rows.length > shown) {
      more.appendChild(
        showMoreButton(rows.length - shown, PAGE_SIZE, () => {
          shown += PAGE_SIZE;
          render();
        }),
      );
    }
  };

  const resetAndRender = () => {
    shown = PAGE_SIZE;
    render();
  };
  search.addEventListener("input", resetAndRender);
  statusFilter.addEventListener("change", resetAndRender);
  phaseFilter.addEventListener("change", resetAndRender);
  sortSelect.addEventListener("change", () => {
    sortKey = sortSelect.value as SortKey;
    descending = sortKey === "start" || sortKey === "enrollment";
    resetAndRender();
  });
  directionButton.addEventListener("click", () => {
    descending = !descending;
    resetAndRender();
  });

  const table = h(
    "table",
    { class: "trials" },
    h(
      "thead",
      null,
      h(
        "tr",
        null,
        h("th", { scope: "col" }, "Trial"),
        sortHeader("phase", "Phase", "phase"),
        sortHeader("status", "Status", "status"),
        sortHeader("enrollment", "Enrollment", "enrollment"),
        sortHeader("start", "Start", "start-date"),
      ),
    ),
    body,
  );
  render();
  return h(
    "div",
    { class: "trial-table" },
    h(
      "div",
      { class: "controls" },
      h("label", { class: "field" }, h("span", null, "Search"), search),
      h("label", { class: "field field-small" }, h("span", null, "Status"), statusFilter),
      h("label", { class: "field field-small" }, h("span", null, "Phase"), phaseFilter),
      h("label", { class: "field field-small" }, h("span", null, "Sort by"), sortSelect),
      h("div", { class: "field field-small" }, h("span", null, "Order"), directionButton),
    ),
    count,
    h("div", { class: "table-wrap" }, table),
    more,
  );
}

// --- page ----------------------------------------------------------------------

export async function collectionPage(meta: Meta, id: string): Promise<Page> {
  const [collection, changes] = await Promise.all([loadCollection(id), loadChanges()]);
  const summary = meta.collections.find((c) => c.id === id);
  const windowStart = shiftDate(meta.snapshot_date, -(meta.recent_window_days - 1));
  const recent = latestChanges(
    changes.events.filter((e) => e.collection === id && e.detected_date >= windowStart),
    Number.MAX_SAFE_INTEGER,
  );
  const since = meta.tracking_since ?? meta.snapshot_date;
  let shownChanges = PAGE_SIZE;
  const changeList = h("div");
  const renderChanges = () => {
    clear(changeList);
    changeList.appendChild(
      h(
        "ul",
        { class: "changes" },
        recent
          .slice(0, shownChanges)
          .map(({ event }) => changeItem({ change: event, trial: { nctId: event.nct_id, title: event.brief_title } })),
      ),
    );
    if (recent.length > shownChanges) {
      changeList.appendChild(
        showMoreButton(recent.length - shownChanges, PAGE_SIZE, () => {
          shownChanges += PAGE_SIZE;
          renderChanges();
        }),
      );
    }
  };
  if (recent.length) renderChanges();

  const node = h(
    "div",
    { class: "page", style: `--accent:${collection.accent}` },
    h("p", { class: "crumb" }, link("/", null, "← All collections")),
    h(
      "div",
      { class: "hero hero-accent" },
      h("p", { class: "eyebrow" }, "Collection"),
      h("h1", null, collection.name),
      h("p", { class: "lede" }, collection.why_it_matters),
    ),
    h(
      "div",
      { class: "stats stats-wide" },
      stat(formatInt(collection.trial_count), term("clinical-trial", "trials")),
      stat(formatInt(collection.recruiting_count), term("status-recruiting", "recruiting now")),
      stat(formatInt(collection.not_yet_recruiting_count), term("status-not_yet_recruiting", "not yet recruiting")),
      stat(formatInt(summary?.changes_recent ?? recent.length), [term("change", "changes"), ` in ${meta.recent_window_days} days`]),
    ),
    collection.partial
      ? h("p", { class: "note" }, "Part of this collection could not be refreshed on the last run, so some trials show the previous day's record.")
      : null,
    definition(collection),
    section("landscape", "Landscape", landscape(collection)),
    section("near", ["Recruiting near a city"], citySearch(collection)),
    section(
      "changes",
      `Changed in the last ${meta.recent_window_days} days`,
      recent.length
        ? changeList
        : emptyState(
            "No changes in this period",
            windowStart <= since
              ? `Tracking started on ${formatDate(since)}. Nothing in this collection has changed since then.`
              : `Nothing changed in this collection between ${formatDate(windowStart)} and ${formatDate(meta.snapshot_date)}.`,
          ),
    ),
    section("trials", "All trials", trialTable(collection)),
  );
  return { title: collection.name, node };
}
