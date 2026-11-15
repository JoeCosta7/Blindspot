"""The document pack: four drafts in the form a county actually uses.

Every fact comes from the opportunity, the county report, the chosen site and
the dated plan. What cannot be known here (names, meeting dates, resolution
numbers, dollar amounts) is left as a [bracketed] blank for staff to fill.
These are drafts for staff and the county attorney, not legal documents.
"""

from __future__ import annotations

from datetime import date

from app.estimates import format_number
from app.models import FundingMatch, Need, PackSection, ProcessPhase, ProcessRequest
from app.narrative import format_need_value

# The governing body and its officers, where the name is fixed by state law.
# Elsewhere the title is left as a blank.
GOVERNING_BODY = {
    "TX": ("Commissioners Court", "County Judge", "County Clerk"),
    "CA": ("Board of Supervisors", "Chair", "Clerk of the Board"),
}
DEFAULT_BODY = ("[Board of County Commissioners]", "[Chair]", "[Clerk of the Board]")


def _day(iso: str | None) -> str:
    if not iso:
        return "[date]"
    d = date.fromisoformat(iso[:10])
    return f"{d:%B} {d.day}, {d.year}"


def _value(v: object) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return format_number(v)
    return str(v).replace("_", " ")


def _need_line(need: Need) -> str:
    value = format_need_value(need.value, need.unit)
    national = format_need_value(need.national_value, need.unit)
    figure = f"{value} in the county" + (f", against {national} nationally" if national else "")
    text = f"{need.label}: {figure if value else (need.detail or 'flagged')}"
    return f"{text} (source: {need.source})"


def _recital(need: Need) -> str:
    """One WHEREAS clause, in sentence form."""
    value = format_need_value(need.value, need.unit)
    national = format_need_value(need.national_value, need.unit)
    label = need.label.lower()
    if value and national:
        return (
            f"county data show {label} at {value}, compared with {national} "
            f"nationally ({need.source})"
        )
    if need.detail:
        return f"county data identify {label}: {need.detail.rstrip('.')} ({need.source})"
    return f"county data identify {label} ({need.source})"


ASSET_WORDS = {
    "lmop_landfill": "landfill listed in EPA's landfill gas database",
    "landfill": "landfill on EPA's RE-Powering list",
    "brownfield": "brownfield",
    "contaminated_site": "cleanup site",
    "mine_land": "former mine site",
    "power_plant": "power plant",
    "non_powered_dam": "dam without hydropower",
    "school": "school",
    "library": "library",
    "college": "college",
}


def _site(request: ProcessRequest) -> tuple[str | None, str | None, str]:
    """(name, address, description) of the chosen site; None for county-wide."""
    if request.site:
        s = request.site
        passed = [c.label.lower() for c in s.checks if c.ok][:4]
        facts = [
            f"{o.label.lower()}: {_value(o.value)}" + (f" {o.unit}" if o.unit else "")
            for o in s.observations
            if not o.by_model
        ][:5]
        text = f"{s.name}" + (f", {s.address}" if s.address else "") + ". "
        if facts:
            text += "Property and aerial records show " + "; ".join(facts) + ". "
        if passed:
            text += "It meets these screening checks: " + "; ".join(passed) + "."
        return s.name, s.address, text.strip()
    if request.site_asset:
        a = request.site_asset
        kind = ASSET_WORDS.get(a.type, a.type.replace("_", " "))
        return a.name, None, f"{a.name}, a {kind} ({a.source})."
    return None, None, "This is a county-wide program; it does not depend on a single site."


def _estimates(request: ProcessRequest) -> list[str]:
    figures = request.site.estimates if request.site else request.opportunity.estimates
    return [
        f"{e.label}: about {format_number(e.value)} {e.unit} (planning estimate; "
        + "; ".join(e.assumptions[:2])
        + ")"
        for e in figures
    ]


def _timeline(phases: list[ProcessPhase], limit: int = 8) -> list[str]:
    steps = [s for p in phases for s in p.steps if s.due]
    steps.sort(key=lambda s: s.due or "")
    return [f"{_day(s.due)}: {s.title}" for s in steps[:limit]]


def _due(phases: list[ProcessPhase], step_id: str) -> str | None:
    return next((s.due for p in phases for s in p.steps if s.id == step_id), None)


def _lead_line(f: FundingMatch) -> str:
    parts = [f"{f.name} ({f.agency})", f.status.replace("_", " ")]
    if f.deadline:
        parts.append(f"deadline {_day(f.deadline)}")
    if f.verified_on:
        parts.append(f"checked {_day(f.verified_on)}")
    return "- " + ", ".join(parts)


