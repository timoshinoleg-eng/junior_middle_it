"""One-shot protected publisher for the public-channel redesign launch.

Temporary endpoint. It is intentionally CRON_SECRET protected and production-only.
GET is a readiness probe; POST publishes exactly one demo card and one info post.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from message_formatter import JobMessageFormatter, build_bot_home_url, build_telegram_share_url
from serverless_health import cron_authorized
from telegram_webhook_admin import _telegram_api

RELEASE_ID = "channel-redesign-2026-09-11"


def _channel_id() -> str:
    value = (os.getenv("CHANNEL_ID") or "").strip()
    if not value:
        raise RuntimeError("channel_not_configured")
    return value


def _bot_username() -> str:
    return (os.getenv("BOT_USERNAME") or "junior_jobs_channel_bot").strip().lstrip("@")


def _demo_job() -> dict:
    return {
        "hash": "channel-redesign-demo-20260911",
        "title": "Junior Python Developer — тест карточки",
        "company": "Junior Middle IT Demo",
        "level": "Junior",
        "category": "development",
        "primary_track": "development",
        "salary": "$2,000–3,000 / month",
        "location": "Worldwide, Remote",
        "remote_scope": "worldwide",
        "description": (
            "Это демонстрационная карточка нового формата канала. "
            "Вакансия не реальная — откликаться на неё не нужно."
        ),
        "url": f"https://t.me/{_bot_username()}?start=channel_demo",
    }


def _send_demo() -> int:
    channel_id = _channel_id()
    username = _bot_username()
    job = _demo_job()
    formatter = JobMessageFormatter()
    formatted = formatter.format_job(job, view_mode="compact", bot_username=username)
    text = "🧪 *ТЕСТ НОВОГО ФОРМАТА — НЕ РЕАЛЬНАЯ ВАКАНСИЯ*\n\n" + formatted.text
    result = _telegram_api(
        "sendMessage",
        {
            "chat_id": channel_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "reply_markup": formatted.reply_markup,
            "disable_web_page_preview": True,
        },
    )
    data = result.get("result") if isinstance(result, dict) else {}
    return int((data or {}).get("message_id") or 0)


def _send_info() -> int:
    channel_id = _channel_id()
    username = _bot_username()
    bot_url = build_bot_home_url(username)
    text = (
        "🚀 <b>Мы обновили Junior/Middle IT</b>\n\n"
        "Теперь вакансии в канале проще читать с телефона: сразу видны уровень, "
        "направление, компания, география, зарплата и короткое описание.\n\n"
        "Что нового:\n"
        "• <b>Откликнуться</b> — сразу перейти к вакансии;\n"
        "• <b>Проверить резюме</b> — сравнить своё резюме с вакансией в личном боте;\n"
        "• <b>Мой поиск вакансий</b> — настроить направления, навыки, зарплату и уведомления;\n"
        "• <b>Поделиться</b> — быстро отправить вакансию другу.\n\n"
        "Канал остаётся общей лентой свежих Junior/Middle remote-вакансий. "
        "Бот — это персональный режим: подборки, новые подходящие вакансии, ежедневная сводка и проверка резюме."
    )
    markup = {
        "inline_keyboard": [
            [{"text": "🎯 Настроить мой поиск", "url": bot_url}],
            [{"text": "📄 Проверить резюме", "url": f"https://t.me/{username}?start=channel_resume"}],
        ]
    }
    result = _telegram_api(
        "sendMessage",
        {
            "chat_id": channel_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": markup,
            "disable_web_page_preview": True,
        },
    )
    data = result.get("result") if isinstance(result, dict) else {}
    return int((data or {}).get("message_id") or 0)


def publish_launch_posts() -> dict:
    if (os.getenv("VERCEL_ENV") or "").strip().lower() != "production":
        raise RuntimeError("not_production")
    demo_id = _send_demo()
    info_id = _send_info()
    return {"release": RELEASE_ID, "demo_message_id": demo_id, "info_message_id": info_id}


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

    def _authorized(self) -> bool:
        return cron_authorized(self.headers.get("authorization", ""))

    def do_GET(self):
        if not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        self._send_json(200, {"ok": True, "ready": True, "release": RELEASE_ID})

    def do_POST(self):
        if not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            result = publish_launch_posts()
        except RuntimeError as exc:
            self._send_json(503, {"ok": False, "error": str(exc)[:120]})
            return
        except Exception as exc:
            self._send_json(503, {"ok": False, "error": type(exc).__name__})
            return
        self._send_json(200, {"ok": True, **result})

    def log_message(self, fmt, *args):
        pass
