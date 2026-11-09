"""The AI layer: plain-language summaries, record-grounded answers, and the judge.

Everything here works from one normalized registry record and nothing else.
Outputs are checked by deterministic assertions (assert_summary / assert_answer)
before they are published or scored; the LLM judge is an extra, optional layer.

Prompt versions carry a hash of the prompt text, so editing a prompt
invalidates cached outputs automatically.
"""

from __future__ import annotations

import hashlib
import json
import re

from .llm import MAX_INPUT_CHARS, LLMClient, LLMError

# --- display values the model must copy exactly --------------------------------

PHASE_LABELS = {
    "EARLY_PHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE1/PHASE2": "Phase 1/2",
    "PHASE2": "Phase 2",
    "PHASE2/PHASE3": "Phase 2/3",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    "NA": "No phase",
}
STATUS_LABELS = {
    "NOT_YET_RECRUITING": "Not yet recruiting",
    "RECRUITING": "Recruiting",
    "ENROLLING_BY_INVITATION": "Enrolling by invitation",
    "ACTIVE_NOT_RECRUITING": "Active, not recruiting",
    "SUSPENDED": "Suspended",
    "TERMINATED": "Terminated",
    "COMPLETED": "Completed",
    "WITHDRAWN": "Withdrawn",
    "UNKNOWN": "Unknown status",
}


def phase_label(phases: list[str] | None) -> str:
    real = [p for p in phases or [] if p != "NA"]
    key = "/".join(real) if real else "NA"
    return PHASE_LABELS.get(key, key)


def status_label(status: str | None) -> str:
    return STATUS_LABELS.get(status or "", status or "Status not given")


def expected_facts(record: dict) -> dict:
    enrollment = record.get("enrollment") or {}
    return {
        "phase": phase_label(record.get("phases")),
        "sponsor": (record.get("lead_sponsor") or {}).get("name"),
        "enrollment": enrollment.get("count"),
        "status": status_label(record.get("overall_status")),
    }


# --- prompts --------------------------------------------------------------------

SUMMARY_SYSTEM = """You write plain-language summaries of clinical trial registry records for a public website. Readers are not experts; many are older family members.

Rules:
- Use only facts in the RECORD. Never add outside knowledge about the drug, the disease or the sponsor.
- The record does not contain trial results. Never describe or guess outcomes, how well a treatment works, or side effects.
- No medical advice. Do not tell the reader what to do, and do not say whether a treatment is right or safe for anyone.
- Write 3 to 5 short sentences at about an 8th-grade reading level. Explain any medical term in everyday words.
- If you mention the phase, the number of participants, the sponsor or the status, copy the value exactly from RECORD.display.
- Mention an NCT number only if it is this trial's own.

Return a JSON object with exactly these keys:
{"summary": "<3 to 5 sentences>",
 "facts": {"phase": "<RECORD.display.phase>", "sponsor": "<RECORD.display.sponsor>", "enrollment": <RECORD.display.enrollment_count, or null>, "status": "<RECORD.display.status>"}}"""

ASK_SYSTEM = """You answer a question about one clinical trial using only its registry RECORD.

Rules:
- If RECORD contains the answer, answer in 1 to 3 plain sentences and set "answerable" to true.
- If RECORD does not contain the answer, set "answerable" to false and say so plainly. You may mention what the record does say that is related, and suggest asking the study team or checking ClinicalTrials.gov. Never fill a gap with outside knowledge or a guess.
- The record never includes trial results, costs or payments, contact details, side effects, or regulatory approvals.
- Never give medical advice or say whether a treatment is safe or suitable for a particular person.

Return a JSON object: {"answer": "<1 to 3 sentences>", "answerable": true or false}"""

JUDGE_SYSTEM = """You grade an AI-written response about a clinical trial against the trial's registry RECORD.

Score two things from 1 to 5:
- faithfulness: 5 = every statement is supported by RECORD and nothing is invented; when RECORD lacks the answer, the response says so instead of guessing. 1 = contains invented or wrong facts, or gives medical advice.
- clarity: 5 = a non-expert, such as an older family member, would understand it easily, with jargon explained. 1 = confusing or full of jargon.

Return a JSON object: {"faithfulness": <1-5>, "clarity": <1-5>, "rationale": "<one sentence>"}"""


def _version(name: str, text: str) -> str:
    return f"{name}-{hashlib.sha256(text.encode()).hexdigest()[:6]}"


SUMMARY_PROMPT_VERSION = _version("summary-v1", SUMMARY_SYSTEM)
ASK_PROMPT_VERSION = _version("ask-v1", ASK_SYSTEM)
JUDGE_PROMPT_VERSION = _version("judge-v1", JUDGE_SYSTEM)


