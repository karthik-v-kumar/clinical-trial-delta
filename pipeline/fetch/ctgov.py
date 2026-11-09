"""ClinicalTrials.gov client: API v2 search with paging, pacing, retries and an
integrity check, plus helpers that express a watchlist query as the equivalent
website search (for count reconciliation).

Standard library only, so the GitHub Actions job needs no installs to run it.
"""

from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from ..config import Query

API_BASE = "https://clinicaltrials.gov/api/v2"
WEB_BASE = "https://clinicaltrials.gov"
USER_AGENT = "clinical-trial-delta/1.0 (+https://github.com/karthik-v-kumar/clinical-trial-delta)"
PAGE_SIZE = 1000  # API maximum

# Only the pieces the normalizer reads. Deliberately excluded: results sections
# (large; the hasResults flag is enough), and every contact / official / responsible
# party field, so that no personal names, phone numbers or e-mails ever enter the
# pipeline (hard rule: no personal data).
FIELDS = (
    "protocolSection.identificationModule.nctId",
    "protocolSection.identificationModule.briefTitle",
    "protocolSection.identificationModule.officialTitle",
    "protocolSection.identificationModule.acronym",
    "protocolSection.statusModule",
    "protocolSection.sponsorCollaboratorsModule.leadSponsor",
    "protocolSection.sponsorCollaboratorsModule.collaborators",
    "protocolSection.descriptionModule.briefSummary",
    "protocolSection.conditionsModule.conditions",
    "protocolSection.designModule",
    "protocolSection.armsInterventionsModule",
    "protocolSection.outcomesModule.primaryOutcomes",
    "protocolSection.eligibilityModule",
    "protocolSection.contactsLocationsModule.locations.facility",
    "protocolSection.contactsLocationsModule.locations.status",
    "protocolSection.contactsLocationsModule.locations.city",
    "protocolSection.contactsLocationsModule.locations.state",
    "protocolSection.contactsLocationsModule.locations.country",
    "protocolSection.contactsLocationsModule.locations.geoPoint",
    "hasResults",
)

# Website filter codes for the study-type checkbox (aggFilters=studyType:<code>).
_WEB_STUDY_TYPE = {"INTERVENTIONAL": "int", "OBSERVATIONAL": "obs", "EXPANDED_ACCESS": "exp"}

_BARE_TERM = re.compile(r"^[A-Za-z0-9]+$")


class FetchError(RuntimeError):
    """The registry could not be read reliably; the run must not write a snapshot."""


class FetchIntegrityError(FetchError):
    """Paging returned a different number of trials than the API's own total."""


def essie_or(terms: tuple[str, ...] | list[str]) -> str:
    """Join terms with OR using ClinicalTrials.gov's search syntax (Essie).

    Single words go bare so the registry's normal stemming/synonyms apply;
    anything with spaces or punctuation is quoted as a phrase.
    """
    out = []
    for t in terms:
        t = t.replace('"', "").strip()
        out.append(t if _BARE_TERM.match(t) else f'"{t}"')
    return " OR ".join(out)


def api_params(q: Query) -> dict[str, str]:
    """API v2 /studies parameters for a watchlist query (without paging)."""
    params: dict[str, str] = {}
    if q.cond:
        params["query.cond"] = essie_or(q.cond)
    if q.intr:
        params["query.intr"] = essie_or(q.intr)
    if q.spons:
        params["query.spons"] = essie_or(q.spons)
    if q.term:
        params["query.term"] = q.term
    if q.study_type:
        params["filter.advanced"] = f"AREA[StudyType]{q.study_type}"
    return params


def web_params(q: Query) -> dict[str, str]:
    """The same search as typed into clinicaltrials.gov/search.

    Condition / Intervention / Sponsor boxes map 1:1; the study-type filter uses
    the website's own "Study type: Interventional" checkbox rather than an
    expression, so the website count is an independent check of our filter.
    """
    params: dict[str, str] = {}
    if q.cond:
        params["cond"] = essie_or(q.cond)
    if q.intr:
        params["intr"] = essie_or(q.intr)
    if q.spons:
        params["spons"] = essie_or(q.spons)
    if q.term:
        params["term"] = q.term
    if q.study_type:
        params["aggFilters"] = f"studyType:{_WEB_STUDY_TYPE[q.study_type]}"
    return params


