// Plain-language glossary. Every piece of trial jargon on the site is rendered
// through term(), which makes it tappable for a one-line explanation, and the
// full list is on the /glossary page.

import { h, link } from "./dom";

export interface GlossaryEntry {
  title: string;
  text: string;
  group: "Basics" | "Phases" | "Statuses" | "Design" | "Dates and results" | "About this site";
}

export const GLOSSARY: Record<string, GlossaryEntry> = {
  // --- Basics
  "clinical-trial": {
    title: "Clinical trial",
    group: "Basics",
    text: "A research study in which volunteers are given a treatment so researchers can learn whether it is safe and whether it works.",
  },
  "nct-number": {
    title: "NCT number",
    group: "Basics",
    text: "The ID that ClinicalTrials.gov gives every registered study. You can use it to look the trial up anywhere.",
  },
  sponsor: {
    title: "Sponsor",
    group: "Basics",
    text: "The company, university or agency that is responsible for running the trial.",
  },
  collaborator: {
    title: "Collaborator",
    group: "Basics",
    text: "Another organization that supports the trial, for example with funding or staff.",
  },
  condition: {
    title: "Condition",
    group: "Basics",
    text: "The disease or health problem the trial is studying.",
  },
  intervention: {
    title: "Intervention",
    group: "Basics",
    text: "What is being tested: a drug, a device, a procedure, or a change such as diet or exercise.",
  },
  enrollment: {
    title: "Enrollment",
    group: "Basics",
    text: "How many people take part. “Estimated” (or target) is how many the trial plans to enroll; “actual” is how many it really did.",
  },
  eligibility: {
    title: "Eligibility criteria",
    group: "Basics",
    text: "The rules for who can join a trial. Inclusion criteria are things you must have; exclusion criteria are things that rule you out.",
  },
  "healthy-volunteers": {
    title: "Healthy volunteers",
    group: "Basics",
    text: "People without the condition being studied. Some trials accept them, often in early safety testing.",
  },
  site: {
    title: "Site",
    group: "Basics",
    text: "A hospital, clinic or research center where the trial takes place.",
  },
  "primary-endpoint": {
    title: "Primary endpoint",
    group: "Basics",
    text: "The main result the trial is designed to measure. It decides whether the treatment is judged to have worked. Also called the primary outcome.",
  },

  // --- Phases
  phase: {
    title: "Phase",
    group: "Phases",
    text: "Trials of a new medicine run in stages called phases. Early phases are small and check safety; later phases are large and check how well the treatment works.",
  },
  "phase-early1": {
    title: "Early Phase 1",
    group: "Phases",
    text: "A very small first study, usually with tiny doses, to see how a drug behaves in the body.",
  },
  "phase-1": {
    title: "Phase 1",
    group: "Phases",
    text: "The first tests in a small group of people, mainly to check safety and find the right dose.",
  },
  "phase-1-2": {
    title: "Phase 1/2",
    group: "Phases",
    text: "One trial that combines Phase 1 (safety and dose) with Phase 2 (early signs that it works).",
  },
  "phase-2": {
    title: "Phase 2",
    group: "Phases",
    text: "A medium-sized trial, often a few hundred people, that checks whether the treatment works and keeps watching safety.",
  },
  "phase-2-3": {
    title: "Phase 2/3",
    group: "Phases",
    text: "One trial that combines Phase 2 (does it work?) with Phase 3 (large-scale confirmation).",
  },
  "phase-3": {
    title: "Phase 3",
    group: "Phases",
    text: "A large trial, often hundreds to thousands of people, that compares the treatment with a placebo or the usual treatment. Usually the last step before approval.",
  },
  "phase-4": {
    title: "Phase 4",
    group: "Phases",
    text: "A study done after a treatment is approved, to learn more about how it works in everyday use over time.",
  },
  "phase-na": {
    title: "Phase not applicable",
    group: "Phases",
    text: "Phases apply to drugs. Trials of devices, diets, exercise or other approaches are listed without one.",
  },

  // --- Statuses
  status: {
    title: "Status",
    group: "Statuses",
    text: "Where the trial is in its life: not started, recruiting, running, finished or stopped.",
  },
  "status-not_yet_recruiting": {
    title: "Not yet recruiting",
    group: "Statuses",
    text: "The trial is registered but has not started signing people up.",
  },
  "status-recruiting": {
    title: "Recruiting",
    group: "Statuses",
    text: "The trial is open and looking for volunteers now.",
  },
  "status-enrolling_by_invitation": {
    title: "Enrolling by invitation",
    group: "Statuses",
    text: "Only people the researchers invite can join; it is not open to the public.",
  },
  "status-active_not_recruiting": {
    title: "Active, not recruiting",
    group: "Statuses",
    text: "The trial is under way with the people already enrolled and is no longer taking new volunteers.",
  },
  "status-suspended": {
    title: "Suspended",
    group: "Statuses",
    text: "The trial is paused and may start again.",
  },
  "status-terminated": {
    title: "Terminated",
    group: "Statuses",
    text: "The trial stopped early and will not restart. People who enrolled are no longer being treated or examined.",
  },
  "status-completed": {
    title: "Completed",
    group: "Statuses",
    text: "The trial has finished: the last participant has had their last visit.",
  },
  "status-withdrawn": {
    title: "Withdrawn",
    group: "Statuses",
    text: "The trial was cancelled before anyone enrolled.",
  },
  "status-unknown": {
    title: "Unknown status",
    group: "Statuses",
    text: "The registry record has not been updated for a long time, so nobody can say what state the trial is in.",
  },

  // --- Design
  "study-type": {
    title: "Interventional study",
    group: "Design",
    text: "A study in which participants are assigned a treatment, as opposed to one that only observes people. Every trial on this site is interventional.",
  },
  arm: {
    title: "Arm",
    group: "Design",
    text: "A group of participants who all get the same treatment plan. Many trials compare two or more arms.",
  },
  randomized: {
    title: "Randomized",
    group: "Design",
    text: "Participants are put into groups by chance, like a coin flip. That makes the comparison between groups fair.",
  },
  masking: {
    title: "Masking (blinding)",
    group: "Design",
    text: "Hiding who gets which treatment so that expectations cannot sway the results. In a “double-blind” trial, neither the participants nor the researchers know.",
  },
  "open-label": {
    title: "Open label",
    group: "Design",
    text: "No masking: everyone knows which treatment each participant is getting.",
  },
  placebo: {
    title: "Placebo",
    group: "Design",
    text: "A look-alike treatment with no active ingredient, used as a comparison.",
  },

  // --- Dates and results
  "start-date": {
    title: "Start date",
    group: "Dates and results",
    text: "The date the first participant was (or is expected to be) enrolled.",
  },
  "primary-completion": {
    title: "Primary completion date",
    group: "Dates and results",
    text: "The date the last participant is examined for the trial’s main result. It is often the best hint of when key results could arrive.",
  },
  completion: {
    title: "Completion date",
    group: "Dates and results",
    text: "The date the trial is fully finished, including all follow-up visits.",
  },
  "estimated-actual": {
    title: "Estimated vs. actual",
    group: "Dates and results",
    text: "“Estimated” is the sponsor’s current plan and can change. “Actual” means it has already happened.",
  },
  "results-posted": {
    title: "Results posted",
    group: "Dates and results",
    text: "The sponsor has published summary results for this trial on ClinicalTrials.gov.",
  },
  change: {
    title: "Change",
    group: "Dates and results",
    text: "A difference between a trial’s registry record today and the copy this site saved on an earlier day.",
  },

  // --- About this site
  "ai-summary": {
    title: "AI-written summary",
    group: "About this site",
    text: "A short plain-language description written by an AI model from the trial’s registry record only. It is checked automatically against the record before it is shown, but it can still be wrong, so verify on ClinicalTrials.gov.",
  },
  "golden-set": {
    title: "Golden set",
    group: "About this site",
    text: "A fixed list of test cases written by hand, each with the correct answer, used to check the AI and the eligibility checklist every day.",
  },
  assertion: {
    title: "Automatic check",
    group: "About this site",
    text: "A rule a computer can verify exactly, such as “the phase in the summary matches the registry”. It passes or fails; no judgment is involved.",
  },
  "ai-judge": {
    title: "AI judge",
    group: "About this site",
    text: "A second, stronger AI model that reads each answer next to the registry record and scores it from 1 to 5 for faithfulness (nothing made up) and clarity (easy to understand).",
  },
  refusal: {
    title: "Refusal and grounding",
    group: "About this site",
    text: "Questions the registry record cannot answer, such as costs or side effects. The right behavior is to say the record doesn’t say, not to guess.",
  },
};

