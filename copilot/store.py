"""Single access point for the Copilot tables behind the durable growth transport.

Every statement here is a single parameterized query against a ``growth_*``
relation, which is exactly what the Edge growth bridge accepts. The module
never talks to Telegram and never imports the bot runtime.
"""
from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

logger = logging.getLogger(__name__)

PROFILES_TABLE = "growth_copilot_profiles"
EVALUATIONS_TABLE = "growth_copilot_evaluations"
DOCUMENTS_TABLE = "growth_copilot_documents"
CREDITS_TABLE = "growth_copilot_credits"
CREDIT_EVENTS_TABLE = "growth_copilot_credit_events"
GRADE_AUDITS_TABLE = "growth_copilot_grade_audits"
ENRICHMENTS_TABLE = "growth_copilot_enrichments"
AI_REPORTS_TABLE = "growth_copilot_ai_reports"
FORWARDED_JOBS_TABLE = "growth_copilot_forwarded_jobs"
APPLICATIONS_TABLE = "growth_copilot_applications"
RATE_LIMITS_TABLE = "growth_copilot_rate_limits"

EVALUATION_RETENTION_DAYS = 90
DOCUMENT_RETENTION_DAYS = 90
GRADE_AUDIT_RETENTION_DAYS = 45
ENRICHMENT_RETENTION_DAYS = 14
AI_REPORT_RETENTION_DAYS = 14
FORWARDED_JOB_RETENTION_DAYS = 30
APPLICATION_RETENTION_DAYS = 180

# The rate limit table keeps one row per scope. The rows of the users belong
# to the request limiter of the Mini App; the row of scope 0 is the counter
# of the global daily budget of the model calls. The two never share a row,
# so the request limiter cannot reset the budget and the budget cannot block
# a user.
LLM_BUDGET_SCOPE = 0


class CopilotStoreUnavailable(RuntimeError):
    """Raised when the durable growth backend cannot serve a Copilot query."""


