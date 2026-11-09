"""Raw ClinicalTrials.gov API v2 study -> normalized trial record.

The normalized record is what gets snapshotted and diffed, so it must be
deterministic: same registry content in, byte-identical record out.
"""

from __future__ import annotations

import hashlib
import json

SCHEMA_VERSION = 1

# Fields excluded from the content hash because they change without the trial
# itself changing in any way we track.
HASH_EXCLUDED_FIELDS = ("last_update_posted",)


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _date(struct: dict | None) -> dict | None:
    if not struct or not struct.get("date"):
        return None
    return {"date": struct["date"], "type": struct.get("type")}


def _str_list(values) -> list[str]:
    return [v.strip() for v in (values or []) if isinstance(v, str) and v.strip()]


def _site(loc: dict) -> dict:
    geo = loc.get("geoPoint") or {}
    return {
        "facility": _text(loc.get("facility")),
        "city": _text(loc.get("city")),
        "state": _text(loc.get("state")),
        "country": _text(loc.get("country")),
        "status": loc.get("status"),
        "lat": geo.get("lat"),
        "lon": geo.get("lon"),
    }


def _site_sort_key(site: dict) -> tuple:
    return tuple((site.get(k) or "").casefold() for k in ("country", "state", "city", "facility")) + (
        site.get("status") or "",
    )


def normalize_study(raw: dict) -> dict:
    ps = raw.get("protocolSection") or {}
    ident = ps.get("identificationModule") or {}
    status = ps.get("statusModule") or {}
    sponsor = ps.get("sponsorCollaboratorsModule") or {}
    design = ps.get("designModule") or {}
    design_info = design.get("designInfo") or {}
    masking = design_info.get("maskingInfo") or {}
    enrollment = design.get("enrollmentInfo") or {}
    arms = ps.get("armsInterventionsModule") or {}
    eligibility = ps.get("eligibilityModule") or {}
    locations = (ps.get("contactsLocationsModule") or {}).get("locations") or []
    lead = sponsor.get("leadSponsor") or {}

    nct_id = ident.get("nctId")
    if not nct_id:
        raise ValueError("study record has no NCT number")

    return {
        "nct_id": nct_id,
        "brief_title": _text(ident.get("briefTitle")),
        "official_title": _text(ident.get("officialTitle")),
        "acronym": _text(ident.get("acronym")),
        "overall_status": status.get("overallStatus"),
        "last_known_status": status.get("lastKnownStatus"),
        "why_stopped": _text(status.get("whyStopped")),
        "study_type": design.get("studyType"),
        "phases": list(design.get("phases") or []),
        "design": {
            "allocation": design_info.get("allocation"),
            "intervention_model": design_info.get("interventionModel"),
            "primary_purpose": design_info.get("primaryPurpose"),
            "masking": masking.get("masking"),
            "who_masked": list(masking.get("whoMasked") or []),
        },
        "enrollment": (
            {"count": enrollment.get("count"), "type": enrollment.get("type")}
            if enrollment.get("count") is not None
            else None
        ),
        "start_date": _date(status.get("startDateStruct")),
        "primary_completion_date": _date(status.get("primaryCompletionDateStruct")),
        "completion_date": _date(status.get("completionDateStruct")),
        "first_posted": (status.get("studyFirstPostDateStruct") or {}).get("date"),
        "last_update_posted": (status.get("lastUpdatePostDateStruct") or {}).get("date"),
        "results_first_posted": (status.get("resultsFirstPostDateStruct") or {}).get("date"),
        "has_results": bool(raw.get("hasResults", False)),
        "lead_sponsor": {"name": _text(lead.get("name")), "class": lead.get("class")} if lead else None,
        "collaborators": [_text(c.get("name")) for c in sponsor.get("collaborators") or [] if _text(c.get("name"))],
        "conditions": _str_list((ps.get("conditionsModule") or {}).get("conditions")),
        "interventions": [
            {
                "type": iv.get("type"),
                "name": _text(iv.get("name")),
                "other_names": _str_list(iv.get("otherNames")),
            }
            for iv in arms.get("interventions") or []
        ],
        "arm_groups": [
            {
                "label": _text(ag.get("label")),
                "type": ag.get("type"),
                "intervention_names": _str_list(ag.get("interventionNames")),
            }
            for ag in arms.get("armGroups") or []
        ],
        "primary_outcomes": [
            {"measure": _text(o.get("measure")), "time_frame": _text(o.get("timeFrame"))}
            for o in (ps.get("outcomesModule") or {}).get("primaryOutcomes") or []
        ],
        "brief_summary": _text((ps.get("descriptionModule") or {}).get("briefSummary")),
        "eligibility": {
            "criteria": _text(eligibility.get("eligibilityCriteria")),
            "sex": eligibility.get("sex"),
            "minimum_age": eligibility.get("minimumAge"),
            "maximum_age": eligibility.get("maximumAge"),
            "healthy_volunteers": eligibility.get("healthyVolunteers"),
            "std_ages": list(eligibility.get("stdAges") or []),
        },
        "sites": sorted((_site(loc) for loc in locations), key=_site_sort_key),
    }


def content_hash(record: dict) -> str:
    """Stable fingerprint of a normalized record, for "did this trial change?".

    Later milestones key cached AI summaries on this, so an LLM is only called
    for records whose hash moved.
    """
    payload = {k: v for k, v in record.items() if k not in HASH_EXCLUDED_FIELDS}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
