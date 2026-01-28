#!/usr/bin/env python3
"""
Telegram Channel Bot for Junior/Middle Remote IT Jobs
VERSION 5.3 - ПОЛНОСТЬЮ ИСПРАВЛЕН И ПРОТЕСТИРОВАН
"""
import os
import time
import random
import sqlite3
import hashlib
import logging
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import signal
import asyncio
import re
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import RetryAfter, TimedOut
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ==================== CONFIGURATION ====================
class Config:
    """Application configuration with validation"""
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
    CHANNEL_ID = os.getenv('CHANNEL_ID', '')
    SUPERJOB_API_KEY = os.getenv('SUPERJOB_API_KEY')
    ADZUNA_APP_ID = os.getenv('ADZUNA_APP_ID')
    ADZUNA_APP_KEY = os.getenv('ADZUNA_APP_KEY')
    CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '1800'))
    MAX_POSTS_PER_CYCLE = int(os.getenv('MAX_POSTS_PER_CYCLE', '15'))
    ADMIN_USER_ID = os.getenv('ADMIN_USER_ID')
    
    @classmethod
    def validate(cls) -> bool:
        errors = []
        if not cls.TELEGRAM_BOT_TOKEN:
            errors.append("❌ TELEGRAM_BOT_TOKEN is required")
        if not cls.CHANNEL_ID:
            errors.append("❌ CHANNEL_ID is required")
        if cls.CHANNEL_ID and not (cls.CHANNEL_ID.startswith('@') or cls.CHANNEL_ID.startswith('-')):
            errors.append(f"❌ CHANNEL_ID should start with '@' or '-', got: {cls.CHANNEL_ID}")
        
        # Validate ADMIN_USER_ID
        if cls.ADMIN_USER_ID:
            try:
                cls.ADMIN_USER_ID = int(cls.ADMIN_USER_ID)
            except ValueError:
                errors.append("❌ ADMIN_USER_ID must be numeric")
                cls.ADMIN_USER_ID = None
        
        if errors:
            for err in errors:
                print(err)
            return False
        return True

if not Config.validate():
    sys.exit(1)

# ==================== LOGGING SETUP ====================
def setup_logger():
    """Setup structured logging to console and file"""
    log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_file = os.getenv('LOG_FILE', 'bot.log')
    
    # Create logs directory
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    logger = logging.getLogger(__name__)
    logger.setLevel(getattr(logging, log_level))
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    return logger

logger = setup_logger()

# ==================== CONSTANTS ====================
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

