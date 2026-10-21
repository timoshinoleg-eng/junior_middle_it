"""HTTP API of the Copilot Mini App.

Every request carries the ``initData`` string that Telegram signed with the
bot token. The signature is the only source of identity: a forged or expired
signature gets nothing but a 401.

Routes:

* ``POST /api/copilot`` - the deterministic analysis of a vacancy for the
  signed user, or the list of the saved searches;
* ``POST /api/copilot/pdf`` - the tailored CV: the cached file is sent again
  without a credit, a new one costs one credit.

Privacy: the resume file is parsed in memory and never reaches the database,
the logs or a file name. Only the signed user's own data is returned.
"""
import base64
import binascii
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

sys.path.append(str(Path(__file__).resolve().parents[1]))

import copilot_webhook_runtime  # noqa: F401  (installs the Copilot runtime)
from copilot import fit as copilot_fit
from copilot import forward as copilot_forward
from copilot import pdf as copilot_pdf
from copilot import webapp as copilot_webapp
from copilot import webapp_auth
from copilot.credit_operations import (
    DELIVERY_DELIVERED,
    DELIVERY_UNKNOWN,
    CreditStoreUnavailable,
    InsufficientCredits,
    cv_operation,
    settle_delivery,
)
from copilot.credits import CreditManager
from copilot.store import CopilotStore
from interactive_webhook_runtime import build_runtime
from resume_documents import ResumeDocumentError, extract_resume_document
from secure_http_logging import configure_sensitive_http_logging

configure_sensitive_http_logging()
logger = logging.getLogger(__name__)

CV_FILENAME = "cv.pdf"


