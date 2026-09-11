"""Conservative normalization for free-text IT skills entered during onboarding.

Only common, high-confidence aliases/typos are corrected. Unknown skills are
kept as typed (lowercased) so the bot never invents expertise for a user.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:  # pragma: no cover
    fuzz = process = None
    RAPIDFUZZ_AVAILABLE = False


SKILL_ALIASES = {
    "python": ("python",),
    "django": ("django",),
    "flask": ("flask",),
    "fastapi": ("fastapi", "fast api"),
    "postgres": ("postgres", "postgresql", "postgre sql", "post gres"),
    "mysql": ("mysql", "my sql"),
    "mongodb": ("mongodb", "mongo db", "mongo"),
    "redis": ("redis",),
    "javascript": ("javascript", "java script", "js"),
    "typescript": ("typescript", "type script", "ts"),
    "react": ("react", "reactjs", "react.js", "react js"),
    "vue": ("vue", "vuejs", "vue.js"),
    "angular": ("angular",),
    "node.js": ("node.js", "nodejs", "node js"),
    "java": ("java",),
    "kotlin": ("kotlin",),
    "swift": ("swift",),
    "go": ("go", "golang"),
    "rust": ("rust",),
    "php": ("php",),
    "laravel": ("laravel",),
    "ruby": ("ruby",),
    "rails": ("rails", "ruby on rails"),
    "c#": ("c#", "csharp", "c sharp"),
    ".net": (".net", "dotnet", "dot net"),
    "c++": ("c++", "cpp"),
    "sql": ("sql",),
    "aws": ("aws", "amazon web services"),
    "azure": ("azure",),
    "gcp": ("gcp", "google cloud"),
    "docker": ("docker",),
    "kubernetes": ("kubernetes", "k8s"),
    "terraform": ("terraform",),
    "ansible": ("ansible",),
    "linux": ("linux",),
    "git": ("git",),
    "github": ("github", "git hub"),
    "gitlab": ("gitlab", "git lab"),
    "pandas": ("pandas",),
    "numpy": ("numpy",),
    "pytorch": ("pytorch", "py torch"),
    "tensorflow": ("tensorflow", "tensor flow"),
    "scikit-learn": ("scikit-learn", "sklearn", "scikit learn"),
    "airflow": ("airflow", "apache airflow"),
    "spark": ("spark", "apache spark"),
    "kafka": ("kafka", "apache kafka"),
    "selenium": ("selenium",),
    "playwright": ("playwright",),
    "pytest": ("pytest", "py test"),
    "jira": ("jira",),
    "figma": ("figma",),
    "tableau": ("tableau",),
    "power bi": ("power bi", "powerbi"),
}

DISPLAY_NAMES = {
    "python": "Python", "django": "Django", "flask": "Flask", "fastapi": "FastAPI",
    "postgres": "PostgreSQL", "mysql": "MySQL", "mongodb": "MongoDB", "redis": "Redis",
    "javascript": "JavaScript", "typescript": "TypeScript", "react": "React", "vue": "Vue",
    "angular": "Angular", "node.js": "Node.js", "java": "Java", "kotlin": "Kotlin",
    "swift": "Swift", "go": "Go", "rust": "Rust", "php": "PHP", "laravel": "Laravel",
    "ruby": "Ruby", "rails": "Rails", "c#": "C#", ".net": ".NET", "c++": "C++",
    "sql": "SQL", "aws": "AWS", "azure": "Azure", "gcp": "GCP", "docker": "Docker",
    "kubernetes": "Kubernetes", "terraform": "Terraform", "ansible": "Ansible", "linux": "Linux",
    "git": "Git", "github": "GitHub", "gitlab": "GitLab", "pandas": "Pandas", "numpy": "NumPy",
    "pytorch": "PyTorch", "tensorflow": "TensorFlow", "scikit-learn": "scikit-learn",
    "airflow": "Airflow", "spark": "Spark", "kafka": "Kafka", "selenium": "Selenium",
    "playwright": "Playwright", "pytest": "pytest", "jira": "Jira", "figma": "Figma",
    "tableau": "Tableau", "power bi": "Power BI",
}


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", "", str(value or "").casefold())


_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canonical, _aliases in SKILL_ALIASES.items():
    for _alias in (_canonical, *_aliases):
        _key = _compact(_alias)
        if _key:
            _ALIAS_TO_CANONICAL[_key] = _canonical

# Fuzzy correction is intentionally limited to unambiguous names >=5 chars.
_FUZZY_KEYS = sorted({
    _compact(canonical)
    for canonical in SKILL_ALIASES
    if len(_compact(canonical)) >= 5
})
_FUZZY_CANONICAL = {_compact(canonical): canonical for canonical in SKILL_ALIASES}


@dataclass(frozen=True)
class NormalizedSkills:
    stored: str
    display: str
    corrections: tuple[tuple[str, str], ...]


def normalize_skill_token(value: str) -> tuple[str, bool]:
    raw = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    if not raw:
        return "", False
    key = _compact(raw)
    exact = _ALIAS_TO_CANONICAL.get(key)
    if exact:
        return exact, raw != exact

    # Examples this safely catches: pyton -> python, javscript -> javascript.
    if RAPIDFUZZ_AVAILABLE and len(key) >= 5 and key.isascii():
        match = process.extractOne(key, _FUZZY_KEYS, scorer=fuzz.ratio, score_cutoff=86)
        if match:
            canonical = _FUZZY_CANONICAL.get(str(match[0]))
            if canonical:
                return canonical, True

    # Preserve unknown skills rather than guessing.
    return raw[:80], False


def normalize_skills_input(value: str) -> NormalizedSkills:
    raw = str(value or "").strip()
    if not raw:
        return NormalizedSkills("", "", ())
    parts = [item.strip() for item in re.split(r"[,;\n]+", raw) if item.strip()]
    normalized: list[str] = []
    corrections: list[tuple[str, str]] = []
    seen: set[str] = set()
    for part in parts[:20]:
        canonical, corrected = normalize_skill_token(part)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        normalized.append(canonical)
        if corrected:
            corrections.append((part, canonical))

    stored = ",".join(normalized)[:500]
    display = ", ".join(DISPLAY_NAMES.get(item, item) for item in normalized)
    return NormalizedSkills(stored, display, tuple(corrections))
