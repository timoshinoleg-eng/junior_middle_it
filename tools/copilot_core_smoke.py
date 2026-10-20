"""Smoke the deterministic Copilot core against live production vacancies.

The Copilot layer is deterministic and offline, so it can be exercised without
credentials: the public Edge projection of the published vacancies is enough
input. This script runs the real modules of the repository over real payloads
and reports what the analysis card would show.

Run it locally with:

    python tools/copilot_core_smoke.py
    python tools/copilot_core_smoke.py --limit 30
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from copilot.fit import compute_fit  # noqa: E402
from copilot.grade_audit import audit_grade  # noqa: E402
from copilot.hr_link import build_contact_message, extract_hr_handle  # noqa: E402
from resume_matcher import job_text  # noqa: E402

EDGE_JOBS = (
    "https://uucjptyqfsdzqsftgiro.supabase.co/functions/v1/growth-proxy/public-jobs"
)

PROFILES = {
    "empty": {},
    "python": {"skills": "python, fastapi, postgresql, docker, git", "categories": ["backend"]},
    "frontend": {"skills": "javascript, react, typescript, css", "categories": ["frontend"]},
    "qa": {"skills": "pytest, postman, sql, testdesign", "categories": ["qa"]},
}


def fetch_jobs(limit: int) -> List[Dict]:
    url = "%s?days=14&limit=%d" % (EDGE_JOBS, max(1, min(limit, 300)))
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = payload.get("rows") or []
    return [row.get("payload") or {} for row in rows]


def check_job(job: Dict) -> List[str]:
    """Run the whole deterministic core and return the problems it produced."""
    problems: List[str] = []
    text = job_text(job)
    try:
        audit = audit_grade(job)
    except Exception as exc:
        return ["grade audit raised %s" % type(exc).__name__]
    if not 0 <= int(audit.realism_score) <= 100:
        problems.append("realism_score out of range: %s" % audit.realism_score)

    scores = {}
    for name, profile in PROFILES.items():
        try:
            report = compute_fit(profile, job)
        except Exception as exc:
            problems.append("fit(%s) raised %s" % (name, type(exc).__name__))
            continue
        scores[name] = report
        if not 0 <= int(report.score) <= 100:
            problems.append("fit(%s).score out of range: %s" % (name, report.score))
        if not 0 <= int(report.stack_match_score) <= 100:
            problems.append("fit(%s).stack_match_score out of range" % name)
        if not 0 <= int(report.keyword_overlap_pct) <= 100:
            problems.append("fit(%s).keyword_overlap_pct out of range" % name)
        again = compute_fit(profile, job)
        if again.as_dict() != report.as_dict():
            problems.append("fit(%s) is not deterministic" % name)

    if scores:
        report = scores["python"]
        try:
            contact = build_contact_message(job, report)
        except Exception as exc:
            problems.append("contact message raised %s" % type(exc).__name__)
        else:
            if len(contact) > 800:
                problems.append("contact message is %d chars" % len(contact))
        try:
            extract_hr_handle(text)
        except Exception as exc:
            problems.append("hr handle raised %s" % type(exc).__name__)
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Smoke the Copilot core on live data.")
    parser.add_argument("--limit", type=int, default=20, help="how many vacancies to analyse")
    parser.add_argument("--json", action="store_true", help="print the raw matrix")
    args = parser.parse_args(argv)

    jobs = fetch_jobs(args.limit)
    if not jobs:
        print("no published vacancies returned by the Edge projection")
        return 1

    rows = []
    failures = 0
    for job in jobs:
        problems = check_job(job)
        failures += len(problems)
        report = compute_fit(PROFILES["python"], job)
        audit = audit_grade(job)
        handle = extract_hr_handle(job_text(job))
        rows.append(
            {
                "title": str(job.get("title") or "")[:48],
                "company": str(job.get("company") or "")[:24],
                "fit_python": report.score,
                "fit_frontend": compute_fit(PROFILES["frontend"], job).score,
                "fit_qa": compute_fit(PROFILES["qa"], job).score,
                "declared_grade": audit.declared_grade,
                "real_grade": audit.real_grade,
                "badge": audit.badge_text,
                "hr_handle": handle or "",
                "matched": report.matched_skills[:4],
                "problems": problems,
            }
        )

    if args.json:
        print(json.dumps({"jobs": rows, "problems": failures}, indent=2, ensure_ascii=False))
        return 1 if failures else 0

    print("%-42s %4s %4s %4s %-8s %-8s %s" % ("title", "py", "fe", "qa", "declared", "real", "badge"))
    print("-" * 140)
    for row in rows:
        print(
            "%-42s %4s %4s %4s %-8s %-8s %s"
            % (
                row["title"],
                row["fit_python"],
                row["fit_frontend"],
                row["fit_qa"],
                row["declared_grade"],
                row["real_grade"],
                row["badge"],
            )
        )
    print("-" * 140)
    conflicts = sum(1 for row in rows if row["declared_grade"] != row["real_grade"])
    handles = sum(1 for row in rows if row["hr_handle"])
    print(
        "jobs: %d, problems: %d, grade conflicts: %d, hr handles found: %d"
        % (len(rows), failures, conflicts, handles)
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
