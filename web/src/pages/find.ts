// Find a trial: trials that are recruiting, or listed as not yet recruiting, near
// a place, narrowed by the age and sex limits the registry lists and by what is
// being tested. Recruiting trials always come first. Everything runs in the
// browser from data/finder.json; the place, age and other answers a visitor
// gives are never sent anywhere or saved, and nothing is written to the URL but
// the chosen area.

import { REGISTRY_URL, type Page } from "../config";
import { loadFinder } from "../data";
import { clear, external, h, link } from "../dom";
import { daysBetween, formatDate, formatInt, plural, sexLabel } from "../format";
import { cityName, fold, matchCities, milesBetween, type City } from "../geo";
import { term } from "../glossary";
import type { FinderIndex, FinderTrial, InterventionKind, Meta } from "../types";
import { emptyState, phaseChip, section, showMoreButton, statusChip } from "../ui";

/** Results shown per group before "Show more"; recruiting trials get the longer first page. */
const PAGE_SIZE: Record<"open" | "soon", number> = { open: 20, soon: 10 };
const RADII = [10, 25, 50, 100, 250];
const ANY_DISTANCE = 0;
const DEFAULT_RADIUS = 50;
/** A site that has not opened is worth a mention when it is at least this much nearer. */
const CLOSER_BY_MILES = 5;

/** "open": a recruiting site is in reach. "soon": only sites that have not opened are. */
type Group = "open" | "soon";
const GROUP_TITLES: Record<Group, string> = { open: "Recruiting now", soon: "Not recruiting yet" };

const KIND_LABELS: Record<InterventionKind, string> = {
  medicine: "Medicines",
  device: "Devices and tests",
  lifestyle: "Diet, exercise or behavior",
  procedure: "Surgery and other procedures",
  other: "Other",
};
const KINDS = Object.keys(KIND_LABELS) as InterventionKind[];

interface Origin {
  lat: number;
  lon: number;
  label: string;
}

interface Filters {
  area: string;
  show: "" | Group;
  origin: Origin | null;
  radius: number;
  age: number | null;
  sex: string;
  kind: string;
  noPlacebo: boolean;
}

interface Near {
  city: City;
  miles: number;
  /** How many of the trial's cities of this kind are within the chosen distance. */
  count: number;
}

interface Hit {
  trial: FinderTrial;
  group: Group;
  /** Nearest site of the group's kind, when a place is set. */
  near: Near | null;
  /** For a recruiting hit: a site that has not opened and is clearly nearer. */
  closer: Near | null;
}

// --- matching -----------------------------------------------------------------

/** Within the trial's listed age limits (inclusive); no age given matches every trial. */
export function fitsAge(trial: FinderTrial, age: number | null): boolean {
  if (age === null) return true;
  if (trial.min_age !== null && age < trial.min_age) return false;
  if (trial.max_age !== null && age > trial.max_age) return false;
  return true;
}

/** A trial for one sex only does not match the other; "ALL" or unlisted matches everyone. */
export function fitsSex(trial: FinderTrial, sex: string): boolean {
  if (!sex || !trial.sex || trial.sex === "ALL") return true;
  return trial.sex === sex;
}

function nearestOf(index: FinderIndex, cities: number[], distance: number[], radius: number): Near | null {
  let best = -1;
  let count = 0;
  for (const i of cities) {
    const miles = distance[i] ?? Infinity;
    if (radius !== ANY_DISTANCE && miles > radius) continue;
    count += 1;
    if (best === -1 || miles < (distance[best] ?? Infinity)) best = i;
  }
  const city = index.cities[best];
  return city ? { city, miles: distance[best] ?? 0, count } : null;
}

/**
 * Matching trials, recruiting ones first. With a place, a trial counts as
 * recruiting when a recruiting site is within the distance, and as "not
 * recruiting yet" when only sites that have not opened are. Without one, the
 * trial's own status decides.
 */
