"""
Telegram Job Parser Module - Парсинг вакансий из Telegram-каналов
Версия: 1.0.0

Требует:
    - TELEGRAM_API_ID и TELEGRAM_API_HASH в переменных окружения
    - Предварительная авторизация: python -m bot.telegram_auth

Источники:
    - remote_developers (100% remote, EN/RU)
    - programmer_remote (remote, EN/RU)
    - digital_nomads (remote worldwide)
    - itfreelance (фильтр по ключевым словам)
    - prog_jobs (структурированные, фильтр remote)
    - coders_jobs (junior/middle, фильтр remote)
    - design_vacancies (UX/UI, фильтр remote)
    - project_managers (PM/Agile, фильтр remote)
    - hitech_jobs (AI/ML, стартапы, фильтр remote)
    - it_jobs (агрегатор, усиленная дедупликация)
"""
import os
import re
import hashlib
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set
from dataclasses import dataclass

# Telethon импортируется conditionally
try:
    from telethon import TelegramClient
    from telethon.tl.types import Message
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False

logger = logging.getLogger(__name__)


# ==================== CONFIGURATION ====================
TELEGRAM_CHANNELS = {
    'remote_developers': {
        'username': 'remote_developers',
        'name': 'Remote Developers',
        'filter_remote': False,  # 100% remote канал
        'filter_level': False,
        'priority': 1,
    },
    'programmer_remote': {
        'username': 'programmer_remote',
        'name': 'Programmer Remote',
        'filter_remote': False,
        'filter_level': False,
        'priority': 1,
    },
    'digital_nomads': {
        'username': 'digital_nomads',
        'name': 'Digital Nomads',
        'filter_remote': False,
        'filter_level': False,
        'priority': 2,
    },
    'itfreelance': {
        'username': 'itfreelance',
        'name': 'IT Freelance',
        'filter_remote': True,  # Нужна фильтрация по ключевым словам
        'keywords': ['удаленно', 'удалённо', 'remote', 'фриланс', 'freelance'],
        'filter_level': False,
        'priority': 2,
    },
    'prog_jobs': {
        'username': 'prog_jobs',
        'name': 'Programming Jobs',
        'filter_remote': True,
        'keywords': ['remote', 'удаленно', 'удалённо', 'work from home', 'wfh'],
        'filter_level': False,
        'priority': 1,
    },
    'coders_jobs': {
        'username': 'coders_jobs',
        'name': 'Coders Jobs',
        'filter_remote': True,
        'keywords': ['remote', 'удаленно', 'удалённо'],
        'filter_level': True,
        'level_keywords': ['junior', 'jr', 'middle', 'mid', 'начинающий'],
        'priority': 1,
    },
    'design_vacancies': {
        'username': 'design_vacancies',
        'name': 'Design Vacancies',
        'filter_remote': True,
        'keywords': ['remote', 'удаленно', 'удалённо'],
        'filter_level': False,
        'category': 'design',
        'priority': 2,
    },
    'project_managers': {
        'username': 'project_managers',
        'name': 'Project Managers',
        'filter_remote': True,
        'keywords': ['remote', 'удаленно', 'удалённо'],
        'filter_level': False,
        'category': 'pm',
        'priority': 2,
    },
    'hitech_jobs': {
        'username': 'hitech_jobs',
        'name': 'Hi-Tech Jobs',
        'filter_remote': True,
        'keywords': ['remote', 'удаленно', 'удалённо', 'ai', 'ml', 'machine learning', 'data'],
        'filter_level': False,
        'priority': 2,
    },
    'it_jobs': {
        'username': 'it_jobs',
        'name': 'IT Jobs',
        'filter_remote': True,
        'keywords': ['remote', 'удаленно', 'удалённо', 'фриланс', 'freelance'],
        'filter_level': False,
        'priority': 3,  # Низкий приоритет из-за дублирования
        'enhanced_dedup': True,  # Усиленная дедупликация
    },
}