def build_sections(
    request: ProcessRequest,
    target: FundingMatch | None,
    phases: list[ProcessPhase],
    today: date,
) -> list[PackSection]:
    opp = request.opportunity
    county = request.county_name
    state = request.state
    body, chair, clerk = GOVERNING_BODY.get(state, DEFAULT_BODY)
    title = opp.title
    site_name, site_address, site_text = _site(request)
    at_site = f" (site: {site_name})" if site_name else ""
    admin = "[County Administrator]"
    # Catalogue descriptions start with a verb: "Divert food scraps ...".
    does = (
        opp.description[0].lower() + opp.description[1:].rstrip(".")
        if opp.description
        else f"carry out {title.lower()}"
    )
    needs = request.needs
    need_lines = [_need_line(n) for n in needs] or ["[Describe the need this project addresses]"]
    need_short = ", ".join(n.label.lower() for n in needs) or "[the need]"
    about = (
        " ".join(x for x in (opp.description, opp.summary) if x) or f"[Describe {title.lower()}]"
    )
    estimates = _estimates(request)
    timeline = _timeline(phases)
    target_text = (
        f"the {target.agency} {target.name} accepts applications through {_day(target.deadline)}"
        if target
        else "[funding program] ([agency]), application deadline [date]"
    )
    target_name = target.name if target else "[funding program]"
    leads = [_lead_line(f) for f in opp.funding] or ["- [No funding lead matched yet]"]

    staff_report = "\n".join(
        [
            f"{county.upper()}, {state}",
            "AGENDA ITEM SUMMARY",
            "",
            f"Meeting date:   [{body} meeting date]",
            "Department:     [Department]",
            "Prepared by:    [Name, title]",
            f"Subject:        Authorization to pursue {title.lower()}{at_site}",
            "",
            "RECOMMENDED ACTION",
            f"Adopt Resolution No. [____] authorizing the {admin}, or designee, to "
            f"develop {title.lower()}{at_site} and to prepare an application to the {target_name}.",
            "",
            "BACKGROUND",
            about,
            "[Add local context: why now, who is affected, and any earlier county action.]",
            "",
            "County data:",
            *[f"- {line}" for line in need_lines],
            "",
            "PROPOSED SITE",
            site_text,
            "",
            "FISCAL IMPACT",
            "This action commits no county funds. Any grant agreement, local match or expenditure "
            f"will return to the {body} for approval.",
            *([f"- {e}" for e in estimates] or ["- [No size estimate is available yet]"]),
            "- Project cost: [staff estimate]",
            "- Local match, if required: [amount and source]",
            "",
            "FUNDING LEADS (confirm status and eligibility with each agency)",
            *leads,
            "",
            "TIMELINE",
            *([f"- {t}" for t in timeline] or ["- [Set milestones]"]),
            "",
            "ATTACHMENTS",
            "1. Draft Resolution No. [____]",
            "2. Site information and aerial photo" if site_name else "2. Program description",
        ]
    )

    resolution = "\n".join(
        [
            "RESOLUTION NO. [____]",
            "",
            f"A RESOLUTION OF THE {body.upper()} OF {county.upper()}, {state}, AUTHORIZING THE "
            f"DEVELOPMENT OF {title.upper()} AND THE PREPARATION OF AN "
            f"APPLICATION TO THE {target_name.upper()}",
            "",
            *[f"WHEREAS, {_recital(n)}; and" for n in needs],
            f"WHEREAS, the proposed project would {does}, addressing these needs; and",
            *(
                [f"WHEREAS, {site_text.rstrip('.')} has been identified as a candidate site; and"]
                if site_name
                else []
            ),
            f"WHEREAS, {target_text}; and",
            f"WHEREAS, the {body} finds that pursuing this project is in the public interest;",
            "",
            f"NOW, THEREFORE, BE IT RESOLVED BY THE {body.upper()} OF {county.upper()}:",
            "",
            "Section 1. The recitals above are true and correct and are adopted as findings.",
            f"Section 2. The {admin}, or designee, is authorized to develop the "
            "project, consult the property owner, partners and the public, and prepare an "
            f"application to the {target_name}.",
            "Section 3. No funds are appropriated by this resolution. Any grant agreement, local "
            f"match, contract or expenditure shall return to the {body} for approval.",
            "Section 4. This resolution takes effect upon adoption.",
            "",
            "PASSED, APPROVED AND ADOPTED this [__] day of [month], [year], by the following vote:",
            "AYES: [ ]   NAYS: [ ]   ABSENT: [ ]   ABSTAIN: [ ]",
            "",
            f"______________________________   [Name], {chair}",
            "",
            f"ATTEST: ______________________________   [Name], {clerk}",
            "",
            "APPROVED AS TO FORM: ______________________________   [Name], County Attorney",
        ]
    )

    reply_by = _due(phases, "confirm_owner") or _due(phases, "operator_agreement")
    if site_name:
        passed = [c.label.lower() for c in request.site.checks if c.ok][:3] if request.site else []
        why = (
            "Our review of aerial imagery and property records identified your property as a "
            "possible fit" + (f": {'; '.join(passed)}." if passed else ".")
        )
        letter_lines = [
            "[County letterhead]",
            "",
            _day(today.isoformat()),
            "",
            "[Owner name]",
            site_name,
            site_address or "[Street address]",
            "[City, state ZIP]",
            "",
            f"Re: Proposed {title.lower()} at {site_name}",
            "",
            "Dear [Owner name]:",
            "",
            f"{county} is exploring {title.lower()} to address {need_short} in the county. {why}",
        ]
        if estimates:
            letter_lines += ["", "Early planning figures: " + "; ".join(estimates) + "."]
        letter_lines += [
            "",
            "We would welcome a conversation about whether a partnership could work for you, "
            "for example a [lease / license / joint project]. "
            "[Add a sentence on what the partnership would offer the owner or the community.] "
            + (
                f"We are preparing an application to {target_name} and would appreciate hearing "
                f"from you by {_day(reply_by)}."
                if target
                else "We would appreciate hearing from you at your convenience."
            ),
            "",
            "This letter is an invitation to discuss the idea. It is not an offer, and nothing "
            "would proceed without your agreement and the approval of the "
            f"{body}.",
            "",
            "Sincerely,",
            "",
            "",
            "[Name]",
            f"[Title], {county}",
            "[Phone]  ·  [Email]",
        ]
    else:
        letter_lines = [
            "[County letterhead]",
            "",
            _day(today.isoformat()),
            "",
            "[Partner organization]",
            "[Contact name, title]",
            "[Address]",
            "",
            f"Re: Partnership on {title.lower()}",
            "",
            "Dear [Contact name]:",
            "",
            f"{county} is planning {title.lower()} to address {need_short}. {about}",
            "",
            "We would like your organization's help with [outreach / delivery / technical "
            "assistance]"
            + (f", and your support for our application to {target_name}." if target else "."),
            "If you are willing, a short letter of support by [date] would strengthen the "
            "application.",
            "",
            "Sincerely,",
            "",
            "",
            "[Name]",
            f"[Title], {county}",
            "[Phone]  ·  [Email]",
        ]
    letter = "\n".join(letter_lines)

    narrative = "\n".join(
        [
            f"PROJECT NARRATIVE: {title}",
            f"Applicant: {county}, {state}",
            f"Program: {target_name}" + (f" ({target.agency})" if target else ""),
            "[Match these headings to the program's required narrative sections.]",
            "",
            "1. Project summary",
            f"{county} proposes {title.lower()}{at_site}. {about}",
            "[Two or three sentences: what will be built or run, where, and for whom.]",
            "",
            "2. Statement of need",
            *[f"- {line}" for line in need_lines],
            "[Explain how these needs affect residents; add local data, history or stories.]",
            "",
            "3. Project description and site",
            site_text,
            "[Describe site control (ownership, lease or agreement) and what will be built.]",
            "",
            "4. Approach and timeline",
            *([f"- {t}" for t in timeline] or ["- [Milestones]"]),
            "",
            "5. Expected outcomes",
            *([f"- {e}" for e in estimates] or ["- [Expected results]"]),
            "[How results will be measured and reported to the program.]",
            "",
            "6. Partners and capacity",
            "[Lead department and staff; partners and their roles; letters of support]",
            "",
            "7. Budget summary",
            "[Total cost; amount requested; local match and its source]",
        ]
    )

    rows = [
        ("summary", "Staff report (agenda item summary)", staff_report),
        ("resolution", "Board resolution", resolution),
        (
            "letter",
            "Letter to the property owner" if site_name else "Partner letter",
            letter,
        ),
        ("narrative", "Grant application narrative", narrative),
    ]
    return [PackSection(id=i, title=t, text=x, generated_by="template") for i, t, x in rows]