def _profile_from_settings(settings):
    """Profile of one user, as the Telegram flow builds it."""
    settings = settings or {}
    skills = settings.get("skills") or ""
    if isinstance(skills, (list, tuple)):
        skills = ", ".join(str(item) for item in skills)
    return {
        "skills": str(skills),
        "categories": list(settings.get("enabled_categories") or []),
    }


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------------------
    # Request plumbing
    # ------------------------------------------------------------------
    def _read_json(self):
        try:
            length = int(self.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            raise copilot_webapp.WebappError(400, "empty_body", "Пустой запрос.")
        if length > copilot_webapp.MAX_BODY_BYTES:
            raise copilot_webapp.WebappError(
                413, "body_too_large", "Файл слишком большой. Максимум 2.5 МБ."
            )
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except (TypeError, ValueError) as exc:
            raise copilot_webapp.WebappError(400, "invalid_body", "Не удалось разобрать запрос.") from exc
        if not isinstance(payload, dict):
            raise copilot_webapp.WebappError(400, "invalid_body", "Не удалось разобрать запрос.")
        return payload

    def _authenticate(self, payload):
        """Signed user of the request, or a 401."""
        token = str(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        if not token:
            raise copilot_webapp.WebappError(
                503, "service_unavailable", "Сервис временно недоступен. Попробуй позже."
            )
        init_data = str(payload.get("init_data") or payload.get("initData") or "")
        try:
            return webapp_auth.verify_user(init_data, token)
        except webapp_auth.InitDataError:
            raise copilot_webapp.WebappError(
                401, "unauthorized", "Сессия недействительна. Открой приложение заново."
            )

    def _runtime(self):
        try:
            application, db, job_bot = build_runtime()
        except Exception as exc:
            logger.error("Copilot webapp runtime failed: %s", type(exc).__name__)
            raise copilot_webapp.WebappError(
                503, "service_unavailable", "Сервис временно недоступен. Попробуй позже."
            )
        store = CopilotStore.from_database(db)
        if store is None:
            db.close()
            raise copilot_webapp.WebappError(
                503, "service_unavailable", "Сервис временно недоступен. Попробуй позже."
            )
        return db, job_bot, store

    def _rate_limit(self, store, user_id):
        try:
            allowed, _hits = store.bump_rate_limit(
                user_id,
                limit=copilot_webapp.RATE_LIMIT_REQUESTS,
                window_minutes=copilot_webapp.RATE_LIMIT_WINDOW_MINUTES,
            )
        except Exception as exc:
            logger.warning("Copilot webapp rate limit failed: %s", type(exc).__name__)
            return
        if not allowed:
            raise copilot_webapp.WebappError(
                429, "rate_limited", "Слишком много запросов. Подожди немного."
            )

    def _load_job(self, db, store, job_hash):
        job = db.get_job_payload(job_hash)
        if job:
            return job
        try:
            row = store.get_forwarded_job(job_hash)
        except Exception as exc:
            logger.warning("Copilot webapp job read failed: %s", type(exc).__name__)
            return None
        if not row:
            return None
        return copilot_forward.job_payload_from_row(row)

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------
    def do_GET(self):
        self.do_POST()

    def do_POST(self):
        path = urlsplit(self.path).path.rstrip("/")
        db = None
        try:
            payload = self._read_json()
            user = self._authenticate(payload)
            user_id = int(user["id"])
            db, job_bot, store = self._runtime()
            self._rate_limit(store, user_id)
            if path.endswith("/pdf"):
                status, body = self._handle_pdf(db, store, payload, user_id)
            else:
                status, body = self._handle_fit(db, store, payload, user_id)
        except copilot_webapp.WebappError as exc:
            self._send_json(exc.status, exc.payload())
            return
        except Exception as exc:
            logger.error("Copilot webapp request failed: %s", type(exc).__name__)
            self._send_json(
                503,
                {
                    "ok": False,
                    "error": "temporarily_unavailable",
                    "message": "Сервис временно недоступен. Попробуй позже.",
                },
            )
            return
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
        self._send_json(status, body)

    def _handle_fit(self, db, store, payload, user_id):
        action = str(payload.get("action") or "fit").strip().lower()
        if action == "searches":
            return 200, {"ok": True, "searches": self._saved_searches(db, user_id)}
        if action != "fit":
            raise copilot_webapp.WebappError(400, "unsupported_action", "Неизвестное действие.")

        job_hash = str(payload.get("job_hash") or "").strip()[:64]
        if not job_hash:
            raise copilot_webapp.WebappError(400, "missing_job", "Не указана вакансия.")
        job = self._load_job(db, store, job_hash)
        if not job:
            raise copilot_webapp.WebappError(404, "job_not_found", "Вакансия не найдена.")

        settings = db.get_user_settings(user_id)
        profile = _profile_from_settings(settings)
        result = copilot_webapp.analysis_payload(job, profile)
        db.log_event(user_id, "copilot_webapp_fit_viewed", {"hash": job_hash})
        return 200, {"ok": True, **result}

    def _saved_searches(self, db, user_id):
        try:
            searches = db.list_saved_searches(user_id)
        except Exception as exc:
            logger.warning("Copilot webapp saved searches failed: %s", type(exc).__name__)
            return []
        out = []
        for item in searches or []:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("query") or "")
                keywords = str(item.get("keywords") or "")
                categories = str(item.get("categories") or "")
            else:
                name = str(getattr(item, "name", "") or "")
                keywords = str(getattr(item, "keywords", "") or "")
                categories = str(getattr(item, "categories", "") or "")
            if not name and not keywords:
                continue
            out.append({"name": name, "keywords": keywords, "categories": categories})
        return out

    def _handle_pdf(self, db, store, payload, user_id):
        job_hash = str(payload.get("job_hash") or "").strip()[:64]
        if not job_hash:
            raise copilot_webapp.WebappError(400, "missing_job", "Не указана вакансия.")
        job = self._load_job(db, store, job_hash)
        if not job:
            raise copilot_webapp.WebappError(404, "job_not_found", "Вакансия не найдена.")

        token = str(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        title = str(job.get("title") or "вакансия")

        cached = None
        try:
            cached = store.get_document(user_id, job_hash)
        except Exception as exc:
            logger.warning("Copilot webapp document cache read failed: %s", type(exc).__name__)
        if cached and cached.get("telegram_file_id"):
            sent = copilot_webapp.resend_document(
                token,
                user_id,
                str(cached["telegram_file_id"]),
                caption=f"🧾 CV под вакансию «{title}».",
            )
            db.log_event(user_id, "copilot_webapp_cv_cached", {"hash": job_hash})
            return 200, {
                "ok": True,
                "cached": True,
                "file_id": sent.get("file_id") or "",
                "message": "Готовый CV отправлен в чат бота.",
            }

        resume = payload.get("resume") or {}
        if not isinstance(resume, dict):
            resume = {}
        content = self._resume_bytes(resume)

        # One build of one vacancy at a time. The slot is the primary key of
        # the document cache, so a parallel request cannot assemble the same
        # document twice and cannot be charged for it either.
        try:
            claimed = store.claim_document(user_id, job_hash, CV_FILENAME)
        except Exception as exc:
            logger.warning("Copilot webapp document claim failed: %s", type(exc).__name__)
            raise copilot_webapp.WebappError(
                503, "store_unavailable", "Не удалось начать сборку. Попробуй позже."
            ) from exc
        if not claimed:
            raise copilot_webapp.WebappError(
                409, "cv_in_progress", "Сборка CV по этой вакансии уже идёт."
            )

        manager = CreditManager(store)
        operation = cv_operation(store, user_id, job_hash).bind_claim(claimed)
        try:
            return self._build_and_send(
                db,
                store,
                user_id,
                job_hash,
                job,
                title,
                token,
                content,
                resume,
                claimed,
                operation,
                manager,
            )
        except BaseException:
            # The claim is given back on every path that does not deliver a
            # document: a build that failed or that was interrupted must not
            # block the next request for fifteen minutes. Only the owner of
            # the claim releases it, so a build that took the slot over keeps
            # it.
            self._release_claim(store, user_id, job_hash, claimed)
            raise

    def _build_and_send(
        self,
        db,
        store,
        user_id,
        job_hash,
        job,
        title,
        token,
        content,
        resume,
        claimed,
        operation,
        manager,
    ):
        """Assemble the CV of one vacancy and send it to the chat.

        The caller owns the claim and releases it when this method raises, so
        every step that can fail - the settings of the user, the analysis, the
        PDF, its validation and the caption - cannot leak the claim.
        """
        try:
            manager.ensure_daily_allowance(user_id)
            balance = manager.balance(user_id)
            # An operation the user already paid for is not paid for again,
            # so an empty balance must not block it.
            already_paid = operation.paid
        except Exception as exc:
            logger.warning("Copilot webapp credits failed: %s", type(exc).__name__)
            raise copilot_webapp.WebappError(
                503, "store_unavailable", "Не удалось проверить кредиты. Попробуй позже."
            ) from exc
        if balance < 1 and not already_paid:
            raise copilot_webapp.WebappError(
                402,
                "no_credits",
                "Бесплатные сборки на сегодня закончились. Пригласи друга — получите бонус.",
            )

        try:
            resume_text = extract_resume_document(
                content,
                filename=str(resume.get("filename") or ""),
                mime_type=str(resume.get("mime_type") or ""),
            )
        except ResumeDocumentError as exc:
            raise copilot_webapp.WebappError(400, "invalid_resume", str(exc)) from exc
        except Exception as exc:
            raise copilot_webapp.WebappError(
                400, "invalid_resume", "Не удалось прочитать файл. Пришли PDF или DOCX."
            ) from exc

        settings = db.get_user_settings(user_id)
        profile = _profile_from_settings(settings)
        report = copilot_fit.compute_fit(profile, job)
        contact = copilot_pdf.extract_contacts(resume_text)
        try:
            pdf_bytes = copilot_pdf.build_cv_pdf(
                contact=contact,
                role=title,
                company=str(job.get("company") or ""),
                skills=report.matched_skills,
                note="Собрано под эту вакансию: совпавшие навыки из твоего профиля.",
            )
        except Exception as exc:
            logger.warning("Copilot webapp CV build failed: %s", type(exc).__name__)
            raise copilot_webapp.WebappError(
                500, "cv_failed", "Не удалось собрать CV. Попробуй позже."
            ) from exc

        ats = copilot_pdf.validate_pdf_bytes(pdf_bytes, job=job)
        caption = f"🧾 CV под вакансию «{title}».\n" + ats.summary()

        # The credit is taken once the document exists and only for the attempt
        # that delivers it. A repeated request of the same vacancy keeps the
        # state the operation already has.
        try:
            operation.reserve()
        except InsufficientCredits as exc:
            raise copilot_webapp.WebappError(
                402,
                "no_credits",
                "Бесплатные сборки на сегодня закончились. Пригласи друга — получите бонус.",
            ) from exc
        except CreditStoreUnavailable as exc:
            raise copilot_webapp.WebappError(
                503, "store_unavailable", "Не удалось списать кредит. Попробуй позже."
            ) from exc

        if not operation.can_deliver():
            raise copilot_webapp.WebappError(
                503, "store_unavailable", "Не удалось проверить кредиты. Попробуй позже."
            )
        try:
            sent = copilot_webapp.send_document_to_chat(
                token, user_id, CV_FILENAME, pdf_bytes, caption=caption
            )
        except copilot_webapp.WebappError as exc:
            # A rejected document is paid back, a lost answer keeps the credit
            # spent: Telegram may have stored the file anyway.
            settle_delivery(operation, exc.delivery or DELIVERY_UNKNOWN)
            raise

        settle_delivery(operation, DELIVERY_DELIVERED)
        try:
            saved = store.save_document(
                user_id,
                job_hash,
                telegram_file_id=str(sent.get("file_id") or ""),
                filename=CV_FILENAME,
                fit_score=report.score,
                claim=claimed,
            )
        except Exception as exc:
            logger.warning("Copilot webapp document cache write failed: %s", type(exc).__name__)
            # The document was delivered, but the cache does not know it. The
            # slot is released, so the next request builds it again and stays
            # free: the operation is already paid for.
            self._release_claim(store, user_id, job_hash, claimed)
        else:
            if not saved:
                # The write was refused: the claim belongs to another build
                # now, so this file is not cached. The delivery already
                # happened and the operation is paid for.
                logger.warning("Copilot webapp document cache write was refused")
                self._release_claim(store, user_id, job_hash, claimed)
        db.log_event(user_id, "copilot_webapp_cv_sent", {"hash": job_hash, "score": report.score})
        return 200, {
            "ok": True,
            "cached": False,
            "file_id": str(sent.get("file_id") or ""),
            "ats": {
                "ok": bool(ats.ok),
                "pages": int(ats.pages),
                "has_text_layer": bool(ats.has_text_layer),
                "keyword_coverage_pct": int(ats.keyword_coverage_pct),
                "problems": list(ats.problems),
                "summary": ats.summary(),
            },
            "message": "CV собран и отправлен в чат бота.",
        }

    def _release_claim(self, store, user_id, job_hash, claim):
        """Give the document slot back after a build that produced nothing.

        Only the owner of the claim releases the slot: a build that took over
        a stale claim keeps it.
        """
        if not claim:
            return
        try:
            store.release_document_claim(user_id, job_hash, claim)
        except Exception as exc:
            logger.warning("Copilot webapp document claim release failed: %s", type(exc).__name__)

    def _resume_bytes(self, resume):
        raw = str(resume.get("content_base64") or "")
        if not raw:
            raise copilot_webapp.WebappError(
                400, "missing_resume", "Пришли файл резюме (PDF или DOCX)."
            )
        try:
            content = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise copilot_webapp.WebappError(
                400, "invalid_resume", "Не удалось прочитать файл. Пришли PDF или DOCX."
            ) from exc
        if not content:
            raise copilot_webapp.WebappError(
                400, "invalid_resume", "Файл пустой. Пришли PDF или DOCX."
            )
        if len(content) > copilot_webapp.MAX_RESUME_BYTES:
            raise copilot_webapp.WebappError(
                413, "resume_too_large", "Файл слишком большой. Максимум 2.5 МБ."
            )
        return content
