"""Post-release verification of the v7.0 AI Career Copilot rollout.

One command that checks every surface of the release:

* the production health contract and the Mini App delivery;
* the published state of the Copilot rollout - the feature flags of the
  production environment, so the last manual step is observable from the
  outside;
* the deployed validator version of both Edge growth bridges, so the Edge
  deploy is observable without credentials;
* the Mini App API signature gate - a forged ``initData`` must stay a 401;
* the P9-P14 Copilot schema, P14 claim column and table privileges (needs a Supabase access token).

Credentials are optional. A check that needs one is reported as skipped instead
of failed, so the script always ends with a readable matrix and a stable exit
code: 0 when the rollout is finished, 1 while a step is still open.

The script only reads. It never writes to Telegram, never posts to the channel
and never changes an environment.

Run it locally with:

    python tools/verify_release_v7.py
    python tools/verify_release_v7.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

PROD_BASE = "https://junior-middle-it.vercel.app"
SUPABASE_PROJECT_REF = "uucjptyqfsdzqsftgiro"
EDGE_BASE = "https://%s.supabase.co/functions/v1" % SUPABASE_PROJECT_REF
VERCEL_PROJECT = "junior-middle-it"
VERCEL_SCOPE = "olegs-projects-bfc4e11a"

# Tables and required columns of migrations P9-P14.
EXPECTED_TABLES = (
    "growth_copilot_ai_reports",
    "growth_copilot_applications",
    "growth_copilot_credit_events",
    "growth_copilot_credit_ops",
    "growth_copilot_credits",
    "growth_copilot_documents",
    "growth_copilot_enrichments",
    "growth_copilot_evaluations",
    "growth_copilot_forwarded_jobs",
    "growth_copilot_grade_audits",
    "growth_copilot_profiles",
    "growth_copilot_rate_limits",
)

HISTORICAL_CREDIT_REFS_QUERY = """SELECT ref, reason, count(*) AS events,
       min(created_at) AS first_seen, max(created_at) AS last_seen
FROM public.growth_copilot_credit_events
WHERE ref IS DISTINCT FROM ''
  AND ref !~ '^[0-9a-f]{40}$'
GROUP BY ref, reason
ORDER BY events DESC;"""

EMPTY_CREDIT_REFS_QUERY = """SELECT ref, reason, count(*) AS events,
       min(created_at) AS first_seen, max(created_at) AS last_seen