export const GLOSSARY_GROUPS: GlossaryEntry["group"][] = [
  "Basics",
  "Phases",
  "Statuses",
  "Design",
  "Dates and results",
  "About this site",
];

/** A tappable piece of jargon. Falls back to plain text for unknown keys. */
/** A glossary word. An inline span acting as a button (not a <button>), so it wraps
 * like the text around it and punctuation after it never starts a new line. */
export function term(key: string, label?: string): HTMLElement {
  const entry = GLOSSARY[key];
  if (!entry) return h("span", null, label ?? key);
  return h(
    "span",
    { class: "term", role: "button", tabindex: "0", "data-term": key, "aria-expanded": "false" },
    label ?? entry.title,
  );
}

// --- the pop-up explanation ---------------------------------------------------

let popup: HTMLElement | null = null;
let openButton: HTMLElement | null = null;

function closePopup(): void {
  if (openButton) openButton.setAttribute("aria-expanded", "false");
  popup?.remove();
  popup = null;
  openButton = null;
}

function openPopup(button: HTMLElement, key: string): void {
  const entry = GLOSSARY[key];
  if (!entry) return;
  closePopup();
  popup = h(
    "div",
    { class: "term-popup", role: "status" },
    h("strong", null, entry.title),
    h("p", null, entry.text),
    link(`/glossary#${key}`, { class: "term-popup-more" }, "Open the glossary"),
  );
  document.body.appendChild(popup);
  openButton = button;
  button.setAttribute("aria-expanded", "true");

  // Place it under the word (its last line, if it wraps), kept inside the viewport.
  const margin = 12;
  const lines = button.getClientRects();
  const rect = lines[lines.length - 1] ?? button.getBoundingClientRect();
  const width = Math.min(320, window.innerWidth - margin * 2);
  popup.style.width = `${width}px`;
  const left = Math.min(Math.max(rect.left, margin), window.innerWidth - width - margin);
  popup.style.left = `${left + window.scrollX}px`;
  const below = rect.bottom + 8;
  const height = popup.offsetHeight;
  const top = below + height > window.innerHeight - margin && rect.top - height - 8 > margin ? rect.top - height - 8 : below;
  popup.style.top = `${top + window.scrollY}px`;
}

function toggle(button: HTMLElement): void {
  if (openButton === button) closePopup();
  else openPopup(button, button.dataset.term ?? "");
}

/** Install the global handlers once. */
export function initGlossaryPopups(): void {
  document.addEventListener("click", (event) => {
    const target = event.target as HTMLElement;
    const button = target.closest<HTMLElement>(".term[data-term]");
    if (button) {
      event.preventDefault();
      toggle(button);
      return;
    }
    if (popup && !popup.contains(target)) closePopup();
    else if (popup && target.closest("a")) closePopup();
  });
  document.addEventListener("keydown", (event) => {
    // Enter and Space work as on a real button.
    const target = event.target as HTMLElement;
    if ((event.key === "Enter" || event.key === " ") && target.matches?.(".term[data-term]")) {
      event.preventDefault();
      toggle(target);
      return;
    }
    if (event.key === "Escape" && popup) {
      const button = openButton;
      closePopup();
      button?.focus();
    }
  });
  window.addEventListener("resize", closePopup);
}

export { closePopup as closeGlossaryPopup };
