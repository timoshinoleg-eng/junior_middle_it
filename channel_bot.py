#!/usr/bin/env python3
"""
Telegram Channel Bot for Junior/Middle Remote IT Jobs
VERSION 4.2 FINAL - SYNTAX VERIFIED
"""

import os
import time
import random
import sqlite3
import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import requests
from telegram import Bot
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
SUPERJOB_API_KEY = os.getenv('SUPERJOB_API_KEY')
ADZUNA_APP_ID = os.getenv('ADZUNA_APP_ID')
ADZUNA_APP_KEY = os.getenv('ADZUNA_APP_KEY')

CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', 1800))
MAX_POSTS_PER_CYCLE = int(os.getenv('MAX_POSTS_PER_CYCLE', 15))

DELAYS = {
    'between_apis': 5,
    'random_jitter': 2,
    'after_error': 30,
    'between_posts': 3
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
]

JUNIOR_SIGNALS = [
    "junior", "jr", "jr.", "entry level", "entry-level", "entry",
    "trainee", "graduate", "начинающий", "начальный",
    "0-1 year", "0-2 years", "1 year", "1+ year", "1-2 years",
    "no experience", "без опыта", "beginner"
]

MIDDLE_SIGNALS = [
    "middle", "mid-level", "mid level", "intermediate",
    "2-3 years", "2-4 years", "3-5 years", "2+ years", "3+ years"
]

EXCLUDE_SIGNALS = [
    "senior", "sr.", "sr ", "lead", "principal", "staff engineer",
    "architect", "head of", "director", "manager", "vp",
    "vice president", "cto", "cfo", "chief", "c-level",
    "старший", "ведущий", "руководитель", "главный"
]

IT_ROLES = [
    "developer", "engineer", "programmer", "designer", "qa", "tester",
    "analyst", "frontend", "backend", "full-stack", "fullstack",
    "devops", "product manager", "data scientist", "data analyst",
    "mobile", "ios", "android", "react", "vue", "angular",
    "python", "javascript", "java", "php", "ruby", "go", "rust",
    "node", "web developer", "software", "support engineer",
    "разработчик", "программист", "инженер", "тестировщик"
]

REMOTE_KEYWORDS = ["remote", "удаленно", "удалённо", "work from home", "дистанционно", "wfh"]

TECH_STACK = [
    'Python', 'JavaScript', 'TypeScript', 'React', 'Vue', 'Angular',
    'Node.js', 'Django', 'Flask', 'FastAPI', 'Express', 'Next.js',
    'PostgreSQL', 'MongoDB', 'MySQL', 'Redis', 'SQLite',
    'Docker', 'Kubernetes', 'AWS', 'Azure', 'GCP',
    'Git', 'CI/CD', 'REST API', 'GraphQL',
    'HTML', 'CSS', 'SASS', 'Tailwind',
    'Figma', 'Sketch',
    'Java', 'C#', 'Go', 'Rust', 'PHP', 'Ruby', 'Swift', 'Kotlin'
]

def classify_job_level(job_data: Dict) -> Optional[str]:
    """Classify job level"""
    full_text = f"{job_data.get('title', '')} {job_data.get('description', '')}".lower()

    if any(word in full_text for word in EXCLUDE_SIGNALS):
        return None

    if any(signal in full_text for signal in JUNIOR_SIGNALS):
        return "Junior"

    if any(signal in full_text for signal in MIDDLE_SIGNALS):
        return "Middle"

    if any(role in full_text for role in IT_ROLES):
        return "Junior"

    return None

def extract_salary(job: Dict) -> str:
    """Extract salary"""
    salary_raw = job.get('salary', '')
    if salary_raw and salary_raw not in ['', 'Not specified', 'Не указана']:
        return salary_raw

    min_sal = job.get('minSalary', 0) or job.get('salary_min', 0)
    max_sal = job.get('maxSalary', 0) or job.get('salary_max', 0)

    if min_sal and max_sal and (min_sal > 0 or max_sal > 0):
        currency = job.get('currency', 'USD')
        if min_sal > 0 and max_sal > 0:
            return f"${min_sal:,}-${max_sal:,} {currency}"
        elif max_sal > 0:
            return f"до ${max_sal:,} {currency}"

    return 'Не указана'

