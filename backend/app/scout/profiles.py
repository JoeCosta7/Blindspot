"""What kind of host fits each opportunity, and where Site Scout looks first.

Two kinds of profile:
- Building profiles (public, industrial, commercial): the project goes in or
  on a building. Candidates come from public buildings, CoStar records and
  Matterport scans, and are judged for the building projects in
  data/scout_solutions.json (rooftop solar, parking canopy, cooling centre).
- Site profiles (`land=True`): the project goes on a piece of land the
  county report already knows about, such as a landfill, a brownfield, a
  power plant or a dam. Candidates are the report's own sites of that type,
  then more found by Vexcel semantic search, and each is judged for the
  opportunity's own project type from data/solutions.json.

Pure data; imported by the pipeline (to decide what is scoutable) and the agent.
"""

from __future__ import annotations

from dataclasses import dataclass

PUBLIC_FIRST = ("search_public_buildings", "search_properties", "search_scans")
PRIVATE_FIRST = ("search_properties", "search_scans", "search_public_buildings")
SITE_FIRST = ("search_report_sites", "search_imagery")


@dataclass(frozen=True)
class Profile:
    source_order: tuple[str, ...]
    property_types: tuple[str, ...]
    prefer_public: bool
    hosts: str  # plain words for the prompt and the timeline
    land: bool = False
    # Site profiles: the report's asset types that can host the project.
    site_types: tuple[str, ...] = ()
    # Site profiles: what to ask Vexcel's semantic search for.
    imagery_text: str | None = None
    # Public building kinds that suit the project, best first.
    public_kinds: tuple[str, ...] = ()
    # Checks to run (ids in data/site_checks.json); empty means the table's
    # own project lists decide.
    check_ids: tuple[str, ...] = ()
    # "gemini" when written for this opportunity, "catalogue" otherwise.
    chosen_by: str = "catalogue"


PUBLIC = Profile(
    PUBLIC_FIRST, ("Office", "Retail", "Flex"), True, "the county's own public buildings"
)
INDUSTRIAL = Profile(
    PRIVATE_FIRST,
    ("Industrial", "Flex", "Retail"),
    False,
    "large industrial, flex or retail buildings",
)
COMMERCIAL = Profile(
    PRIVATE_FIRST, ("Industrial", "Retail", "Office", "Flex"), False, "large commercial buildings"
)
LANDFILL = Profile(
    SITE_FIRST,
    (),
    False,
    "landfills",
    land=True,
    site_types=("lmop_landfill", "landfill"),
    imagery_text="landfill with large bare graded earth areas, access roads and truck traffic",
)
RECLAIMED_LAND = Profile(
    SITE_FIRST,
    (),
    False,
    "reclaimed land: brownfields, closed landfills, mine land and cleanup sites",
    land=True,
    site_types=("brownfield", "landfill", "mine_land", "contaminated_site"),
    imagery_text="large vacant cleared industrial lot with bare ground and no buildings",
)
POWER_PLANT = Profile(
    SITE_FIRST,
    (),
    False,
    "retiring or retired power plants",
    land=True,
    site_types=("power_plant",),
    imagery_text="power plant with smokestacks, switchyard and open land",
)
PAVED = Profile(
    ("search_imagery", "search_properties"),
    ("Retail", "Industrial", "Office"),
    False,
    "large paved lots: shopping centres, offices and industrial sites",
    land=True,
    imagery_text="large paved parking lot with very few trees",
)
INTERIOR = Profile(
    ("search_properties", "search_public_buildings", "search_scans"),
    ("Office", "Retail", "Hospitality", "Specialty"),
    False,
    "existing buildings with large, open interiors",
    public_kinds=("library", "college"),
    check_ids=(
        "big_footprint",
        "floor_area_record",
        "has_scan",
        "scan_vs_footprint",
        "room_for_people",
        "record_vs_footprint",
    ),
)
DAM = Profile(
    SITE_FIRST,
    (),
    False,
    "dams without hydropower",
    land=True,
    site_types=("non_powered_dam",),
    imagery_text="concrete dam with spillway on a river",
)

# By the main catalogue's project type (data/solutions.json).
PROFILES: dict[str, Profile] = {
    "resilience_hub": PUBLIC,
    "weatherization_clinic": PUBLIC,
    "public_ev_charging": PUBLIC,
    "shade_trees_depaving": PAVED,
    "school_heat_pumps": PUBLIC,
    "mobility_hub": PUBLIC,
    "green_stormwater": PAVED,
    "utility_assistance_drive": PUBLIC,
    "community_solar": RECLAIMED_LAND,
    "wind_on_reclaimed_land": RECLAIMED_LAND,
    "landfill_gas_energy": LANDFILL,
    "landfill_composting": LANDFILL,
    "repower_fossil_plant": POWER_PLANT,
    "small_hydro": DAM,
    "cooling_center": INTERIOR,
}
_PUBLIC_WORDS = (
    "school",
    "library",
    "librar",
    "public building",
    "community cent",
    "civic",
    "clinic",
    "hub",
)
_INDUSTRIAL_WORDS = ("industrial", "warehouse", "factory", "reuse", "convert", "vacant")


def profile_for(solution_id: str | None, focus: str | None) -> Profile:
    """The profile for an opportunity: by project type, else by words in its
    title (an idea of Gemini's own), else large commercial buildings."""
    if solution_id and solution_id in PROFILES:
        return PROFILES[solution_id]
    text = (focus or "").lower()
    if "landfill" in text or "compost" in text:
        return LANDFILL
    if "brownfield" in text or "reclaimed" in text:
        return RECLAIMED_LAND
    if any(w in text for w in _PUBLIC_WORDS):
        return PUBLIC
    if any(w in text for w in _INDUSTRIAL_WORDS):
        return INDUSTRIAL
    return COMMERCIAL


def is_site_project(solution_id: str | None) -> bool:
    profile = PROFILES.get(solution_id or "")
    return bool(profile and profile.land)
