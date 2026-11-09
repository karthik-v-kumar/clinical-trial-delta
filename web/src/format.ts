// Turning registry codes into plain words, plus date and number formatting.

import type { ChangeCore, DateValue, Enrollment } from "./types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatInt(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toLocaleString("en-US");
}

/** "2018-06-04" -> "Jun 4, 2018"; "2027-06" -> "Jun 2027". No timezone math. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const [year, month, day] = value.split("-");
  const name = MONTHS[Number(month) - 1];
  if (!year || !name) return value;
  return day ? `${name} ${Number(day)}, ${year}` : `${name} ${year}`;
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${formatInt(count)} ${count === 1 ? one : many}`;
}

/** SOME_CODE -> "Some code", for codes we have no nicer wording for. */
export function sentenceCase(code: string | null | undefined): string {
  if (!code) return "—";
  const words = code.replace(/_/g, " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// --- status ------------------------------------------------------------------

export const STATUS_ORDER = [
  "NOT_YET_RECRUITING",
  "RECRUITING",
  "ENROLLING_BY_INVITATION",
  "ACTIVE_NOT_RECRUITING",
  "SUSPENDED",
  "TERMINATED",
  "COMPLETED",
  "WITHDRAWN",
  "UNKNOWN",
];

const STATUS_LABELS: Record<string, string> = {
  NOT_YET_RECRUITING: "Not yet recruiting",
  RECRUITING: "Recruiting",
  ENROLLING_BY_INVITATION: "Enrolling by invitation",
  ACTIVE_NOT_RECRUITING: "Active, not recruiting",
  SUSPENDED: "Suspended",
  TERMINATED: "Terminated",
  COMPLETED: "Completed",
  WITHDRAWN: "Withdrawn",
  UNKNOWN: "Unknown status",
};

export function statusLabel(status: string | null | undefined): string {
  if (!status) return "Status not given";
  return STATUS_LABELS[status] ?? sentenceCase(status);
}

export function statusTermKey(status: string | null | undefined): string {
  return status && STATUS_LABELS[status] ? `status-${status.toLowerCase()}` : "status";
}

// --- phase -------------------------------------------------------------------

export const PHASE_ORDER = [
  "EARLY_PHASE1",
  "PHASE1",
  "PHASE1/PHASE2",
  "PHASE2",
  "PHASE2/PHASE3",
  "PHASE3",
  "PHASE4",
  "NA",
];

const PHASE_INFO: Record<string, { label: string; short: string; term: string }> = {
  EARLY_PHASE1: { label: "Early Phase 1", short: "Early 1", term: "phase-early1" },
  PHASE1: { label: "Phase 1", short: "1", term: "phase-1" },
  "PHASE1/PHASE2": { label: "Phase 1/2", short: "1/2", term: "phase-1-2" },
  PHASE2: { label: "Phase 2", short: "2", term: "phase-2" },
  "PHASE2/PHASE3": { label: "Phase 2/3", short: "2/3", term: "phase-2-3" },
  PHASE3: { label: "Phase 3", short: "3", term: "phase-3" },
  PHASE4: { label: "Phase 4", short: "4", term: "phase-4" },
  NA: { label: "No phase", short: "None", term: "phase-na" },
};

/** The bucket key the pipeline uses: ["PHASE2","PHASE3"] -> "PHASE2/PHASE3". */
export function phaseBucket(phases: string[] | null | undefined): string {
  const real = (phases ?? []).filter((p) => p !== "NA");
  return real.length ? real.join("/") : "NA";
}

export function phaseLabel(bucket: string): string {
  return PHASE_INFO[bucket]?.label ?? sentenceCase(bucket);
}

export function phaseShort(bucket: string): string {
  return PHASE_INFO[bucket]?.short ?? sentenceCase(bucket);
}

export function phaseTermKey(bucket: string): string {
  return PHASE_INFO[bucket]?.term ?? "phase";
}

export function phaseRank(bucket: string): number {
  const index = PHASE_ORDER.indexOf(bucket);
  return index === -1 ? PHASE_ORDER.length : index;
}

// --- design ------------------------------------------------------------------

const MASKING: Record<string, string> = {
  NONE: "Open label (no masking)",
  SINGLE: "Single-blind",
  DOUBLE: "Double-blind",
  TRIPLE: "Triple-blind",
  QUADRUPLE: "Quadruple-blind",
};
const WHO_MASKED: Record<string, string> = {
  PARTICIPANT: "participants",
  CARE_PROVIDER: "care providers",
  INVESTIGATOR: "investigators",
  OUTCOMES_ASSESSOR: "the people assessing results",
};
const ALLOCATION: Record<string, string> = {
  RANDOMIZED: "Randomized",
  NON_RANDOMIZED: "Not randomized",
  NA: "Single group (no assignment)",
};
const MODEL: Record<string, string> = {
  SINGLE_GROUP: "Single group",
  PARALLEL: "Parallel groups",
  CROSSOVER: "Crossover (each person gets every treatment in turn)",
  FACTORIAL: "Factorial (treatments tested in combination)",
  SEQUENTIAL: "Sequential groups",
};
const INTERVENTION_TYPE: Record<string, string> = {
  DRUG: "Drug",
  DEVICE: "Device",
  BIOLOGICAL: "Biological",
  PROCEDURE: "Procedure",
  RADIATION: "Radiation",
  BEHAVIORAL: "Behavioral",
  GENETIC: "Genetic",
  DIETARY_SUPPLEMENT: "Dietary supplement",
  COMBINATION_PRODUCT: "Combination product",
  DIAGNOSTIC_TEST: "Diagnostic test",
  OTHER: "Other",
};
const ARM_TYPE: Record<string, string> = {
  EXPERIMENTAL: "Experimental",
  ACTIVE_COMPARATOR: "Active comparator",
  PLACEBO_COMPARATOR: "Placebo comparator",
  SHAM_COMPARATOR: "Sham comparator",
  NO_INTERVENTION: "No intervention",
  OTHER: "Other",
};
const SPONSOR_CLASS: Record<string, string> = {
  INDUSTRY: "Industry",
  NIH: "U.S. National Institutes of Health",
  FED: "U.S. federal agency",
  OTHER_GOV: "Government",
  INDIV: "Individual",
  NETWORK: "Research network",
  OTHER: "University, hospital or other",
  UNKNOWN: "Unknown",
};
const SEX: Record<string, string> = { ALL: "All sexes", FEMALE: "Female only", MALE: "Male only" };

const lookup = (map: Record<string, string>) => (code: string | null | undefined) =>
  code ? (map[code] ?? sentenceCase(code)) : "—";

export const maskingLabel = lookup(MASKING);
export const allocationLabel = lookup(ALLOCATION);
export const modelLabel = lookup(MODEL);
export const interventionTypeLabel = lookup(INTERVENTION_TYPE);
export const armTypeLabel = lookup(ARM_TYPE);
export const sponsorClassLabel = lookup(SPONSOR_CLASS);
export const sexLabel = lookup(SEX);

export function whoMaskedLabel(who: string[]): string {
  return who.map((w) => WHO_MASKED[w] ?? sentenceCase(w).toLowerCase()).join(", ");
}

export function estimatedOrActual(type: string | null | undefined): string {
  if (type === "ACTUAL") return "actual";
  if (type === "ESTIMATED") return "estimated";
  return "";
}

export function formatDateValue(value: DateValue | null | undefined): string {
  if (!value?.date) return "—";
  const kind = estimatedOrActual(value.type);
  return kind ? `${formatDate(value.date)} (${kind})` : formatDate(value.date);
}

export function formatEnrollment(value: Enrollment | null | undefined): string {
  if (!value) return "—";
  const kind = value.type === "ACTUAL" ? "actual" : value.type === "ESTIMATED" ? "target" : "";
  return `${plural(value.count, "person", "people")}${kind ? ` (${kind})` : ""}`;
}

// --- changes -----------------------------------------------------------------

export const CHANGE_LABELS: Record<string, string> = {
  new_trial: "New trial",
  status_change: "Status",
  enrollment_change: "Enrollment",
  endpoint_change: "Endpoint",
  site_added: "Sites added",
  site_removed: "Sites removed",
  date_change: "Date moved",
  results_posted: "Results",
};

interface SiteRef {
  facility?: string | null;
  city?: string | null;
  state?: string | null;
  country?: string | null;
}

function placeName(site: SiteRef): string {
  return [site.city, site.state ?? site.country].filter(Boolean).join(", ") || site.facility || "unnamed site";
}

function listPlaces(sites: SiteRef[]): string {
  const names = [...new Set(sites.map(placeName))];
  const shown = names.slice(0, 3).join("; ");
  return names.length > 3 ? `${shown}; and ${names.length - 3} more` : shown;
}

function monthsBetween(from: string, to: string): number | null {
  const [fy, fm] = from.split("-").map(Number);
  const [ty, tm] = to.split("-").map(Number);
  if (!fy || !fm || !ty || !tm) return null;
  return (ty - fy) * 12 + (tm - fm);
}

/** A change in one plain sentence, plus optional detail lines. */
export function describeChange(change: ChangeCore): { headline: string; details: string[] } {
  const oldValue = change.old_value as never;
  const newValue = change.new_value as never;
  const context = change.context ?? {};
  switch (change.type) {
    case "new_trial": {
      const info = (newValue ?? {}) as { overall_status?: string; first_posted?: string };
      const details = [];
      if (info.first_posted) details.push(`First posted on the registry ${formatDate(info.first_posted)}.`);
      if (info.overall_status) details.push(`Status: ${statusLabel(info.overall_status)}.`);
      return { headline: "Started appearing in this collection", details };
    }
    case "status_change": {
      const details = context["why_stopped"] ? [`Reason given: ${String(context["why_stopped"])}`] : [];
      return { headline: `${statusLabel(oldValue)} → ${statusLabel(newValue)}`, details };
    }
    case "enrollment_change":
      return {
        headline: `${formatEnrollment(oldValue)} → ${formatEnrollment(newValue)}`,
        details: [],
      };
    case "endpoint_change": {
      const before = (oldValue ?? []) as string[];
      const after = (newValue ?? []) as string[];
      return {
        headline: "Primary endpoint wording changed",
        details: [`Before: ${before.join("; ") || "none listed"}`, `Now: ${after.join("; ") || "none listed"}`],
      };
    }
    case "site_added": {
      const sites = (newValue ?? []) as SiteRef[];
      return { headline: `${plural(sites.length, "site")} added`, details: [listPlaces(sites)] };
    }
    case "site_removed": {
      const sites = (oldValue ?? []) as SiteRef[];
      return { headline: `${plural(sites.length, "site")} removed`, details: [listPlaces(sites)] };
    }
    case "date_change": {
      const before = oldValue as DateValue | null;
      const after = newValue as DateValue | null;
      const which = change.field === "primary_completion_date" ? "Primary completion" : "Completion";
      const details: string[] = [];
      if (before?.date && after?.date) {
        const months = monthsBetween(before.date, after.date);
        if (months) details.push(`${plural(Math.abs(months), "month")} ${months > 0 ? "later" : "earlier"} than before.`);
      }
      return {
        headline: `${which}: ${formatDateValue(before)} → ${formatDateValue(after)}`,
        details,
      };
    }
    case "results_posted": {
      const posted = context["results_first_posted"];
      return {
        headline: "Results posted on ClinicalTrials.gov",
        details: posted ? [`Posted ${formatDate(String(posted))}.`] : [],
      };
    }
  }
}

// --- "data as of" ------------------------------------------------------------

/** Today's calendar date in Los Angeles (the pipeline's clock), YYYY-MM-DD. */
export function todayInLosAngeles(now = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Los_Angeles",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function daysBetween(fromDate: string, toDate: string): number {
  const from = Date.UTC(Number(fromDate.slice(0, 4)), Number(fromDate.slice(5, 7)) - 1, Number(fromDate.slice(8, 10)));
  const to = Date.UTC(Number(toDate.slice(0, 4)), Number(toDate.slice(5, 7)) - 1, Number(toDate.slice(8, 10)));
  return Math.round((to - from) / 86_400_000);
}

export function shiftDate(date: string, days: number): string {
  const base = new Date(Date.UTC(Number(date.slice(0, 4)), Number(date.slice(5, 7)) - 1, Number(date.slice(8, 10))));
  base.setUTCDate(base.getUTCDate() + days);
  return base.toISOString().slice(0, 10);
}