function findTrials(index: FinderIndex, filters: Filters): Hit[] {
  const { origin, radius, show } = filters;
  const distance = origin ? index.cities.map((city) => milesBetween(origin, city)) : null;
  const hits: Hit[] = [];
  for (const trial of index.trials) {
    if (filters.area && !trial.collections.includes(filters.area)) continue;
    if (!fitsAge(trial, filters.age) || !fitsSex(trial, filters.sex)) continue;
    if (filters.kind && !trial.kinds.includes(filters.kind as InterventionKind)) continue;
    if (filters.noPlacebo && trial.placebo) continue;
    if (!distance) {
      const group: Group = trial.status === "RECRUITING" ? "open" : "soon";
      if (!show || show === group) hits.push({ trial, group, near: null, closer: null });
      continue;
    }
    const open = nearestOf(index, trial.cities, distance, radius);
    const soon = nearestOf(index, trial.soon, distance, radius);
    if (open) {
      if (show === "soon") continue;
      const closer = !show && soon && soon.miles + CLOSER_BY_MILES <= open.miles ? soon : null;
      hits.push({ trial, group: "open", near: open, closer });
    } else if (soon && show !== "open") {
      hits.push({ trial, group: "soon", near: soon, closer: null });
    }
  }
  const byGroup = (a: Hit, b: Hit) => (a.group === b.group ? 0 : a.group === "open" ? -1 : 1);
  if (distance) {
    return hits.sort(
      (a, b) => byGroup(a, b) || (a.near?.miles ?? 0) - (b.near?.miles ?? 0) || a.trial.nct_id.localeCompare(b.trial.nct_id),
    );
  }
  // No place given: the newest news first, then the most recently registered.
  const newest = (t: FinderTrial) =>
    [t.opened ?? "", t.new_sites?.[0]?.date ?? "", t.first_posted ?? ""].sort().at(-1) ?? "";
  return hits.sort(
    (a, b) => byGroup(a, b) || newest(b.trial).localeCompare(newest(a.trial)) || b.trial.nct_id.localeCompare(a.trial.nct_id),
  );
}

// --- wording ------------------------------------------------------------------