def _date_text(value: dict | None) -> str | None:
    if not value or not value.get("date"):
        return None
    kind = {"ACTUAL": "actual", "ESTIMATED": "estimated"}.get(value.get("type") or "")
    return f"{value['date']} ({kind})" if kind else value["date"]


def _clip(text: str | None, limit: int) -> str | None:
    if not text:
        return text
    return text if len(text) <= limit else text[:limit].rstrip() + " ...(truncated)"


def prompt_record(record: dict, text_limit: int = 3000) -> dict:
    """The part of a normalized record the model sees. Sites are left out (shown
    deterministically on the page); long free text is clipped to fit the request limit."""
    facts = expected_facts(record)
    enrollment = record.get("enrollment") or {}
    eligibility = record.get("eligibility") or {}
    return {
        "nct_id": record.get("nct_id"),
        "display": {
            "phase": facts["phase"],
            "status": facts["status"],
            "sponsor": facts["sponsor"],
            "enrollment_count": facts["enrollment"],
            "enrollment_type": {"ACTUAL": "actual", "ESTIMATED": "target"}.get(enrollment.get("type") or ""),
        },
        "title": record.get("brief_title"),
        "official_title": record.get("official_title"),
        "why_stopped": record.get("why_stopped"),
        "conditions": record.get("conditions") or [],
        "interventions": [
            f"{i.get('name')} ({(i.get('type') or '').lower()})" for i in record.get("interventions") or []
        ],
        "groups": [
            f"{g.get('label')} ({(g.get('type') or '').lower().replace('_', ' ')})"
            for g in record.get("arm_groups") or []
        ],
        "primary_outcomes": [
            f"{o.get('measure')}" + (f" (measured: {o.get('time_frame')})" if o.get("time_frame") else "")
            for o in record.get("primary_outcomes") or []
        ],
        "dates": {
            "start": _date_text(record.get("start_date")),
            "primary_completion": _date_text(record.get("primary_completion_date")),
            "completion": _date_text(record.get("completion_date")),
        },
        "results_posted_on_clinicaltrials_gov": bool(record.get("has_results")),
        "eligibility": {
            "sex": eligibility.get("sex"),
            "minimum_age": eligibility.get("minimum_age"),
            "maximum_age": eligibility.get("maximum_age"),
            "healthy_volunteers": eligibility.get("healthy_volunteers"),
            "criteria": _clip(eligibility.get("criteria"), text_limit),
        },
        "sponsor_summary": _clip(record.get("brief_summary"), text_limit),
    }


def record_block(record: dict, budget: int) -> str:
    """RECORD as JSON, shrinking the free text until it fits `budget` characters."""
    for limit in (3000, 2000, 1200, 600, 300):
        text = json.dumps(prompt_record(record, limit), ensure_ascii=False, indent=1)
        if len(text) <= budget:
            return text
    return text[:budget]


