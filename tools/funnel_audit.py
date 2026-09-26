"""Read-only per-source yield audit for the public vacancy funnel.

The two silent regressions that stopped the channel were invisible in the cron
response: a source can keep returning 200 with zero usable vacancies, and a
filter can reject everything before the counters that matter. This script walks
the same pipeline the collector uses and prints one row per source so a daily
job can diff the yield.

It never writes, never posts and never touches Telegram. Run it locally with:

    python tools/funnel_audit.py --json
    python tools/funnel_audit.py --baseline baseline.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("VERCEL", "1")

import channel_bot as core  # noqa: E402
import job_sources_extra as extra  # noqa: E402
import serverless_publication_policy as policy  # noqa: E402
from growth_utils import apply_editorial_quality_gate  # noqa: E402


def _sources() -> List[Tuple[str, object]]:
    return [
        ("Arbeitnow", core.fetch_arbeitnow),
        ("Greenhouse", core.fetch_greenhouse),
        ("Lever", core.fetch_lever),
        ("Ashby", core.fetch_ashby),
        ("4dayweek", extra.fetch_4dayweek),
        ("TheMuse", extra.fetch_themuse),
        ("WorkingNomads", extra.fetch_working_nomads),
        ("RemoteOK", core.fetch_remoteok),
        ("RemoteOK Dev", extra.fetch_remoteok_dev),
        ("Himalayas", core.fetch_himalayas),
        ("We Work Remotely", core.fetch_weworkremotely),
        ("Remotive", core.fetch_remotive),
        ("Jobicy", core.fetch_jobicy),
        ("DevITJobs UK", core.fetch_devitjobs),
        ("SuperJob", core.fetch_superjob),
    ]


def audit_source(name: str, fetch) -> Dict[str, object]:
    stages: Counter = Counter()
    samples: List[Dict[str, str]] = []
    try:
        jobs = fetch() or []
    except Exception as exc:  # noqa: BLE001
        return {"source": name, "error": f"{type(exc).__name__}: {exc}"}
    stages["fetched"] = len(jobs)
    for job in jobs:
        stages["fetched"] = max(stages["fetched"], 0)
        try:
            if not policy._ORIGINAL_IS_SUITABLE_JOB(job):
                stages["not_suitable"] += 1
                continue
            stages["suitable"] += 1
            if not policy.is_fresh_for_serverless_publication(job):
                stages["stale_or_unknown"] += 1
                continue
            stages["fresh"] += 1
            level = core.classify_job_level(job)
            if not level:
                stages["no_level"] += 1
                continue
            stages["level_passed"] += 1
            job["level"] = level
            job["hash"] = core.generate_job_hash(job)
            apply_editorial_quality_gate(
                job, remote_only_sources=tuple(core.REMOTE_ONLY_SOURCES)
            )
            if job.get("quality_gate_status") != "passed":
                stages["gate_rejected"] += 1
                if len(samples) < 3:
                    samples.append(
                        {
                            "title": str(job.get("title"))[:70],
                            "gate": str(job.get("quality_gate_status")),
                            "reasons": ",".join(job.get("quarantine_reasons") or []),
                        }
                    )
                continue
            stages["classified"] += 1
        except Exception as exc:  # noqa: BLE001
            stages["errors"] += 1
            samples.append({"error": f"{type(exc).__name__}: {exc}"})
    return {"source": name, "stages": dict(stages), "gate_samples": samples}


def run() -> Dict[str, object]:
    policy.install_serverless_publication_policy()
    policy.reset_publication_policy_stats()
    rows = [audit_source(name, fetch) for name, fetch in _sources()]
    totals: Counter = Counter()
    for row in rows:
        for stage, value in (row.get("stages") or {}).items():
            totals[stage] += value
    return {
        "totals": dict(totals),
        "sources": rows,
        "policy": policy.publication_policy_snapshot(),
    }


def _classified_map(report: Dict[str, object]) -> Dict[str, int]:
    return {
        str(row["source"]): int((row.get("stages") or {}).get("classified") or 0)
        for row in report.get("sources", [])
        if not row.get("error")
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the raw report only")
    parser.add_argument("--baseline", help="compare classified counts against a saved report")
    parser.add_argument("--save", help="write the report for a later --baseline comparison")
    parser.add_argument(
        "--regression-factor",
        type=float,
        default=0.0,
        help="fail when a source drops below this fraction of its baseline",
    )
    args = parser.parse_args()

    report = run()
    if args.save:
        Path(args.save).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    current = _classified_map(report)
    baseline: Dict[str, int] = {}
    if args.baseline and Path(args.baseline).exists():
        baseline = _classified_map(json.loads(Path(args.baseline).read_text(encoding="utf-8")))

    print(f"{'source':<20}{'fetched':>9}{'suitable':>9}{'fresh':>7}{'level':>7}{'classif':>9}  note")
    for row in report["sources"]:
        name = str(row["source"])
        if row.get("error"):
            print(f"{name:<20}{'-':>9}{'-':>9}{'-':>7}{'-':>7}{'-':>9}  ERROR {row['error'][:40]}")
            continue
        stages = row.get("stages") or {}
        note = ""
        if name in baseline:
            was = baseline[name]
            now = current.get(name, 0)
            if was > 0 and now == 0:
                note = f"REGRESSION (baseline {was})"
            else:
                note = f"baseline {was}"
        print(
            f"{name:<20}{stages.get('fetched', 0):>9}{stages.get('suitable', 0):>9}"
            f"{stages.get('fresh', 0):>7}{stages.get('level_passed', 0):>7}"
            f"{stages.get('classified', 0):>9}  {note}"
        )
    totals = report["totals"]
    print(
        f"{'TOTAL':<20}{totals.get('fetched', 0):>9}{totals.get('suitable', 0):>9}"
        f"{totals.get('fresh', 0):>7}{totals.get('level_passed', 0):>7}{totals.get('classified', 0):>9}"
    )
    print(f"max_posts_per_cycle={report['policy'].get('max_posts_per_cycle')}")

    if args.regression_factor > 0 and baseline:
        failed = []
        for name, was in baseline.items():
            if was <= 0:
                continue
            now = current.get(name, 0)
            if now < was * args.regression_factor:
                failed.append(f"{name}: {now} < {was} * {args.regression_factor}")
        if failed:
            print("FUNNEL REGRESSION: " + "; ".join(failed), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
