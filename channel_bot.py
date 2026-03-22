#!/usr/bin/env python3
"""
Telegram Channel Bot for Junior/Middle Remote IT Jobs
VERSION 6.0 - С улучшенными источниками, классификацией и форматированием

Новые возможности:
- 10 Telegram-каналов в качестве источников (Telethon)
- Автоматическая классификация по 7+ категориям
- MarkdownV2 форматирование с inline-кнопками
- Избранное и настройки пользователей
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
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, ContextTypes, 
    CallbackQueryHandler, filters
)
from telegram.error import RetryAfter, TimedOut
from telegram.constants import ParseMode
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Импорт новых модулей (с fallback)
try:
    from job_classifier import JobClassifier, get_job_category_info
    CLASSIFIER_AVAILABLE = True
except ImportError:
    CLASSIFIER_AVAILABLE = False
    logging.warning("⚠️ job_classifier не найден, классификация отключена")

try:
    from telegram_job_parser import TelegramJobParser, fetch_telegram_jobs
    TELEGRAM_PARSER_AVAILABLE = True
except ImportError:
    TELEGRAM_PARSER_AVAILABLE = False
    logging.warning("⚠️ telegram_job_parser не найден, Telegram-каналы отключены")

try:
    from message_formatter import JobMessageFormatter, format_job_message
    FORMATTER_AVAILABLE = True
except ImportError:
    FORMATTER_AVAILABLE = False
    logging.warning("⚠️ message_formatter не найден, используется стандартное форматирование")

# ==================== CONFIGURATION ====================
class Config:
    """Application configuration with validation"""
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
    CHANNEL_ID = os.getenv('CHANNEL_ID', '')
    SUPERJOB_API_KEY = os.getenv('SUPERJOB_API_KEY')
    ADZUNA_APP_ID = os.getenv('ADZUNA_APP_ID')
    ADZUNA_APP_KEY = os.getenv('ADZUNA_APP_KEY')
    TELEGRAM_API_ID = os.getenv('TELEGRAM_API_ID')
    TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')
    CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '1800'))
    MAX_POSTS_PER_CYCLE = int(os.getenv('MAX_POSTS_PER_CYCLE', '15'))
    ADMIN_USER_ID = os.getenv('ADMIN_USER_ID')
    ENABLE_TELEGRAM_CHANNELS = os.getenv('ENABLE_TELEGRAM_CHANNELS', 'true').lower() == 'true'
    ENABLE_MARKDOWN_V2 = os.getenv('ENABLE_MARKDOWN_V2', 'true').lower() == 'true'
    
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
    "разработчик", "программист", "инженер", "тестировщик",
    "менеджер проекта", "product owner", "scrum master"
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

CATEGORY_NAMES_RU = {
    'development': 'Разработка',
    'qa': 'QA',
    'devops': 'DevOps',
    'data': 'Данные',
    'marketing': 'Маркетинг',
    'sales': 'Продажи',
    'pm': 'Менеджмент',
    'design': 'Дизайн',
    'support': 'Поддержка',
    'security': 'Безопасность',
    'other': 'Другое',
}

# ==================== DATABASE ====================
class DatabaseConnection:
    """Thread-safe SQLite database connection with enhanced schema"""
    def __init__(self, db_path: str = 'jobs.db'):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._initialize()
    
    def _initialize(self):
        """Initialize database schema with migrations"""
        c = self.conn.cursor()
        
        # Main jobs table
        c.execute("""
            CREATE TABLE IF NOT EXISTS posted_jobs (
                hash TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                level TEXT,
                url TEXT,
                source TEXT,
                category TEXT DEFAULT 'other',
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # User favorites
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_hash TEXT NOT NULL,
                saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, job_hash)
            )
        """)
        
        # User settings
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                enabled_categories TEXT DEFAULT 'development,qa,devops,data,marketing,sales,pm,design,other',
                hide_senior BOOLEAN DEFAULT 1,
                min_salary_filter INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Telegram content hashes for dedup
        c.execute("""
            CREATE TABLE IF NOT EXISTS telegram_content_hashes (
                hash TEXT PRIMARY KEY,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Indexes
        c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_category ON posted_jobs(category)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON posted_jobs(posted_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_favorites_user ON user_favorites(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tg_hashes_created ON telegram_content_hashes(created_at)")
        
        # Migration: add category if not exists
        try:
            c.execute("SELECT category FROM posted_jobs LIMIT 1")
        except sqlite3.OperationalError:
            c.execute("ALTER TABLE posted_jobs ADD COLUMN category TEXT DEFAULT 'other'")
        
        self.conn.commit()
        logger.info("✅ Database initialized")
    
    def execute(self, query: str, params: tuple = ())->"sqlite3.Cursor":
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
    
    # User favorites methods
    def add_favorite(self, user_id: int, job_hash: str) -> bool:
        """Add job to user favorites"""
        try:
            self.execute(
                'INSERT OR IGNORE INTO user_favorites (user_id, job_hash) VALUES (?, ?)',
                (user_id, job_hash)
            )
            return True
        except Exception as e:
            logger.error(f"Error adding favorite: {e}")
            return False
    
    def remove_favorite(self, user_id: int, job_hash: str) -> bool:
        """Remove job from user favorites"""
        try:
            self.execute(
                'DELETE FROM user_favorites WHERE user_id = ? AND job_hash = ?',
                (user_id, job_hash)
            )
            return True
        except Exception as e:
            logger.error(f"Error removing favorite: {e}")
            return False
    
    def get_user_favorites(self, user_id: int) -> List[Dict]:
        """Get user's favorite jobs"""
        results = self.fetchall("""
            SELECT j.hash, j.title, j.company, j.level, j.category, j.url
            FROM user_favorites f
            JOIN posted_jobs j ON f.job_hash = j.hash
            WHERE f.user_id = ?
            ORDER BY f.saved_at DESC
        """, (user_id,))
        
        return [
            {
                'hash': row[0],
                'title': row[1],
                'company': row[2],
                'level': row[3],
                'category': row[4],
                'url': row[5],
            }
            for row in results
        ]
    
    # User settings methods
    def get_user_settings(self, user_id: int) -> Dict:
        """Get user settings"""
        result = self.fetchone(
            'SELECT enabled_categories, hide_senior, min_salary_filter FROM user_settings WHERE user_id = ?',
            (user_id,)
        )
        
        if result:
            return {
                'enabled_categories': result[0].split(','),
                'hide_senior': bool(result[1]),
                'min_salary_filter': result[2],
            }
        
        # Default settings
        return {
            'enabled_categories': list(CATEGORY_NAMES_RU.keys()),
            'hide_senior': True,
            'min_salary_filter': 0,
        }
    
    def update_user_categories(self, user_id: int, categories: List[str]) -> bool:
        """Update user's enabled categories"""
        try:
            self.execute("""
                INSERT OR REPLACE INTO user_settings (user_id, enabled_categories, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (user_id, ','.join(categories)))
            return True
        except Exception as e:
            logger.error(f"Error updating categories: {e}")
            return False
    
    def hide_category_for_user(self, user_id: int, category: str) -> bool:
        """Hide category for user"""
        settings = self.get_user_settings(user_id)
        enabled = [c for c in settings['enabled_categories'] if c != category]
        return self.update_user_categories(user_id, enabled)


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
    job['hash'] = job_hash  # Сохраняем hash в job для дальнейшего использования
    
    # Check if exists
    result = db.fetchone('SELECT 1 FROM posted_jobs WHERE hash = ?', (job_hash,))
    if result:
        logger.debug(f"⏭️ Duplicate skipped: {job.get('title', 'N/A')}")
        return True
    
    # Register new job
    db.execute(
        'INSERT INTO posted_jobs (hash, title, company, level, url, source, category) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (
            job_hash,
            job.get('title', ''),
            job.get('company', ''),
            job.get('level', 'Junior'),
            job.get('url', ''),
            job.get('source', ''),
            job.get('category', 'other')
        )
    )
    logger.debug(f"💾 Saved new job: {job.get('title', 'N/A')}")
    return False

# ==================== UTILS ====================
def get_headers() -> Dict[str, str]:
    return {"User-Agent": random.choice(USER_AGENTS)}


def escape_html(text: str) -> str:
    """Escape HTML special characters safely"""
    if not text:
        return ''
    return (
        text.replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
    )


def safe_fetch_with_retry(fetch_func, source_name: str, max_retries: int = 3) -> List[Dict]:
    """Retry wrapper with exponential backoff"""
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
def classify_job_level(job_data: Dict) -> Optional[str]:
    """Classify job level with exclusion logic"""
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


def auto_classify_category(job: Dict) -> str:
    """Автоматическая классификация категории"""
    if CLASSIFIER_AVAILABLE:
        try:
            classifier = JobClassifier()
            return classifier.classify(job)
        except Exception as e:
            logger.error(f"Error classifying job: {e}")
    return 'other'


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
        return f"{dt.day} {months_ru[dt.month-1]}"
    except:
        try:
            dt = datetime.strptime(str(date_raw)[:10], '%Y-%m-%d')
            months_ru = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
            return f"{dt.day} {months_ru[dt.month-1]}"
        except:
            return "Недавно"


def extract_employment_type(job: Dict) -> str:
    """Extract employment type"""
    emp = job.get('employment_type', '') or job.get('type', '') or job.get('job_type', '') or job.get('contract_type', '')
    emp_lower = str(emp).lower()
    if 'full' in emp_lower or 'полная' in emp_lower:
        return "⏰ Полная"
    elif 'part' in emp_lower or 'частичная' in emp_lower:
        return "⏱ Частичная"
    elif 'contract' in emp_lower or 'контракт' in emp_lower:
        return "📝 Контракт"
    elif emp:
        return f"⏰ {emp}"
    return "⏰ Не указана"


def extract_description(job: Dict, max_length: int = 350) -> str:
    """Extract and sanitize description"""
    desc = job.get('description', '')
    desc = re.sub(r'<[^>]+>', '', desc)
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


def format_job_message_legacy(job: Dict) -> str:
    """Legacy HTML formatter (fallback)"""
    level = job.get('level', 'Junior')
    emoji = "🟢" if level == "Junior" else "🟡" if level == "Middle" else "🔵"
    salary = extract_salary(job)
    skills = extract_skills(job)
    posted_date = extract_posted_date(job)
    employment = extract_employment_type(job)
    description = extract_description(job)
    
    title = escape_html(job['title'])
    company = escape_html(job['company'])
    location = escape_html(job.get('location', 'Remote'))
    source = escape_html(job['source'])
    category = job.get('category', 'other')
    cat_emoji = {'development': '💻', 'qa': '🧪', 'devops': '🔧', 'data': '📊', 
                 'marketing': '📢', 'sales': '💼', 'pm': '📋', 'design': '🎨'}.get(category, '📌')
    
    url = job.get('url', '').strip()
    if not url or not url.startswith('http'):
        url = 'https://example.com'
    
    parts = [
        f"{cat_emoji} <b>{title}</b>",
        "",
        f"🏢 <b>Компания:</b> {company}",
        f"📍 <b>Локация:</b> {location}",
        f"💵 <b>Зарплата:</b> {salary}",
        f"🎯 <b>Уровень:</b> {level}",
        f"📅 <b>Дата:</b> {posted_date} | {employment}",
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
    
    message = "\n".join(parts)
    
    if len(message) > 4096:
        message = message[:4090] + "...\n<i>(сообщение сокращено)</i>"
    
    return message

# ==================== API FETCHERS ====================
def fetch_remotive() -> List[Dict]:
    """Remotive API - 100% remote"""
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
    """RemoteOK API"""
    try:
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
    except Exception as e:
        logger.error(f"❌ RemoteOK error: {e}")
        return []


def fetch_arbeitnow() -> List[Dict]:
    """Arbeitnow API"""
    try:
        url = "https://www.arbeitnow.com/api/job-board-api"
        params = {'page': 1, 'limit': 50, 'tags': 'it,software,developer,engineer'}
        response = requests.get(url, params=params, headers=get_headers(), timeout=15)
        response.raise_for_status()
        data = response.json()
        
        jobs = []
        for job in data.get('data', []):
            salary = 'Не указана'
            if job.get('salary_min') and job.get('salary_max'):
                salary = f"${job['salary_min']:,}-${job['salary_max']:}"
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
    """Himalayas API"""
    try:
        url = "https://himalayas.app/api/v1/jobs"
        params = {'limit': 30, 'remote': 'true'}
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
    """We Work Remotely JSON API"""
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
    """HeadHunter API"""
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
        headers = {'X-Api-App-Id': Config.SUPERJOB_API_KEY, **get_headers()}
        params = {'keyword': 'программист разработчик', 'count': 20}
        
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


async def fetch_telegram_channels() -> List[Dict]:
    """Fetch jobs from Telegram channels"""
    if not Config.ENABLE_TELEGRAM_CHANNELS:
        return []
    
    if not TELEGRAM_PARSER_AVAILABLE:
        logger.debug("⚠️ Telegram parser not available")
        return []
    
    if not Config.TELEGRAM_API_ID or not Config.TELEGRAM_API_HASH:
        logger.debug("⚠️ Telegram API credentials not configured")
        return []
    
    try:
        # Для cron-запуска используем 1 час, для ручного - 24 часа
        hours_back = 1
        jobs = await fetch_telegram_jobs(hours_back=hours_back)
        logger.info(f"📥 Fetched {len(jobs)} jobs from Telegram channels")
        return jobs
    except Exception as e:
        logger.error(f"❌ Telegram channels error: {e}")
        return []

# ==================== TELEGRAM BOT ====================
class JobBot:
    """Telegram bot with enhanced features"""
    
    def __init__(self, application: Application, db: DatabaseConnection):
        self.application = application
        self.db = db
        self.is_paused = False
        self.formatter = JobMessageFormatter() if FORMATTER_AVAILABLE else None
        self.classifier = JobClassifier() if CLASSIFIER_AVAILABLE else None
    
    async def check_admin(self, update: Update) -> bool:
        """Check if user is admin"""
        if not Config.ADMIN_USER_ID:
            return True
        
        user_id = update.effective_user.id
        if user_id != Config.ADMIN_USER_ID:
            await update.message.reply_text("❌ У вас нет прав для этой команды")
            logger.warning(f"Unauthorized access attempt by user {user_id}")
            return False
        return True
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_text = (
            "👋 Привет! Я бот для сбора IT-вакансий Junior/Middle уровня.\n\n"
            "*Доступные команды:*\n"
            "/status - Статистика бота\n"
            "/last N - Последние N вакансий\n"
            "/favorites - Мои сохраненные вакансии\n"
            "/categories - Настройка категорий\n"
            "/pause - Приостановить публикацию (admin)\n"
            "/resume - Возобновить публикацию (admin)"
        )
        
        await update.message.reply_text(
            welcome_text,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        if not await self.check_admin(update):
            return
        
        result = self.db.fetchone('SELECT COUNT(*) FROM posted_jobs')
        total_posted = result[0] if result else 0
        
        # Count by category
        cat_results = self.db.fetchall(
            'SELECT category, COUNT(*) FROM posted_jobs GROUP BY category'
        )
        categories = {row[0]: row[1] for row in cat_results}
        
        stats = {
            'total_jobs': total_posted,
            'total_sources': 9 + (10 if Config.ENABLE_TELEGRAM_CHANNELS else 0),
            'is_paused': self.is_paused,
            'last_update': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'categories': categories,
        }
        
        if self.formatter:
            message = self.formatter.format_status_message(stats)
            await update.message.reply_text(
                message,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            message = (
                "📊 <b>Статистика бота:</b>\n\n"
                f"✅ Всего опубликовано: {total_posted}\n"
                f"⏸️ Статус: {'Приостановлен' if self.is_paused else 'Активен'}\n"
                f"🕐 Обновление: {stats['last_update']}"
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
            'SELECT title, company, level, category, posted_at, source FROM posted_jobs '
            'ORDER BY posted_at DESC LIMIT ?',
            (limit,)
        )
        
        if not results:
            await update.message.reply_text("📭 Нет опубликованных вакансий")
            return
        
        jobs = [
            {
                'title': row[0],
                'company': row[1],
                'level': row[2],
                'category': row[3],
            }
            for row in results
        ]
        
        if self.formatter:
            message = self.formatter.format_job_list(jobs, limit)
            await update.message.reply_text(
                message,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            message = f"🆕 <b>Последние {len(results)} вакансий:</b>\n\n"
            for row in results:
                title, company, level, category, posted_at, source = row
                message += f"• {escape_html(title)}\n  🏢 {escape_html(company)} | 🎯 {level}\n\n"
            await update.message.reply_text(message, parse_mode='HTML')
    
    async def cmd_favorites(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /favorites command"""
        user_id = update.effective_user.id
        favorites = self.db.get_user_favorites(user_id)
        
        if self.formatter:
            message = self.formatter.format_favorites_list(favorites)
            await update.message.reply_text(
                message,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            if not favorites:
                await update.message.reply_text("💾 Список избранного пуст")
            else:
                message = f"💾 <b>Избранное ({len(favorites)}):</b>\n\n"
                for job in favorites[:10]:
                    message += f"• {escape_html(job['title'])}\n  🏢 {escape_html(job['company'])}\n\n"
                await update.message.reply_text(message, parse_mode='HTML')
    
    async def cmd_categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /categories command"""
        user_id = update.effective_user.id
        settings = self.db.get_user_settings(user_id)
        enabled = settings['enabled_categories']
        
        if self.formatter:
            keyboard = self.formatter.create_category_settings_keyboard(enabled)
            await update.message.reply_text(
                "📂 *Настройка категорий:*\n\n"
                "Выберите категории для отображения:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard['inline_keyboard'])
            )
        else:
            cat_list = "\n".join([f"  • {CATEGORY_NAMES_RU.get(c, c)}" for c in enabled])
            await update.message.reply_text(
                f"📂 Активные категории:\n{cat_list}\n\n"
                f"(Детальная настройка доступна с модулем форматирования)"
            )
    
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
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline keyboard callbacks"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = update.effective_user.id
        
        if data.startswith('save:'):
            job_hash = data.split(':', 1)[1]
            self.db.add_favorite(user_id, job_hash)
            await query.edit_message_text(
                query.message.text + "\n\n💾 *Сохранено в избранное*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=None
            )
        
        elif data.startswith('expand:'):
            # Получаем полную информацию о вакансии и показываем развернутый вид
            await query.answer("Разворачиваю... (в разработке)")
        
        elif data.startswith('compact:'):
            await query.answer("Сворачиваю... (в разработке)")
        
        elif data.startswith('hide_cat:'):
            category = data.split(':', 1)[1]
            self.db.hide_category_for_user(user_id, category)
            cat_name = CATEGORY_NAMES_RU.get(category, category)
            await query.answer(f"Категория {cat_name} скрыта")
            await query.edit_message_text(
                query.message.text + f"\n\n🚫 Категория '{cat_name}' скрыта",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=None
            )
        
        elif data.startswith('toggle_cat:'):
            category = data.split(':', 1)[1]
            settings = self.db.get_user_settings(user_id)
            enabled = settings['enabled_categories']
            
            if category in enabled:
                enabled.remove(category)
            else:
                enabled.append(category)
            
            self.db.update_user_categories(user_id, enabled)
            
            # Обновляем клавиатуру
            if self.formatter:
                keyboard = self.formatter.create_category_settings_keyboard(enabled)
                await query.edit_message_reply_markup(
                    reply_markup=InlineKeyboardMarkup(keyboard['inline_keyboard'])
                )
        
        elif data == 'close_settings':
            await query.delete_message()
    
    async def post_job(self, job: Dict) -> bool:
        """Post job to channel with enhanced formatting"""
        if self.is_paused:
            logger.info("⏸️ Skipped posting (bot is paused)")
            return False
        
        try:
            # Используем новый форматтер если доступен
            if Config.ENABLE_MARKDOWN_V2 and self.formatter:
                formatted = self.formatter.format_job(job, view_mode='compact')
                await self.application.bot.send_message(
                    chat_id=Config.CHANNEL_ID,
                    text=formatted.text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=InlineKeyboardMarkup(formatted.reply_markup['inline_keyboard']),
                    disable_web_page_preview=formatted.disable_web_page_preview
                )
            else:
                # Fallback на legacy HTML форматирование
                message = format_job_message_legacy(job)
                await self.application.bot.send_message(
                    chat_id=Config.CHANNEL_ID,
                    text=message,
                    parse_mode='HTML',
                    disable_web_page_preview=True
                )
            
            logger.info(f"✅ Posted: {job.get('title', 'N/A')} [{job.get('category', 'other')}]")
            return True
            
        except RetryAfter as e:
            logger.warning(f"⏳ Telegram flood control: retry after {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            return await self.post_job(job)
        except TimedOut:
            logger.error("❌ Telegram timeout")
            return False
        except Exception as e:
            logger.error(f"❌ Failed to post job: {e}")
            return False

# ==================== MAIN LOOP ====================
async def main():
    """Main application loop"""
    logger.info("=" * 60)
    logger.info("🚀 Job Bot Starting (v6.0 - Enhanced Edition)")
    logger.info(f"📡 Channel: {Config.CHANNEL_ID}")
    logger.info(f"⏱️ Check interval: {Config.CHECK_INTERVAL}s")
    logger.info(f"📊 Max posts per cycle: {Config.MAX_POSTS_PER_CYCLE}")
    logger.info(f"🤖 MarkdownV2: {Config.ENABLE_MARKDOWN_V2}")
    logger.info(f"📱 Telegram channels: {Config.ENABLE_TELEGRAM_CHANNELS}")
    logger.info(f"🧠 Classifier: {CLASSIFIER_AVAILABLE}")
    logger.info(f"🎨 Formatter: {FORMATTER_AVAILABLE}")
    if Config.ADMIN_USER_ID:
        logger.info(f"👤 Admin user ID: {Config.ADMIN_USER_ID}")
    logger.info("=" * 60)
    
    # Initialize database
    db = init_database()
    
    # Setup Telegram bot
    application = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
    job_bot = JobBot(application, db)
    
    # Register command handlers
    application.add_handler(CommandHandler("start", job_bot.cmd_start))
    application.add_handler(CommandHandler("status", job_bot.cmd_status))
    application.add_handler(CommandHandler("last", job_bot.cmd_last))
    application.add_handler(CommandHandler("favorites", job_bot.cmd_favorites))
    application.add_handler(CommandHandler("categories", job_bot.cmd_categories))
    application.add_handler(CommandHandler("pause", job_bot.cmd_pause))
    application.add_handler(CommandHandler("resume", job_bot.cmd_resume))
    application.add_handler(CallbackQueryHandler(job_bot.handle_callback))
    
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
    api_fetch_functions = [
        (fetch_remotive, "Remotive"),
        (fetch_remoteok, "RemoteOK"),
        (fetch_arbeitnow, "Arbeitnow"),
        (fetch_himalayas, "Himalayas"),
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
            
            # Fetch from API sources
            for fetch_func, source_name in api_fetch_functions:
                jobs = await loop.run_in_executor(
                    None, safe_fetch_with_retry, fetch_func, source_name
                )
                all_jobs.extend(jobs)
                logger.info(f"📥 Fetched {len(jobs)} jobs from {source_name}")
            
            # Fetch from Telegram channels
            if Config.ENABLE_TELEGRAM_CHANNELS:
                tg_jobs = await fetch_telegram_channels()
                all_jobs.extend(tg_jobs)
            
            logger.info(f"📊 Total jobs fetched: {len(all_jobs)}")
            
            # Filter, classify and process
            classified_jobs = []
            for job in all_jobs:
                if not is_suitable_job(job):
                    continue
                
                # Classify level
                level = classify_job_level(job)
                if level:
                    job['level'] = level
                else:
                    continue
                
                # Auto-classify category
                category = auto_classify_category(job)
                job['category'] = category
                
                classified_jobs.append(job)
            
            logger.info(f"🎯 Suitable Junior/Middle jobs: {len(classified_jobs)}")
            
            # Post jobs
            posted_count = 0
            for job in classified_jobs[:Config.MAX_POSTS_PER_CYCLE]:
                if not is_duplicate_job(job, db):
                    if await job_bot.post_job(job):
                        posted_count += 1
                        await asyncio.sleep(DELAYS['between_posts'])
            
            logger.info(f"✅ Posted {posted_count} new jobs to channel")
            logger.info(f"⏳ Waiting {Config.CHECK_INTERVAL//60} minutes before next cycle...")
            await asyncio.sleep(Config.CHECK_INTERVAL)
            
        except Exception as e:
            logger.error(f"❌ Error in main loop: {e}", exc_info=True)
            await asyncio.sleep(300)


async def shutdown(signal, application, db):
    """Graceful shutdown handler"""
    logger.info(f"🛑 Received exit signal {signal.name}")
    
    await application.stop()
    await application.shutdown()
    db.close()
    logger.info("👋 Bot shutdown complete")
    sys.exit(0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
        sys.exit(0)