class CopilotStore:
    """Typed access to the Copilot tables of the durable growth backend."""

    def __init__(self, growth_store):
        if growth_store is None:
            raise CopilotStoreUnavailable("Copilot store requires a durable growth backend")
        self._store = growth_store

    @classmethod
    def from_database(cls, db) -> Optional["CopilotStore"]:
        """Build a store from a bot database object, or ``None`` without Postgres."""
        growth_store = getattr(db, "_growth_store", None)
        if growth_store is None:
            return None
        return cls(growth_store)

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        try:
            cursor = self._store._ensure_conn().cursor()
        except Exception as exc:
            raise CopilotStoreUnavailable(
                f"Growth backend unavailable: {type(exc).__name__}"
            ) from exc
        with cursor:
            yield cursor

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------
    def save_profile(self, user_id: int, *, grade: str, skills: str, source: str) -> bool:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_copilot_profiles (user_id, grade, skills, source, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    grade = EXCLUDED.grade,
                    skills = EXCLUDED.skills,
                    source = EXCLUDED.source,
                    updated_at = NOW()
                RETURNING user_id
                """,
                (int(user_id), str(grade or ""), str(skills or ""), str(source or "")),
            )
            return bool(cur.fetchone())

    def get_profile(self, user_id: int) -> Optional[Dict]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT grade, skills, source, updated_at
                FROM growth_copilot_profiles
                WHERE user_id = %s
                """,
                (int(user_id),),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "grade": str(row[0] or ""),
            "skills": str(row[1] or ""),
            "source": str(row[2] or ""),
            "updated_at": row[3],
        }

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------
    def save_evaluation(
        self,
        user_id: int,
        job_id: str,
        *,
        fit_score: int,
        stack_match_score: int = 0,
        keyword_overlap_pct: int = 0,
        matched_keywords: Optional[List[str]] = None,
        missing_skills: Optional[List[str]] = None,
        llm_used: bool = False,
        model: str = "",
    ) -> bool:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_copilot_evaluations
                    (user_id, job_id, fit_score, stack_match_score, keyword_overlap_pct,
                     matched_keywords, missing_skills, llm_used, model, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (user_id, job_id) DO UPDATE SET
                    fit_score = EXCLUDED.fit_score,
                    stack_match_score = EXCLUDED.stack_match_score,
                    keyword_overlap_pct = EXCLUDED.keyword_overlap_pct,
                    matched_keywords = EXCLUDED.matched_keywords,
                    missing_skills = EXCLUDED.missing_skills,
                    llm_used = EXCLUDED.llm_used,
                    model = EXCLUDED.model,
                    updated_at = NOW()
                RETURNING fit_score
                """,
                (
                    int(user_id),
                    str(job_id),
                    int(fit_score),
                    int(stack_match_score),
                    int(keyword_overlap_pct),
                    json.dumps(list(matched_keywords or []), ensure_ascii=False),
                    json.dumps(list(missing_skills or []), ensure_ascii=False),
                    bool(llm_used),
                    str(model or ""),
                ),
            )
            return bool(cur.fetchone())

    def get_evaluation(self, user_id: int, job_id: str) -> Optional[Dict]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT fit_score, stack_match_score, keyword_overlap_pct,
                       matched_keywords, missing_skills, llm_used, model, created_at
                FROM growth_copilot_evaluations
                WHERE user_id = %s AND job_id = %s
                """,
                (int(user_id), str(job_id)),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "fit_score": int(row[0] or 0),
            "stack_match_score": int(row[1] or 0),
            "keyword_overlap_pct": int(row[2] or 0),
            "matched_keywords": _decode_list(row[3]),
            "missing_skills": _decode_list(row[4]),
            "llm_used": bool(row[5]),
            "model": str(row[6] or ""),
            "created_at": row[7],
        }

    # ------------------------------------------------------------------
    # Grade audit
    # ------------------------------------------------------------------
    def save_grade_audit(self, job_hash: str, audit) -> bool:
        payload = audit.as_dict() if hasattr(audit, "as_dict") else dict(audit or {})
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_copilot_grade_audits
                    (job_hash, declared_grade, real_grade, realism_score, is_fake_grade,
                     badge_text, signals, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (job_hash) DO UPDATE SET
                    declared_grade = EXCLUDED.declared_grade,
                    real_grade = EXCLUDED.real_grade,
                    realism_score = EXCLUDED.realism_score,
                    is_fake_grade = EXCLUDED.is_fake_grade,
                    badge_text = EXCLUDED.badge_text,
                    signals = EXCLUDED.signals,
                    updated_at = NOW()
                RETURNING job_hash
                """,
                (
                    str(job_hash),
                    str(payload.get("declared_grade") or ""),
                    str(payload.get("real_grade") or ""),
                    int(payload.get("realism_score") or 0),
                    bool(payload.get("is_fake_grade")),
                    str(payload.get("badge_text") or ""),
                    json.dumps(list(payload.get("signals") or []), ensure_ascii=False),
                ),
            )
            return bool(cur.fetchone())

    def get_grade_audit(self, job_hash: str) -> Optional[Dict]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT declared_grade, real_grade, realism_score, is_fake_grade,
                       badge_text, signals, created_at
                FROM growth_copilot_grade_audits
                WHERE job_hash = %s
                """,
                (str(job_hash),),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "declared_grade": str(row[0] or ""),
            "real_grade": str(row[1] or ""),
            "realism_score": int(row[2] or 0),
            "is_fake_grade": bool(row[3]),
            "badge_text": str(row[4] or ""),
            "signals": _decode_list(row[5]),
            "created_at": row[6],
        }

    # ------------------------------------------------------------------
    # Enrichment cache
    # ------------------------------------------------------------------
    def save_enrichment(self, job_hash: str, source_url: str, text: str) -> bool:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_copilot_enrichments (job_hash, source_url, text, fetched_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (job_hash) DO UPDATE SET
                    source_url = EXCLUDED.source_url,
                    text = EXCLUDED.text,
                    fetched_at = NOW()
                RETURNING job_hash
                """,
                (str(job_hash), str(source_url or ""), str(text or "")),
            )
            return bool(cur.fetchone())

    def get_enrichment(self, job_hash: str) -> Optional[Dict]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT source_url, text, fetched_at
                FROM growth_copilot_enrichments
                WHERE job_hash = %s
                """,
                (str(job_hash),),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {"source_url": str(row[0] or ""), "text": str(row[1] or ""), "fetched_at": row[2]}

    # ------------------------------------------------------------------
    # Generated document cache
    # ------------------------------------------------------------------
    def save_document(
        self,
        user_id: int,
        job_id: str,
        *,
        telegram_file_id: str,
        filename: str,
        fit_score: int = 0,
        claim: str,
    ) -> bool:
        """Remember the generated CV so a repeated tap sends the cached file.

        The claim token of the owner is required, so a build that lost the
        slot cannot overwrite a file that was delivered in the meantime.
        """
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_copilot_documents
                    (user_id, job_id, telegram_file_id, filename, fit_score, created_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (user_id, job_id) DO UPDATE SET
                    telegram_file_id = EXCLUDED.telegram_file_id,
                    filename = EXCLUDED.filename,
                    fit_score = EXCLUDED.fit_score,
                    created_at = NOW()
                WHERE growth_copilot_documents.telegram_file_id = ''
                  AND growth_copilot_documents.filename = %s
                RETURNING job_id
                """,
                (
                    int(user_id),
                    str(job_id),
                    str(telegram_file_id or ""),
                    str(filename or ""),
                    int(fit_score),
                    str(claim),
                ),
            )
            return bool(cur.fetchone())

    def get_document(self, user_id: int, job_id: str) -> Optional[Dict]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT telegram_file_id, filename, fit_score, created_at
                FROM growth_copilot_documents
                WHERE user_id = %s AND job_id = %s
                """,
                (int(user_id), str(job_id)),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "telegram_file_id": str(row[0] or ""),
            "filename": str(row[1] or ""),
            "fit_score": int(row[2] or 0),
            "created_at": row[3],
        }

    # ------------------------------------------------------------------
    # AI report cache
    # ------------------------------------------------------------------
    def claim_ai_report(
        self, user_id: int, job_id: str, *, stale_after_minutes: int = 15
    ) -> Optional[str]:
        """Claim the analysis slot of one (user, job) pair.

        The slot is the unique key of the report cache, so a second tap on
        the same button cannot start a second analysis in parallel. Without
        the claim two parallel taps would call the model twice, burn two
        slots of the global budget and could charge the user twice for one
        vacancy.

        A stored report may always be taken over, because it is the report of
        an older attempt. A claim without a report is taken over only when it
        is older than ``stale_after_minutes``, so an analysis that died does
        not block the user forever.

        The returned token identifies the owner of the claim. Only the owner
        may write the answer or release the slot, so a late answer cannot
        overwrite the report of a newer attempt. The token lives in the model
        column of the claim row, so no column has to be added to the table.
        """
        token = "claim:%s" % uuid.uuid4().hex
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_copilot_ai_reports
                    (user_id, job_id, model, answer, used_resume, created_at, updated_at)
                VALUES (%s, %s, %s, '', %s, NOW(), NOW())
                ON CONFLICT (user_id, job_id) DO UPDATE SET
                    model = EXCLUDED.model,
                    answer = '',
                    used_resume = EXCLUDED.used_resume,
                    updated_at = NOW()
                WHERE growth_copilot_ai_reports.answer <> ''
                   OR growth_copilot_ai_reports.updated_at
                      < NOW() - (%s * INTERVAL '1 minute')
                RETURNING job_id
                """,
                (
                    int(user_id),
                    str(job_id),
                    token,
                    False,
                    int(stale_after_minutes),
                ),
            )
            row = cur.fetchone()
        return token if row else None

    def release_ai_report_claim(self, user_id: int, job_id: str, claim: str) -> bool:
        """Release a claim that produced no answer.

        Only the owner of the claim releases the slot, and only while the
        claim still holds no answer, so a late release cannot delete the
        report of a newer attempt.
        """
        with self._cursor() as cur:
            cur.execute(
                """
                DELETE FROM growth_copilot_ai_reports
                WHERE user_id = %s AND job_id = %s
                  AND model = %s AND answer = ''
                RETURNING job_id
                """,
                (int(user_id), str(job_id), str(claim)),
            )
            return bool(cur.fetchone())

    def save_ai_report(
        self,
        user_id: int,
        job_id: str,
        *,
        claim: str,
        model: str = "",
        answer: str = "",
        used_resume: bool = False,
    ) -> bool:
        """Remember an AI answer so a repeated tap does not pay twice.

        The answer text is only stored when it was produced without the resume:
        an answer that was built from a resume may echo personal data, and the
        privacy invariant forbids keeping resume content in the database.

        Only the owner of the claim may write the answer: a late answer of an
        attempt whose claim was taken over is refused, so it cannot overwrite
        the report of the newer attempt.
        """
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_copilot_ai_reports
                    (user_id, job_id, model, answer, used_resume, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (user_id, job_id) DO UPDATE SET
                    model = EXCLUDED.model,
                    answer = EXCLUDED.answer,
                    used_resume = EXCLUDED.used_resume,
                    created_at = NOW(),
                    updated_at = NOW()
                WHERE growth_copilot_ai_reports.model = %s
                RETURNING job_id
                """,
                (
                    int(user_id),
                    str(job_id),
                    str(model or ""),
                    str(answer or ""),
                    bool(used_resume),
                    str(claim),
                ),
            )
            return bool(cur.fetchone())

    def get_ai_report(self, user_id: int, job_id: str) -> Optional[Dict]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT model, answer, used_resume, created_at
                FROM growth_copilot_ai_reports
                WHERE user_id = %s AND job_id = %s
                """,
                (int(user_id), str(job_id)),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "model": str(row[0] or ""),
            "answer": str(row[1] or ""),
            "used_resume": bool(row[2]),
            "created_at": row[3],
        }

    # ------------------------------------------------------------------
    # Forwarded vacancies
    # ------------------------------------------------------------------
    def save_forwarded_job(
        self,
        job_hash: str,
        *,
        title: str = "",
        company: str = "",
        url: str = "",
        text: str = "",
    ) -> bool:
        """Remember a vacancy the user forwarded into the private chat.

        The text is a public vacancy posting, never a resume: the forward
        handler refuses resume-like texts before they reach this method.
        """
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_copilot_forwarded_jobs
                    (job_hash, title, company, url, text, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (job_hash) DO UPDATE SET
                    title = EXCLUDED.title,
                    company = EXCLUDED.company,
                    url = EXCLUDED.url,
                    text = EXCLUDED.text,
                    updated_at = NOW()
                RETURNING job_hash
                """,
                (
                    str(job_hash),
                    str(title or ""),
                    str(company or ""),
                    str(url or ""),
                    str(text or ""),
                ),
            )
            return bool(cur.fetchone())

    def get_forwarded_job(self, job_hash: str) -> Optional[Dict]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT job_hash, title, company, url, text, created_at
                FROM growth_copilot_forwarded_jobs
                WHERE job_hash = %s
                """,
                (str(job_hash),),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "job_hash": str(row[0] or ""),
            "title": str(row[1] or ""),
            "company": str(row[2] or ""),
            "url": str(row[3] or ""),
            "text": str(row[4] or ""),
            "created_at": row[5],
        }

    # ------------------------------------------------------------------
    # Application tracker
    # ------------------------------------------------------------------
    def save_application(
        self,
        user_id: int,
        job_id: str,
        *,
        company: str = "",
        title: str = "",
    ) -> bool:
        """Mark that the user sent an application; a repeat tap restarts it."""
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_copilot_applications
                    (user_id, job_id, company, title, stage, sent_at,
                     reminded_at, answered_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, 'sent', NOW(), NULL, NULL, NOW(), NOW())
                ON CONFLICT (user_id, job_id) DO UPDATE SET
                    company = EXCLUDED.company,
                    title = EXCLUDED.title,
                    stage = 'sent',
                    sent_at = NOW(),
                    reminded_at = NULL,
                    answered_at = NULL,
                    updated_at = NOW()
                RETURNING job_id
                """,
                (
                    int(user_id),
                    str(job_id),
                    str(company or ""),
                    str(title or ""),
                ),
            )
            return bool(cur.fetchone())

    def get_application(self, user_id: int, job_id: str) -> Optional[Dict]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT job_id, company, title, stage, sent_at, reminded_at, answered_at
                FROM growth_copilot_applications
                WHERE user_id = %s AND job_id = %s
                """,
                (int(user_id), str(job_id)),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "job_id": str(row[0] or ""),
            "company": str(row[1] or ""),
            "title": str(row[2] or ""),
            "stage": str(row[3] or ""),
            "sent_at": row[4],
            "reminded_at": row[5],
            "answered_at": row[6],
        }

    def list_applications_due(self, *, days: int = 7, limit: int = 100) -> List[Dict]:
        """Applications that are old enough to ask about, and not asked yet."""
        wait = max(1, min(int(days), 90))
        rows_limit = max(1, min(int(limit), 500))
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT user_id, job_id, company, title, stage, sent_at
                FROM growth_copilot_applications
                WHERE stage = 'sent'
                  AND reminded_at IS NULL
                  AND sent_at <= NOW() - (%s * INTERVAL '1 day')
                ORDER BY sent_at ASC
                LIMIT %s
                """,
                (wait, rows_limit),
            )
            rows = cur.fetchall()
        out: List[Dict] = []
        for row in rows or []:
            out.append(
                {
                    "user_id": int(row[0] or 0),
                    "job_id": str(row[1] or ""),
                    "company": str(row[2] or ""),
                    "title": str(row[3] or ""),
                    "stage": str(row[4] or ""),
                    "sent_at": row[5],
                }
            )
        return out

    def mark_application_reminded(self, user_id: int, job_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE growth_copilot_applications
                SET reminded_at = NOW(), updated_at = NOW()
                WHERE user_id = %s AND job_id = %s
                RETURNING job_id
                """,
                (int(user_id), str(job_id)),
            )
            return bool(cur.fetchone())

    def set_application_stage(self, user_id: int, job_id: str, stage: str) -> bool:
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE growth_copilot_applications
                SET stage = %s, answered_at = NOW(), updated_at = NOW()
                WHERE user_id = %s AND job_id = %s
                RETURNING stage
                """,
                (str(stage or ""), int(user_id), str(job_id)),
            )
            return bool(cur.fetchone())

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------
    def bump_rate_limit(
        self,
        user_id: int,
        *,
        limit: int = 10,
        window_minutes: int = 10,
    ) -> Tuple[bool, int]:
        """Count one request and report whether the caller stays in the limit.

        The window is a fixed one: the counter restarts as soon as the stored
        window start is older than the window itself.
        """
        minutes = max(1, min(int(window_minutes), 120))
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_copilot_rate_limits
                    (user_id, window_start, hits, updated_at)
                VALUES (%s, NOW(), 1, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    hits = CASE
                        WHEN growth_copilot_rate_limits.window_start
                             > NOW() - (%s * INTERVAL '1 minute')
                        THEN growth_copilot_rate_limits.hits + 1
                        ELSE 1
                    END,
                    window_start = CASE
                        WHEN growth_copilot_rate_limits.window_start
                             > NOW() - (%s * INTERVAL '1 minute')
                        THEN growth_copilot_rate_limits.window_start
                        ELSE NOW()
                    END,
                    updated_at = NOW()
                RETURNING hits
                """,
                (int(user_id), minutes, minutes),
            )
            row = cur.fetchone()
        hits = int(row[0] or 0) if row else 0
        return hits <= int(limit), hits

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------
    def count_events_by_name(self, names: Iterable[str], *, days: int = 30) -> Dict[str, int]:
        """Count the growth events of the given names over one window."""
        wanted = [str(name) for name in (names or []) if str(name).strip()]
        if not wanted:
            return {}
        window = max(1, min(int(days), 90))
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT name, COUNT(*) FROM growth_events
                WHERE name = ANY(%s) AND ts >= NOW() - (%s * INTERVAL '1 day')
                GROUP BY name
                """,
                (wanted, window),
            )
            rows = cur.fetchall()
        return {str(row[0]): int(row[1] or 0) for row in rows or []}

    # ------------------------------------------------------------------
    # Credits
    # ------------------------------------------------------------------
    def ensure_credit_row(self, user_id: int) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_copilot_credits (user_id, balance, updated_at)
                VALUES (%s, 0, NOW())
                ON CONFLICT (user_id) DO NOTHING
                """,
                (int(user_id),),
            )

    def get_balance(self, user_id: int) -> int:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT balance FROM growth_copilot_credits WHERE user_id = %s
                """,
                (int(user_id),),
            )
            row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def grant_daily_allowance(self, user_id: int, amount: int) -> Optional[int]:
        """Add the daily free allowance exactly once per calendar day."""
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE growth_copilot_credits
                SET balance = balance + %s,
                    free_granted_on = CURRENT_DATE,
                    updated_at = NOW()
                WHERE user_id = %s
                  AND free_granted_on IS DISTINCT FROM CURRENT_DATE
                RETURNING balance
                """,
                (int(amount), int(user_id)),
            )
            row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def reserve_llm_attempt(
        self, user_id: int, budget: int, reason: str, ref: str = ""
    ) -> bool:
        """Take one slot of the global daily model budget.

        The budget is a counter in a row of its own, and the check and the
        increment happen in one statement. The lock of that row serializes
        parallel callers, so two requests that arrive at the same moment
        cannot both take the last slot. A plain ``COUNT(*)`` of the journal
        would be racy: a parallel transaction cannot see the event of the
        other one yet, so both of them would count the same number of calls.

        The attempt is written before the call, because a call that fails
        still costs the operator.
        """
        limit = int(budget)
        if limit <= 0:
            return False
        with self._cursor() as cur:
            cur.execute(
                """
                WITH slot AS (
                    INSERT INTO growth_copilot_rate_limits
                        (user_id, window_start, hits, updated_at)
                    VALUES (%s, CURRENT_DATE, 1, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        hits = CASE
                            WHEN growth_copilot_rate_limits.window_start < CURRENT_DATE
                            THEN 1
                            ELSE growth_copilot_rate_limits.hits + 1
                        END,
                        window_start = CASE
                            WHEN growth_copilot_rate_limits.window_start < CURRENT_DATE
                            THEN CURRENT_DATE
                            ELSE growth_copilot_rate_limits.window_start
                        END,
                        updated_at = NOW()
                    WHERE growth_copilot_rate_limits.window_start < CURRENT_DATE
                       OR growth_copilot_rate_limits.hits < %s
                    RETURNING hits
                )
                INSERT INTO growth_copilot_credit_events (user_id, delta, reason, ref, created_at)
                SELECT %s, 0, %s, %s, NOW() FROM slot
                RETURNING id
                """,
                (
                    LLM_BUDGET_SCOPE,
                    limit,
                    int(user_id),
                    str(reason or ""),
                    str(ref or ""),
                ),
            )
            return bool(cur.fetchone())

    def grant_credits_with_event(
        self, user_id: int, amount: int, reason: str, ref: str = ""
    ) -> bool:
        """Credit the balance and log the credit in one statement.

        The balance and the journal entry are written together, so a failed
        write can no longer leave a credited balance without a journal entry.
        """
        with self._cursor() as cur:
            cur.execute(
                """
                WITH credit AS (
                    UPDATE growth_copilot_credits
                    SET balance = balance + %s, updated_at = NOW()
                    WHERE user_id = %s
                    RETURNING balance
                )
                INSERT INTO growth_copilot_credit_events (user_id, delta, reason, ref, created_at)
                SELECT %s, %s, %s, %s, NOW() FROM credit
                RETURNING id
                """,
                (
                    int(amount),
                    int(user_id),
                    int(user_id),
                    int(amount),
                    str(reason or ""),
                    str(ref or ""),
                ),
            )
            return bool(cur.fetchone())

    def grant_credits(self, user_id: int, amount: int) -> Optional[int]:
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE growth_copilot_credits
                SET balance = balance + %s,
                    updated_at = NOW()
                WHERE user_id = %s
                RETURNING balance
                """,
                (int(amount), int(user_id)),
            )
            row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def spend_credits(self, user_id: int, amount: int) -> Optional[int]:
        """Atomically spend credits; ``None`` means the balance was too low."""
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE growth_copilot_credits
                SET balance = balance - %s,
                    updated_at = NOW()
                WHERE user_id = %s
                  AND balance >= %s
                RETURNING balance
                """,
                (int(amount), int(user_id), int(amount)),
            )
            row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def log_credit_event(self, user_id: int, delta: int, reason: str, ref: str = "") -> bool:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_copilot_credit_events (user_id, delta, reason, ref, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (int(user_id), int(delta), str(reason or ""), str(ref or "")),
            )
            return bool(cur.fetchone())

    def count_credit_events(self, reason: str, today_only: bool = False) -> int:
        if today_only:
            statement = """
                SELECT COUNT(*) FROM growth_copilot_credit_events
                WHERE reason = %s AND created_at >= CURRENT_DATE
            """
        else:
            statement = """
                SELECT COUNT(*) FROM growth_copilot_credit_events WHERE reason = %s
            """
        with self._cursor() as cur:
            cur.execute(statement, (str(reason),))
            row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    # ------------------------------------------------------------------
    # Costed operations
    # ------------------------------------------------------------------
    # The balance and the journal are always written by one statement, so they
    # can never drift apart. A single statement is also the only shape the Edge
    # growth bridge accepts: it forwards exactly one query per request, so a
    # multi statement transaction is not available over the bridge.
    def reserve_credit(
        self,
        user_id: int,
        amount: int,
        reason: str,
        ref: str,
        *,
        release_reason: str = "spend_release",
    ) -> bool:
        """Debit the balance and log the reservation in one statement.

        ``False`` means the balance was too low or the operation is already
        paid for. The debit only happens when no reservation of this operation
        is unmatched, so a repeated request cannot charge the user twice, and
        a request after a refund is charged again as a new attempt.
        """
        with self._cursor() as cur:
            cur.execute(
                """
                WITH debit AS (
                    UPDATE growth_copilot_credits
                    SET balance = balance - %s, updated_at = NOW()
                    WHERE user_id = %s
                      AND balance >= %s
                      AND (
                          (SELECT COUNT(*) FROM growth_copilot_credit_events
                            WHERE user_id = %s AND ref = %s AND reason = %s)
                          <=
                          (SELECT COUNT(*) FROM growth_copilot_credit_events
                            WHERE user_id = %s AND ref = %s AND reason = %s)
                      )
                    RETURNING balance
                )
                INSERT INTO growth_copilot_credit_events (user_id, delta, reason, ref, created_at)
                SELECT %s, %s, %s, %s, NOW() FROM debit
                RETURNING id
                """,
                (
                    int(amount),
                    int(user_id),
                    int(amount),
                    int(user_id),
                    str(ref or ""),
                    str(reason or ""),
                    int(user_id),
                    str(ref or ""),
                    str(release_reason or ""),
                    int(user_id),
                    -int(amount),
                    str(reason or ""),
                    str(ref or ""),
                ),
            )
            return bool(cur.fetchone())

    def release_credit(
        self,
        user_id: int,
        amount: int,
        reason: str,
        ref: str,
        *,
        reserved_reason: str,
    ) -> bool:
        """Give a reserved credit back and log the release in one statement.

        The credit is only returned when a reservation of this operation is
        unmatched, so a repeated release cannot credit the user twice.
        """
        with self._cursor() as cur:
            cur.execute(
                """
                WITH refund AS (
                    UPDATE growth_copilot_credits
                    SET balance = balance + %s, updated_at = NOW()
                    WHERE user_id = %s
                      AND (
                          (SELECT COUNT(*) FROM growth_copilot_credit_events
                            WHERE user_id = %s AND ref = %s AND reason = %s)
                          >
                          (SELECT COUNT(*) FROM growth_copilot_credit_events
                            WHERE user_id = %s AND ref = %s AND reason = %s)
                      )
                    RETURNING balance
                )
                INSERT INTO growth_copilot_credit_events (user_id, delta, reason, ref, created_at)
                SELECT %s, %s, %s, %s, NOW() FROM refund
                RETURNING id
                """,
                (
                    int(amount),
                    int(user_id),
                    int(user_id),
                    str(ref or ""),
                    str(reserved_reason or ""),
                    int(user_id),
                    str(ref or ""),
                    str(reason or ""),
                    int(user_id),
                    int(amount),
                    str(reason or ""),
                    str(ref or ""),
                ),
            )
            return bool(cur.fetchone())

    def grant_daily_allowance_with_event(
        self, user_id: int, amount: int, reason: str, ref: str
    ) -> bool:
        """Grant the daily allowance and log it in one statement.

        The grant itself stays idempotent per calendar day, and the journal
        entry appears exactly when the balance changed.
        """
        with self._cursor() as cur:
            cur.execute(
                """
                WITH granted AS (
                    UPDATE growth_copilot_credits
                    SET balance = balance + %s,
                        free_granted_on = CURRENT_DATE,
                        updated_at = NOW()
                    WHERE user_id = %s
                      AND free_granted_on IS DISTINCT FROM CURRENT_DATE
                    RETURNING balance
                )
                INSERT INTO growth_copilot_credit_events (user_id, delta, reason, ref, created_at)
                SELECT %s, %s, %s, %s, NOW() FROM granted
                RETURNING id
                """,
                (
                    int(amount),
                    int(user_id),
                    int(user_id),
                    int(amount),
                    str(reason or ""),
                    str(ref or ""),
                ),
            )
            return bool(cur.fetchone())

    def credit_event_counts(
        self, user_id: int, reasons: Sequence[str], ref: str
    ) -> Dict[str, int]:
        """Count the journal entries of one operation reference."""
        wanted = [str(item) for item in reasons if str(item)]
        if not wanted:
            return {}
        placeholders = ", ".join(["%s"] * len(wanted))
        statement = (
            "SELECT reason, COUNT(*) FROM growth_copilot_credit_events "
            "WHERE user_id = %s AND ref = %s AND reason IN ("
            + placeholders
            + ") GROUP BY reason"
        )
        with self._cursor() as cur:
            cur.execute(statement, (int(user_id), str(ref or ""), *wanted))
            rows = cur.fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def claim_document(
        self, user_id: int, job_id: str, filename: str, *, stale_after_minutes: int = 15
    ) -> Optional[str]:
        """Claim the document slot of one (user, job) pair.

        The slot is the unique key of the document cache, so a second request
        for the same vacancy cannot start a parallel build. A claim without a
        file id that is older than ``stale_after_minutes`` is taken over, so a
        build that died does not block the user forever.

        The returned token identifies the owner of the claim. Only the owner
        may release the slot or write the file: a build that took over a
        stale claim keeps it, and a late build cannot overwrite a file that
        was delivered in the meantime. The token lives in the file name of the
        claim row, so no column has to be added to the table.
        """
        token = "claim:%s" % uuid.uuid4().hex
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO growth_copilot_documents
                    (user_id, job_id, telegram_file_id, filename, fit_score, created_at)
                VALUES (%s, %s, '', %s, 0, NOW())
                ON CONFLICT (user_id, job_id) DO UPDATE SET
                    filename = %s,
                    created_at = NOW()
                WHERE growth_copilot_documents.telegram_file_id = ''
                  AND growth_copilot_documents.created_at
                      < NOW() - (%s * INTERVAL '1 minute')
                RETURNING job_id
                """,
                (
                    int(user_id),
                    str(job_id),
                    token,
                    token,
                    int(stale_after_minutes),
                ),
            )
            row = cur.fetchone()
        return token if row else None

    def release_document_claim(self, user_id: int, job_id: str, claim: str) -> bool:
        """Release a claim that produced no document.

        Only the owner of the claim releases the slot: a build that took over
        a stale claim keeps it, so a third request cannot start in parallel.
        """
        with self._cursor() as cur:
            cur.execute(
                """
                DELETE FROM growth_copilot_documents
                WHERE user_id = %s AND job_id = %s
                  AND telegram_file_id = '' AND filename = %s
                RETURNING job_id
                """,
                (int(user_id), str(job_id), str(claim)),
            )
            return bool(cur.fetchone())

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------
    def prune_expired(self) -> Dict[str, int]:
        """Drop rows that outlived their retention window."""
        removed: Dict[str, int] = {}
        statements = (
            (
                EVALUATIONS_TABLE,
                f"DELETE FROM {EVALUATIONS_TABLE} "
                f"WHERE created_at < NOW() - INTERVAL '{EVALUATION_RETENTION_DAYS} days'",
            ),
            (
                DOCUMENTS_TABLE,
                f"DELETE FROM {DOCUMENTS_TABLE} "
                f"WHERE created_at < NOW() - INTERVAL '{DOCUMENT_RETENTION_DAYS} days'",
            ),
            (
                GRADE_AUDITS_TABLE,
                f"DELETE FROM {GRADE_AUDITS_TABLE} "
                f"WHERE created_at < NOW() - INTERVAL '{GRADE_AUDIT_RETENTION_DAYS} days'",
            ),
            (
                ENRICHMENTS_TABLE,
                f"DELETE FROM {ENRICHMENTS_TABLE} "
                f"WHERE fetched_at < NOW() - INTERVAL '{ENRICHMENT_RETENTION_DAYS} days'",
            ),
            (
                AI_REPORTS_TABLE,
                f"DELETE FROM {AI_REPORTS_TABLE} "
                f"WHERE created_at < NOW() - INTERVAL '{AI_REPORT_RETENTION_DAYS} days'",
            ),
            (
                FORWARDED_JOBS_TABLE,
                f"DELETE FROM {FORWARDED_JOBS_TABLE} "
                f"WHERE created_at < NOW() - INTERVAL '{FORWARDED_JOB_RETENTION_DAYS} days'",
            ),
            (
                APPLICATIONS_TABLE,
                f"DELETE FROM {APPLICATIONS_TABLE} "
                f"WHERE created_at < NOW() - INTERVAL '{APPLICATION_RETENTION_DAYS} days'",
            ),
        )
        for table, statement in statements:
            try:
                with self._cursor() as cur:
                    cur.execute(statement)
                    removed[table] = int(cur.rowcount or 0)
            except Exception as exc:
                logger.info("Copilot prune failed for %s: %s", table, type(exc).__name__)
                removed[table] = -1
        return removed


def _decode_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]
