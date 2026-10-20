"""Extraction of a recruiter contact from a vacancy post.

Vacancy texts mention dozens of ``@``-like tokens (packages, frameworks, social
handles). Only a handle that sits in a recruiting context is treated as a
contact, and every produced link is a plain ``t.me`` deep link with a prefilled
message - the bot never writes to a recruiter on its own.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence
from urllib.parse import quote

HANDLE_RE = re.compile(r"(?<![\w@/])@([A-Za-z][A-Za-z0-9_]{4,31})\b")

# Tokens that are never a recruiter contact.
DENIED_HANDLES = {
    "amazon", "amazonaws", "android", "angular", "ansible", "apache", "apple",
    "apply", "atlassian", "aws", "azure", "babel", "bitbucket", "bitrise",
    "celery", "circleci", "clojure", "codeigniter", "confluence", "cypress",
    "datadog", "django", "docker", "dotenv", "dotnet", "elastic", "eslint",
    "express", "fastapi", "figma", "firebase", "flask", "flutter", "gcp",
    "github", "gitlab", "golang", "graphql", "gunicorn", "heroku", "hubspot",
    "java", "javascript", "jenkins", "jira", "jquery", "json", "jupyter",
    "kafka", "kubernetes", "laravel", "linux", "magento", "mongodb", "mysql",
    "nestjs", "nextjs", "node", "notion", "npm", "numpy", "oauth", "openai",
    "oracle", "pandas", "php", "postgresql", "prettier", "prometheus",
    "prisma", "pytest", "python", "rabbitmq", "react", "reactjs", "redis",
    "redux", "ruby", "rust", "salesforce", "sass", "scala", "selenium",
    "sentry", "shopify", "slack", "spring", "sqlite", "sqlalchemy", "stripe",
    "svelte", "symfony", "tailwind", "terraform", "travis", "twilio",
    "typeorm", "typescript", "vercel", "vite", "vitest", "vscode", "vue",
    "vuejs", "webflow", "webpack", "wordpress", "yii", "zend", "zoom",
}

# A recruiter contact is expected next to one of these words.
CONTACT_CONTEXT = (
    "hr", "hr-", "recruiter", "recruiting", "менеджер", "менеджер по",
    "пишите", "пиши", "напишите", "написать", "свяжитесь", "связаться",
    "отклик", "откликнуться", "вопросы по вакансии", "по вакансии",
    "обсудить", "обсуждение", "телеграм", "telegram", "t.me", "контакт",
    "contact", "questions about the role",
)

MAX_MESSAGE_CHARS = 800


def _normalize_handle(value: str) -> str:
    return str(value or "").strip().lstrip("@").strip()


def extract_hr_handle(
    text: str,
    *,
    ignore: Sequence[str] = (),
    context_chars: int = 90,
) -> Optional[str]:
    """Return the first recruiter contact handle found in a vacancy text."""
    source = str(text or "")
    if not source:
        return None
    blocked = {_normalize_handle(item).lower() for item in ignore if item}
    lowered = source.lower()

    for match in HANDLE_RE.finditer(source):
        handle = match.group(1)
        normalized = handle.lower()
        if normalized in blocked:
            continue
        if normalized.endswith("bot"):
            continue
        if normalized in DENIED_HANDLES:
            continue
        window = lowered[max(0, match.start() - context_chars): match.end() + context_chars]
        if not any(marker in window for marker in CONTACT_CONTEXT):
            continue
        return handle
    return None


def build_hr_link(handle: str, message: str = "", *, max_chars: int = MAX_MESSAGE_CHARS) -> str:
    """Build a ``t.me`` deep link with a prefilled, length-bounded message."""
    username = _normalize_handle(handle)
    if not username:
        return ""
    text = " ".join(str(message or "").split())
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    if not text:
        return f"https://t.me/{username}"
    return f"https://t.me/{username}?text={quote(text, safe='')}"


def build_contact_message(job: Dict, report=None, *, max_chars: int = MAX_MESSAGE_CHARS) -> str:
    """Deterministic application message built only from vacancy facts.

    The message never claims experience that is not visible in the profile, it
    only repeats the role, the stack that matched and a clear next step.
    """
    title = str(job.get("title") or "вакансия").strip()
    company = str(job.get("company") or "").strip()
    target = f"{title} ({company})" if company else title

    parts = [f"Здравствуйте! Откликаюсь на вакансию «{target}»."]
    matched = list(getattr(report, "matched_skills", None) or [])
    if matched:
        parts.append("Совпадает мой стек: " + ", ".join(matched[:3]) + ".")
    else:
        parts.append("Готов обсудить детали и показать релевантный опыт.")
    parts.append("Подскажите, пожалуйста, как лучше прислать резюме?")

    text = " ".join(" ".join(parts).split())
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text
