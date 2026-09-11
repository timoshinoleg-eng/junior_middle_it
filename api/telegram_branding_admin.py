import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import requests

sys.path.append(str(Path(__file__).resolve().parents[1]))

from serverless_health import cron_authorized


CHANNEL_DESCRIPTION = (
    "💻 Свежие Junior & Middle IT-вакансии: разработка, Python, Frontend, QA, "
    "Data / AI, DevOps, Product, Design и другие направления.\n\n"
    "📢 В канале — общая лента вакансий.\n"
    "🤖 @junior_jobs_channel_bot — персональный поиск по навыкам, зарплате и интересам."
)

BOT_SHORT_DESCRIPTION = (
    "🤖 Поиск Junior/Middle IT-вакансий по навыкам и зарплате. "
    "Удалёнка, персональные подборки и уведомления."
)

BOT_DESCRIPTION = (
    "🔎 Персональный поиск Junior & Middle IT-вакансий.\n\n"
    "Расскажите, какие направления вам интересны, укажите желаемую зарплату и свои навыки — "
    "бот будет отбирать подходящие вакансии.\n\n"
    "Можно получать новые совпадения сразу, одной подборкой в день или искать вручную.\n\n"
    "Также можно сохранить интересную вакансию и проверить резюме под конкретную позицию.\n\n"
    "📢 Общая лента вакансий: @junior_middle_it"
)

PIN_TITLE = "💼 Junior & Middle IT — вакансии без лишнего шума"
PIN_TEXT = """<b>💼 Junior &amp; Middle IT — вакансии без лишнего шума</b>

Ищете первую работу в IT или следующую позицию на уровне Junior / Middle? Здесь собраны свежие вакансии, чтобы не тратить время на десятки сайтов и сотни нерелевантных объявлений.

<b>Что публикуем</b>
• разработка — Python, JavaScript, Frontend, Backend и другие стеки;
• QA и тестирование;
• Data / AI;
• DevOps и инфраструктура;
• Product и Project Management;
• Design;
• Support, Security и другие IT-направления.

Мы стараемся держать ленту полезной: меньше дублей, устаревших объявлений и вакансий, которые явно не подходят Junior / Middle аудитории.

<b>📢 Канал — общая лента вакансий</b>
Можно просто подписаться и следить за новыми публикациями. Увидели подходящую вакансию — открыли описание и перешли к отклику.

<b>🤖 Бот — персональный поиск</b>
@junior_jobs_channel_bot позволяет указать интересующие направления, желаемую зарплату и навыки — например Python, PostgreSQL, React или QA.

После настройки можно выбрать удобный режим:
🔔 получать подходящие вакансии сразу;
📅 получать одну подборку в день;
🔕 отключить автоматические сообщения и искать вручную.

В боте также можно сохранить вакансию и проверить резюме под конкретную позицию.

<b>С чего начать?</b>
Если хотите просто смотреть вакансии — оставайтесь в канале.
Если нужен более точный поиск под ваш опыт и навыки — откройте бота и настройте подбор.

Наша цель простая: помочь Junior и Middle специалистам быстрее находить подходящие IT-вакансии и тратить меньше времени на неподходящие предложения."""


def _telegram_call(token: str, method: str, payload: dict | None = None):
    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=payload or {},
        timeout=25,
    )
    data = response.json()
    if not response.ok or not data.get("ok"):
        description = str(data.get("description") or f"HTTP {response.status_code}")
        raise RuntimeError(f"{method}: {description[:180]}")
    return data.get("result")


def _try_call(token: str, method: str, payload: dict | None = None):
    try:
        return True, _telegram_call(token, method, payload), ""
    except RuntimeError as exc:
        return False, None, str(exc)[:220]


def _apply_branding() -> dict:
    if (os.getenv("VERCEL_ENV") or "").strip().lower() != "production":
        raise RuntimeError("not_production")

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    channel_id = (os.getenv("CHANNEL_ID") or "").strip() or "@junior_middle_it"
    if not token:
        raise RuntimeError("telegram_token_missing")

    channel_write_ok, _, channel_write_error = _try_call(
        token,
        "setChatDescription",
        {"chat_id": channel_id, "description": CHANNEL_DESCRIPTION},
    )

    short_write_ok, _, short_write_error = _try_call(
        token,
        "setMyShortDescription",
        {"short_description": BOT_SHORT_DESCRIPTION},
    )
    bot_write_ok, _, bot_write_error = _try_call(
        token,
        "setMyDescription",
        {"description": BOT_DESCRIPTION},
    )

    chat_ok, chat, chat_error = _try_call(token, "getChat", {"chat_id": channel_id})
    chat = chat or {}
    pinned = chat.get("pinned_message") or {}
    pinned_text = str(pinned.get("text") or pinned.get("caption") or "")
    message_id = pinned.get("message_id") if pinned_text.startswith(PIN_TITLE) else None
    post_error = ""
    pin_error = ""

    if chat_ok and not message_id:
        post_ok, message, post_error = _try_call(
            token,
            "sendMessage",
            {
                "chat_id": channel_id,
                "text": PIN_TEXT,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "disable_notification": True,
                "reply_markup": {
                    "inline_keyboard": [[{
                        "text": "🤖 Настроить персональный поиск",
                        "url": "https://t.me/junior_jobs_channel_bot",
                    }]]
                },
            },
        )
        if post_ok and message:
            message_id = message.get("message_id")
            pin_ok, _, pin_error = _try_call(
                token,
                "pinChatMessage",
                {
                    "chat_id": channel_id,
                    "message_id": message_id,
                    "disable_notification": True,
                },
            )
            if not pin_ok:
                message_id = None

    verified_chat_ok, verified_chat, verify_chat_error = _try_call(
        token, "getChat", {"chat_id": channel_id}
    )
    desc_ok, my_description, desc_error = _try_call(token, "getMyDescription")
    short_ok, my_short, short_error = _try_call(token, "getMyShortDescription")
    verified_chat = verified_chat or {}
    verified_pinned = verified_chat.get("pinned_message") or {}

    result = {
        "channel_description_ok": bool(
            verified_chat_ok and verified_chat.get("description") == CHANNEL_DESCRIPTION
        ),
        "channel_description_write_ok": channel_write_ok,
        "bot_description_ok": bool(
            desc_ok and (my_description or {}).get("description") == BOT_DESCRIPTION
        ),
        "bot_short_description_ok": bool(
            short_ok and (my_short or {}).get("short_description") == BOT_SHORT_DESCRIPTION
        ),
        "pinned_message_id": verified_pinned.get("message_id"),
        "pinned_post_ok": bool(
            verified_chat_ok
            and str(verified_pinned.get("text") or "").startswith(PIN_TITLE)
        ),
        "channel_error": channel_write_error or chat_error or verify_chat_error,
        "bot_description_error": bot_write_error or desc_error,
        "bot_short_description_error": short_write_error or short_error,
        "post_error": post_error,
        "pin_error": pin_error,
    }
    result["automated_ok"] = all([
        result["bot_description_ok"],
        result["bot_short_description_ok"],
        result["pinned_post_ok"],
    ])
    return result


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

    def do_POST(self):
        if not cron_authorized(self.headers.get("authorization", "")):
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            result = _apply_branding()
        except RuntimeError as exc:
            error = str(exc)
            self._send_json(409 if error == "not_production" else 503, {"ok": False, "error": error})
            return
        except Exception as exc:
            self._send_json(503, {"ok": False, "error": type(exc).__name__})
            return
        self._send_json(200 if result.get("automated_ok") else 503, {"ok": bool(result.get("automated_ok")), **result})

    def log_message(self, fmt, *args):
        pass