def web_url(q: Query) -> str:
    """A link a person can open to see ClinicalTrials.gov's own count."""
    return f"{WEB_BASE}/search?" + urllib.parse.urlencode(web_params(q), quote_via=urllib.parse.quote)


def registry_url(nct_id: str) -> str:
    return f"{WEB_BASE}/study/{nct_id}"


# A transport takes a full URL and returns (status, headers, body). Injected so
# tests can run without network access.
Transport = Callable[[str], "tuple[int, dict[str, str], bytes]"]


def urllib_transport(url: str, timeout: float = 60.0) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read() or b""


@dataclass
class QueryResult:
    query_id: str
    total_count: int  # the API's own totalCount for this search
    studies: list[dict]  # raw study records, deduplicated by NCT number
    pages: int


class Client:
    def __init__(
        self,
        transport: Transport = urllib_transport,
        min_interval: float = 1.0,
        max_attempts: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._min_interval = min_interval
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self.requests_made = 0

    def _pace(self) -> None:
        if self._last_request is not None:
            wait = self._min_interval - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def get_json(self, url: str) -> dict:
        last_error = ""
        for attempt in range(1, self._max_attempts + 1):
            self._pace()
            self.requests_made += 1
            try:
                status, headers, body = self._transport(url)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                status, headers, body, last_error = 0, {}, b"", f"{type(exc).__name__}: {exc}"
            if status == 200:
                try:
                    return json.loads(body)
                except json.JSONDecodeError as exc:
                    last_error = f"invalid JSON: {exc}"
            elif status and status != 429 and status < 500:
                raise FetchError(f"HTTP {status} for {url}: {body[:300]!r}")
            else:
                last_error = last_error if status == 0 else f"HTTP {status}"
            if attempt < self._max_attempts:
                retry_after = headers.get("Retry-After") or headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2.0**attempt
                self._sleep(delay + random.uniform(0, 0.5))
        raise FetchError(f"giving up after {self._max_attempts} attempts ({last_error}) for {url}")

    def version(self) -> dict:
        return self.get_json(f"{API_BASE}/version")

    def count(self, q: Query) -> int:
        """The API's totalCount for a query, without downloading the trials."""
        params = {**api_params(q), "countTotal": "true", "pageSize": "1", "fields": "NCTId"}
        return int(self.get_json(f"{API_BASE}/studies?" + urllib.parse.urlencode(params))["totalCount"])

    def web_count(self, q: Query) -> int:
        """The count the clinicaltrials.gov search page shows for the same search.

        Reads the website's own search backend (/api/int/studies, the endpoint the
        search page calls) with the website's parameters. Undocumented, so only the
        reconciliation script uses it; the daily run never depends on it.
        """
        params = {**web_params(q), "checkSpell": "true", "from": "0", "limit": "1", "fields": "NCTId"}
        return int(self.get_json(f"{WEB_BASE}/api/int/studies?" + urllib.parse.urlencode(params))["total"])

    def fetch_query(self, q: Query) -> QueryResult:
        """All trials matching a query. Retries once if paging and totalCount disagree."""
        for _attempt in (1, 2):
            result = self._fetch_query_once(q)
            if len(result.studies) == result.total_count:
                return result
            mismatch = (
                f"query '{q.id}': fetched {len(result.studies)} unique trials "
                f"but the API reported totalCount={result.total_count}"
            )
        raise FetchIntegrityError(mismatch + " (after a retry)")

    def _fetch_query_once(self, q: Query) -> QueryResult:
        base = {**api_params(q), "pageSize": str(PAGE_SIZE), "fields": ",".join(FIELDS)}
        studies: dict[str, dict] = {}
        total_count: int | None = None
        page_token: str | None = None
        pages = 0
        while True:
            params = dict(base)
            if page_token is None:
                params["countTotal"] = "true"
            else:
                params["pageToken"] = page_token
            data = self.get_json(f"{API_BASE}/studies?" + urllib.parse.urlencode(params))
            pages += 1
            if page_token is None:
                total_count = int(data["totalCount"])
            for study in data.get("studies", []):
                nct = study["protocolSection"]["identificationModule"]["nctId"]
                studies.setdefault(nct, study)
            page_token = data.get("nextPageToken")
            if not page_token:
                break
            if pages > 1000:
                raise FetchError(f"query '{q.id}': paging did not terminate")
        assert total_count is not None
        return QueryResult(query_id=q.id, total_count=total_count, studies=list(studies.values()), pages=pages)
