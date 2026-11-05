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
        actions.append(f'<a class="btn" href="{bot_url}">В боте</a>')
    if resume_url:
        actions.append(f'<a class="btn ghost" href="{resume_url}">Resume Match</a>')

    return f"""
    <article class="job-card">
      <div class="badges"><span class="brace">{{{level.lower()}}}</span><span class="brace">{{{category_label.lower()}}}</span></div>
      <h2>{title}</h2>
      <p class="company">{company}</p>
      <div class="facts"><span>{location}</span><span class="salary">{salary}</span></div>
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

    category_items = [("", "Все")] + [
        (key, CATEGORY_LABELS[key])
        for key in ("development", "qa", "devops", "data", "design", "pm", "security")
    ]
    category_links = []
    for key, label in category_items:
        href = "/" if not key else f"/?category={key}"
        active = (category == key) if key else (not category)
        cls = ' class="on"' if active else ""
        category_links.append(f'<a{cls} href="{href}">{html.escape(label)}</a>')
    for key in ("junior", "middle"):
        cls = ' class="on"' if level == key else ""
        category_links.append(f'<a{cls} href="/?level={key}">{key}</a>')

    if selected:
        cards = "".join(_job_card(job, bot_username) for job in selected)
        jobs_section = f'<div class="jobs"><div class="grid">{cards}</div></div>'
        count_copy = f"Показано {len(selected)} свежих вакансий"
    else:
        jobs_section = (
            '<div class="jobs"><section class="empty"><h2>Подборка обновляется</h2>'
            '<p>Открой Telegram-бота: там доступны свежие вакансии, персональный digest, '
            'realtime alerts и Resume Match.</p></section></div>'
        )
        count_copy = "Свежая веб-подборка появится после синхронизации"

    strip = (
        f'<div class="strip"><a href="{html.escape(tg_channel, quote=True)}">'
        "Канал с живыми вакансиями — читать в Telegram&nbsp;→</a></div>"
        if tg_channel else ""
    )
    header_cells = ""
    if tg_channel:
        header_cells += (
            f'<a class="cell" href="{html.escape(tg_channel, quote=True)}">Канал</a>'
        )
    if main_bot:
        header_cells += (
            f'<a class="cell square" href="{html.escape(main_bot, quote=True)}">Подобрать</a>'
        )
    crop_marks = '<i class="c1"></i><i class="c2"></i><i class="c3"></i><i class="c4"></i>'
    main_cta = (
        f'<span class="crop"><a class="hero-btn" href="{html.escape(main_bot, quote=True)}">'
        f"Подобрать вакансии под себя</a>{crop_marks}</span>"
        if main_bot else ""
    )
    channel_cta = (
        f'<a class="hero-link" href="{html.escape(tg_channel, quote=True)}">Открыть Telegram-канал</a>'
        if tg_channel else ""
    )

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
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' fill='%23002bff'/%3E%3Ctext x='16' y='22' font-family='monospace' font-size='15' fill='%23ffffff' text-anchor='middle'%3Ej/m%3C/text%3E%3C/svg%3E">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap">
  <style>
    :root{{--canvas:#fbfbfc;--paper:#ffffff;--ink:#1c1917;--graphite:#57534e;--pebble:#a8a29e;
      --hairline:#e7e5e4;--cobalt:#002bff;--cobalt-hover:#0022d6;--cobalt-50:#7f95ff;--cobalt-5:#f2f4ff;
      --sans:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
      --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--canvas);color:var(--ink);font:16px/1.5 var(--sans);-webkit-font-smoothing:antialiased}}
    a{{color:inherit}}
    a:focus-visible,button:focus-visible{{outline:2px solid var(--cobalt);outline-offset:2px}}
    .sheet{{max-width:1184px;margin:0 auto;border-left:1px solid var(--hairline);border-right:1px solid var(--hairline);background:var(--canvas)}}
    .strip{{min-height:33px;background:var(--cobalt);display:flex;align-items:center;padding:0 32px}}
    .strip a{{color:#fff;text-decoration:none;font-size:14px;line-height:16.8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .strip a:hover{{text-decoration:underline}}
    header.bar{{min-height:64px;display:flex;align-items:center;justify-content:space-between;padding:0 32px;border-bottom:1px solid var(--hairline);gap:16px}}
    .brand{{font:500 15px/1 var(--mono);letter-spacing:.02em;text-decoration:none;white-space:nowrap}}
    nav.cells{{display:flex;align-items:stretch;height:39px}}
    nav.cells a{{display:flex;align-items:center;padding:0 14px;border:1px solid var(--hairline);border-left:0;text-decoration:none;font-size:15px;background:var(--canvas)}}
    nav.cells a:first-child{{border-left:1px solid var(--hairline)}}
    nav.cells a:hover{{background:var(--cobalt-5)}}
    nav.cells a.square{{background:var(--cobalt);border-color:var(--cobalt);color:#fff}}
    nav.cells a.square:hover{{background:var(--cobalt-hover);border-color:var(--cobalt-hover)}}
    .hero{{position:relative;padding:72px 32px 64px;border-bottom:1px solid var(--hairline);overflow:hidden}}
    .hero::before{{content:"";position:absolute;top:-70px;left:-60px;right:-60px;height:360px;z-index:0;
      background:linear-gradient(90deg,rgba(255,59,0,.16) 0%,rgba(246,0,157,.16) 38%,rgba(151,62,198,.16) 71%,rgba(0,142,255,.16) 100%);
      filter:blur(64px)}}
    .hero .dots{{position:absolute;top:0;right:0;width:42%;height:100%;z-index:0;pointer-events:none;
      background-image:radial-gradient(circle,rgba(0,43,255,.30) 1px,transparent 1.4px);background-size:12px 12px;
      -webkit-mask-image:linear-gradient(to left,#000 25%,transparent 92%);mask-image:linear-gradient(to left,#000 25%,transparent 92%)}}
    .hero-inner{{position:relative;z-index:1;max-width:780px}}
    .eyebrow{{font-size:14px;font-weight:500;line-height:1.5;letter-spacing:1.8px;text-transform:uppercase;color:var(--cobalt)}}
    h1{{font-size:clamp(32px,5vw,44px);line-height:1.1;font-weight:700;letter-spacing:-.88px;margin:14px 0 18px;max-width:840px}}
    h1 .accent{{color:var(--cobalt)}}
    .lead{{font-size:18px;line-height:1.5;color:var(--graphite);max-width:640px;margin:0 0 28px}}
    .hero-actions,.actions{{display:flex;gap:12px;flex-wrap:wrap;align-items:center}}
    .hero-btn,.btn{{text-decoration:none;font-size:15px;line-height:21px;padding:8px 16px;border:1px solid var(--hairline);background:var(--canvas);color:var(--ink)}}
    .hero-btn:hover,.btn.primary:hover{{background:var(--cobalt-hover);border-color:var(--cobalt-hover)}}
    .btn:hover{{background:var(--cobalt-5)}}
    .btn.primary{{background:var(--cobalt);border-color:var(--cobalt);color:#fff}}
    .btn.ghost{{background:transparent}}
    .hero-link{{padding:8px 4px;color:var(--ink);text-decoration:underline;text-underline-offset:3px}}
    .crop{{position:relative;display:inline-block}}
    .crop i{{position:absolute;width:9px;height:9px;pointer-events:none}}
    .crop .c1{{top:-8px;left:-8px;border-top:1px solid var(--ink);border-left:1px solid var(--ink)}}
    .crop .c2{{top:-8px;right:-8px;border-top:1px solid var(--ink);border-right:1px solid var(--ink)}}
    .crop .c3{{bottom:-8px;left:-8px;border-bottom:1px solid var(--ink);border-left:1px solid var(--ink)}}
    .crop .c4{{bottom:-8px;right:-8px;border-bottom:1px solid var(--ink);border-right:1px solid var(--ink)}}
    nav.rail{{display:flex;flex-wrap:wrap;border-bottom:1px solid var(--hairline)}}
    nav.rail a{{font:400 13px/19.5px var(--mono);padding:10px 14px;color:var(--graphite);text-decoration:none;border-right:1px solid var(--hairline)}}
    nav.rail a:first-child{{border-left:1px solid var(--hairline)}}
    nav.rail a:hover{{background:var(--cobalt-5)}}
    nav.rail a.on{{background:var(--ink);color:var(--canvas)}}
    .section-head{{display:flex;align-items:end;justify-content:space-between;gap:12px;padding:34px 32px 0}}
    .section-head .eyebrow{{color:var(--pebble)}}
    .section-head h2{{font-size:32px;line-height:1.2;font-weight:700;letter-spacing:-.6px;margin:8px 0 4px}}
    .section-head p{{color:var(--graphite);margin:0;font-size:15px}}
    .jobs{{padding:22px 32px 0}}
    .grid{{display:grid;grid-template-columns:repeat(3,1fr);border-top:1px solid var(--hairline);border-left:1px solid var(--hairline)}}
    .job-card{{background:var(--paper);border-right:1px solid var(--hairline);border-bottom:1px solid var(--hairline);padding:28px 32px;display:flex;flex-direction:column}}
    .badges{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}}
    .brace{{font:400 13px/1.5 var(--mono);color:var(--cobalt)}}
    .job-card h2{{font-size:20px;line-height:1.4;font-weight:700;margin:0 0 6px}}
    .company{{color:var(--graphite);margin:0 0 16px;font-size:15px}}
    .facts{{display:flex;gap:16px;flex-wrap:wrap;font:400 13px/1.6 var(--mono);color:var(--graphite);margin-bottom:12px}}
    .facts .salary{{color:var(--cobalt)}}
    .tags{{display:flex;gap:6px;flex-wrap:wrap;min-height:24px;margin-bottom:18px}}
    .tag{{font:400 12px/1.5 var(--mono);color:var(--graphite);border:1px solid var(--hairline);padding:2px 8px}}
    .actions{{margin-top:auto}}
    .actions .btn{{font-size:14px;padding:8px 12px}}
    .source{{color:var(--pebble);font:400 12px/1.6 var(--mono);margin:16px 0 0}}
    .empty{{background:var(--paper);border:1px solid var(--hairline);padding:36px 32px}}
    .empty h2{{font-size:24px;font-weight:700;margin:0 0 10px}}
    .empty p{{color:var(--graphite);max-width:560px;margin:0}}
    footer{{margin-top:64px;border-top:1px solid var(--hairline);padding:26px 32px;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;color:var(--pebble);font:400 13px/1.7 var(--mono)}}
    footer a{{color:var(--graphite)}}
    @media(max-width:1024px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}
    @media(max-width:640px){{
      .grid{{grid-template-columns:1fr}}
      .hero{{padding:48px 20px 44px}}
      header.bar{{padding:0 20px}}
      nav.cells a{{padding:0 10px;font-size:14px}}
      .section-head,.jobs,.strip{{padding-left:20px;padding-right:20px}}
      footer{{padding:22px 20px}}
    }}
  </style>
</head>
<body>
  <div class="sheet">
    {strip}
    <header class="bar">
      <a class="brand" href="/">junior_middle_it</a>
      <nav class="cells" aria-label="Навигация">{header_cells}</nav>
    </header>
    <main>
      <section class="hero">
        <span class="dots" aria-hidden="true"></span>
        <div class="hero-inner">
          <div class="eyebrow">Junior / Middle · Remote IT</div>
          <h1>Вакансии без <span class="accent">шума</span>: подборка, Resume&nbsp;Match и алерты.</h1>
          <p class="lead">Публичная витрина проекта junior_middle_it. В подборку попадают вакансии после редакционных фильтров; персональный поиск и мгновенные уведомления доступны в Telegram.</p>
          <div class="hero-actions">{main_cta}{channel_cta}</div>
        </div>
      </section>
      <nav class="rail" aria-label="Фильтры">{''.join(category_links)}</nav>
      <div class="section-head"><div><div class="eyebrow">Свежая подборка</div><h2>Вакансии</h2><p>{html.escape(count_copy)}</p></div></div>
      {jobs_section}
    </main>
    <footer><span>Никаких платных размещений в ранжировании. Проверяй условия вакансии на странице работодателя перед откликом.</span>{f'<span><a href="{html.escape(tg_channel, quote=True)}">t.me/junior_middle_it</a></span>' if tg_channel else ''}</footer>
  </div>
</body>
</html>"""
