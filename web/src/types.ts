// Shapes of the committed data files. These mirror the writer allowlists in
// pipeline/writers.py: a key only exists here if a writer publishes it.

export interface DateValue {
  date: string; // YYYY-MM-DD or YYYY-MM
  type: string | null; // ACTUAL | ESTIMATED
}

export interface Enrollment {
  count: number;
  type: string | null; // ACTUAL | ESTIMATED
}

export interface Site {
  facility: string | null;
  city: string | null;
  state: string | null;
  country: string | null;
  status: string | null;
  lat: number | null;
  lon: number | null;
}

export interface ChecklistItem {
  text: string;
  group?: string;
  sub_items?: string[];
}

export interface Checklist {
  parsed: boolean;
  inclusion: ChecklistItem[];
  exclusion: ChecklistItem[];
  notes: string[];
}

export type EventType =
  | "new_trial"
  | "status_change"
  | "enrollment_change"
  | "endpoint_change"
  | "site_added"
  | "site_removed"
  | "date_change"
  | "results_posted";

export interface ChangeCore {
  change_id: string;
  type: EventType;
  field: string;
  old_value: unknown;
  new_value: unknown;
  detected_date: string;
  context?: Record<string, unknown>;
}

/** One row of data/changes.json. */
export interface ChangeEvent extends ChangeCore {
  id: string;
  nct_id: string;
  collection: string;
  brief_title: string | null;
}

/** One entry of a trial file's history (collections merged). */
export interface HistoryEntry extends ChangeCore {
  collections: string[];
}

export interface ChangesFile {
  schema_version: number;
  event_types: EventType[];
  event_count: number;
  events: ChangeEvent[];
}

export interface Trial {
  schema_version: number;
  nct_id: string;
  brief_title: string | null;
  official_title: string | null;
  acronym: string | null;
  overall_status: string | null;
  last_known_status: string | null;
  why_stopped: string | null;
  study_type: string | null;
  phases: string[];
  design: {
    allocation: string | null;
    intervention_model: string | null;
    primary_purpose: string | null;
    masking: string | null;
    who_masked: string[];
  };
  enrollment: Enrollment | null;
  start_date: DateValue | null;
  primary_completion_date: DateValue | null;
  completion_date: DateValue | null;
  first_posted: string | null;
  last_update_posted: string | null;
  results_first_posted: string | null;
  has_results: boolean;
  lead_sponsor: { name: string | null; class: string | null } | null;
  collaborators: string[];
  conditions: string[];
  interventions: { type: string | null; name: string | null; other_names: string[] }[];
  arm_groups: { label: string | null; type: string | null; intervention_names: string[] }[];
  primary_outcomes: { measure: string | null; time_frame: string | null }[];
  brief_summary: string | null;
  eligibility: {
    criteria: string | null;
    sex: string | null;
    minimum_age: string | null;
    maximum_age: string | null;
    healthy_volunteers: boolean | null;
    std_ages: string[];
  };
  sites: Site[];
  registry_url: string;
  content_hash: string;
  collections: string[];
  matched_queries: string[];
  sponsors_of_note: string[];
  eligibility_checklist?: Checklist;
  in_watchlist: boolean;
  left_watchlist_date?: string;
  history: HistoryEntry[];
}

export interface TrialRow {
  nct_id: string;
  brief_title: string | null;
  acronym: string | null;
  phases: string[];
  overall_status: string | null;
  enrollment_count: number | null;
  enrollment_type: string | null;
  start_date: string | null;
  primary_completion_date: string | null;
  lead_sponsor: string | null;
  sponsors_of_note: string[];
  has_results: boolean;
  site_count: number;
  recruiting_site_count: number;
  countries: string[];
  matched_queries: string[];
}

export interface CollectionQuery {
  id: string;
  label: string;
  registry_count: number | null;
  matched: number;
  failed: boolean;
  web_url: string;
}

export interface Collection {
  schema_version: number;
  id: string;
  name: string;
  why_it_matters: string;
  accent: string;
  sponsors_of_note: string[];
  partial: boolean;
  queries: CollectionQuery[];
  trial_count: number;
  recruiting_count: number;
  not_yet_recruiting_count: number;
  by_phase: { phase: string; count: number }[];
  by_status: { status: string; count: number }[];
  trials: TrialRow[];
}

/** A city with at least one recruiting site, located by the registry's own coordinates. */
export interface Place {
  city: string;
  state: string | null;
  country: string | null;
  lat: number;
  lon: number;
}

export interface SitesIndex {
  schema_version: number;
  collection: string;
  snapshot_date: string;
  cities: Place[];
  trials: { nct_id: string; cities: number[] }[];
  recruiting_site_count: number;
  sites_without_coordinates: number;
}

/** What a trial tests, grouped the way a patient would ask (pipeline INTERVENTION_KINDS). */
export type InterventionKind = "medicine" | "device" | "lifestyle" | "procedure" | "other";

