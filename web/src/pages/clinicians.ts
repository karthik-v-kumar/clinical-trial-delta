// For clinical professionals: a way into the finder's clinician view for
// referring patients, and the trials that show a sign they may still be adding
// sites or patients (data/expanding.json). The site lists institutions, never
// investigators: named investigators and study contacts stay on ClinicalTrials.gov.

import { REGISTRY_URL, type Page } from "../config";
import { loadExpanding } from "../data";
import { clear, external, h, link } from "../dom";
import { formatDate, formatInt, plural, sponsorClassLabel } from "../format";
import { followButton } from "../follow";
import { term } from "../glossary";
import type { ExpandingIndex, ExpandingSignal, ExpandingSignalType, ExpandingTrial, Meta } from "../types";
import { emptyState, phaseChip, section, showMoreButton, statusChip } from "../ui";

const PAGE_SIZE = 20;
const COUNTRIES_SHOWN = 4;

function signalLabels(index: ExpandingIndex): Record<ExpandingSignalType, string> {
  return {
    sites_added: `Sites added in the last ${index.window_days} days`,
    enrollment_raised: `Enrollment target raised in the last ${index.window_days} days`,
    not_yet_recruiting: "Not yet recruiting",
    newly_registered: `First posted in the last ${index.registered_days} days`,
  };
}