def extract_skills(job: Dict) -> List[str]:
    """Extract skills"""
    skills = set()

    tags = job.get('tags', [])
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and len(tag) < 25:
                skills.add(tag.strip().title())

    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    for tech in TECH_STACK:
        if tech.lower() in text:
            skills.add(tech)

    return sorted(list(skills))[:5]

def extract_posted_date(job: Dict) -> str:
    """Extract date"""
    date_raw = job.get('published') or job.get('created') or job.get('publication_date') or job.get('date_published')

    if not date_raw:
        return "Недавно"

    try:
        dt = datetime.fromisoformat(str(date_raw).replace('Z', '+00:00'))
        months_ru = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
        return f"{dt.day} {months_ru[dt.month-1]} {dt.year}"
    except:
        try:
            dt = datetime.strptime(str(date_raw)[:10], '%Y-%m-%d')
            months_ru = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
            return f"{dt.day} {months_ru[dt.month-1]} {dt.year}"
        except:
            return "Недавно"

def extract_employment_type(job: Dict) -> str:
    """Extract employment type"""
    emp = job.get('employment_type', '') or job.get('type', '') or job.get('job_type', '') or job.get('contract_type', '')
    emp_lower = str(emp).lower()

    if 'full' in emp_lower or 'полная' in emp_lower:
        return "⏰ Полная занятость"
    elif 'part' in emp_lower or 'частичная' in emp_lower:
        return "⏱ Частичная занятость"
    elif 'contract' in emp_lower or 'контракт' in emp_lower:
        return "📝 Контракт"
    elif emp:
        return f"⏰ {emp}"
    return "⏰ Не указана"

def extract_description(job: Dict, max_length: int = 350) -> str:
    """Extract description"""
    desc = job.get('description', '')
    desc = re.sub(r'<[^>]+>', '', desc)
    desc = ' '.join(desc.split())

    if len(desc) > max_length:
        desc = desc[:max_length].rsplit(' ', 1)[0] + '...'

    return desc or "Описание не указано"

def get_headers() -> Dict[str, str]:
    return {"User-Agent": random.choice(USER_AGENTS)}

def safe_fetch_with_retry(fetch_func, source_name: str, max_retries: int = 3) -> List[Dict]:
    """Retry wrapper"""
    for attempt in range(max_retries):
        try:
            result = fetch_func()
            time.sleep(DELAYS['between_apis'] + random.uniform(0, DELAYS['random_jitter']))
            return result
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                wait = int(e.response.headers.get('Retry-After', 60))
                logger.warning(f"⏳ {source_name} rate limit, waiting {wait}s")
                time.sleep(wait)
            else:
                break
        except Exception as e:
            logger.error(f"❌ {source_name} error: {e}")
            if attempt < max_retries - 1:
                time.sleep(DELAYS['after_error'])
    return []

def fetch_remoteok() -> List[Dict]:
    """RemoteOK API"""
    url = "https://remoteok.com/api"
    response = requests.get(url, headers=get_headers(), timeout=15)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for job in data[1:]:
        jobs.append({
            'title': job.get('position', ''),
            'company': job.get('company', ''),
            'description': job.get('description', ''),
            'url': job.get('url', ''),
            'salary': job.get('salary', ''),
            'location': job.get('location', 'Remote'),
            'published': job.get('date', ''),
            'employment_type': job.get('position_type', ''),
            'source': 'RemoteOK',
            'tags': job.get('tags', [])
        })
    return jobs

