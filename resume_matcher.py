"""Privacy-first resume-to-vacancy matching without an LLM.

The matcher is deliberately conservative: it only credits skills explicitly
found in the resume text and only reports missing skills explicitly found in the
vacancy.  It is a practical lead magnet, not an ATS certification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Set


SKILL_ALIASES = {
    "python": ("python",),
    "javascript": ("javascript", "js"),
    "typescript": ("typescript", "ts"),
    "react": ("react", "react.js", "reactjs"),
    "vue": ("vue", "vue.js", "vuejs"),
    "angular": ("angular",),
    "next.js": ("next.js", "nextjs", "next js"),
    "node.js": ("node.js", "nodejs", "node js"),
    "fastapi": ("fastapi",),
    "django": ("django",),
    "flask": ("flask",),
    "java": ("java",),
    "kotlin": ("kotlin",),
    "swift": ("swift",),
    "go": ("golang", "go language"),
    "rust": ("rust",),
    "php": ("php",),
    "ruby": ("ruby",),
    "c#": ("c#", "c sharp"),
    "c++": ("c++", "cpp"),
    "sql": ("sql",),
    "postgresql": ("postgresql", "postgres", "psql"),
    "mysql": ("mysql",),
    "sqlite": ("sqlite",),
    "mongodb": ("mongodb", "mongo db", "mongo"),
    "redis": ("redis",),
    "elasticsearch": ("elasticsearch", "elastic search"),
    "docker": ("docker",),
    "kubernetes": ("kubernetes", "k8s"),
    "terraform": ("terraform",),
    "ansible": ("ansible",),
    "aws": ("aws", "amazon web services"),
    "gcp": ("gcp", "google cloud"),
    "azure": ("azure",),
    "linux": ("linux",),
    "git": ("git", "github", "gitlab"),
    "ci/cd": ("ci/cd", "continuous integration", "continuous delivery"),
    "rest api": ("rest api", "restful", "rest"),
    "graphql": ("graphql",),
    "grpc": ("grpc",),
    "html": ("html",),
    "css": ("css",),
    "tailwind": ("tailwind", "tailwindcss"),
    "selenium": ("selenium",),
    "playwright": ("playwright",),
    "cypress": ("cypress",),
    "pytest": ("pytest",),
    "junit": ("junit",),
    "figma": ("figma",),
    "jira": ("jira",),
    "scrum": ("scrum",),
    "agile": ("agile",),
    "pandas": ("pandas",),
    "numpy": ("numpy",),
    "pytorch": ("pytorch",),
    "tensorflow": ("tensorflow",),
    "scikit-learn": ("scikit-learn", "sklearn"),
    "machine learning": ("machine learning", "ml"),
    "llm": ("llm", "large language model"),
    "langchain": ("langchain",),
    "airflow": ("airflow",),
    "spark": ("apache spark", "spark"),
    "kafka": ("kafka",),
    "dbt": ("dbt",),
    "power bi": ("power bi", "powerbi"),
    "tableau": ("tableau",),
}

STOPWORDS = {
    "and", "the", "for", "with", "from", "that", "this", "you", "your",
    "our", "are", "will", "have", "has", "job", "role", "team", "work",
    "remote", "company", "looking", "experience", "years", "skills", "using",
    "или", "для", "это", "как", "что", "при", "работа", "опыт", "команда",
    "вакансия", "требования", "задачи", "будет", "нужен", "нужна", "ищем",
}


@dataclass(frozen=True)
class ResumeMatchReport:
    score: int
    matched_skills: List[str]
    missing_skills: List[str]
    required_skills: List[str]
    keyword_overlap_pct: int
    role_overlap: bool
    recommendations: List[str]


def _normalize(text: str) -> str:
    value = str(text or "").lower().replace("ё", "е")
    value = re.sub(r"[\u2010-\u2015]", "-", value)
    return re.sub(r"\s+", " ", value).strip()


def _has_alias(text: str, alias: str) -> bool:
    alias = _normalize(alias)
    if not alias:
        return False
    if re.fullmatch(r"[a-zа-я0-9+#./-]+", alias):
        pattern = r"(?<![\w])" + re.escape(alias) + r"(?![\w])"
        return re.search(pattern, text, flags=re.IGNORECASE) is not None
    return alias in text


def extract_skills(text: str) -> Set[str]:
    normalized = _normalize(text)
    found: Set[str] = set()
    for canonical, aliases in SKILL_ALIASES.items():
        if any(_has_alias(normalized, alias) for alias in aliases):
            found.add(canonical)
    return found


def job_text(job: Dict) -> str:
    tags = job.get("tags") or []
    if not isinstance(tags, Sequence) or isinstance(tags, (str, bytes)):
        tags = [str(tags)]
    return " ".join(
        [
            str(job.get("title") or ""),
            str(job.get("description") or ""),
            " ".join(str(tag) for tag in tags),
        ]
    )


def _keywords(text: str, limit: int = 40) -> Set[str]:
    tokens = re.findall(r"[a-zа-я][a-zа-я0-9+#.-]{2,}", _normalize(text))
    out: List[str] = []
    seen = set()
    for token in tokens:
        if token in STOPWORDS or token in seen or token.isdigit():
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= limit:
            break
    return set(out)


def _role_terms(title: str) -> Set[str]:
    title = _normalize(title)
    role_map = {
        "developer": ("developer", "разработчик"),
        "engineer": ("engineer", "инженер"),
        "frontend": ("frontend", "front-end", "фронтенд"),
        "backend": ("backend", "back-end", "бэкенд", "бекенд"),
        "fullstack": ("fullstack", "full-stack", "фулстек"),
        "qa": ("qa", "quality assurance", "тестировщик", "tester"),
        "devops": ("devops", "sre", "site reliability"),
        "data": ("data", "аналитик", "analyst"),
        "designer": ("designer", "дизайнер", "ux", "ui"),
        "product": ("product manager", "product owner", "продакт"),
    }
    found = set()
    for canonical, aliases in role_map.items():
        if any(alias in title for alias in aliases):
            found.add(canonical)
    return found


def analyze_resume_match(resume_text: str, job: Dict) -> ResumeMatchReport:
    resume_text = str(resume_text or "").strip()
    vacancy_text = job_text(job)
    required = extract_skills(vacancy_text)
    resume_skills = extract_skills(resume_text)
    matched = sorted(required & resume_skills)
    missing = sorted(required - resume_skills)

    if required:
        skill_pct = len(matched) / len(required)
        skill_points = 75.0 * skill_pct
    else:
        # Avoid pretending precision when a vacancy does not expose a useful stack.
        skill_points = 30.0

    vacancy_keywords = _keywords(vacancy_text)
    resume_keywords = _keywords(resume_text)
    if vacancy_keywords:
        keyword_pct = int(round(100 * len(vacancy_keywords & resume_keywords) / len(vacancy_keywords)))
    else:
        keyword_pct = 0
    keyword_points = min(20.0, keyword_pct * 0.20)

    roles = _role_terms(str(job.get("title") or ""))
    resume_roles = _role_terms(resume_text[:1500])
    role_overlap = bool(roles & resume_roles) if roles else False
    role_points = 5.0 if role_overlap else 0.0

    score = int(round(max(0.0, min(100.0, skill_points + keyword_points + role_points))))

    recommendations: List[str] = []
    if missing:
        shown = ", ".join(missing[:5])
        recommendations.append(
            "В вакансии явно встречаются навыки, которых я не нашёл в резюме: " + shown + "."
        )
        recommendations.append(
            "Если ты реально ими владеешь, добавь конкретный проект/задачу с этими технологиями; "
            "не добавляй навыки только ради ключевых слов."
        )
    if matched:
        recommendations.append(
            "Подними выше релевантный опыт с совпадающими навыками: " + ", ".join(matched[:5]) + "."
        )
    if not role_overlap and roles:
        recommendations.append(
            "В summary/заголовке резюме стоит явно отразить целевую роль: " + ", ".join(sorted(roles)) + "."
        )
    if keyword_pct < 35:
        recommendations.append(
            "Слабое текстовое пересечение с описанием вакансии: адаптируй формулировки достижений под её задачи."
        )
    if not re.search(r"\b\d+(?:[.,]\d+)?%|\b\d+[+]?\s*(?:users|пользов|requests|запрос|projects|проект)", _normalize(resume_text)):
        recommendations.append(
            "Добавь измеримый результат хотя бы к 1–2 проектам: скорость, нагрузка, пользователи, экономия или объём работы."
        )

    if not recommendations:
        recommendations.append(
            "Совпадение сильное. Перед откликом проверь, что первые 5–7 строк резюме показывают именно этот релевантный опыт."
        )

    return ResumeMatchReport(
        score=score,
        matched_skills=matched,
        missing_skills=missing,
        required_skills=sorted(required),
        keyword_overlap_pct=keyword_pct,
        role_overlap=role_overlap,
        recommendations=recommendations[:5],
    )


def format_resume_match_report(report: ResumeMatchReport, job: Dict) -> str:
    title = str(job.get("title") or "вакансия")
    company = str(job.get("company") or "").strip()
    heading = f"{title} · {company}" if company else title

    lines = [
        f"📄 Resume Match: {report.score}%",
        heading,
        "",
        "Это эвристическая оценка по тексту вакансии, не официальный ATS-score.",
    ]
    if report.matched_skills:
        lines.append("✅ Совпадает: " + ", ".join(report.matched_skills[:8]))
    if report.missing_skills:
        lines.append("⚠️ Не нашёл в резюме: " + ", ".join(report.missing_skills[:8]))
    if not report.required_skills:
        lines.append("ℹ️ В вакансии мало явно указанных технологий — точность оценки ниже.")
    lines.append(f"🔎 Пересечение ключевых слов: {report.keyword_overlap_pct}%")
    lines.append("")
    lines.append("Что улучшить перед откликом:")
    for item in report.recommendations:
        lines.append("• " + item)
    lines.append("")
    lines.append("Текст резюме не сохранялся.")
    return "\n".join(lines)[:3900]