# Ключевые слова для извлечения данных
COMPANY_PATTERNS = [
    r'(?:компания|company)[\s:]*(\S[^\n]+)',
    r'(?:в компании|at|@)[\s:]*(\S[^\n,]+)',
    r'(?:работа в|work at)[\s:]*(\S[^\n]+)',
]

SALARY_PATTERNS = [
    r'(?:зарплата|salary|доход|вознаграждение)[\s:]*(\S[^\n]+)',
    r'(\d[\d\s]*(?:000|k|K)[\s]*(?:\$|€|£|₽|руб|RUB|USD|EUR)?(?:[\s/-]*\d[\d\s]*(?:000|k|K)?)?)',
    r'(от\s+\d[\d\s]*\s*(?:\$|€|£|₽|руб)?)',
    r'(до\s+\d[\d\s]*\s*(?:\$|€|£|₽|руб)?)',
    r'(\d[\d\s]*\s*[-–]\s*\d[\d\s]*\s*(?:\$|€|£|₽|руб|RUB|USD))',
]

LOCATION_PATTERNS = [
    r'(?:локация|location|местоположение|регион)[\s:]*(\S[^\n]+)',
    r'(?:remote|удаленно|удалённо)[\s]*(?:из|from)?[\s:]*(\S[^\n]+)',
]

TECH_STACK_KEYWORDS = [
    'python', 'javascript', 'typescript', 'java', 'c++', 'c#', 'go', 'golang', 'rust',
    'php', 'ruby', 'swift', 'kotlin', 'scala', 'perl', 'r', 'matlab',
    'react', 'vue', 'angular', 'svelte', 'next.js', 'nuxt', 'django', 'flask',
    'fastapi', 'spring', 'laravel', 'rails', 'express', 'node.js', 'nodejs',
    'postgresql', 'mysql', 'mongodb', 'redis', 'sqlite', 'elasticsearch',
    'docker', 'kubernetes', 'aws', 'azure', 'gcp', 'terraform', 'ansible',
    'jenkins', 'gitlab', 'github', 'ci/cd', 'git', 'rest api', 'graphql',
    'html', 'css', 'sass', 'tailwind', 'bootstrap', 'figma', 'sketch',
    'machine learning', 'deep learning', 'tensorflow', 'pytorch', 'scikit-learn',
    'pandas', 'numpy', 'tableau', 'power bi', 'spark', 'hadoop', 'kafka',
    'selenium', 'cypress', 'playwright', 'junit', 'pytest', 'jest',
    'linux', 'ubuntu', 'nginx', 'apache', 'kafka', 'rabbitmq',
    'react native', 'flutter', 'ios', 'android', 'mobile',
]

LEVEL_KEYWORDS = {
    'junior': ['junior', 'jr', 'jr.', 'entry level', 'entry-level', 'начинающий', 'начальный', 'без опыта'],
    'middle': ['middle', 'mid', 'mid-level', 'intermediate', 'опытный'],
    'senior': ['senior', 'sr', 'sr.', 'lead', 'principal', 'staff', 'architect', 'старший', 'ведущий'],
}


@dataclass
class ParsedJob:
    """Структура распарсенной вакансии"""
    title: str
    company: str
    description: str
    url: str
    salary: str
    location: str
    source: str
    tags: List[str]
    level: str
    category: str
    published: datetime
    content_hash: str  # Для дедупликации