def fetch_remotive() -> List[Dict]:
    """Remotive API"""
    url = "https://remotive.com/api/remote-jobs"
    response = requests.get(url, headers=get_headers(), timeout=15)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for job in data.get('jobs', []):
        jobs.append({
            'title': job.get('title', ''),
            'company': job.get('company_name', ''),
            'description': job.get('description', ''),
            'url': job.get('url', ''),
            'salary': job.get('salary', ''),
            'location': job.get('candidate_required_location', 'Remote'),
            'published': job.get('publication_date', ''),
            'employment_type': job.get('job_type', ''),
            'source': 'Remotive',
            'tags': job.get('tags', [])
        })
    return jobs

def fetch_jobicy() -> List[Dict]:
    """Jobicy API"""
    try:
        url = "https://jobicy.com/api/v2/remote-jobs?count=50"
        response = requests.get(url, headers=get_headers(), timeout=15)
        response.raise_for_status()
        data = response.json()

        jobs = []
        for job in data.get('jobs', []):
            jobs.append({
                'title': job.get('jobTitle', ''),
                'company': job.get('companyName', ''),
                'description': job.get('jobExcerpt', ''),
                'url': job.get('url', ''),
                'salary': '',
                'location': job.get('jobGeo', 'Remote'),
                'published': job.get('jobPosted', ''),
                'employment_type': job.get('jobType', ''),
                'source': 'Jobicy',
                'tags': []
            })
        return jobs
    except Exception as e:
        logger.error(f"Jobicy error: {e}")
        return []

def fetch_adzuna() -> List[Dict]:
    """Adzuna API - FIXED"""
    try:
        if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
            logger.warning("⚠️ Adzuna API keys not found, skipping")
            return []

        countries = ['us', 'gb']
        all_jobs = []

        for country in countries:
            try:
                url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"

                params = {
                    'app_id': ADZUNA_APP_ID,
                    'app_key': ADZUNA_APP_KEY,
                    'results_per_page': 30,
                    'what': 'developer programmer engineer',
                    'where': 'remote',
                    'sort_by': 'date'
                }

                response = requests.get(url, params=params, timeout=15)
                response.raise_for_status()
                data = response.json()

                for job in data.get('results', []):
                    salary = 'Не указана'
                    if job.get('salary_min') and job.get('salary_max'):
                        salary = f"${job['salary_min']:,.0f}-${job['salary_max']:,.0f}"
                    elif job.get('salary_min'):
                        salary = f"от ${job['salary_min']:,.0f}"

                    all_jobs.append({
                        'title': job.get('title', ''),
                        'company': job.get('company', {}).get('display_name', ''),
                        'description': job.get('description', ''),
                        'url': job.get('redirect_url', ''),
                        'salary': salary,
                        'location': job.get('location', {}).get('display_name', 'Remote'),
                        'published': job.get('created', ''),
                        'employment_type': job.get('contract_type', ''),
                        'source': 'Adzuna',
                        'tags': []
                    })

                logger.info(f"✅ Adzuna {country.upper()} успешно: {len(data.get('results', []))} вакансий")
                time.sleep(2)

            except Exception as e:
                logger.error(f"❌ Adzuna {country} error: {e}")
                continue

        logger.info(f"✅ Adzuna ИТОГО: {len(all_jobs)} вакансий")
        return all_jobs

    except Exception as e:
        logger.error(f"❌ Adzuna general error: {e}")
        return []

