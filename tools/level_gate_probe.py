"""Probe: how many no-level vacancies would classify under extended signals.

Read-only measurement for widening classify_job_level. Walks the same funnel
as funnel_audit, collects fresh suitable vacancies that currently return
None (after EXCLUDE/JUNIOR/MIDDLE/structured/ladder checks), then reports how
many each candidate phrase would rescue, with samples for manual review.

Never writes, never posts, never touches Telegram. Run from the repo root:

    python tools/level_gate_probe.py --json --save level-probe.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("VERCEL", "1")

import channel_bot as core  # noqa: E402
import job_sources_extra as extra  # noqa: E402
import serverless_publication_policy as policy  # noqa: E402

# Candidate phrases, grouped by the bucket they would join. These are measured
# here first; only the ones that prove clean on live data get committed.
CANDIDATE_JUNIOR = [
    "0-1 years", "1+ years",          # plural gaps of existing singular signals
    "0-3 years", "1-3 years",         # junior-mid ranges, junior-accepting floor
    "up to 2 years", "up to 1 year",
    "no experience required", "experience not required",
    "0 to 1 year", "0 to 2 years", "1 to 2 years", "1 to 3 years",
    "2 years",
]

CANDIDATE_MIDDLE = [
    "2-3 года",                        # sanity: Russian range should not appear
    "2 to 3 years", "2 to 4 years", "3 to 5 years",
    "3 years",
]

# En-dash variants of ranges that already exist with a hyphen; measured as one
# group because they are mechanical equivalents.
EN_DASH_JUNIOR = ["0–1 year", "0–2 years", "1–2 years", "0–3 years", "1–3 years"]
EN_DASH_MIDDLE = ["2–3 years", "2–4 years", "3–5 years"]

SOURCES = [
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
]


def _hits(text: str, phrases: List[str]) -> List[str]:
    return [p for p in phrases if core.has_text_signal(text, p)]


def run() -> Dict[str, object]:
    policy.install_serverless_publication_policy()
    policy.reset_publication_policy_stats()

    counts: Counter = Counter()
    rescued_junior: Counter = Counter()
    rescued_middle: Counter = Counter()
    samples: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    title_only = Counter()  # candidates matched only in the title
    exclude_hits: Counter = Counter()
    excluded_samples: List[Dict[str, str]] = []
    open_samples: List[Dict[str, str]] = []

    for source_name, fetch in SOURCES:
        try:
            jobs = fetch() or []
        except Exception as exc:  # noqa: BLE001
            counts["fetch_errors"] += 1
            print(f"fetch error {source_name}: {exc}", file=sys.stderr)
            continue
        for job in jobs:
            if not policy._ORIGINAL_IS_SUITABLE_JOB(job):
                continue
            if not policy.is_fresh_for_serverless_publication(job):
                continue
            counts["fresh_suitable"] += 1
            if core.classify_job_level(job):
                counts["already_classified"] += 1
                continue
            title = str(job.get("title", "") or "")
            full = f"{job.get('title', '')} {job.get('description', '')}".lower()
            ex_hits = [w for w in core.EXCLUDE_SIGNALS if core.has_text_signal(full, w)]
            title_pos = [
                w for w in core.JUNIOR_SIGNALS + core.MIDDLE_SIGNALS
                if core.has_text_signal(title.lower(), w)
            ]
            if ex_hits:
                counts["excluded_senior"] += 1
                if title_pos:
                    counts["excluded_but_title_positive"] += 1
                    if len(samples["__title_override"]) < 15:
                        samples["__title_override"].append(
                            {
                                "source": source_name,
                                "title": title[:90],
                                "title_signal": ",".join(title_pos),
                                "exclude_hits": ",".join(ex_hits),
                            }
                        )
                for w in ex_hits:
                    exclude_hits[w] += 1
                if len(excluded_samples) < 40:
                    excluded_samples.append(
                        {
                            "source": source_name,
                            "title": title[:90],
                            "hits": ",".join(ex_hits),
                            "in_title": str(
                                any(core.has_text_signal(title.lower(), w) for w in ex_hits)
                            ),
                        }
                    )
                continue
            counts["no_level_open"] += 1
            if len(open_samples) < 40:
                open_samples.append({"source": source_name, "title": title[:90]})

            j_hits = _hits(full, CANDIDATE_JUNIOR) + _hits(full, EN_DASH_JUNIOR)
            m_hits = _hits(full, CANDIDATE_MIDDLE) + _hits(full, EN_DASH_MIDDLE)
            for p in j_hits:
                rescued_junior[p] += 1
            for p in m_hits:
                rescued_middle[p] += 1
            for p in j_hits + m_hits:
                if len(samples[p]) < 6:
                    in_title = core.has_text_signal(title.lower(), p)
                    samples[p].append(
                        {
                            "source": source_name,
                            "title": title[:90],
                            "in_title": str(in_title),
                        }
                    )
                if not core.has_text_signal(title.lower(), p):
                    title_only[p] += 1

    return {
        "counts": dict(counts),
        "rescued_junior": dict(rescued_junior),
        "rescued_middle": dict(rescued_middle),
        "description_only_hits": dict(title_only),
        "exclude_signal_hits": dict(exclude_hits),
        "excluded_samples": excluded_samples,
        "open_samples": open_samples,
        "samples": dict(samples),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--save")
    args = parser.parse_args()

    report = run()
    if args.save:
        Path(args.save).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(json.dumps(report["counts"], indent=2))
    print("JUNIOR candidates:", json.dumps(report["rescued_junior"], indent=2))
    print("MIDDLE candidates:", json.dumps(report["rescued_middle"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
