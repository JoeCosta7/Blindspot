"""Outreach letter and grant paragraph for a shortlisted site.

Template only for now: every figure comes from the candidate's estimates and
observations, and the text promises nothing. A Gemini version would go
through the same number check as the opportunities (plan, section 8).
"""

from __future__ import annotations

from app.estimates import format_number
from app.geo import County
from app.models import SiteCandidate, SiteDrafts

SOLUTION_WORDS = {
    "rooftop_solar_commercial": "a rooftop solar array",
    "parking_canopy_solar": "a solar canopy over part of the parking lot, with some depaving",
    "cooling_center": "a cooling center and resilience hub for heat waves and power outages",
}


def _estimate_sentence(candidate: SiteCandidate) -> str:
    by_label = {e.label: e for e in candidate.estimates}
    capacity = by_label.get("Capacity")
    homes = by_label.get("Household equivalent")
    if capacity is None:
        return ""
    text = (
        f"From the measured roof, we estimate room for about {format_number(capacity.value)} "
        f"{capacity.unit} of solar"
    )
    if homes is not None:
        text += f", roughly the yearly electricity of {format_number(homes.value)} homes"
    return text + ". These are planning estimates, not an engineering design."


def template_drafts(county: County, candidate: SiteCandidate) -> SiteDrafts:
    projects = [SOLUTION_WORDS.get(s, s.replace("_", " ")) for s in candidate.solution_ids]
    what = " and ".join(projects) if projects else "a community sustainability project"
    place = candidate.address or candidate.name
    estimate = _estimate_sentence(candidate)
    funding = [f.name for f in candidate.funding if f.status in ("open", "open_with_deadlines")]
    funding_line = (
        f" Programs we would look into with you include {', '.join(funding[:2])}; "
        "eligibility would need to be confirmed."
        if funding
        else ""
    )
    letter = (
        f"To the owner of {place},\n\n"
        f"{county.name} is looking for sites that could host {what}. Your building came up in a "
        "review of aerial imagery and property records as a possible fit, and we would like to "
        f"talk about whether a partnership could work for you. {estimate}{funding_line}\n\n"
        "This letter is an invitation to a conversation, not a proposal or a commitment. If you "
        "are open to it, please reply and we will arrange a time.\n\n"
        f"Sincerely,\n{county.name} Sustainability Office"
    )
    paragraph = (
        f"The project would place {what} at {place} in {county.name}, {county.state}. "
        f"The site was selected from aerial property records and 3D interior scans. {estimate} "
        "Project figures are preliminary estimates and the applicant will confirm eligibility "
        "with each program before applying."
    ).replace("  ", " ")
    return SiteDrafts(
        building_id=candidate.building_id,
        outreach_letter=letter,
        grant_paragraph=paragraph,
        generated_by="template",
    )