function years(value: number): string {
  if (value < 1) {
    const months = Math.round(value * 12);
    return months === 1 ? "1 month" : `${months} months`;
  }
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export function ageText(min: number | null, max: number | null): string {
  if (min === null && max === null) return "No age limits listed";
  if (min !== null && max !== null) {
    return min < 1 ? `Ages ${years(min)} to ${years(max)} years` : `Ages ${years(min)} to ${years(max)}`;
  }
  if (min !== null) return `Ages ${years(min)} and older`;
  const upper = max ?? 0;
  return upper < 1 ? `Up to ${years(upper)} old` : `Up to age ${years(upper)}`;
}

function distanceText(miles: number): string {
  if (miles < 1) return "less than a mile away";
  const rounded = miles < 10 ? Math.round(miles) : Math.round(miles / 5) * 5;
  return `about ${formatInt(rounded)} ${rounded === 1 ? "mile" : "miles"} away`;
}

function listText(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

/** "Type 2 diabetes" -> "type 2 diabetes" mid-sentence; "GLP-1 & obesity" keeps its acronym. */
function inSentence(name: string): string {
  return /^[A-Z][a-z]/.test(name) ? name.charAt(0).toLowerCase() + name.slice(1) : name;
}

/** "in Germany and Japan" for one or two countries, "in 5 countries" for more. */
function countriesText(index: FinderIndex, cities: number[]): string {
  const countries: string[] = [];
  for (const i of cities) {
    const country = index.cities[i]?.country;
    if (country && !countries.includes(country)) countries.push(country);
  }
  if (!countries.length) return "";
  return countries.length <= 2 ? ` in ${listText(countries)}` : ` in ${countries.length} countries`;
}

/** A planned start is the sponsor's estimate; say so plainly when it has already gone by. */
export function plannedStartText(start: string, today: string): string {
  const passed = start < today.slice(0, start.length);
  return passed ? `${formatDate(start)}, now past; the record still says not yet recruiting` : formatDate(start);
}

// --- results ------------------------------------------------------------------

function newsLines(index: FinderIndex, hit: Hit, filters: Filters): HTMLElement[] {
  const today = index.snapshot_date;
  const recent = (date: string | null | undefined) =>
    Boolean(date) && daysBetween(date as string, today) < index.news_window_days;
  const { origin, radius } = filters;
  const lines: HTMLElement[] = [];
  const { trial } = hit;
  if (trial.opened) lines.push(h("li", null, `Started recruiting ${formatDate(trial.opened)}`));
  for (const added of trial.new_sites ?? []) {
    const all = added.cities.flatMap((i) => {
      const city = index.cities[i];
      return city ? [{ city, open: trial.cities.includes(i) }] : [];
    });
    const near = origin ? all.filter((site) => radius === ANY_DISTANCE || milesBetween(origin, site.city) <= radius) : [];
    const named = near.length ? near : all;
    if (!named.length) continue;
    // A site that was added but has not opened is a planned one.
    const noun = named.every((site) => !site.open) ? "planned site" : "site";
    const what = `${named.length === 1 ? "New" : `${named.length} new`} ${noun}${named.length === 1 ? "" : "s"}${near.length ? " nearby" : ""}`;
    const where = named.length === 1 && named[0] ? ` in ${named[0].city.city}` : "";
    lines.push(h("li", null, `${what}${where}, added ${formatDate(added.date)}`));
  }
  if (!trial.opened && recent(trial.first_posted)) {
    lines.push(h("li", null, `First posted ${formatDate(trial.first_posted)}`));
  }
  return lines;
}

function placeboChip(): HTMLElement {
  const chip = term("placebo", "Placebo group");
  chip.classList.add("chip");
  return chip;
}

/** Where the trial is, in words: the nearest site when a place is set, a site count otherwise. */
function whereFact(index: FinderIndex, hit: Hit, filters: Filters): { label: Node | string; value: (Node | string)[] } {
  const { trial, group, near } = hit;
  const sub = (text: string) => h("span", { class: "find-sub" }, text);
  if (!near) {
    if (group === "open") {
      const value: (Node | string)[] = [
        h("span", { class: "find-place" }, `${plural(trial.site_count, "recruiting site")}${countriesText(index, trial.cities)}`),
      ];
      if (trial.planned_site_count) value.push(sub(`${plural(trial.planned_site_count, "more site")} not open yet`));
      return { label: term("site", "Sites"), value };
    }
    return {
      label: term("site", "Planned sites"),
      value: [`${plural(trial.planned_site_count, "site")}${countriesText(index, trial.soon)}, none open yet`],
    };
  }
  const scope = filters.radius === ANY_DISTANCE ? "" : ` within ${filters.radius} miles`;
  const detail = [distanceText(near.miles)];
  if (near.count > 1) detail.push(`${plural(near.count - 1, "more city", "more cities")}${scope}`);
  const value: (Node | string)[] = [h("span", { class: "find-place" }, cityName(near.city))];
  if (group === "open") {
    value.push(sub(detail.join(" · ")));
    if (hit.closer) {
      value.push(sub(`A closer site, in ${cityName(hit.closer.city)} (${distanceText(hit.closer.miles)}), is not open yet.`));
    }
    return { label: "Nearest site", value };
  }
  value.push(sub(`${detail.join(" · ")} · not open yet`));
  if (trial.status === "RECRUITING" && trial.site_count) {
    value.push(sub(`Recruiting now at ${plural(trial.site_count, "site")} farther away.`));
  }
  return { label: "Planned site", value };
}

function resultItem(index: FinderIndex, hit: Hit, filters: Filters): HTMLElement {
  const { trial, group } = hit;
  const news = newsLines(index, hit, filters);
  const where = whereFact(index, hit, filters);
  const fact = (label: Node | string, ...value: (Node | string)[]) =>
    h("div", { class: "find-fact" }, h("dt", null, label), h("dd", null, ...value));
  // A trial that has not started says so; one that is recruiting elsewhere only has a nearby site to open.
  const notOpen =
    trial.status === "NOT_YET_RECRUITING"
      ? statusChip(trial.status)
      : group === "soon"
        ? h("span", { class: "chip chip-plain" }, "Nearby site not open yet")
        : null;
  return h(
    "li",
    { class: "find-item" },
    news.length ? h("ul", { class: "find-news", "aria-label": "Recent changes" }, news) : null,
    h("h3", { class: "find-title" }, link(`/t/${trial.nct_id}`, { class: "row-title" }, trial.title ?? trial.nct_id)),
    h(
      "p",
      { class: "row-meta" },
      notOpen,
      phaseChip(trial.phases),
      trial.placebo ? placeboChip() : null,
      h("span", { class: "row-sub" }, trial.nct_id, trial.sponsor ? ` · ${trial.sponsor}` : ""),
    ),
    h(
      "dl",
      { class: "find-facts" },
      fact(where.label, ...where.value),
      trial.status === "NOT_YET_RECRUITING" && trial.start_date
        ? fact(term("start-date", "Planned start"), plannedStartText(trial.start_date, index.snapshot_date))
        : null,
      fact(term("age-limits", "Who"), ageText(trial.min_age, trial.max_age), " · ", sexLabel(trial.sex ?? "ALL").toLowerCase()),
      trial.tested.length ? fact(term("intervention", "Testing"), trial.tested.join(", ")) : null,
      trial.conditions.length ? fact(term("condition", "For"), trial.conditions.join(", ")) : null,
    ),
    h(
      "p",
      { class: "find-links" },
      link(`/t/${trial.nct_id}`, null, "Trial details and eligibility"),
      external(`${REGISTRY_URL}study/${trial.nct_id}`, null, "Contacts on ClinicalTrials.gov"),
    ),
  );
}

// --- page -------------------------------------------------------------------

function select(id: string, options: [string, string][], value: string): HTMLSelectElement {
  return h(
    "select",
    { id, class: "input" },
    options.map(([v, label]) => h("option", { value: v, selected: v === value }, label)),
  );
}

function finder(index: FinderIndex, initialArea: string): HTMLElement {
  const filters: Filters = {
    area: index.collections.some((c) => c.id === initialArea) ? initialArea : "",
    show: "",
    origin: null,
    radius: DEFAULT_RADIUS,
    age: null,
    sex: "",
    kind: "",
    noPlacebo: false,
  };
  const shown = { ...PAGE_SIZE };

  const inArea = (id: string) => index.trials.filter((t) => t.collections.includes(id)).length;
  const area = select(
    "find-area",
    [
      ["", `All three areas (${formatInt(index.trials.length)})`],
      ...index.collections.map((c): [string, string] => [c.id, `${c.name} (${formatInt(inArea(c.id))})`]),
    ],
    filters.area,
  );
  const show = select(
    "find-show",
    [
      ["", "Recruiting or not yet recruiting"],
      ["open", "Recruiting now only"],
      ["soon", "Not yet recruiting only"],
    ],
    "",
  );
  const place = h("input", {
    type: "search",
    id: "find-place",
    class: "input",
    placeholder: "City, for example Boston",
    autocomplete: "off",
    "aria-controls": "find-place-options",
    "aria-describedby": "find-place-help",
  });
  const locate = h("button", { type: "button", class: "button button-quiet", id: "find-locate" }, "Use my location");
  const placeHelp = h(
    "p",
    { class: "field-help", id: "find-place-help", "aria-live": "polite" },
    "Pick from the cities that have a trial site, or use your location. Distances are straight-line, between city centers.",
  );
  const options = h("ul", { class: "city-options", id: "find-place-options" });
  const radius = select(
    "find-radius",
    [...RADII.map((m): [string, string] => [String(m), `${m} miles`]), [String(ANY_DISTANCE), "Any distance"]],
    String(DEFAULT_RADIUS),
  );
  const age = h("input", {
    type: "number",
    id: "find-age",
    class: "input",
    inputmode: "numeric",
    min: 0,
    max: 120,
    step: 1,
    placeholder: "Any",
  });
  const sex = select("find-sex", [["", "Any"], ["FEMALE", "Female"], ["MALE", "Male"]], "");
  const kindCount = (kind: InterventionKind) => index.trials.filter((t) => t.kinds.includes(kind)).length;
  const kind = select(
    "find-kind",
    [["", "Anything"], ...KINDS.filter((k) => kindCount(k) > 0).map((k): [string, string] => [k, KIND_LABELS[k]])],
    "",
  );
  const noPlacebo = h("input", { type: "checkbox", id: "find-no-placebo" });
  const reset = h("button", { type: "button", class: "link-button", id: "find-reset" }, "Clear all");

  const count = h("p", { class: "find-count", "aria-live": "polite" });
  const results = h("div", { class: "find-results" });

  const render = () => {
    const hits = findTrials(index, filters);
    clear(results);
    const counts: Record<Group, number> = { open: 0, soon: 0 };
    for (const hit of hits) counts[hit.group] += 1;
    const where = filters.origin
      ? filters.radius === ANY_DISTANCE
        ? `, nearest to ${filters.origin.label} first`
        : ` within ${filters.radius} miles of ${filters.origin.label}, nearest first`
      : "";
    clear(count);
    if (!hits.length) {
      const reach = filters.origin && filters.radius !== ANY_DISTANCE;
      results.appendChild(
        emptyState(
          reach ? `No trial matches within ${filters.radius} miles of ${filters.origin?.label}` : "No trial matches these answers",
          reach ? "Try a wider distance, or clear an answer. " : "Try clearing an answer. ",
          "Trials open and close every week, and this list is refreshed daily.",
        ),
      );
      return;
    }
    // "26 trials recruiting now and 19 not recruiting yet within 50 miles of ...": the
    // second count jumps to its group, which sits below the recruiting trials.
    const soonText = `${counts.open ? formatInt(counts.soon) : plural(counts.soon, "trial")} not recruiting yet`;
    count.append(
      counts.open ? `${plural(counts.open, "trial")} recruiting now` : "",
      counts.open && counts.soon ? " and " : "",
      counts.soon ? (counts.open ? h("a", { href: "#find-group-soon" }, soonText) : soonText) : "",
      `${where}.${filters.origin ? "" : " Add a place to see the nearest first."}`,
    );
    for (const group of ["open", "soon"] as const) {
      const all = hits.filter((hit) => hit.group === group);
      if (!all.length) continue;
      results.appendChild(
        h(
          "section",
          { class: "find-group", "aria-labelledby": `find-group-${group}` },
          h(
            "h2",
            { class: "find-group-title", id: `find-group-${group}` },
            GROUP_TITLES[group],
            h("span", { class: "find-group-count" }, ` ${formatInt(all.length)}`),
          ),
          group === "soon"
            ? h(
                "p",
                { class: "section-note" },
                "These are not open to participants yet. Planned dates are the sponsor's estimates and often move; the study contact on ClinicalTrials.gov can say when a site expects to open.",
              )
            : null,
          h("ol", { class: "find-list" }, all.slice(0, shown[group]).map((hit) => resultItem(index, hit, filters))),
          all.length > shown[group]
            ? h(
                "div",
                { class: "more" },
                showMoreButton(all.length - shown[group], PAGE_SIZE.open, () => {
                  shown[group] += PAGE_SIZE.open;
                  render();
                }),
              )
            : null,
        ),
      );
    }
  };
  const update = () => {
    Object.assign(shown, PAGE_SIZE);
    render();
  };

  // Place: a city from the list, or the device's own location.
  const choose = (city: City) => {
    filters.origin = { lat: city.lat, lon: city.lon, label: cityName(city) };
    place.value = cityName(city);
    clear(options);
    update();
  };
  const renderOptions = () => {
    clear(options);
    const typed = place.value;
    if (filters.origin && fold(filters.origin.label) === fold(typed)) return;
    const matches = matchCities(index.cities, typed);
    if (fold(typed).length >= 2 && !matches.length) {
      options.appendChild(
        h("li", { class: "city-none" }, "No trial site in a city with that name. Try the nearest large city, or use your location."),
      );
      return;
    }
    for (const city of matches) {
      options.appendChild(
        h("li", null, h("button", { type: "button", class: "city-option", onclick: () => choose(city) }, cityName(city))),
      );
    }
  };
  place.addEventListener("input", () => {
    const hadOrigin = filters.origin !== null;
    filters.origin = null;
    renderOptions();
    if (hadOrigin) update();
  });
  place.addEventListener("keydown", (event) => {
    if ((event as KeyboardEvent).key !== "Enter") return;
    event.preventDefault();
    const first = matchCities(index.cities, place.value, 1)[0];
    if (first) choose(first);
  });
  locate.addEventListener("click", () => {
    if (!("geolocation" in navigator)) {
      placeHelp.textContent = "This browser cannot share a location. Type a city instead.";
      return;
    }
    locate.setAttribute("disabled", "");
    placeHelp.textContent = "Finding your location… It stays on this device.";
    navigator.geolocation.getCurrentPosition(
      (position) => {
        locate.removeAttribute("disabled");
        filters.origin = { lat: position.coords.latitude, lon: position.coords.longitude, label: "your location" };
        place.value = "My location";
        clear(options);
        placeHelp.textContent = "Using your location. It stays on this device and is not saved.";
        update();
      },
      (error) => {
        locate.removeAttribute("disabled");
        placeHelp.textContent =
          error.code === error.PERMISSION_DENIED
            ? "Location was not shared. Type a city instead."
            : "Your location could not be found. Type a city instead.";
      },
      { enableHighAccuracy: false, timeout: 15_000, maximumAge: 10 * 60_000 },
    );
  });

  area.addEventListener("change", () => {
    filters.area = area.value;
    const url = new URL(location.href);
    if (filters.area) url.searchParams.set("area", filters.area);
    else url.searchParams.delete("area");
    history.replaceState(history.state, "", url.pathname + url.search);
    update();
  });
  show.addEventListener("change", () => {
    filters.show = show.value as Filters["show"];
    update();
  });
  radius.addEventListener("change", () => {
    filters.radius = Number(radius.value);
    update();
  });
  age.addEventListener("input", () => {
    const value = age.value.trim() === "" ? NaN : Number(age.value);
    filters.age = Number.isFinite(value) && value >= 0 && value <= 120 ? Math.floor(value) : null;
    update();
  });
  sex.addEventListener("change", () => {
    filters.sex = sex.value;
    update();
  });
  kind.addEventListener("change", () => {
    filters.kind = kind.value;
    update();
  });
  noPlacebo.addEventListener("change", () => {
    filters.noPlacebo = noPlacebo.checked;
    update();
  });
  reset.addEventListener("click", () => {
    Object.assign(filters, { area: "", show: "", origin: null, radius: DEFAULT_RADIUS, age: null, sex: "", kind: "", noPlacebo: false });
    area.value = "";
    show.value = "";
    place.value = "";
    radius.value = String(DEFAULT_RADIUS);
    age.value = "";
    sex.value = "";
    kind.value = "";
    noPlacebo.checked = false;
    clear(options);
    history.replaceState(history.state, "", location.pathname);
    update();
  });

  update();

  const field = (label: string | Node, control: HTMLElement, extra = "") =>
    h("label", { class: `field ${extra}`.trim() }, h("span", null, label), control);

  return h(
    "div",
    { class: "finder" },
    h(
      "form",
      { class: "finder-form", role: "search", "aria-label": "Find a trial", onsubmit: (e: Event) => e.preventDefault() },
      field("Condition area", area, "field-area"),
      field("Trial status", show, "field-show"),
      h(
        "div",
        { class: "field field-place" },
        h("label", { for: "find-place" }, "Near"),
        h("div", { class: "place-row" }, place, locate),
        placeHelp,
        options,
      ),
      field("Within", radius, "field-small"),
      field("Your age", age, "field-small"),
      field("Sex", sex, "field-small"),
      field("What is tested", kind, "field-small"),
      h(
        "div",
        { class: "finder-foot" },
        h(
          "label",
          { class: "check" },
          noPlacebo,
          h("span", null, "Leave out trials with a ", term("placebo", "placebo"), " group"),
        ),
        reset,
      ),
    ),
    count,
    results,
  );
}

export async function findPage(meta: Meta): Promise<Page> {
  const index = await loadFinder();
  const initialArea = new URLSearchParams(location.search).get("area") ?? "";
  const areas = listText(index.collections.map((c) => inSentence(c.name)));
  const openCount = index.trials.filter((t) => t.status === "RECRUITING").length;
  const soonCount = index.trials.length - openCount;
  // "2 recruiting trials and 68 trials that are not yet recruiting name no such site ..."
  const missing = [
    index.trials_without_location ? plural(index.trials_without_location, "recruiting trial") : "",
    index.not_yet_without_location
      ? `${formatInt(index.not_yet_without_location)} that ${index.not_yet_without_location === 1 ? "is" : "are"} not yet recruiting`
      : "",
  ].filter(Boolean);
  const missingCount = index.trials_without_location + index.not_yet_without_location;
  const unlisted = missing.length
    ? `${missing.join(" and ")} ${missingCount === 1 ? "names" : "name"} no such site and ${missingCount === 1 ? "is" : "are"} on the collection pages only.`
    : "";
  const node = h(
    "div",
    { class: "page find-page" },
    h(
      "header",
      { class: "hero hero-accent" },
      h("p", { class: "eyebrow" }, "For patients and caregivers"),
      h("h1", null, "Find a trial near you"),
      h(
        "p",
        { class: "lede" },
        `Search ${formatInt(openCount)} trials in ${areas} that are `,
        term("status-recruiting", "recruiting"),
        ` now, and ${formatInt(soonCount)} more that are `,
        term("status-not_yet_recruiting", "not yet recruiting"),
        ". Narrow them by where you live, your age and what is being tested, then take the trials that interest you to your doctor.",
      ),
    ),
    h(
      "aside",
      { class: "find-notice", "aria-label": "Before you search" },
      h(
        "p",
        null,
        h("strong", null, "Not medical advice. "),
        "Only a trial's study team can say whether you qualify, using its full ",
        term("eligibility", "eligibility criteria"),
        ". Talk to your doctor before you contact a trial or change any treatment.",
      ),
      h(
        "p",
        null,
        h("strong", null, "Private. "),
        "Your place, age and other answers stay in this browser. They are not sent anywhere or saved.",
      ),
    ),
    finder(index, initialArea),
    section(
      "find-about",
      "About these results",
      h(
        "ul",
        { class: "find-about" },
        h(
          "li",
          null,
          "Listed: trials whose registry record says recruiting or not yet recruiting, and that name at least one site with a location. ",
          unlisted,
        ),
        h(
          "li",
          null,
          "With a place chosen, a trial is under “Recruiting now” when one of its recruiting sites is within the distance, and under “Not recruiting yet” when only sites that have not opened are. A trial that has not started gives its sites no status, so all of them count as planned.",
        ),
        h(
          "li",
          null,
          "The age and sex filters use only the limits each record lists. Every other criterion is on the trial page and in the registry.",
        ),
        h(
          "li",
          null,
          `“Started recruiting” and “new site” come from this site's daily comparison of the registry, over the last ${index.news_window_days} days. Recruiting status and planned start dates can lag behind what is happening at a site, so confirm with the study contact.`,
        ),
        h(
          "li",
          null,
          `This page covers only the three areas this site tracks. For any other condition, use the search on `,
          external(REGISTRY_URL, null, "ClinicalTrials.gov"),
          ".",
        ),
        h("li", null, `Data as of ${formatDate(meta.snapshot_date)}.`),
      ),
    ),
  );
  return { title: "Find a trial", node };
}
