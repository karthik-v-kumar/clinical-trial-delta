// Small shared pieces used by more than one page.

import { h, link, type Child } from "./dom";
import {
  CHANGE_LABELS,
  describeChange,
  formatDate,
  phaseBucket,
  phaseLabel,
  phaseTermKey,
  statusLabel,
  statusTermKey,
} from "./format";
import { term } from "./glossary";
import type { ChangeCore, CollectionSummary } from "./types";

/** Status as a tappable chip. Recruiting gets a filled dot; the label always carries the meaning. */
export function statusChip(status: string | null | undefined): HTMLElement {
  const chip = term(statusTermKey(status), statusLabel(status));
  chip.classList.add("chip", "chip-status");
  if (status) chip.dataset.status = status;
  chip.prepend(h("span", { class: "dot", "aria-hidden": "true" }));
  return chip;
}

export function phaseChip(phases: string[] | null | undefined): HTMLElement {
  const bucket = phaseBucket(phases);
  const chip = term(phaseTermKey(bucket), phaseLabel(bucket));
  chip.classList.add("chip");
  return chip;
}

export function stat(value: Child, label: Child): HTMLElement {
  return h("div", { class: "stat" }, h("div", { class: "stat-value" }, value), h("div", { class: "stat-label" }, label));
}

export function emptyState(title: string, ...body: Child[]): HTMLElement {
  return h("div", { class: "empty" }, h("p", { class: "empty-title" }, title), h("p", null, ...body));
}

export function section(id: string, title: Child, ...children: Child[]): HTMLElement {
  return h("section", { class: "section", id }, h("h2", null, title), ...children);
}

export function collectionTag(collection: CollectionSummary): HTMLElement {
  return h(
    "span",
    { class: "collection-tag" },
    h("span", { class: "swatch", style: `background:${collection.accent}`, "aria-hidden": "true" }),
    collection.name,
  );
}

export interface ChangeItemOptions {
  change: ChangeCore;
  /** When set, the item names the trial and links to it (feeds). */
  trial?: { nctId: string; title: string | null };
  collections?: CollectionSummary[];
}

/** One change, as a list item: what kind, when, what happened. */
export function changeItem({ change, trial, collections }: ChangeItemOptions): HTMLElement {
  const { headline, details } = describeChange(change);
  return h(
    "li",
    { class: "change", "data-type": change.type },
    h(
      "div",
      { class: "change-meta" },
      h("span", { class: "change-type" }, CHANGE_LABELS[change.type] ?? change.type),
      h("time", { datetime: change.detected_date }, `Detected ${formatDate(change.detected_date)}`),
    ),
    h("p", { class: "change-headline" }, headline),
    details.map((line) => h("p", { class: "change-detail" }, line)),
    trial
      ? h(
          "p",
          { class: "change-trial" },
          link(`/t/${trial.nctId}`, null, trial.title ?? trial.nctId),
          h("span", { class: "nct" }, trial.nctId),
        )
      : null,
    collections?.length ? h("p", { class: "change-collections" }, collections.map(collectionTag)) : null,
  );
}

/** A "Show more" pager over a list that can be re-rendered. */
export function showMoreButton(remaining: number, step: number, onClick: () => void): HTMLElement {
  return h(
    "button",
    { type: "button", class: "button", onclick: onClick },
    `Show ${Math.min(step, remaining)} more`,
    h("span", { class: "muted" }, ` (${remaining.toLocaleString("en-US")} left)`),
  );
}

export function errorBox(message: string): HTMLElement {
  return h(
    "div",
    { class: "empty", role: "alert" },
    h("p", { class: "empty-title" }, "This page could not load"),
    h("p", null, message),
    h("p", null, link("/", null, "Back to the home page")),
  );
}

export function loading(what: string): HTMLElement {
  return h("p", { class: "loading", role: "status" }, `Loading ${what}…`);
}