def fetch_headhunter() -> List[Dict]:
    """HeadHunter API"""
    try:
        url = "https://api.hh.ru/vacancies"

        params = {
            'text': 'программист разработчик developer',
            'per_page': 50,
            'page': 0
        }

        headers = get_headers()

        response = requests.get(url, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()

        jobs = []
        for item in data.get('items', []):
            salary_info = item.get('salary')
            salary = 'Не указана'

            if salary_info:
                currency = salary_info.get('currency', 'RUB')
                if salary_info.get('from') and salary_info.get('to'):
                    salary = f"{salary_info['from']:,}-{salary_info['to']:,} {currency}"
                elif salary_info.get('from'):
                    salary = f"от {salary_info['from']:,} {currency}"
                elif salary_info.get('to'):
                    salary = f"до {salary_info['to']:,} {currency}"

            snippet = item.get('snippet', {})
            description = f"{snippet.get('requirement', '')} {snippet.get('responsibility', '')}"

            employment = item.get('employment', {})
            employment_name = employment.get('name', '') if isinstance(employment, dict) else ''

            jobs.append({
                'title': item.get('name', ''),
                'company': item.get('employer', {}).get('name', ''),
                'description': description,
                'url': item.get('alternate_url', ''),
                'salary': salary,
                'location': item.get('area', {}).get('name', 'Удалённо'),
                'published': item.get('published_at', ''),
                'employment_type': employment_name,
                'source': 'HeadHunter',
                'tags': []
            })

        logger.info(f"✅ HeadHunter успешно: {len(jobs)} вакансий")
        return jobs

    except Exception as e:
        logger.error(f"❌ HeadHunter error: {e}")
        return []

def fetch_superjob() -> List[Dict]:
    """SuperJob API"""
    try:
        if not SUPERJOB_API_KEY:
            logger.warning("⚠️ SuperJob API key not found, skipping")
            return []

        url = "https://api.superjob.ru/2.0/vacancies/"

        headers = {
            'X-Api-App-Id': SUPERJOB_API_KEY,
            **get_headers()
        }

        params = {
            'keyword': 'программист разработчик',
            'count': 20
        }

        response = requests.get(url, headers=headers, params=params, timeout=15)

        if response.status_code == 403:
            logger.error("❌ SuperJob 403: Проверь API ключ в .env")
            return []

        response.raise_for_status()
        data = response.json()

        jobs = []
        for item in data.get('objects', []):
            salary = 'Не указана'
            if item.get('payment_from') and item.get('payment_to'):
                salary = f"{item['payment_from']:,}-{item['payment_to']:,} RUB"
            elif item.get('payment_from'):
                salary = f"от {item['payment_from']:,} RUB"

            employment_type = item.get('type_of_work', {})
            employment_name = employment_type.get('title', '') if isinstance(employment_type, dict) else ''

            jobs.append({
                'title': item.get('profession', ''),
                'company': item.get('firm_name', ''),
                'description': item.get('candidat', ''),
                'url': item.get('link', ''),
                'salary': salary,
                'location': 'Удалённо',
                'published': str(item.get('date_published', '')),
                'employment_type': employment_name,
                'source': 'SuperJob',
                'tags': []
            })

        logger.info(f"✅ SuperJob успешно: {len(jobs)} вакансий")
        return jobs

    except Exception as e:
        logger.error(f"❌ SuperJob error: {e}")
        return []

def is_suitable_job(job: Dict) -> bool:
    """Check if job is suitable"""
    text = f"{job['title']} {job.get('description', '')}".lower()
    has_remote = any(kw in text for kw in REMOTE_KEYWORDS)
    has_it_role = any(role in text for role in IT_ROLES)
    return has_remote and has_it_role

def init_database() -> sqlite3.Connection:
    """Initialize database"""
    conn = sqlite3.connect('jobs.db')
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS posted_jobs (
            hash TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            level TEXT,
            posted_at TIMESTAMP
        )
    """)
    conn.commit()
    return conn

def is_duplicate_job(job: Dict, conn: sqlite3.Connection) -> bool:
    """Check duplicate"""
    c = conn.cursor()
    job_hash = hashlib.md5(f"{job['title']}_{job['company']}".lower().encode()).hexdigest()

    c.execute('DELETE FROM posted_jobs WHERE posted_at < ?',
              (datetime.now() - timedelta(days=7),))

    c.execute('SELECT 1 FROM posted_jobs WHERE hash = ?', (job_hash,))
    if c.fetchone():
        return True

    c.execute('INSERT INTO posted_jobs VALUES (?, ?, ?, ?, ?)',
              (job_hash, job['title'], job['company'], job.get('level', 'Junior'), datetime.now()))
    conn.commit()
    return False

def format_job_message(job: Dict) -> str:
    """Format job message - SAFE VERSION"""
    level = job.get('level', 'Junior')
    emoji = "🟢" if level == "Junior" else "🟡"

    salary = extract_salary(job)
    skills = extract_skills(job)
    posted_date = extract_posted_date(job)
    employment = extract_employment_type(job)
    description = extract_description(job)

    # Build message using list
    parts = [
        f"{emoji} <b>{job['title']}</b>",
        "",
        f"🏢 <b>Компания:</b> {job['company']}",
        f"📍 <b>Локация:</b> {job.get('location', 'Remote')}",
        f"💵 <b>Зарплата:</b> {salary}",
        f"🎯 <b>Уровень:</b> {level}",
        f"📅 <b>Дата публикации:</b> {posted_date}",
        employment,
        "",
        f"📋 <b>Описание:</b>",
        description,
        "",
        "<b>🛠 Навыки:</b>"
    ]

    if skills:
        for skill in skills:
            parts.append(f"  • {skill}")
    else:
        parts.append("  Не указаны")

    parts.extend([
        "",
        f"🔗 <a href=\"{job['url']}\">Откликнуться на вакансию</a>",
        f"📌 Источник: {job['source']}"
    ])

    return "\n".join(parts)

async def post_job_to_channel(job: Dict, bot: Bot) -> bool:
    """Post to channel"""
    try:
        message = format_job_message(job)
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        return True
    except Exception as e:
        logger.error(f"Failed to post job: {e}")
        return False

async def main():
    """Main loop"""
    if not TELEGRAM_BOT_TOKEN or not CHANNEL_ID:
        logger.error("Missing TELEGRAM_BOT_TOKEN or CHANNEL_ID")
        return

    logger.info("🚀 Bot started! Junior/Middle remote IT jobs every 30 minutes...")
    logger.info("📡 Sources: 6 stable (RemoteOK, Remotive, Jobicy, HeadHunter, SuperJob, Adzuna)")

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    conn = init_database()

    fetch_functions = [
        (fetch_remoteok, "RemoteOK"),
        (fetch_remotive, "Remotive"),
        (fetch_jobicy, "Jobicy"),
        (fetch_headhunter, "HeadHunter"),
        (fetch_superjob, "SuperJob"),
        (fetch_adzuna, "Adzuna")
    ]

    while True:
        try:
            all_jobs = []

            for fetch_func, source_name in fetch_functions:
                jobs = safe_fetch_with_retry(fetch_func, source_name)
                all_jobs.extend(jobs)
                logger.info(f"📥 Fetched {len(jobs)} jobs from {source_name}")

            logger.info(f"📊 Total jobs fetched: {len(all_jobs)}")

            classified_jobs = []
            for job in all_jobs:
                if not is_suitable_job(job):
                    continue

                level = classify_job_level(job)
                if level:
                    job['level'] = level
                    classified_jobs.append(job)

            logger.info(f"🎯 Suitable Junior/Middle jobs: {len(classified_jobs)}")

            posted_count = 0
            for job in classified_jobs[:MAX_POSTS_PER_CYCLE]:
                if not is_duplicate_job(job, conn):
                    if await post_job_to_channel(job, bot):
                        posted_count += 1
                        time.sleep(DELAYS['between_posts'])

            logger.info(f"✅ Posted {posted_count} new jobs to channel")

            logger.info(f"⏳ Waiting {CHECK_INTERVAL//60} minutes before next cycle...")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            logger.info("🛑 Bot stopped by user")
            break
        except Exception as e:
            logger.error(f"❌ Error in main loop: {e}")
            time.sleep(300)

    conn.close()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