def input_hash(record: dict) -> str:
    blob = json.dumps(prompt_record(record), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# --- calls ----------------------------------------------------------------------


def summarize(client: LLMClient, record: dict, feedback: list[str] | None = None) -> dict:
    user = "RECORD:\n" + record_block(record, MAX_INPUT_CHARS - len(SUMMARY_SYSTEM) - 600)
    if feedback:
        user += "\n\nYour previous answer had these problems. Fix them:\n- " + "\n- ".join(feedback)
    return client.chat_json(client.config.model, SUMMARY_SYSTEM, user, max_tokens=700)


def answer(client: LLMClient, record: dict, question: str) -> dict:
    user = (
        "RECORD:\n"
        + record_block(record, MAX_INPUT_CHARS - len(ASK_SYSTEM) - 800 - len(question))
        + f"\n\nQUESTION: {question}"
    )
    return client.chat_json(client.config.model, ASK_SYSTEM, user, max_tokens=400)


def judge(client: LLMClient, record: dict, task: str, response_text: str) -> dict:
    user = (
        "RECORD:\n"
        + record_block(record, MAX_INPUT_CHARS - len(JUDGE_SYSTEM) - 1500 - len(response_text))
        + f"\n\nTASK GIVEN TO THE AI: {task}\n\nRESPONSE TO GRADE:\n{response_text}"
    )
    raw = client.chat_json(client.config.judge_model, JUDGE_SYSTEM, user, max_tokens=300)
    scores = {}
    for key in ("faithfulness", "clarity"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
            raise LLMError(f"judge returned an invalid {key} score: {value!r}")
        scores[key] = value
    rationale = raw.get("rationale")
    scores["rationale"] = rationale.strip()[:300] if isinstance(rationale, str) else ""
    return scores


# --- deterministic assertions ------------------------------------------------------

NCT_TOKEN = re.compile(r"\bNCT\w*", re.IGNORECASE)
NCT_VALID = re.compile(r"^NCT\d{8}$")
_PHASE_MENTION = re.compile(
    r"\bphase\s+((?:[1-4]|I{1,3}|IV)(?:\s*(?:/|and|-|or)\s*(?:[1-4]|I{1,3}|IV))?)\b", re.IGNORECASE
)
_ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4"}
_COUNT = re.compile(
    r"\b(\d{1,3}(?:,\d{3})+|\d+)\s+(?:([a-z-]+)\s+)?"
    r"(people|participants|adults|patients|volunteers|subjects|individuals|children|men|women)\b",
    re.IGNORECASE,
)
_NOT_A_COUNT_WORD = {"years", "year", "months", "weeks", "days", "kg", "mg", "percent", "to", "or", "and", "%"}


def _phase_numbers(label: str) -> set[str]:
    return set(re.findall(r"[1-4]", label)) if label != "No phase" else set()


def check_citations(text: str, known_ids: set[str] | None) -> list[str]:
    problems = []
    for token in NCT_TOKEN.findall(text):
        nct = token.upper()
        if not NCT_VALID.match(nct):
            problems.append(f"malformed NCT number {token!r}")
        elif known_ids is not None and nct not in known_ids:
            problems.append(f"cites {nct}, which is not in our data")
    return problems


def assert_summary(record: dict, output: dict, known_ids: set[str] | None = None) -> list[str]:
    """Every problem with a summary; an empty list means it passes."""
    problems: list[str] = []
    if not isinstance(output, dict):
        return ["output is not a JSON object"]
    summary = output.get("summary")
    facts = output.get("facts")
    if not isinstance(summary, str) or not summary.strip():
        return ["summary is missing"]
    if not 60 <= len(summary) <= 1200:
        problems.append(f"summary is {len(summary)} characters (expected 60-1200)")
    if not isinstance(facts, dict):
        return problems + ["facts are missing"]

    expected = expected_facts(record)
    for key in ("phase", "sponsor", "status"):
        if facts.get(key) != expected[key]:
            problems.append(f"stated {key} {facts.get(key)!r} does not match the record ({expected[key]!r})")
    stated_enrollment = facts.get("enrollment")
    if isinstance(stated_enrollment, str) and stated_enrollment.replace(",", "").isdigit():
        stated_enrollment = int(stated_enrollment.replace(",", ""))
    if stated_enrollment != expected["enrollment"]:
        problems.append(
            f"stated enrollment {facts.get('enrollment')!r} does not match the record ({expected['enrollment']!r})"
        )

    # The prose must agree with the record too, not just the declared facts.
    allowed = _phase_numbers(expected["phase"])
    for mention in _PHASE_MENTION.findall(summary):
        numbers = {_ROMAN.get(part.upper(), part) for part in re.findall(r"[1-4]|IV|I{1,3}", mention, re.IGNORECASE)}
        if numbers != allowed:  # "Phase 3" for a Phase 2/3 trial is wrong, not partly right
            problems.append(f"summary says 'phase {mention}' but the record's phase is {expected['phase']!r}")
    for number, word, _noun in _COUNT.findall(summary):
        if word and word.lower() in _NOT_A_COUNT_WORD:
            continue
        count = int(number.replace(",", ""))
        if count != expected["enrollment"]:
            problems.append(
                f"summary mentions {number} people but the record's enrollment is {expected['enrollment']!r}"
            )
    problems += check_citations(summary, known_ids)
    if record.get("nct_id"):
        for token in NCT_TOKEN.findall(summary):
            if token.upper() != record["nct_id"] and NCT_VALID.match(token.upper()):
                problems.append(f"summary cites another trial ({token.upper()})")
    return problems


def assert_answer(
    output: dict,
    *,
    expect_answerable: bool | None,
    blocklist: list[str] | None = None,
    known_ids: set[str] | None = None,
) -> list[str]:
    """Problems with a grounded answer. For refusal cases, `blocklist` holds patterns
    for specifics the record does not contain: any match means something was invented."""
    if not isinstance(output, dict):
        return ["output is not a JSON object"]
    text = output.get("answer")
    if not isinstance(text, str) or not text.strip():
        return ["answer is missing"]
    problems = []
    if len(text) > 900:
        problems.append(f"answer is {len(text)} characters (expected at most 900)")
    if expect_answerable is not None and output.get("answerable") is not expect_answerable:
        problems.append(f"answerable is {output.get('answerable')!r}; expected {expect_answerable}")
    for pattern in blocklist or []:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            problems.append(f"states a specific the record does not contain: {match.group(0)!r}")
    problems += check_citations(text, known_ids)
    return problems