class TelegramJobParser:
    """
    Парсер вакансий из Telegram-каналов с использованием Telethon.
    """
    
    def __init__(self, api_id: Optional[str] = None, api_hash: Optional[str] = None, 
                 session_name: str = 'job_parser_session'):
        """
        Инициализация парсера.
        
        Args:
            api_id: Telegram API ID (из my.telegram.org)
            api_hash: Telegram API Hash (из my.telegram.org)
            session_name: Имя файла сессии
        """
        if not TELETHON_AVAILABLE:
            raise ImportError(
                "Telethon не установлен. Установите: pip install telethon"
            )
        
        self.api_id = api_id or os.getenv('TELEGRAM_API_ID')
        self.api_hash = api_hash or os.getenv('TELEGRAM_API_HASH')
        
        if not self.api_id or not self.api_hash:
            raise ValueError(
                "TELEGRAM_API_ID и TELEGRAM_API_HASH должны быть указаны. "
                "Получите их на https://my.telegram.org/apps"
            )
        
        self.session_name = session_name
        self.client: Optional[TelegramClient] = None
    
    async def connect(self) -> bool:
        """Подключение к Telegram"""
        try:
            self.client = TelegramClient(
                self.session_name, 
                int(self.api_id), 
                self.api_hash
            )
            await self.client.connect()
            
            if not await self.client.is_user_authorized():
                logger.error(
                    "❌ Пользователь не авторизован. "
                    "Выполните предварительную авторизацию: python telegram_auth.py"
                )
                return False
            
            logger.info("✅ Подключение к Telegram успешно")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к Telegram: {e}")
            return False
    
    async def disconnect(self):
        """Отключение от Telegram"""
        if self.client:
            await self.client.disconnect()
            logger.info("🔌 Отключение от Telegram")
    
    def _extract_with_patterns(self, text: str, patterns: List[str]) -> Optional[str]:
        """Извлечение данных с помощью регулярных выражений"""
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result = match.group(1).strip()
                # Очистка результата
                result = re.sub(r'[\n\r\t]+', ' ', result)
                result = re.sub(r'\s+', ' ', result)
                if len(result) > 3 and len(result) < 200:
                    return result
        return None
    
    def _extract_company(self, text: str) -> str:
        """Извлечение названия компании"""
        company = self._extract_with_patterns(text, COMPANY_PATTERNS)
        if company:
            return company
        
        # Пробуем найти по @username
        match = re.search(r'@([a-zA-Z][a-zA-Z0-9_]{4,})', text)
        if match:
            return f"@{match.group(1)}"
        
        return 'Не указана'
    
    def _extract_salary(self, text: str) -> str:
        """Извлечение зарплаты"""
        salary = self._extract_with_patterns(text, SALARY_PATTERNS)
        if salary:
            return salary
        
        # Ищем паттерны зарплат
        patterns = [
            r'(\d{3,6}[\s]?[-–][\s]?\d{3,6}[\s]?(?:\$|€|£|₽|руб|RUB|USD|EUR)?)',
            r'((?:от|до)\s+\d{3,6}[\s]?(?:\$|€|£|₽|руб)?)',
            r'(\d{1,3}[kK][\s]?[-–][\s]?\d{1,3}[kK][\s]?(?:\$|€|£)?)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        return 'Не указана'
    
    def _extract_location(self, text: str) -> str:
        """Извлечение локации"""
        location = self._extract_with_patterns(text, LOCATION_PATTERNS)
        if location:
            return location
        
        # Проверяем на remote
        remote_keywords = ['remote', 'удаленно', 'удалённо', 'worldwide', 'весь мир', 'любая точка']
        text_lower = text.lower()
        for kw in remote_keywords:
            if kw in text_lower:
                return 'Remote / Worldwide'
        
        return 'Remote'
    
    def _extract_tech_stack(self, text: str) -> List[str]:
        """Извлечение технологий из текста"""
        text_lower = text.lower()
        found_tech = []
        
        for tech in TECH_STACK_KEYWORDS:
            # Ищем слово как отдельное (с границами слов)
            pattern = r'\b' + re.escape(tech.lower()) + r'\b'
            if re.search(pattern, text_lower):
                found_tech.append(tech.title() if tech != tech.upper() else tech)
        
        return list(set(found_tech))[:8]  # Максимум 8 технологий
    
    def _detect_level(self, text: str) -> str:
        """Определение уровня (junior/middle/senior)"""
        text_lower = text.lower()
        
        # Проверяем на senior (исключаем)
        for kw in LEVEL_KEYWORDS['senior']:
            if kw in text_lower:
                return 'Senior'
        
        # Проверяем на junior
        for kw in LEVEL_KEYWORDS['junior']:
            if kw in text_lower:
                return 'Junior'
        
        # Проверяем на middle
        for kw in LEVEL_KEYWORDS['middle']:
            if kw in text_lower:
                return 'Middle'
        
        return 'Not specified'
    
    def _extract_url(self, text: str) -> str:
        """Извлечение ссылки на вакансию"""
        # Ищем URL
        url_pattern = r'(https?://[^\s\n]+)'
        match = re.search(url_pattern, text)
        if match:
            url = match.group(1)
            # Очищаем URL от пунктуации в конце
            url = url.rstrip('.,;:!?)')
            return url
        
        # Ищем @username бота
        bot_match = re.search(r'(@[a-zA-Z][a-zA-Z0-9_]{4,}_?bot)', text, re.IGNORECASE)
        if bot_match:
            return f"https://t.me/{bot_match.group(1)[1:]}"
        
        return ''
    
    def _extract_title(self, text: str) -> str:
        """Извлечение заголовка вакансии"""
        lines = text.strip().split('\n')
        
        for line in lines[:5]:  # Проверяем первые 5 строк
            line = line.strip()
            # Пропускаем пустые и короткие строки
            if len(line) < 10:
                continue
            # Пропускаем строки с эмодзи в начале (обычно это заголовки разделов)
            if line[0].encode('utf-8')[:3] == b'\xf0\x9f':
                continue
            # Ищем должность
            if any(kw in line.lower() for kw in ['developer', 'engineer', 'manager', 'designer', 'analyst', 'разработчик', 'менеджер', 'дизайнер', 'аналитик']):
                # Очищаем от лишнего
                title = re.sub(r'^[\s\-\•\·\*]+', '', line)
                if len(title) > 5 and len(title) < 150:
                    return title
        
        # Если не нашли, берем первую непустую строку подходящей длины
        for line in lines:
            line = line.strip()
            if len(line) >= 10 and len(line) < 150:
                return line
        
        return 'Вакансия'
    
    def _generate_content_hash(self, text: str) -> str:
        """Генерация хеша для дедупликации"""
        # Нормализуем текст
        normalized = re.sub(r'\s+', ' ', text.lower().strip())
        normalized = re.sub(r'https?://\S+', '', normalized)  # Убираем URL
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]
    
    def _is_remote_job(self, text: str, channel_config: Dict) -> bool:
        """Проверка, является ли вакансия удаленной"""
        if not channel_config.get('filter_remote', False):
            return True
        
        text_lower = text.lower()
        keywords = channel_config.get('keywords', [])
        
        for kw in keywords:
            if kw.lower() in text_lower:
                return True
        
        return False
    
    def _is_suitable_level(self, text: str, channel_config: Dict) -> bool:
        """Проверка уровня (junior/middle)"""
        if not channel_config.get('filter_level', False):
            return True
        
        text_lower = text.lower()
        level_keywords = channel_config.get('level_keywords', [])
        
        for kw in level_keywords:
            if kw.lower() in text_lower:
                return True
        
        return False
    
    def _parse_message(self, message: Message, channel_config: Dict) -> Optional[ParsedJob]:
        """Парсинг одного сообщения"""
        if not message.text:
            return None
        
        text = message.text
        
        # Фильтрация по remote
        if not self._is_remote_job(text, channel_config):
            return None
        
        # Фильтрация по уровню
        if not self._is_suitable_level(text, channel_config):
            return None
        
        # Проверка на рекламу/спам (простая)
        spam_keywords = ['купить', 'продажа', 'скидка', 'распродажа', 'crypto', 'casino', 'ставки']
        text_lower = text.lower()
        for kw in spam_keywords:
            if kw in text_lower:
                return None
        
        # Извлекаем данные
        title = self._extract_title(text)
        company = self._extract_company(text)
        salary = self._extract_salary(text)
        location = self._extract_location(text)
        url = self._extract_url(text)
        tags = self._extract_tech_stack(text)
        level = self._detect_level(text)
        content_hash = self._generate_content_hash(text)
        
        # Категория из конфига или auto-detect
        category = channel_config.get('category', 'other')
        
        # Получаем дату публикации
        published = message.date.replace(tzinfo=None) if message.date else datetime.now()
        
        return ParsedJob(
            title=title,
            company=company,
            description=text[:500] + ('...' if len(text) > 500 else ''),
            url=url,
            salary=salary,
            location=location,
            source=channel_config['name'],
            tags=tags,
            level=level,
            category=category,
            published=published,
            content_hash=content_hash
        )
    
    async def fetch_channel_jobs(self, channel_key: str, 
                                  hours_back: int = 1) -> List[ParsedJob]:
        """
        Получение вакансий из одного канала.
        
        Args:
            channel_key: Ключ канала из TELEGRAM_CHANNELS
            hours_back: За сколько часов назад получать сообщения
        
        Returns:
            Список распарсенных вакансий
        """
        if not self.client:
            logger.error("❌ Клиент не подключен")
            return []
        
        channel_config = TELEGRAM_CHANNELS.get(channel_key)
        if not channel_config:
            logger.error(f"❌ Неизвестный канал: {channel_key}")
            return []
        
        jobs = []
        
        try:
            # Получаем entity канала
            entity = await self.client.get_entity(channel_config['username'])
            
            # Вычисляем время, с которого начинать
            since_time = datetime.now() - timedelta(hours=hours_back)
            
            # Получаем сообщения
            message_count = 0
            async for message in self.client.iter_messages(entity, limit=50):
                message_count += 1
                
                # Пропускаем старые сообщения
                if message.date and message.date.replace(tzinfo=None) < since_time:
                    break
                
                # Парсим сообщение
                job = self._parse_message(message, channel_config)
                if job:
                    jobs.append(job)
            
            logger.info(f"📥 Получено {len(jobs)} вакансий из {channel_config['name']} "
                       f"(проверено {message_count} сообщений)")
        
        except Exception as e:
            logger.error(f"❌ Ошибка получения сообщений из {channel_key}: {e}")
        
        return jobs
    
    async def fetch_all_channels(self, hours_back: int = 1) -> List[ParsedJob]:
        """
        Получение вакансий из всех настроенных каналов.
        
        Args:
            hours_back: За сколько часов назад получать сообщения
        
        Returns:
            Список вакансий со всех каналов
        """
        if not await self.connect():
            return []
        
        all_jobs = []
        seen_hashes = set()  # Для дедупликации между каналами
        
        try:
            # Сортируем каналы по приоритету
            sorted_channels = sorted(
                TELEGRAM_CHANNELS.items(),
                key=lambda x: x[1].get('priority', 2)
            )
            
            for channel_key, config in sorted_channels:
                try:
                    jobs = await self.fetch_channel_jobs(channel_key, hours_back)
                    
                    for job in jobs:
                        # Дедупликация по хешу контента
                        if job.content_hash not in seen_hashes:
                            seen_hashes.add(job.content_hash)
                            all_jobs.append(job)
                        elif config.get('enhanced_dedup'):
                            # Для агрегаторов пропускаем дубли
                            logger.debug(f"⏭️ Дубликат пропущен: {job.title}")
                    
                    # Небольшая задержка между каналами
                    import asyncio
                    await asyncio.sleep(1)
                
                except Exception as e:
                    logger.error(f"❌ Ошибка обработки канала {channel_key}: {e}")
                    continue
        
        finally:
            await self.disconnect()
        
        logger.info(f"📊 Всего получено {len(all_jobs)} уникальных вакансий из Telegram")
        return all_jobs
    
    def to_dict(self, job: ParsedJob) -> Dict:
        """Конвертация ParsedJob в dict для совместимости с существующим кодом"""
        return {
            'title': job.title,
            'company': job.company,
            'description': job.description,
            'url': job.url,
            'salary': job.salary,
            'location': job.location,
            'source': f"TG: {job.source}",
            'tags': job.tags,
            'level': job.level if job.level != 'Not specified' else 'Junior',
            'category': job.category,
            'published': job.published.isoformat() if job.published else None,
            'content_hash': job.content_hash,
        }


