"""Run the real pipeline for demo counties: fills the disk cache and saves each
report to data/fixtures/<fips>.json, which `POST /api/analyze?fixture=1` serves
with no network.

    python scripts/warm_cache.py 54055 36007
    python scripts/warm_cache.py --no-save 54055      # warm the cache only

Run it with the real keys in backend/.env. A report saved while a source was
failing is still saved, with the failure recorded in its source_status, so
check the printed summary before relying on a fixture.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.estimates import format_number  # noqa: E402
from app.main import load_county_index  # noqa: E402
from app.pipeline import analyze_county  # noqa: E402


async def main(argv: list[str]) -> int:
    save = "--no-save" not in argv
    fips_list = [a for a in argv if not a.startswith("--")]
    if not fips_list:
        print(__doc__)
        return 2

    settings = get_settings()
    index = load_county_index(settings)
    fixtures = settings.data_dir / "fixtures"
    failures = 0

    async with httpx.AsyncClient() as client:
        for fips in fips_list:  # one at a time: be gentle with the free APIs
            county = index.get(fips)
            if county is None:
                print(f"{fips}: no such county")
                failures += 1
                continue
            started = time.perf_counter()
            report = await analyze_county(county, client, settings)
            elapsed = time.perf_counter() - started

            print(f"\n{report.name}, {report.state} ({fips})  {elapsed:.1f} s")
            print(f"  population: {report.population}")
            ranked = [(n.id, n.priority, n.severity) for n in report.needs]
            print(f"  needs (priority, severity): {ranked}")
            print(f"  assets found: {report.asset_counts}  (on map: {len(report.assets)})")
            print(f"  not found: {report.missing_asset_types}")
            for o in report.opportunities:
                figures = ", ".join(
                    f"{e.label} {format_number(e.value)} {e.unit}" for e in o.estimates
                )
                funds = ", ".join(f"{f.program_id} [{f.status}]" for f in o.funding)
                print(
                    f"  {o.score:3d}  {o.title}  [{o.generated_by}: {o.solution_id or 'own idea'}]"
                )
                print(f"       needs:     {', '.join(o.need_ids)}")
                print(f"       uses:      {', '.join(o.factor_ids + o.asset_ids) or 'none'}")
                if o.summary:
                    print(f"       what:      {o.summary}")
                print(f"       estimates: {figures or 'none'}")
                print(f"       funding:   {funds or 'none'}")
            print(f"  factors: {len(report.factors)}")
            print(f"  narrative ({report.narrative.generated_by}): {report.narrative.summary}")
            for s in report.source_status:
                state = "ok" if s.ok else "FAILED"
                note = " (cached)" if s.cached else ""
                print(f"  {s.source:18s} {state}{note}{'  ' + s.message if s.message else ''}")
            # Gemini entries are not data: without them the report is still complete.
            failures += sum(
                1 for s in report.source_status if not s.ok and not s.source.startswith("gemini")
            )

            if save:
                fixtures.mkdir(parents=True, exist_ok=True)
                path = fixtures / f"{fips}.json"
                path.write_text(report.model_dump_json(indent=1), encoding="utf-8")
                print(f"  saved {path.relative_to(settings.data_dir.parent)}")

    if failures:
        print(f"\n{failures} source failure(s). Fixtures saved above are incomplete.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
