"""Site Scout: a building-level agent for the counties with sponsor data.

See SITE_SCOUT_PLAN.md at the repository root. The county report says what a
county needs; the scout finds specific commercial buildings that could host a
fix, inspects each through three data layers (property record, aerial image,
interior scan) and checks the layers against each other.

Layers: Matterport 3D scans (sources/matterport.py) seed the candidates and
describe interiors; Vexcel (sources/vexcel.py) gives the parcel and structure
record, live object detection and the aerial image. Coordinates join them.

Modules:
- provider.py  the layers behind one protocol, so tests can inject a fake
- measure.py   geodesic areas, cross-layer checks, confidence (pure functions)
- tools.py     the actions the agent may take, and the run state
- agent.py     the decision loop (Gemini structured output) and the rule fallback
- drafts.py    outreach letter and grant paragraph (template)
"""