/** One trial in data/finder.json: recruiting now, or listed as not yet recruiting. */
export interface FinderTrial {
  nct_id: string;
  title: string | null;
  status: "RECRUITING" | "NOT_YET_RECRUITING";
  collections: string[];
  phases: string[];
  conditions: string[];
  tested: string[];
  kinds: InterventionKind[];
  placebo: boolean;
  sex: string | null;
  min_age: number | null; // years
  max_age: number | null; // years
  sponsor: string | null;
  first_posted: string | null;
  start_date?: string; // planned start of a trial that is not yet recruiting
  cities: number[]; // where it is recruiting now (indexes into FinderIndex.cities)
  site_count: number;
  soon: number[]; // where a site is listed but has not opened
  planned_site_count: number;
  opened?: string; // date the registry status changed to recruiting, if recent
  new_sites?: { date: string; cities: number[] }[]; // sites added recently
}

export interface FinderIndex {
  schema_version: number;
  snapshot_date: string;
  news_window_days: number;
  collections: { id: string; name: string }[];
  cities: Place[];
  trials: FinderTrial[];
  recruiting_trials: number;
  trials_without_location: number;
  not_yet_recruiting_trials: number;
  not_yet_without_location: number;
}

export interface CollectionSummary {
  id: string;
  name: string;
  why_it_matters: string;
  accent: string;
  partial: boolean;
  trial_count: number;
  recruiting_count: number;
  changes_recent: number;
}

export interface Meta {
  schema_version: number;
  app: string;
  snapshot_date: string;
  tracking_since?: string;
  generated_at: string;
  run: {
    mode: string;
    started_at?: string;
    finished_at?: string;
    compared_against?: string | null;
    baseline?: boolean;
    partial?: boolean;
    failed_queries?: Record<string, string>;
    events_detected?: number;
    records_changed?: number;
    warnings?: string[];
  };
  rebuild?: { mode: string; started_at: string; finished_at: string };
  source: { name: string; url: string; api_version?: string; data_timestamp?: string };
  totals: { trials: number; events_all_time: number; changes_all_time: number };
  recent_window_days: number;
  collections: CollectionSummary[];
  queries: (CollectionQuery & { collection: string })[];
}

// --- AI summaries and evals (pipeline/summarize.py, pipeline/evals.py) ---------

export interface AISummary {
  schema_version: number;
  nct_id: string;
  content_hash: string;
  summary: string;
  facts: { phase: string; sponsor: string | null; enrollment: number | null; status: string };
  provider: string;
  model: string;
  prompt_version: string;
  generated_at: string;
  checks: string;
}

export interface SummaryIndex {
  updated_at: string;
  status: string;
  provider: string | null;
  model: string | null;
  trials: number;
  up_to_date: number;
  to_do: number;
  written: number;
  rejected: number;
  stopped_because: string | null;
}

export interface JudgeSummary {
  status: "scored" | "not_run" | "not_applicable";
  scored: number;
  faithfulness_mean: number | null;
  clarity_mean: number | null;
}

export interface EvalTypeRow {
  type: "summary" | "eligibility" | "refusal";
  label: string;
  cases: number;
  passed: number;
  failed: number;
  no_output: number;
  pass_rate: number | null;
  judge: JudgeSummary;
}

export interface EvalCase {
  id: string;
  type: EvalTypeRow["type"];
  title: string;
  nct_id: string | null;
  input: Record<string, unknown>;
  expected: Record<string, unknown>;
  output: Record<string, unknown> | null;
  output_meta: { model?: string | null; prompt_version?: string; generated_at?: string; cached?: boolean; note?: string };
  status: "pass" | "fail" | "no_output";
  problems: string[];
  judge: {
    status: string;
    faithfulness?: number;
    clarity?: number;
    rationale?: string;
    model?: string;
    error?: string;
  } | null;
}

export interface EvalRun {
  run_at: string;
  date: string;
  provider: string | null;
  model: string | null;
  judge_model: string | null;
  prompt_versions: Record<string, string>;
  judge_prompt_version: string;
  judge_status: "scored" | "not_run";
  judge_note: string | null;
  golden_cases: number;
  passed: number;
  failed: number;
  no_output: number;
}

export interface EvalReport {
  schema_version: number;
  run: EvalRun;
  by_type: EvalTypeRow[];
  production: {
    published: number;
    checked: number;
    passed: number;
    failed: number;
    stale: number;
    rejected: number;
    failures: { nct_id: string; problems: string[]; summary: string | null; rejected?: boolean }[];
  };
  cases: EvalCase[];
}

export interface EvalHistory {
  runs: (EvalRun & {
    by_type: { type: string; cases: number; passed: number; pass_rate: number | null; faithfulness_mean: number | null; clarity_mean: number | null }[];
  })[];
}