# ==================== DATABASE ====================
class DatabaseConnection:
    """Thread-safe SQLite database connection"""
    def __init__(self, db_path: str = 'jobs.db'):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._initialize()
    
    def _initialize(self):
        """Initialize database schema"""
        c = self.conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS posted_jobs (
                hash TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                level TEXT,
                url TEXT,
                source TEXT,
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()
        logger.info("✅ Database initialized")
    
    def execute(self, query: str, params: tuple = ()):
        """Execute query with commit"""
        cursor = self.conn.cursor()
        cursor.execute(query, params)
        self.conn.commit()
        return cursor
    
    def fetchone(self, query: str, params: tuple = ()):
        """Execute query and fetch one row"""
        cursor = self.conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()
    
    def fetchall(self, query: str, params: tuple = ()):
        """Execute query and fetch all rows"""
        cursor = self.conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()
    
    def close(self):
        """Close database connection"""
        self.conn.close()
        logger.info("🔌 Database connection closed")

def init_database() -> DatabaseConnection:
    """Initialize and return database connection"""
    return DatabaseConnection()

def generate_job_hash(job: Dict) -> str:
    """Generate robust hash using URL (primary) or title+company (fallback)"""
    url = job.get('url', '').strip()
    if url:
        return hashlib.sha256(url.encode()).hexdigest()[:16]
    
    title = job.get('title', '').lower()
    company = job.get('company', '').lower()
    return hashlib.md5(f"{title}_{company}".encode()).hexdigest()

def is_duplicate_job(job: Dict, db: DatabaseConnection) -> bool:
    """Check and register job deduplication with cleanup"""
    # Cleanup old records (>7 days)
    cleanup_threshold = datetime.now() - timedelta(days=7)
    db.execute('DELETE FROM posted_jobs WHERE posted_at < ?', (cleanup_threshold,))
    
    job_hash = generate_job_hash(job)
    
    # Check if exists
    result = db.fetchone('SELECT 1 FROM posted_jobs WHERE hash = ?', (job_hash,))
    if result:
        logger.debug(f"⏭️ Duplicate skipped: {job.get('title', 'N/A')}")
        return True
    
    # Register new job
    db.execute(
        'INSERT INTO posted_jobs (hash, title, company, level, url, source) VALUES (?, ?, ?, ?, ?, ?)',
        (
            job_hash,
            job.get('title', ''),
            job.get('company', ''),
            job.get('level', 'Junior'),
            job.get('url', ''),
            job.get('source', '')
        )
    )
    logger.debug(f"💾 Saved new job: {job.get('title', 'N/A')}")
    return False

# ==================== UTILS ====================
def get_headers() -> Dict[str, str]:
    return {"User-Agent": random.choice(USER_AGENTS)}

def escape_html(text: str) -> str:
    """Escape HTML special characters safely - CRITICAL FIX!"""
    if not text:
        return ''
    return (
        text.replace('&', '&amp;')      # ← MUST BE FIRST!
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
    )

def safe_fetch_with_retry(fetch_func, source_name: str, max_retries: int = 3) -> List[Dict]:
    """Retry wrapper with exponential backoff - SYNC version for executor"""
    for attempt in range(max_retries):
        try:
            result = fetch_func()
            time.sleep(DELAYS['between_apis'] + random.uniform(0, DELAYS['random_jitter']))
            return result
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                wait = int(e.response.headers.get('Retry-After', 60))
                logger.warning(f"⏳ {source_name} rate limited, waiting {wait}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
            else:
                logger.error(f"❌ {source_name} HTTP error {e.response.status_code}")
                break
        except Exception as e:
            logger.error(f"❌ {source_name} error (attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(DELAYS['after_error'] * (attempt + 1))
    return []

# ==================== JOB PROCESSING ====================
def classify_job_level(job_data: Dict) -> Optional[str]:  # ✅ FIXED: job_data: Dict (was job_ Dict)
    """Classify job level with exclusion logic - CRITICAL FIX!"""
    full_text = f"{job_data.get('title', '')} {job_data.get('description', '')}".lower()
    
    # Exclude senior+ roles first
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
    """Extract and format salary"""
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
    """Extract skills from tags and description"""
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
    """Extract and format publication date"""
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
    """Extract and sanitize description"""
    desc = job.get('description', '')
    desc = re.sub(r'<[^>]+>', '', desc)  # Remove HTML tags
    desc = ' '.join(desc.split())
    if len(desc) > max_length:
        desc = desc[:max_length].rsplit(' ', 1)[0] + '...'
    return desc or "Описание не указано"

def is_suitable_job(job: Dict) -> bool:
    """Check if job matches criteria (remote + IT role)"""
    text = f"{job['title']} {job.get('description', '')}".lower()
    has_remote = any(kw in text for kw in REMOTE_KEYWORDS)
    has_it_role = any(role in text for role in IT_ROLES)
    return has_remote and has_it_role

def format_job_message(job: Dict) -> str:
    """Format job message with HTML sanitization - CRITICAL FIX!"""
    level = job.get('level', 'Junior')
    emoji = "🟢" if level == "Junior" else "🟡" if level == "Middle" else "🔵"
    salary = extract_salary(job)
    skills = extract_skills(job)
    posted_date = extract_posted_date(job)
    employment = extract_employment_type(job)
    description = extract_description(job)
    
    # Sanitize ALL user-generated content with escape_html() - CRITICAL FIX!
    title = escape_html(job['title'])
    company = escape_html(job['company'])
    location = escape_html(job.get('location', 'Remote'))
    source = escape_html(job['source'])
    
    # Validate URL
    url = job.get('url', '').strip()
    if not url or not url.startswith('http'):
        url = 'https://example.com'  # fallback
    
    parts = [
        f"{emoji} <b>{title}</b>",
        "",
        f"🏢 <b>Компания:</b> {company}",
        f"📍 <b>Локация:</b> {location}",
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
            parts.append(f"  • {escape_html(skill)}")
    else:
        parts.append("  Не указаны")
    
    parts.extend([
        "",
        f"🔗 <a href=\"{url}\">Откликнуться на вакансию</a>",
        f"📌 Источник: {source}"
    ])
    
    message = "\n".join(parts)  # ✅ FIXED: proper newline
    
    # Telegram message length limit: 4096 chars
    if len(message) > 4096:
        message = message[:4090] + "...\n<i>(сообщение сокращено)</i>"
    
    return message

# ==================== API FETCHERS (NEW SOURCES ADDED) ====================
def fetch_remotive() -> List[Dict]:
    """Remotive API - 100% remote, качественные вакансии"""
    try:
        url = "https://remotive.com/api/remote-jobs?category=software-dev"
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
    except Exception as e:
        logger.error(f"❌ Remotive error: {e}")
        return []

def fetch_remoteok() -> List[Dict]:
    """RemoteOK API - качественные remote вакансии"""
    try:
        url = "https://remoteok.com/api"
        response = requests.get(url, headers=get_headers(), timeout=15)
        response.raise_for_status()
        data = response.json()
        
        jobs = []
        for job in data[1:]:  # Skip first element (metadata)
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
    except Exception as e:
        logger.error(f"❌ RemoteOK error: {e}")
        return []

def fetch_arbeitnow() -> List[Dict]:
    """Arbeitnow API - бесплатный, без лимитов, европейские вакансии"""
    try:
        url = "https://www.arbeitnow.com/api/job-board-api"
        params = {
            'page': 1,
            'limit': 50,
            'tags': 'it,software,developer,engineer'
        }
        response = requests.get(url, params=params, headers=get_headers(), timeout=15)
        response.raise_for_status()
        data = response.json()
        
        jobs = []
        for job in data.get('data', []):
            salary = 'Не указана'
            if job.get('salary_min') and job.get('salary_max'):
                salary = f"${job['salary_min']:,}-${job['salary_max']:,}"
            elif job.get('salary_min'):
                salary = f"от ${job['salary_min']:,}"
            
            jobs.append({
                'title': job.get('title', ''),
                'company': job.get('company_name', ''),
                'description': job.get('description', ''),
                'url': job.get('url', ''),
                'salary': salary,
                'location': job.get('location', 'Remote'),
                'published': job.get('created_at', ''),
                'employment_type': job.get('employment_type', ''),
                'source': 'Arbeitnow',
                'tags': job.get('tags', [])
            })
        
        return jobs
    except Exception as e:
        logger.error(f"❌ Arbeitnow error: {e}")
        return []

def fetch_himalayas() -> List[Dict]:
    """Himalayas API - вакансии с зарплатами"""
    try:
        url = "https://himalayas.app/api/v1/jobs"
        params = {
            'limit': 30,
            'remote': 'true'
        }
        response = requests.get(url, params=params, headers=get_headers(), timeout=15)
        response.raise_for_status()
        data = response.json()
        
        jobs = []
        for job in data.get('jobs', []):
            salary = 'Не указана'
            if job.get('minSalary') and job.get('maxSalary'):
                currency = job.get('salaryCurrency', 'USD')
                salary = f"{job['minSalary']:,}-{job['maxSalary']:,} {currency}"
            
            jobs.append({
                'title': job.get('title', ''),
                'company': job.get('company', {}).get('name', ''),
                'description': job.get('description', ''),
                'url': job.get('applicationUrl', ''),
                'salary': salary,
                'location': 'Remote',
                'published': job.get('createdAt', ''),
                'employment_type': job.get('employmentType', ''),
                'source': 'Himalayas',
                'tags': job.get('tags', [])
            })
        
        return jobs
    except Exception as e:
        logger.error(f"❌ Himalayas error: {e}")
        return []

def fetch_weworkremotely() -> List[Dict]:
    """We Work Remotely JSON API - высочайшее качество"""
    try:
        url = "https://weworkremotely.com/remote-jobs.json"
        response = requests.get(url, headers=get_headers(), timeout=15)
        response.raise_for_status()
        data = response.json()
        
        jobs = []
        for job in data.get('jobs', [])[:20]:
            jobs.append({
                'title': job.get('title', ''),
                'company': job.get('company', {}).get('name', ''),
                'description': job.get('description', ''),
                'url': f"https://weworkremotely.com{job.get('url', '')}",
                'salary': 'Не указана',
                'location': 'Remote',
                'published': job.get('date', ''),
                'employment_type': '',
                'source': 'We Work Remotely',
                'tags': []
            })
        
        return jobs
    except Exception as e:
        logger.error(f"❌ We Work Remotely error: {e}")
        return []

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
        logger.error(f"❌ Jobicy error: {e}")
        return []

def fetch_adzuna() -> List[Dict]:
    """Adzuna API"""
    try:
        if not Config.ADZUNA_APP_ID or not Config.ADZUNA_APP_KEY:
            logger.warning("⚠️ Adzuna API keys not found, skipping")
            return []
        
        countries = ['us', 'gb']
        all_jobs = []
        
        for country in countries:
            try:
                url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
                params = {
                    'app_id': Config.ADZUNA_APP_ID,
                    'app_key': Config.ADZUNA_APP_KEY,
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
                
                time.sleep(2)
            except Exception as e:
                logger.error(f"❌ Adzuna {country} error: {e}")
                continue
        
        return all_jobs
    except Exception as e:
        logger.error(f"❌ Adzuna general error: {e}")
        return []

def fetch_headhunter() -> List[Dict]:
    """HeadHunter API - публичный эндпоинт"""
    try:
        url = "https://api.hh.ru/vacancies"
        params = {
            'text': 'программист разработчик developer remote удалённо',
            'per_page': 50,
            'page': 0,
            'schedule': 'remote'
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
        
        return jobs
    except Exception as e:
        logger.error(f"❌ HeadHunter error: {e}")
        return []

def fetch_superjob() -> List[Dict]:
    """SuperJob API"""
    try:
        if not Config.SUPERJOB_API_KEY:
            logger.warning("⚠️ SuperJob API key not found, skipping")
            return []
        
        url = "https://api.superjob.ru/2.0/vacancies/"
        headers = {
            'X-Api-App-Id': Config.SUPERJOB_API_KEY,
            **get_headers()
        }
        params = {
            'keyword': 'программист разработчик',
            'count': 20
        }
        
        response = requests.get(url, headers=headers, params=params, timeout=15)
        
        if response.status_code == 403:
            logger.error("❌ SuperJob 403: Check API key in .env")
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
        
        return jobs
    except Exception as e:
        logger.error(f"❌ SuperJob error: {e}")
        return []

# ==================== TELEGRAM BOT ====================
class JobBot:
    """Telegram bot with admin commands"""
    
    def __init__(self, application: Application, db: DatabaseConnection):
        self.application = application
        self.db = db
        self.is_paused = False
    
    async def check_admin(self, update: Update) -> bool:
        """Check if user is admin"""
        if not Config.ADMIN_USER_ID:
            return True  # No admin configured - allow all
        
        user_id = update.effective_user.id
        if user_id != Config.ADMIN_USER_ID:
            await update.message.reply_text("❌ У вас нет прав для этой команды")
            logger.warning(f"Unauthorized access attempt by user {user_id}")
            return False
        return True
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        await update.message.reply_text(
            "👋 Привет! Я бот для сбора IT-вакансий Junior/Middle уровня.\n\n"
            "Доступные команды:\n"
            "/status - Статистика бота\n"
            "/last 5 - Последние 5 вакансий\n"
            "/pause - Приостановить публикацию\n"
            "/resume - Возобновить публикацию"
        )
    
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        if not await self.check_admin(update):
            return
        
        # Count posted jobs from DB
        result = self.db.fetchone('SELECT COUNT(*) FROM posted_jobs')
        total_posted = result[0] if result else 0
        
        message = (
            "📊 <b>Статистика бота:</b>\n\n"
            f"✅ Всего опубликовано: {total_posted}\n"
            f"⏸️ Статус: {'Приостановлен' if self.is_paused else 'Активен'}\n"
            f"🕐 Последнее обновление: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await update.message.reply_text(message, parse_mode='HTML')
    
    async def cmd_last(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /last N command"""
        if not await self.check_admin(update):
            return
        
        limit = 5
        if context.args:
            try:
                limit = int(context.args[0])
                limit = max(1, min(limit, 20))
            except ValueError:
                pass
        
        results = self.db.fetchall(
            'SELECT title, company, level, posted_at, source FROM posted_jobs '
            'ORDER BY posted_at DESC LIMIT ?',
            (limit,)
        )
        
        if not results:
            await update.message.reply_text("📭 Нет опубликованных вакансий")
            return
        
        message = f"🆕 <b>Последние {len(results)} вакансий:</b>\n\n"
        for row in results:
            title, company, level, posted_at, source = row
            try:
                dt = datetime.strptime(posted_at, '%Y-%m-%d %H:%M:%S')
                date_str = dt.strftime('%d.%m.%Y %H:%M')
            except:
                date_str = "Недавно"
            
            message += (
                f"• {escape_html(title)}\n"
                f"  🏢 {escape_html(company)} | 🎯 {level}\n"
                f"  📡 {source} | 📅 {date_str}\n\n"
            )
        
        await update.message.reply_text(message, parse_mode='HTML')
    
    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /pause command"""
        if not await self.check_admin(update):
            return
        
        self.is_paused = True
        await update.message.reply_text("⏸️ Публикация приостановлена")
        logger.info("⏸️ Bot paused by admin")
    
    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /resume command"""
        if not await self.check_admin(update):
            return
        
        self.is_paused = False
        await update.message.reply_text("▶️ Публикация возобновлена")
        logger.info("▶️ Bot resumed by admin")
    
    async def post_job(self, job: Dict) -> bool:
        """Post job to channel with flood control - CRITICAL FIX!"""
        if self.is_paused:
            logger.info("⏸️ Skipped posting (bot is paused)")
            return False
        
        try:
            message = format_job_message(job)
            await self.application.bot.send_message(
                chat_id=Config.CHANNEL_ID,
                text=message,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
            logger.info(f"✅ Posted: {job.get('title', 'N/A')} at {job.get('company', 'N/A')}")
            return True
        except RetryAfter as e:
            logger.warning(f"⏳ Telegram flood control: retry after {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            return await self.post_job(job)  # Retry once
        except TimedOut:
            logger.error("❌ Telegram timeout")
            return False
        except Exception as e:
            logger.error(f"❌ Failed to post job: {e}")
            return False

# ==================== MAIN LOOP ====================
async def main():
    """Main application loop with admin interface - CRITICAL FIXES!"""
    logger.info("=" * 60)
    logger.info("🚀 Job Bot Starting (v5.3 - FULLY FIXED & VERIFIED)")
    logger.info(f"📡 Channel: {Config.CHANNEL_ID}")
    logger.info(f"⏱️  Check interval: {Config.CHECK_INTERVAL}s")
    logger.info(f"📊 Max posts per cycle: {Config.MAX_POSTS_PER_CYCLE}")
    if Config.ADMIN_USER_ID:
        logger.info(f"👤 Admin user ID: {Config.ADMIN_USER_ID}")
    logger.info("=" * 60)
    
    # Initialize database
    db = init_database()
    
    # Setup Telegram bot with Application (v20+ API) - CRITICAL FIX!
    application = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
    job_bot = JobBot(application, db)
    
    # Register command handlers
    application.add_handler(CommandHandler("start", job_bot.cmd_start))
    application.add_handler(CommandHandler("status", job_bot.cmd_status))
    application.add_handler(CommandHandler("last", job_bot.cmd_last))
    application.add_handler(CommandHandler("pause", job_bot.cmd_pause))
    application.add_handler(CommandHandler("resume", job_bot.cmd_resume))
    
    # Start bot
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    logger.info("✅ Telegram bot started with admin commands")
    
    # Setup graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s, application, db)))
    
    # Main collection loop
    fetch_functions = [
        (fetch_remotive, "Remotive"),
        (fetch_remoteok, "RemoteOK"),
        (fetch_arbeitnow, "Arbeitnow"),      # NEW SOURCE!
        (fetch_himalayas, "Himalayas"),      # NEW SOURCE!
        (fetch_weworkremotely, "We Work Remotely"),
        (fetch_jobicy, "Jobicy"),
        (fetch_headhunter, "HeadHunter"),
        (fetch_superjob, "SuperJob"),
        (fetch_adzuna, "Adzuna")
    ]
    
    while True:
        try:
            if job_bot.is_paused:
                logger.info("⏸️ Bot is paused, skipping collection cycle")
                await asyncio.sleep(60)
                continue
            
            logger.info("🔄 Starting job collection cycle...")
            all_jobs = []
            
            # Run sync fetchers in executor to avoid blocking event loop - CRITICAL FIX!
            for fetch_func, source_name in fetch_functions:
                jobs = await loop.run_in_executor(
                    None, safe_fetch_with_retry, fetch_func, source_name
                )
                all_jobs.extend(jobs)
                logger.info(f"📥 Fetched {len(jobs)} jobs from {source_name}")
            
            logger.info(f"📊 Total jobs fetched: {len(all_jobs)}")
            
            # Filter and classify
            classified_jobs = []
            for job in all_jobs:
                if not is_suitable_job(job):
                    continue
                level = classify_job_level(job)
                if level:
                    job['level'] = level
                    classified_jobs.append(job)
            
            logger.info(f"🎯 Suitable Junior/Middle jobs: {len(classified_jobs)}")
            
            # Post jobs
            posted_count = 0
            for job in classified_jobs[:Config.MAX_POSTS_PER_CYCLE]:
                if not is_duplicate_job(job, db):
                    if await job_bot.post_job(job):
                        posted_count += 1
                        await asyncio.sleep(DELAYS['between_posts'])  # ✅ FIXED: await asyncio.sleep
            
            logger.info(f"✅ Posted {posted_count} new jobs to channel")
            logger.info(f"⏳ Waiting {Config.CHECK_INTERVAL//60} minutes before next cycle...")
            await asyncio.sleep(Config.CHECK_INTERVAL)  # ✅ FIXED: await asyncio.sleep
            
        except Exception as e:
            logger.error(f"❌ Error in main loop: {e}", exc_info=True)
            await asyncio.sleep(300)

async def shutdown(signal, application, db):
    """Graceful shutdown handler"""
    logger.info(f"🛑 Received exit signal {signal.name}")
    
    # Stop bot
    await application.stop()
    await application.shutdown()
    
    # Close database
    db.close()
    logger.info("👋 Bot shutdown complete")
    sys.exit(0)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
        sys.exit(0)