FROM public.growth_copilot_credit_events
WHERE ref = '' OR ref IS NULL
GROUP BY ref, reason
ORDER BY events DESC;"""


def check_historical_credit_refs(results, url, headers):
    """Stop cutover before P13 when journal references cannot map to job hashes."""
    for name, query in (("supabase.copilot_legacy_refs", HISTORICAL_CREDIT_REFS_QUERY),
                        ("supabase.copilot_empty_refs", EMPTY_CREDIT_REFS_QUERY)):
        status, body = http(url, method="POST", headers=headers, body=json_body({"query": query}))
        try:
            rows = json.loads(body) if status == 200 else None
            if not isinstance(rows, list):
                raise ValueError("invalid audit response")
            findings = []
            for row in rows:
                ref = row["ref"]
                events = int(row["events"])
                if events < 1:
                    raise ValueError("invalid event count")
                # Do not print arbitrary historical refs: they may contain private data.
                label = repr(ref) if ref in ("cv", "pdf", "", None) else "<unmapped reference>"
                findings.append("%s: %d events" % (label, events))
            detail = ("Pre-P13 release blocked; owner-approved mapping required: " + "; ".join(findings)) if findings else "Pre-P13 reference audit passed"
            result = FAIL if findings else PASS
        except (ValueError, TypeError, KeyError, OverflowError):
            detail = "Pre-P13 reference audit not confirmed (HTTP %s); release blocked" % status
            result = FAIL
        results.append({"name": name, "status": result, "detail": detail})


EXPECTED_P14_CLAIM_COLUMN = {
    "table_name": "growth_copilot_ai_reports", "column_name": "claim",
    "data_type": "text", "is_nullable": "NO", "column_default": "''::text",
}


def column_problems(rows):
    """Require the P14 ownership column, including its type and constraints."""
    expected = EXPECTED_P14_CLAIM_COLUMN
    row = next((row for row in rows if row.get("table_name") == expected["table_name"]
                and row.get("column_name") == expected["column_name"]), None)
    if row is None:
        return ["P14 growth_copilot_ai_reports.claim is missing"]
    return ["P14 claim %s must be %r" % (key, expected[key])
            for key in ("data_type", "is_nullable", "column_default") if row.get(key) != expected[key]]


def check_p14_column(results, url, headers):
    query = ("SELECT table_name, column_name, data_type, is_nullable, column_default "
             "FROM information_schema.columns WHERE table_schema = 'public' "
             "AND table_name = 'growth_copilot_ai_reports' AND column_name = 'claim'")
    status, body = http(url, method="POST", headers=headers, body=json_body({"query": query}))
    try:
        rows = json.loads(body) if status == 200 else None
        problems = column_problems(rows) if isinstance(rows, list) else ["P14 column query failed or returned invalid data"]
    except (ValueError, TypeError, AttributeError):
        problems = ["P14 column query returned invalid data"]
    results.append({"name": "supabase.copilot_p14_claim", "status": FAIL if problems else PASS,
                    "detail": "; ".join(problems) if problems else "P14 claim TEXT NOT NULL DEFAULT ''"})


# Feature flags of the production environment. The LLM layer stays off until a
# provider key exists, and the resume never reaches a model by default.
EXPECTED_FLAGS = {
    "COPILOT_ENABLED": "true",
    "COPILOT_PDF_ENABLED": "true",
    "COPILOT_FORWARD_ENABLED": "true",
    "COPILOT_OUTCOME_ENABLED": "true",
    "COPILOT_UPSKILL_ENABLED": "true",
    "COPILOT_WEBAPP_ENABLED": "true",
    "COPILOT_WEBAPP_URL": "https://junior-middle-it.vercel.app/copilot",
    "COPILOT_LLM_ENABLED": "false",
    "COPILOT_LLM_SEND_RESUME": "false",
}

# The statement that the Edge validator used to reject: its FROM is a scalar
# comparison, not a table reference.
VALIDATOR_PROBE = (
    "SELECT balance FROM growth_copilot_credits "
    "WHERE user_id = 1 AND free_granted_on IS DISTINCT FROM CURRENT_DATE"
)

# Version of the private SQL validator. Version 2 understands
# "IS [NOT] DISTINCT FROM" as a scalar comparison instead of a table
# reference, so the deployed Edge build is observable without credentials.
EXPECTED_VALIDATOR_VERSION = 2

# Features that have to be switched on when the rollout is finished.
REQUIRED_ROLLOUT_FLAGS = (
    "enabled",
    "pdf",
    "forward",
    "outcome",
    "upskill",
    "webapp",
)

PASS = "pass"
FAIL = "fail"
SKIP = "skip"


# --------------------------------------------------------------------------- #
# Pure checks, kept separate from the network so they can be unit tested.
# --------------------------------------------------------------------------- #
def health_problems(payload: Dict[str, object]) -> List[str]:
    """Problems of the readiness payload of the production health endpoint."""
    problems: List[str] = []
    if payload.get("durable_growth") is not True:
        problems.append("durable_growth is not true")
    if payload.get("collector_ready") is not True:
        problems.append("collector_ready is not true")
    missing = payload.get("missing_required")
    if missing:
        problems.append("missing_required: %s" % ", ".join(str(item) for item in missing))
    return problems


def html_problems(body: str) -> List[str]:
    """Problems of the delivered Mini App page."""
    problems: List[str] = []
    if "telegram-web-app.js" not in body:
        problems.append("Telegram Web App script is missing")
    if "/miniapp/app.js" not in body:
        problems.append("Mini App script is missing")
    if "Career Copilot" not in body:
        problems.append("Mini App title is missing")
    return problems


def flag_problems(flags: Dict[str, str]) -> List[str]:
    """Problems of the Copilot flags, given the visible values."""
    problems: List[str] = []
    for name, expected in EXPECTED_FLAGS.items():
        if name not in flags:
            problems.append("%s is missing" % name)
            continue
        value = flags[name]
        if value == "":
            problems.append("%s has a hidden value" % name)
            continue
        if value != expected:
            problems.append("%s is %r, expected %r" % (name, value, expected))
    return problems


def table_problems(table_rows: List[Dict[str, object]], grant_rows: List[Dict[str, object]]) -> List[str]:
    """Problems of the Copilot tables and of their privileges."""
    problems: List[str] = []
    names = {str(row.get("table_name", "")) for row in table_rows}
    missing = [name for name in EXPECTED_TABLES if name not in names]
    if missing:
        problems.append("tables missing: %s" % ", ".join(missing))
    extra = sorted(name for name in names if name not in EXPECTED_TABLES)
    if extra:
        problems.append("unexpected tables: %s" % ", ".join(extra))
    for row in table_rows:
        if row.get("rls_enabled") is not True:
            problems.append("%s has no RLS" % row.get("table_name"))
    for row in grant_rows:
        problems.append(
            "table %s grants %s to %s"
            % (row.get("table_name"), row.get("privilege_type"), row.get("grantee"))
        )
    return problems


def deployment_problems(deployment_created: Optional[int], env_created: Dict[str, int]) -> List[str]:
    """Problems of the deployment that has to carry the new environment."""
    if not deployment_created:
        return []
    problems: List[str] = []
    for name, created in sorted(env_created.items()):
        if created and created > deployment_created:
            problems.append("%s was added after the last production deployment - redeploy" % name)
    return problems


def rollout_problems(state: object) -> List[str]:
    """Problems of the published Copilot rollout state."""
    if not isinstance(state, dict):
        return ["the health payload has no copilot section"]
    problems: List[str] = []
    for name in REQUIRED_ROLLOUT_FLAGS:
        if state.get(name) is not True:
            problems.append("%s is not switched on" % name)
    if state.get("webapp_url") is not True:
        problems.append("COPILOT_WEBAPP_URL is not set")
    if state.get("llm") is True:
        problems.append("the LLM layer is switched on without a provider key")
    return problems


def validator_version_problems(payload: object) -> List[str]:
    """Problems of the validator version reported by an Edge function."""
    if not isinstance(payload, dict):
        return ["the health response is not JSON"]
    version = payload.get("validator")
    if version != EXPECTED_VALIDATOR_VERSION:
        return [
            "validator is %r, expected %r - the Edge function was not redeployed"
            % (version, EXPECTED_VALIDATOR_VERSION)
        ]
    return []


# --------------------------------------------------------------------------- #
# Network helpers
# --------------------------------------------------------------------------- #
def http(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[bytes] = None,
    timeout: int = 25,
) -> Tuple[int, str]:
    """Perform one request and return the status code and the body."""
    request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", "replace")
    except Exception as exc:  # network, DNS, TLS - all mean "unreachable"
        return 0, "%s: %s" % (type(exc).__name__, exc)


def json_body(payload: Dict[str, object]) -> bytes:
    return json.dumps(payload).encode("utf-8")


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_copilot_tables(results: List[Dict[str, str]], payload: Optional[Dict[str, object]]) -> None:
    """The table state reported by the production health endpoint."""
    state = payload.get("copilot") if payload else None
    tables = state.get("tables") if isinstance(state, dict) else None
    if tables is True:
        results.append(
            {"name": "prod.copilot_tables", "status": PASS, "detail": "the migrations are applied"}
        )
        return
    if tables is False:
        results.append(
            {
                "name": "prod.copilot_tables",
                "status": FAIL,
                "detail": "the tables of P9-P12 are missing",
            }
        )
        return
    results.append(
        {
            "name": "prod.copilot_tables",
            "status": SKIP,
            "detail": "the table state is unknown",
        }
    )


def check_copilot_validator(results: List[Dict[str, str]], payload: Optional[Dict[str, object]]) -> None:
    """Whether the deployed Edge validator accepts the credit statement."""
    state = payload.get("copilot") if payload else None
    accepted = state.get("validator_ok") if isinstance(state, dict) else None
    if accepted is True:
        results.append(
            {
                "name": "prod.copilot_validator",
                "status": PASS,
                "detail": "the credit statement is accepted",
            }
        )
        return
    if accepted is False:
        results.append(
            {
                "name": "prod.copilot_validator",
                "status": FAIL,
                "detail": "the deployed Edge validator rejects the credit statement",
            }
        )
        return
    results.append(
        {
            "name": "prod.copilot_validator",
            "status": SKIP,
            "detail": "the validator state is unknown",
        }
    )


def check_health(results: List[Dict[str, str]]) -> Optional[Dict[str, object]]:
    status, body = http(PROD_BASE + "/api/health")
    if status != 200:
        results.append({"name": "prod.health", "status": FAIL, "detail": "HTTP %s" % status})
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        results.append({"name": "prod.health", "status": FAIL, "detail": "response is not JSON"})
        return None
    problems = health_problems(payload)
    results.append(
        {
            "name": "prod.health",
            "status": FAIL if problems else PASS,
            "detail": "; ".join(problems) if problems else "ready",
        }
    )
    return payload


def check_copilot_rollout(results: List[Dict[str, str]], payload: Optional[Dict[str, object]]) -> None:
    if payload is None:
        results.append(
            {
                "name": "prod.copilot_rollout",
                "status": FAIL,
                "detail": "the health payload is unreachable",
            }
        )
        return
    problems = rollout_problems(payload.get("copilot"))
    results.append(
        {
            "name": "prod.copilot_rollout",
            "status": FAIL if problems else PASS,
            "detail": "; ".join(problems) if problems else "all features are switched on",
        }
    )


def check_validator_versions(
    results: List[Dict[str, str]], payload: Optional[Dict[str, object]] = None
) -> None:
    """The validator build of the Edge functions.

    The version marker is only a proxy: a deployment that already accepts the
    credit statement works, even if it predates the marker. The functional
    probe of the health payload therefore decides.
    """
    state = payload.get("copilot") if payload else None
    accepted = state.get("validator_ok") if isinstance(state, dict) else None
    for name, path in (
        ("edge.growth_bridge.validator", "/growth-proxy/health"),
        ("edge.interactive_bridge.validator", "/interactive-growth-proxy/health"),
    ):
        if accepted is True:
            results.append(
                {
                    "name": name,
                    "status": PASS,
                    "detail": "the deployed build accepts the credit statement",
                }
            )
            continue
        status, body = http(EDGE_BASE + path)
        if status != 200:
            results.append(
                {
                    "name": name,
                    "status": FAIL,
                    "detail": "HTTP %s - the Edge function was not redeployed" % status,
                }
            )
            continue
        try:
            edge_payload = json.loads(body)
        except ValueError:
            results.append({"name": name, "status": FAIL, "detail": "response is not JSON"})
            continue
        problems = validator_version_problems(edge_payload)
        results.append(
            {
                "name": name,
                "status": FAIL if problems else PASS,
                "detail": "; ".join(problems) if problems else "validator %s" % edge_payload.get("validator"),
            }
        )


def check_miniapp(results: List[Dict[str, str]]) -> None:
    status, body = http(PROD_BASE + "/copilot")
    if status != 200:
        results.append({"name": "prod.miniapp.page", "status": FAIL, "detail": "HTTP %s" % status})
    else:
        problems = html_problems(body)
        results.append(
            {
                "name": "prod.miniapp.page",
                "status": FAIL if problems else PASS,
                "detail": "; ".join(problems) if problems else "%d bytes" % len(body),
            }
        )
    status, body = http(PROD_BASE + "/miniapp/app.js")
    if status != 200 or len(body.strip()) < 500:
        results.append({"name": "prod.miniapp.script", "status": FAIL, "detail": "HTTP %s" % status})
    else:
        results.append(
            {"name": "prod.miniapp.script", "status": PASS, "detail": "%d bytes" % len(body)}
        )


def check_signature_gate(results: List[Dict[str, str]]) -> None:
    status, body = http(
        PROD_BASE + "/api/copilot",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=json_body({"init_data": "forged"}),
    )
    if status == 401:
        results.append({"name": "prod.api.signature_gate", "status": PASS, "detail": "401 as expected"})
        return
    detail = "HTTP %s" % status
    if status == 200:
        detail = "forged initData was accepted"
    results.append({"name": "prod.api.signature_gate", "status": FAIL, "detail": detail})


def check_edge_health(results: List[Dict[str, str]]) -> None:
    status, body = http(EDGE_BASE + "/growth-proxy/health")
    if status != 200:
        results.append({"name": "edge.growth_bridge.health", "status": FAIL, "detail": "HTTP %s" % status})
        return
    try:
        payload = json.loads(body)
    except ValueError:
        results.append({"name": "edge.growth_bridge.health", "status": FAIL, "detail": "response is not JSON"})
        return
    problems = []
    if payload.get("ok") is not True:
        problems.append("ok is not true")
    if payload.get("render_bridge") is not True:
        problems.append("render_bridge is not true")
    results.append(
        {
            "name": "edge.growth_bridge.health",
            "status": FAIL if problems else PASS,
            "detail": "; ".join(problems) if problems else "ok",
        }
    )


def check_interactive_bridge(results: List[Dict[str, str]]) -> None:
    status, _ = http(
        EDGE_BASE + "/interactive-growth-proxy",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=json_body({"query": "SELECT 1 FROM growth_copilot_credits", "params": []}),
    )
    if status == 401:
        results.append(
            {"name": "edge.interactive_bridge.auth", "status": PASS, "detail": "401 without a token"}
        )
        return
    results.append(
        {
            "name": "edge.interactive_bridge.auth",
            "status": FAIL,
            "detail": "HTTP %s without a bot token" % status,
        }
    )


def check_validator_fix(results: List[Dict[str, str]]) -> None:
    key = str(os.getenv("GROWTH_BRIDGE_KEY") or "").strip()
    if not key:
        results.append(
            {
                "name": "edge.validator.distinct_from",
                "status": SKIP,
                "detail": "GROWTH_BRIDGE_KEY is not set",
            }
        )
        return
    status, body = http(
        EDGE_BASE + "/growth-proxy",
        method="POST",
        headers={"Content-Type": "application/json", "x-growth-key": key},
        body=json_body({"query": VALIDATOR_PROBE, "params": []}),
    )
    if status == 200:
        results.append({"name": "edge.validator.distinct_from", "status": PASS, "detail": "query accepted"})
        return
    detail = "HTTP %s" % status
    if status == 400 and "rejected" in body:
        detail = "query rejected - the validator fix is not deployed"
    elif status == 503:
        detail = "query failed - the Copilot tables are probably not applied yet"
    results.append({"name": "edge.validator.distinct_from", "status": FAIL, "detail": detail})


def check_tables(results: List[Dict[str, str]]) -> None:
    token = str(os.getenv("SUPABASE_ACCESS_TOKEN") or "").strip()
    if not token:
        results.append(
            {"name": "supabase.copilot_tables", "status": SKIP, "detail": "SUPABASE_ACCESS_TOKEN is not set"}
        )
        for name in ("supabase.copilot_legacy_refs", "supabase.copilot_empty_refs"):
            results.append({"name": name, "status": FAIL,
                            "detail": "Pre-P13 reference audit requires SUPABASE_ACCESS_TOKEN; release blocked"})
        return
    url = "https://api.supabase.com/v1/projects/%s/database/query" % SUPABASE_PROJECT_REF
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    check_historical_credit_refs(results, url, headers)
    check_p14_column(results, url, headers)
    table_query = (
        "SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname LIKE 'growth_copilot_%' "
        "ORDER BY c.relname"
    )
    grant_query = (
        "SELECT table_name, grantee, privilege_type "
        "FROM information_schema.role_table_grants "
        "WHERE table_schema = 'public' AND table_name LIKE 'growth_copilot_%' "
        "AND grantee IN ('anon', 'authenticated')"
    )
    status, body = http(url, method="POST", headers=headers, body=json_body({"query": table_query}))
    if status != 200:
        results.append(
            {"name": "supabase.copilot_tables", "status": FAIL, "detail": "query failed with HTTP %s" % status}
        )
        return
    try:
        table_rows = json.loads(body)
    except ValueError:
        results.append({"name": "supabase.copilot_tables", "status": FAIL, "detail": "response is not JSON"})
        return
    status, body = http(url, method="POST", headers=headers, body=json_body({"query": grant_query}))
    if status != 200:
        results.append(
            {"name": "supabase.copilot_tables", "status": FAIL, "detail": "grant query failed with HTTP %s" % status}
        )
        return
    try:
        grant_rows = json.loads(body)
    except ValueError:
        grant_rows = []
    problems = table_problems(table_rows, grant_rows)
    results.append(
        {
            "name": "supabase.copilot_tables",
            "status": FAIL if problems else PASS,
            "detail": "; ".join(problems) if problems else "%d tables, RLS on" % len(table_rows),
        }
    )


def _vercel_team_id(token: str) -> str:
    status, body = http("https://api.vercel.com/v2/teams", headers={"Authorization": "Bearer " + token})
    if status != 200:
        return ""
    try:
        teams = json.loads(body)
    except ValueError:
        return ""
    if isinstance(teams, dict):
        teams = teams.get("teams") or []
    for team in teams:
        if isinstance(team, dict) and str(team.get("slug", "")) == VERCEL_SCOPE:
            return str(team.get("id", ""))
    if len(teams) == 1 and isinstance(teams[0], dict):
        return str(teams[0].get("id", ""))
    return ""


def check_vercel_flags(results: List[Dict[str, str]]) -> None:
    token = str(os.getenv("VERCEL_TOKEN") or "").strip()
    if not token:
        results.append(
            {"name": "vercel.production_flags", "status": SKIP, "detail": "VERCEL_TOKEN is not set"}
        )
        return
    headers = {"Authorization": "Bearer " + token}
    team_id = _vercel_team_id(token)
    query = ("?teamId=" + urllib.parse.quote(team_id)) if team_id else ""
    status, body = http(
        "https://api.vercel.com/v9/projects/%s/env%s" % (VERCEL_PROJECT, query), headers=headers
    )
    if status == 404:
        results.append(
            {
                "name": "vercel.production_flags",
                "status": FAIL,
                "detail": "project %s is not visible for this token" % VERCEL_PROJECT,
            }
        )
        return
    if status != 200:
        results.append(
            {"name": "vercel.production_flags", "status": FAIL, "detail": "env list failed with HTTP %s" % status}
        )
        return
    try:
        entries = json.loads(body)
    except ValueError:
        results.append({"name": "vercel.production_flags", "status": FAIL, "detail": "response is not JSON"})
        return
    if isinstance(entries, dict):
        entries = entries.get("envs") or entries.get("env") or []
    flags: Dict[str, str] = {}
    env_created: Dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        targets = entry.get("target") or []
        targets = [targets] if isinstance(targets, str) else list(targets)
        if "production" not in targets:
            continue
        key = str(entry.get("key", ""))
        if key not in EXPECTED_FLAGS:
            continue
        flags[key] = str(entry.get("value", "") or "")
        created = entry.get("created") or entry.get("createdAt")
        try:
            env_created[key] = int(created)
        except (TypeError, ValueError):
            env_created[key] = 0

    problems = flag_problems(flags)

    deployment_created = 0
    status, body = http(
        "https://api.vercel.com/v6/deployments?projectId=%s&target=production&limit=5%s"
        % (urllib.parse.quote(VERCEL_PROJECT), query),
        headers=headers,
    )
    if status == 200:
        try:
            deployments = json.loads(body)
        except ValueError:
            deployments = []
        if isinstance(deployments, dict):
            deployments = deployments.get("deployments") or []
        for deployment in deployments:
            if not isinstance(deployment, dict):
                continue
            try:
                created = int(deployment.get("created") or 0)
            except (TypeError, ValueError):
                created = 0
            deployment_created = max(deployment_created, created)
        problems.extend(deployment_problems(deployment_created or None, env_created))
    results.append(
        {
            "name": "vercel.production_flags",
            "status": FAIL if problems else PASS,
            "detail": "; ".join(problems) if problems else "%d flags in production" % len(flags),
        }
    )


def run_checks() -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    payload = check_health(results)
    check_copilot_rollout(results, payload)
    check_copilot_tables(results, payload)
    check_copilot_validator(results, payload)
    check_miniapp(results)
    check_signature_gate(results)
    check_edge_health(results)
    check_validator_versions(results, payload)
    check_interactive_bridge(results)
    check_validator_fix(results)
    check_tables(results)
    check_vercel_flags(results)
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the v7.0 Copilot rollout.")
    parser.add_argument("--json", action="store_true", help="print the raw result matrix")
    args = parser.parse_args(argv)

    results = run_checks()
    failed = [item for item in results if item["status"] == FAIL]
    skipped = [item for item in results if item["status"] == SKIP]

    if args.json:
        print(json.dumps({"results": results, "failed": len(failed), "skipped": len(skipped)}, indent=2))
        return 1 if failed else 0

    width = max(len(item["name"]) for item in results) if results else 10
    for item in results:
        marker = {PASS: "OK  ", FAIL: "FAIL", SKIP: "SKIP"}[item["status"]]
        print("%s  %-*s  %s" % (marker, width, item["name"], item["detail"]))
    print("-" * (width + 8))
    print("checks: %d, failed: %d, skipped: %d" % (len(results), len(failed), len(skipped)))
    if skipped:
        print("skipped checks need: SUPABASE_ACCESS_TOKEN, VERCEL_TOKEN or GROWTH_BRIDGE_KEY")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