# ==================== CONVENIENCE FUNCTIONS ====================

async def fetch_telegram_jobs(hours_back: int = 1) -> List[Dict]:
    """
    Удобная функция для получения вакансий из Telegram-каналов.
    
    Args:
        hours_back: За сколько часов назад получать сообщения (по умолчанию 1)
    
    Returns:
        Список вакансий в формате dict
    """
    if not TELETHON_AVAILABLE:
        logger.warning("⚠️ Telethon не установлен. Пропускаем Telegram-каналы.")
        return []
    
    if not os.getenv('TELEGRAM_API_ID') or not os.getenv('TELEGRAM_API_ID'):
        logger.warning("⚠️ TELEGRAM_API_ID/TELEGRAM_API_HASH не настроены. "
                      "Пропускаем Telegram-каналы.")
        return []
    
    try:
        parser = TelegramJobParser()
        jobs = await parser.fetch_all_channels(hours_back=hours_back)
        return [parser.to_dict(job) for job in jobs]
    except Exception as e:
        logger.error(f"❌ Ошибка при получении вакансий из Telegram: {e}")
        return []


# ==================== AUTH UTILITY ====================

async def authorize_telegram():
    """
    Утилита для первоначальной авторизации в Telegram.
    Запускается один раз для создания файла сессии.
    
    Usage:
        python -c "import asyncio; from telegram_job_parser import authorize_telegram; asyncio.run(authorize_telegram())"
    """
    if not TELETHON_AVAILABLE:
        print("❌ Telethon не установлен. Установите: pip install telethon")
        return
    
    api_id = os.getenv('TELEGRAM_API_ID')
    api_hash = os.getenv('TELEGRAM_API_HASH')
    
    if not api_id or not api_hash:
        print("❌ Укажите TELEGRAM_API_ID и TELEGRAM_API_HASH в переменных окружения")
        print("   Получите их на https://my.telegram.org/apps")
        return
    
    client = TelegramClient('job_parser_session', int(api_id), api_hash)
    
    async with client:
        me = await client.get_me()
        print(f"✅ Авторизация успешна!")
        print(f"   Пользователь: {me.first_name} (@{me.username})")
        print(f"   Файл сессии создан: job_parser_session.session")


if __name__ == '__main__':
    import asyncio
    
    # Тестирование
    async def test():
        print("🧪 Тестирование Telegram Job Parser")
        print("=" * 50)
        
        # Проверяем авторизацию
        if not os.getenv('TELEGRAM_API_ID'):
            print("❌ Установите TELEGRAM_API_ID и TELEGRAM_API_HASH")
            return
        
        parser = TelegramJobParser()
        
        if await parser.connect():
            # Тестируем один канал
            jobs = await parser.fetch_channel_jobs('remote_developers', hours_back=24)
            print(f"\n📊 Найдено {len(jobs)} вакансий:")
            for job in jobs[:3]:
                print(f"\n💼 {job.title}")
                print(f"   🏢 {job.company}")
                print(f"   💵 {job.salary}")
                print(f"   📍 {job.location}")
                print(f"   🔗 {job.url or 'Нет ссылки'}")
            
            await parser.disconnect()
        else:
            print("❌ Не удалось подключиться")
    
    asyncio.run(test())