function listText(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

function signalText(signal: ExpandingSignal, today: string): string {
  switch (signal.type) {
    case "sites_added": {
      const countries = signal.countries ?? [];
      const where = countries.length
        ? ` in ${countries.length <= 3 ? listText(countries) : `${countries.slice(0, 3).join(", ")} and ${countries.length - 3} more`}`
        : "";
      return `${plural(signal.count ?? 0, "site")} added${where}, ${formatDate(signal.date)}`;
    }
    case "enrollment_raised":
      return `Enrollment target raised ${formatInt(signal.from)} → ${formatInt(signal.to)}, ${formatDate(signal.date)}`;
    case "not_yet_recruiting": {
      if (!signal.date) return "Not yet recruiting";
      const passed = signal.date < today.slice(0, signal.date.length);
      return passed
        ? `Not yet recruiting; planned start ${formatDate(signal.date)} has passed`
        : `Not yet recruiting; start planned ${formatDate(signal.date)}`;
    }
    case "newly_registered":
      return `First posted ${formatDate(signal.date)}`;
  }
}

function countriesText(trial: ExpandingTrial): string {
  const shown = trial.countries.slice(0, COUNTRIES_SHOWN).map(([country, n]) => `${country} ${formatInt(n)}`);
  const more = trial.countries.length - COUNTRIES_SHOWN;
  return more > 0 ? `${shown.join(", ")} and ${plural(more, "more country", "more countries")}` : shown.join(", ");
}

function card(trial: ExpandingTrial, index: ExpandingIndex): HTMLElement {
  const fact = (label: Node | string, ...value: (Node | string)[]) =>
    h("div", { class: "find-fact" }, h("dt", null, label), h("dd", null, ...value));
  const sites = [
    trial.site_count ? `${formatInt(trial.site_count)} recruiting` : "",
    trial.planned_site_count ? `${formatInt(trial.planned_site_count)} not open yet` : "",
  ].filter(Boolean);
  return h(
    "li",
    { class: "find-item" },
    h("ul", { class: "find-news", "aria-label": "Signs of expansion" }, trial.signals.map((s) => h("li", null, signalText(s, index.snapshot_date)))),
    h("h3", { class: "find-title" }, link(`/t/${trial.nct_id}`, { class: "row-title" }, trial.title ?? trial.nct_id)),
    h(
      "p",
      { class: "row-meta" },
      statusChip(trial.status),
      phaseChip(trial.phases),
      h(
        "span",
        { class: "row-sub" },
        trial.nct_id,
        trial.sponsor ? ` · ${trial.sponsor}` : "",
        trial.sponsor_class ? ` (${sponsorClassLabel(trial.sponsor_class).toLowerCase()})` : "",
      ),
    ),
    h(
      "dl",
      { class: "find-facts" },
      fact(term("site", "Sites"), sites.length ? sites.join(", ") : "None listed yet"),
      trial.countries.length ? fact("Countries", countriesText(trial)) : null,
    ),
    h(
      "p",
      { class: "find-links" },
      link(`/t/${trial.nct_id}`, null, "Trial details"),
      external(`${REGISTRY_URL}study/${trial.nct_id}`, null, "Sponsor and study contacts on ClinicalTrials.gov"),
      followButton(trial.nct_id, trial.title, index.snapshot_date, "button-small"),
    ),
  );
}

function select(id: string, options: [string, string][]): HTMLSelectElement {
  return h("select", { id, class: "input" }, options.map(([value, label]) => h("option", { value }, label)));
}

function expandingList(index: ExpandingIndex, meta: Meta): HTMLElement {
  const labels = signalLabels(index);
  const countryTrials = new Map<string, number>();
  for (const trial of index.trials) {
    for (const [country] of trial.countries) countryTrials.set(country, (countryTrials.get(country) ?? 0) + 1);
  }
  const area = select("exp-area", [
    ["", "All three areas"],
    ...meta.collections.map((c): [string, string] => [c.id, c.name]),
  ]);
  const country = select("exp-country", [
    ["", "Any country"],
    ...[...countryTrials.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([name, n]): [string, string] => [name, `${name} (${formatInt(n)})`]),
  ]);
  const signal = select("exp-signal", [
    ["", "Any sign"],
    ...(Object.keys(labels) as ExpandingSignalType[]).map((type): [string, string] => [type, labels[type]]),
  ]);
  const sponsor = select("exp-sponsor", [
    ["", "Any sponsor"],
    ["INDUSTRY", "Industry"],
    ["OTHER", "Not industry"],
  ]);
  const count = h("p", { class: "find-count", "aria-live": "polite" });
  const results = h("div");
  let shown = PAGE_SIZE;

  const render = () => {
    const hits = index.trials.filter(
      (t) =>
        (!area.value || t.collections.includes(area.value)) &&
        (!country.value || t.countries.some(([name]) => name === country.value)) &&
        (!signal.value || t.signals.some((s) => s.type === signal.value)) &&
        (!sponsor.value || (sponsor.value === "INDUSTRY") === (t.sponsor_class === "INDUSTRY")),
    );
    count.textContent = hits.length ? `${plural(hits.length, "trial")}, newest sign first.` : "";
    clear(results);
    if (!hits.length) {
      results.append(emptyState("No trial matches these choices", "Try another country or sign."));
      return;
    }
    results.append(h("ol", { class: "find-list" }, hits.slice(0, shown).map((t) => card(t, index))));
    if (hits.length > shown) {
      results.append(
        h(
          "div",
          { class: "more" },
          showMoreButton(hits.length - shown, PAGE_SIZE, () => {
            shown += PAGE_SIZE;
            render();
          }),
        ),
      );
    }
  };
  for (const control of [area, country, signal, sponsor]) {
    control.addEventListener("change", () => {
      shown = PAGE_SIZE;
      render();
    });
  }
  render();
  const field = (label: string, control: HTMLElement) => h("label", { class: "field field-small" }, h("span", null, label), control);
  return h(
    "div",
    null,
    h(
      "form",
      { class: "finder-form expanding-form", "aria-label": "Filter trials adding sites", onsubmit: (e: Event) => e.preventDefault() },
      field("Area", area),
      field("Country", country),
      field("Sign", signal),
      field("Sponsor", sponsor),
    ),
    count,
    results,
  );
}

export async function cliniciansPage(meta: Meta): Promise<Page> {
  const index = await loadExpanding();
  const since = meta.tracking_since ?? meta.snapshot_date;
  const node = h(
    "div",
    { class: "page clinicians-page" },
    h(
      "header",
      { class: "hero hero-accent" },
      h("p", { class: "eyebrow" }, "For clinical professionals"),
      h("h1", null, "Refer patients, and find trials still adding sites"),
      h(
        "p",
        { class: "lede" },
        "The same daily registry record, arranged for clinical work: the lab cut-offs, design and endpoints you check before a referral, and the trials whose records show they may still be adding sites or patients.",
      ),
    ),
    h(
      "aside",
      { class: "find-notice", "aria-label": "About investigators and contacts" },
      h(
        "p",
        null,
        h("strong", null, "Institutions, not investigators. "),
        "This site lists the institutions running each trial. ",
        term("principal-investigator", "Principal investigators"),
        " and study contacts are on ClinicalTrials.gov, the official and current source, and every trial here links to its record.",
      ),
      h(
        "p",
        null,
        h("strong", null, "Not medical advice. "),
        "Whether a patient qualifies is for the study team to decide, using the trial's full criteria.",
      ),
    ),
    section(
      "refer",
      "Refer a patient",
      h(
        "p",
        { class: "section-note" },
        "The clinician view of Find a trial adds, for every result, the HbA1c, BMI, eGFR and lipid cut-offs quoted from the eligibility criteria, the design and comparators, the primary endpoint and target enrollment, and the institutions nearest a place you choose.",
      ),
      h(
        "p",
        { class: "trial-actions" },
        link("/find?view=clinician", { class: "button button-primary" }, "Open the clinician view"),
        link("/drugs", { class: "button button-quiet" }, "Browse by drug"),
      ),
    ),
    section(
      "expanding",
      "Trials that may still be adding sites",
      h(
        "p",
        { class: "section-note" },
        "ClinicalTrials.gov has no sign-up for investigators. Sponsors choose sites themselves (",
        term("site-selection", "site selection"),
        "), often through a ",
        term("cro", "contract research organization"),
        ". These recruiting or not-yet-recruiting trials show a sign that sites or patients are still being added, so their sponsor's contact on ClinicalTrials.gov is the place to ask.",
      ),
      expandingList(index, meta),
    ),
    section(
      "signals",
      "How the signs are found",
      h(
        "ul",
        { class: "find-about" },
        h(
          "li",
          null,
          `Sites added and enrollment targets raised come from this site's daily comparison of the registry, over the last ${index.window_days} days. Tracking started on ${formatDate(since)}, so the list of these grows over time.`,
        ),
        h(
          "li",
          null,
          `“Not yet recruiting” and “first posted” come from each trial's own record; a new or not-yet-open trial may still be choosing sites, or may already have chosen them all.`,
        ),
        h("li", null, "A sign is not an invitation: only the sponsor can say whether it is still looking for sites."),
        h("li", null, `Data as of ${formatDate(index.snapshot_date)}.`),
      ),
    ),
  );
  return { title: "For clinical professionals", node };
}
