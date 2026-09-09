"""Privacy-first public acquisition site for published Junior/Middle jobs.

The page is deliberately read-only. It reads durable vacancy payloads when the
PostgreSQL growth store is configured, renders escaped HTML, and attributes only
actual Telegram bot starts via deep-link payloads. No third-party analytics or
resume/user data is exposed here.
"""
from __future__ import annotations

import html
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

CATEGORY_LABELS = {
    "development": "Разработка",
    "qa": "QA",
    "devops": "DevOps",
    "data": "Data / AI",
    "design": "Дизайн",
    "pm": "Product / Project",
    "security": "Безопасность",
    "support": "Поддержка",
    "marketing": "Маркетинг",
    "sales": "Продажи",
    "other": "Другое",
}
BOT_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{4,64}$")


def growth_dsn() -> str:
    return (os.getenv("GROWTH_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()


def safe_http_url(value: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return raw


def safe_bot_username(value: str) -> str:
    username = str(value or "").strip().lstrip("@")
    return username if BOT_USERNAME_RE.fullmatch(username) else ""


def bot_deep_link(bot_username: str, payload: str) -> str:
    username = safe_bot_username(bot_username)
    payload = re.sub(r"[^A-Za-z0-9_-]", "", str(payload or ""))[:64]
    if not username or not payload:
        return ""
    return f"https://t.me/{username}?start={payload}"


def channel_link(channel_id: str) -> str:
    value = str(channel_id or "").strip()
    if value.startswith("@") and len(value) > 1:
        return f"https://t.me/{value[1:]}"
    return ""


def load_recent_public_jobs(days: int = 14, limit: int = 120) -> List[Dict]:
    """Read recent durable payloads without creating schemas or writing data."""
    dsn = growth_dsn()
    if not dsn or psycopg is None:
        return []
    days = max(1, min(int(days), 45))
    limit = max(1, min(int(limit), 300))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT hash, payload, updated_at
                    FROM growth_job_payloads
                    WHERE updated_at >= %s
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (cutoff, limit),
                )
                rows = cur.fetchall()
    except Exception as exc:  # external service; never expose DSN/details
        logger.warning("Public jobs store unavailable: %s", type(exc).__name__)
        return []

    out: List[Dict] = []
    for job_hash, payload, updated_at in rows:
        if not isinstance(payload, dict):
            continue
        job = dict(payload)
        job.setdefault("hash", str(job_hash))
        job["_updated_at"] = updated_at
        out.append(job)
    return out


def is_public_job(job: Dict) -> bool:
    if not str(job.get("title") or "").strip():
        return False
    if not str(job.get("company") or "").strip():
        return False
    if not safe_http_url(job.get("url") or ""):
        return False
    level = str(job.get("level") or "").strip().lower()
    if not (level.startswith("junior") or level.startswith("middle")):
        return False
    gate = str(job.get("quality_gate_status") or "").strip().lower()
    if gate and gate != "passed":
        return False
    if str(job.get("url_preflight_status") or "").lower() == "excluded":
        return False
    return True


def select_public_jobs(
    jobs: Iterable[Dict],
    *,
    category: str = "",
    level: str = "",
    limit: int = 24,
) -> List[Dict]:
    """Keep the landing useful and diverse rather than duplicating the channel."""
    category = str(category or "").strip().lower()
    if category not in CATEGORY_LABELS:
        category = ""
    level = str(level or "").strip().lower()
    if level not in {"junior", "middle"}:
        level = ""
    limit = max(1, min(int(limit), 40))

    company_counts = defaultdict(int)
    category_counts = defaultdict(int)
    selected: List[Dict] = []
    for source in jobs:
        job = dict(source)
        if not is_public_job(job):
            continue
        job_category = str(job.get("category") or "other").strip().lower()
        job_level = str(job.get("level") or "").strip().lower()
        if category and job_category != category:
            continue
        if level and not job_level.startswith(level):
            continue

        company_key = re.sub(r"\s+", " ", str(job.get("company") or "").strip().lower())
        # Without an explicit filter, avoid a single ATS/company/category taking
        # over the page. Filtered pages may naturally contain more of one track.
        if company_counts[company_key] >= 2:
            continue
        if not category and category_counts[job_category] >= 8:
            continue
        company_counts[company_key] += 1
        category_counts[job_category] += 1
        selected.append(job)
        if len(selected) >= limit:
            break
    return selected


def _salary_text(job: Dict) -> str:
    salary = str(job.get("salary") or "").strip()
    if salary and salary.lower() not in {"не указана", "not specified", "none"}:
        return salary[:80]
    min_usd = job.get("salary_min_usd")
    try:
        if min_usd and int(min_usd) > 0:
            return f"от ${int(min_usd):,}".replace(",", " ")
    except (TypeError, ValueError):
        pass
    return "зарплата не указана"


def _job_card(job: Dict, bot_username: str) -> str:
    title = html.escape(str(job.get("title") or "IT vacancy")[:180])
    company = html.escape(str(job.get("company") or "")[:120])
    category = str(job.get("category") or "other").lower()
    category_label = html.escape(CATEGORY_LABELS.get(category, category.title()))
    level = html.escape(str(job.get("level") or "Junior/Middle")[:40])
    location = html.escape(str(job.get("location") or "Remote")[:100])
    salary = html.escape(_salary_text(job))
    source = html.escape(str(job.get("source") or "")[:60])
    apply_url = html.escape(safe_http_url(job.get("url") or ""), quote=True)
    job_hash = re.sub(r"[^A-Za-z0-9]", "", str(job.get("hash") or ""))[:40]
    bot_url = html.escape(bot_deep_link(bot_username, f"web_{job_hash}"), quote=True)
    resume_url = html.escape(bot_deep_link(bot_username, f"resume_{job_hash}"), quote=True)

    tags = job.get("tags") or []
    if isinstance(tags, str):
        tags = [part.strip() for part in re.split(r"[,;|]", tags) if part.strip()]
    tag_html = "".join(
        f'<span class="tag">{html.escape(str(tag)[:30])}</span>'
        for tag in list(tags)[:5]
    )
    actions = [f'<a class="btn primary" href="{apply_url}" rel="nofollow noopener">Откликнуться</a>']
    if bot_url:
        actions.append(f'<a class="btn" href="{bot_url}">Открыть в боте</a>')
    if resume_url:
        actions.append(f'<a class="btn ghost" href="{resume_url}">Проверить резюме</a>')

    return f"""
    <article class="job-card">
      <div class="badges"><span>{level}</span><span>{category_label}</span></div>
      <h2>{title}</h2>
      <p class="company">{company}</p>
      <div class="facts"><span>🌍 {location}</span><span>💰 {salary}</span></div>
      <div class="tags">{tag_html}</div>
      <div class="actions">{''.join(actions)}</div>
      <p class="source">Источник: {source or 'публичный job board / ATS'}</p>
    </article>
    """


def render_landing(
    jobs: Iterable[Dict],
    *,
    bot_username: str = "",
    channel_id: str = "",
    category: str = "",
    level: str = "",
    public_site_url: str = "",
) -> str:
    selected = select_public_jobs(jobs, category=category, level=level)
    bot_username = safe_bot_username(bot_username)
    main_bot = bot_deep_link(bot_username, "web_home")
    tg_channel = channel_link(channel_id)
    canonical = safe_http_url(public_site_url)

    filter_bits = []
    if category in CATEGORY_LABELS:
        filter_bits.append(CATEGORY_LABELS[category])
    if level in {"junior", "middle"}:
        filter_bits.append(level.title())
    suffix = " · " + " / ".join(filter_bits) if filter_bits else ""
    title = f"Junior/Middle Remote IT вакансии{suffix}"
    description = (
        "Свежие Junior и Middle remote IT-вакансии: разработка, QA, DevOps, Data/AI, "
        "дизайн и product. Персональные подборки, алерты и Resume Match в Telegram."
    )
    canonical_html = f'<link rel="canonical" href="{html.escape(canonical, quote=True)}">' if canonical else ""
    og_url = f'<meta property="og:url" content="{html.escape(canonical, quote=True)}">' if canonical else ""
    main_cta = (
        f'<a class="hero-btn" href="{html.escape(main_bot, quote=True)}">Подобрать вакансии под себя</a>'
        if main_bot else ""
    )
    channel_cta = (
        f'<a class="hero-link" href="{html.escape(tg_channel, quote=True)}">Открыть Telegram-канал</a>'
        if tg_channel else ""
    )

    category_links = ['<a href="/">Все</a>']
    for key in ("development", "qa", "devops", "data", "design", "pm", "security"):
        category_links.append(
            f'<a href="/?category={key}">{html.escape(CATEGORY_LABELS[key])}</a>'
        )
    level_links = '<a href="/?level=junior">Junior</a><a href="/?level=middle">Middle</a>'

    if selected:
        cards = "".join(_job_card(job, bot_username) for job in selected)
        jobs_section = f'<div class="grid">{cards}</div>'
        count_copy = f"Показано {len(selected)} свежих вакансий"
    else:
        jobs_section = (
            '<section class="empty"><h2>Подборка обновляется</h2>'
            '<p>Открой Telegram-бота: там доступны свежие вакансии, персональный digest, '
            'realtime alerts и Resume Match.</p></section>'
        )
        count_copy = "Свежая веб-подборка появится после синхронизации"

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(title)}</title>
  <meta name="description" content="{html.escape(description, quote=True)}">
  <meta name="robots" content="index,follow,max-snippet:-1,max-image-preview:large">
  <meta property="og:type" content="website">
  <meta property="og:title" content="{html.escape(title, quote=True)}">
  <meta property="og:description" content="{html.escape(description, quote=True)}">
  {canonical_html}{og_url}
  <style>
    :root{{--bg:#f6f7fb;--card:#fff;--text:#151821;--muted:#626a7a;--line:#e4e7ee;--accent:#265cf0}}
    *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
    a{{color:inherit}} .wrap{{max-width:1120px;margin:auto;padding:24px}} .hero{{padding:48px 0 28px}}
    .eyebrow{{font-weight:700;color:var(--accent)}} h1{{font-size:clamp(32px,6vw,58px);line-height:1.02;margin:12px 0;max-width:900px}}
    .lead{{font-size:19px;color:var(--muted);max-width:760px}} .hero-actions,.actions,.filters{{display:flex;gap:10px;flex-wrap:wrap}}
    .hero-btn,.btn{{text-decoration:none;border:1px solid var(--line);background:#fff;border-radius:12px;padding:11px 15px;font-weight:650}}
    .hero-btn,.btn.primary{{background:var(--accent);color:#fff;border-color:var(--accent)}} .hero-link{{padding:11px 4px;color:var(--accent);font-weight:650}}
    .filters{{margin:20px 0}} .filters a{{text-decoration:none;background:#fff;border:1px solid var(--line);border-radius:999px;padding:7px 11px;font-size:14px}}
    .section-head{{display:flex;align-items:end;justify-content:space-between;gap:12px;margin:34px 0 14px}} .section-head p{{color:var(--muted);margin:0}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px}} .job-card{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:0 3px 14px rgba(25,35,60,.04)}}
    .job-card h2{{font-size:20px;line-height:1.25;margin:10px 0 4px}} .company{{font-weight:650;margin:0 0 14px}} .badges,.facts,.tags{{display:flex;gap:8px;flex-wrap:wrap}}
    .badges span,.tag{{background:#eef2ff;border-radius:999px;padding:4px 8px;font-size:12px;font-weight:650}} .facts{{color:var(--muted);font-size:14px;margin-bottom:12px}}
    .tags{{min-height:26px;margin-bottom:16px}} .tag{{background:#f2f3f6;font-weight:500}} .btn{{font-size:13px;padding:8px 10px}} .btn.ghost{{background:transparent}}
    .source{{color:var(--muted);font-size:11px;margin:14px 0 0}} .empty{{background:#fff;border:1px solid var(--line);border-radius:18px;padding:28px}}
    footer{{color:var(--muted);font-size:13px;padding:42px 0 20px}} @media(max-width:600px){{.wrap{{padding:18px}}.hero{{padding-top:30px}}}}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="eyebrow">Junior / Middle · Remote IT</div>
      <h1>Вакансии без шума. Подборка, Resume Match и алерты.</h1>
      <p class="lead">Публичная витрина проекта junior_middle_it. В подборку попадают вакансии после редакционных фильтров; персональный поиск и мгновенные уведомления доступны в Telegram.</p>
      <div class="hero-actions">{main_cta}{channel_cta}</div>
    </section>
    <nav class="filters" aria-label="Фильтры">{''.join(category_links)}{level_links}</nav>
    <div class="section-head"><div><h2>Свежие вакансии</h2><p>{html.escape(count_copy)}</p></div></div>
    {jobs_section}
    <footer>Никаких платных размещений в ранжировании. Проверяй условия вакансии на странице работодателя перед откликом.</footer>
  </main>
</body>
</html>"""
