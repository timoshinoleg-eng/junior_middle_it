"""
Message Formatter Module - Форматирование сообщений вакансий с MarkdownV2 и inline-кнопками
Версия: 1.0.0
"""
import re
from typing import Dict, List, Optional
from dataclasses import dataclass

# Эмодзи для категорий
CATEGORY_EMOJIS = {
    'development': '💻',
    'qa': '🧪',
    'devops': '🔧',
    'data': '📊',
    'marketing': '📢',
    'sales': '💼',
    'pm': '📋',
    'design': '🎨',
    'support': '🎧',
    'security': '🔒',
    'other': '📌',
}

# Эмодзи для уровней
LEVEL_EMOJIS = {
    'Junior': '🟢',
    'Middle': '🟡',
    'Senior': '🔴',
    'Not specified': '⚪',
}

# Названия категорий на русском
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


@dataclass
class FormattedMessage:
    """Структура отформатированного сообщения"""
    text: str
    parse_mode: str
    reply_markup: Optional[Dict]  # Inline keyboard
    disable_web_page_preview: bool


class JobMessageFormatter:
    """
    Форматтер сообщений вакансий с поддержкой MarkdownV2 и inline-кнопок.
    """
    
    def __init__(self):
        self.parse_mode = 'MarkdownV2'
    
    def _escape_markdown_v2(self, text: str) -> str:
        """
        Экранирование специальных символов для MarkdownV2.
        Символы: _ * [ ] ( ) ~ ` > # + - = | { } . !
        """
        if not text:
            return ''
        
        # Экранируем все спецсимволы MarkdownV2
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        for char in escape_chars:
            text = text.replace(char, f'\\{char}')
        
        return text
    
    def _escape_url(self, url: str) -> str:
        """Экранирование URL для MarkdownV2"""
        if not url:
            return ''
        # В URL экранируем только ) и \
        return url.replace(')', '%29').replace('\\', '%5C')
    
    def _format_salary(self, salary: str) -> str:
        """Форматирование зарплаты"""
        if not salary or salary in ['Не указана', 'Not specified', '']:
            return '💵 _Договорная_'
        
        # Экранируем для MarkdownV2
        escaped = self._escape_markdown_v2(salary)
        return f'💵 *{escaped}*'
    
    def _format_skills(self, skills: List[str]) -> str:
        """Форматирование навыков в моноширинный текст"""
        if not skills:
            return '_Не указаны_'
        
        formatted = []
        for skill in skills[:6]:  # Максимум 6 навыков
            escaped = self._escape_markdown_v2(skill)
            formatted.append(f'`{escaped}`')
        
        return ' '.join(formatted)
    
    def _format_location(self, location: str) -> str:
        """Форматирование локации"""
        location = location or 'Remote'
        
        # Определяем эмодзи
        loc_lower = location.lower()
        if 'remote' in loc_lower or 'удален' in loc_lower or 'worldwide' in loc_lower:
            emoji = '🌍'
        elif 'usa' in loc_lower or 'us' in loc_lower or 'united states' in loc_lower:
            emoji = '🇺🇸'
        elif 'uk' in loc_lower or 'united kingdom' in loc_lower or 'london' in loc_lower:
            emoji = '🇬🇧'
        elif 'eu' in loc_lower or 'europe' in loc_lower:
            emoji = '🇪🇺'
        elif 'ru' in loc_lower or 'russia' in loc_lower or 'россия' in loc_lower:
            emoji = '🇷🇺'
        else:
            emoji = '📍'
        
        escaped = self._escape_markdown_v2(location)
        return f'{emoji} {escaped}'
    
    def _get_category_emoji(self, category: str) -> str:
        """Получение эмодзи для категории"""
        return CATEGORY_EMOJIS.get(category, '📌')
    
    def _format_compact(self, job: Dict) -> str:
        """Компактный формат сообщения"""
        title = job.get('title', 'Вакансия')
        company = job.get('company', 'Не указана')
        level = job.get('level', 'Junior')
        category = job.get('category', 'other')
        salary = job.get('salary', 'Не указана')
        location = job.get('location', 'Remote')
        url = job.get('url', '')
        
        cat_emoji = self._get_category_emoji(category)
        level_emoji = LEVEL_EMOJIS.get(level, '⚪')
        
        lines = [
            f"{cat_emoji} *{self._escape_markdown_v2(title)}*",
            f"",
            f"🏢 _{self._escape_markdown_v2(company)}_",
            f"{self._format_location(location)}  |  {level_emoji} {self._escape_markdown_v2(level)}",
            f"{self._format_salary(salary)}",
        ]
        
        if url:
            lines.append(f"")
            lines.append(f"[🔗 Откликнуться]({self._escape_url(url)})")
        
        return '\n'.join(lines)
    
    def _format_full(self, job: Dict) -> str:
        """Полный формат сообщения"""
        title = job.get('title', 'Вакансия')
        company = job.get('company', 'Не указана')
        level = job.get('level', 'Junior')
        category = job.get('category', 'other')
        category_name = CATEGORY_NAMES_RU.get(category, category)
        salary = job.get('salary', 'Не указана')
        location = job.get('location', 'Remote')
        description = job.get('description', '')
        skills = job.get('tags', [])
        source = job.get('source', 'Unknown')
        url = job.get('url', '')
        
        cat_emoji = self._get_category_emoji(category)
        level_emoji = LEVEL_EMOJIS.get(level, '⚪')
        
        # Ограничиваем описание
        if description:
            desc_text = description[:300] + ('...' if len(description) > 300 else '')
        else:
            desc_text = 'Описание не указано'
        
        lines = [
            f"{cat_emoji} *{self._escape_markdown_v2(title)}*",
            f"",
            f"🏢 _{self._escape_markdown_v2(company)}_",
            f"📂 Категория: {self._escape_markdown_v2(category_name)}",
            f"{self._format_location(location)}  |  {level_emoji} {self._escape_markdown_v2(level)}",
            f"{self._format_salary(salary)}",
            f"",
            f"📋 _Описание:_",
            f"{self._escape_markdown_v2(desc_text)}",
            f"",
            f"🛠 _Технологии:_",
            f"{self._format_skills(skills)}",
            f"",
            f"📡 _Источник:_ {self._escape_markdown_v2(source)}",
        ]
        
        if url:
            lines.append(f"")
            lines.append(f"[🔗 Открыть вакансию]({self._escape_url(url)})")
        
        return '\n'.join(lines)
    
    def create_inline_keyboard(self, job: Dict, view_mode: str = 'compact') -> Dict:
        """
        Создание inline-клавиатуры для сообщения.
        
        Args:
            job: Данные вакансии
            view_mode: 'compact' или 'full'
        
        Returns:
            Inline keyboard в формате dict
        """
        job_id = job.get('hash') or job.get('content_hash') or 'unknown'
        url = job.get('url', '')
        category = job.get('category', 'other')
        title = job.get('title', '')[:30]  # Для share query
        
        keyboard = []
        
        # Первая строка: основные действия
        row1 = []
        
        if url:
            row1.append({
                'text': '🔗 Открыть',
                'url': url
            })
        
        row1.append({
            'text': '💾 Сохранить',
            'callback_data': f"save:{job_id}"
        })
        
        row1.append({
            'text': '📤 Поделиться',
            'switch_inline_query': f"{title} - вакансия для {job.get('level', 'Junior')}"
        })
        
        keyboard.append(row1)
        
        # Вторая строка: управление отображением
        row2 = []
        
        if view_mode == 'compact':
            row2.append({
                'text': '⬇️ Подробнее',
                'callback_data': f"expand:{job_id}"
            })
        else:
            row2.append({
                'text': '⬆️ Свернуть',
                'callback_data': f"compact:{job_id}"
            })
        
        row2.append({
            'text': f'🚫 Скрыть {CATEGORY_NAMES_RU.get(category, category)}',
            'callback_data': f"hide_cat:{category}"
        })
        
        keyboard.append(row2)
        
        return {'inline_keyboard': keyboard}
    
    def format_job(self, job: Dict, view_mode: str = 'compact') -> FormattedMessage:
        """
        Форматирование вакансии для отправки.
        
        Args:
            job: Данные вакансии
            view_mode: 'compact' или 'full'
        
        Returns:
            FormattedMessage с текстом и клавиатурой
        """
        if view_mode == 'compact':
            text = self._format_compact(job)
        else:
            text = self._format_full(job)
        
        keyboard = self.create_inline_keyboard(job, view_mode)
        
        return FormattedMessage(
            text=text,
            parse_mode=self.parse_mode,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )
    
    def format_job_list(self, jobs: List[Dict], limit: int = 10) -> str:
        """
        Форматирование списка вакансий (для команды /last).
        
        Args:
            jobs: Список вакансий
            limit: Максимальное количество
        
        Returns:
            Отформатированный текст
        """
        if not jobs:
            return '📭 *Нет доступных вакансий*'
        
        lines = [f'🆕 *Последние {min(len(jobs), limit)} вакансий:*\n']
        
        for i, job in enumerate(jobs[:limit], 1):
            title = job.get('title', 'Вакансия')
            company = job.get('company', 'Не указана')
            level = job.get('level', 'Junior')
            category = job.get('category', 'other')
            
            cat_emoji = self._get_category_emoji(category)
            level_emoji = LEVEL_EMOJIS.get(level, '⚪')
            
            lines.append(
                f"{i}\. {cat_emoji} *{self._escape_markdown_v2(title)}*\n"
                f"   🏢 _{self._escape_markdown_v2(company)}_  |  {level_emoji} {self._escape_markdown_v2(level)}\n"
            )
        
        return '\n'.join(lines)
    
    def format_status_message(self, stats: Dict) -> str:
        """
        Форматирование сообщения статуса.
        
        Args:
            stats: Словарь со статистикой
        
        Returns:
            Отформатированный текст
        """
        total_jobs = stats.get('total_jobs', 0)
        total_sources = stats.get('total_sources', 0)
        is_paused = stats.get('is_paused', False)
        last_update = stats.get('last_update', 'Неизвестно')
        categories = stats.get('categories', {})
        
        status_emoji = '⏸️' if is_paused else '✅'
        status_text = 'Приостановлен' if is_paused else 'Активен'
        
        lines = [
            '📊 *Статистика бота*\n',
            f'{status_emoji} *Статус:* {status_text}',
            f'📋 *Всего вакансий:* {total_jobs}',
            f'📡 *Источников:* {total_sources}',
            f'🕐 *Обновление:* {self._escape_markdown_v2(last_update)}',
        ]
        
        if categories:
            lines.append('\n📂 *По категориям:*')
            for cat, count in sorted(categories.items(), key=lambda x: x[1], reverse=True)[:7]:
                cat_name = CATEGORY_NAMES_RU.get(cat, cat)
                cat_emoji = self._get_category_emoji(cat)
                lines.append(f"  {cat_emoji} {self._escape_markdown_v2(cat_name)}: {count}")
        
        return '\n'.join(lines)
    
    def format_favorites_list(self, jobs: List[Dict]) -> str:
        """
        Форматирование списка избранных вакансий.
        
        Args:
            jobs: Список сохраненных вакансий
        
        Returns:
            Отформатированный текст
        """
        if not jobs:
            return '💾 *Список избранного пуст*\n\nИспользуйте кнопку «💾 Сохранить» под вакансиями'
        
        lines = [f'💾 *Избранное ({len(jobs)})*\n']
        
        for i, job in enumerate(jobs[:20], 1):
            title = job.get('title', 'Вакансия')
            company = job.get('company', 'Не указана')
            category = job.get('category', 'other')
            
            cat_emoji = self._get_category_emoji(category)
            
            lines.append(
                f"{i}\. {cat_emoji} *{self._escape_markdown_v2(title)}*\n"
                f"   🏢 _{self._escape_markdown_v2(company)}_\n"
            )
        
        if len(jobs) > 20:
            lines.append(f"\n_\.\.\. и еще {len(jobs) - 20} вакансий_")
        
        return '\n'.join(lines)
    
    def create_category_settings_keyboard(self, enabled_categories: List[str]) -> Dict:
        """
        Создание клавиатуры для настройки категорий.
        
        Args:
            enabled_categories: Список включенных категорий
        
        Returns:
            Inline keyboard
        """
        keyboard = []
        row = []
        
        for cat in CATEGORY_NAMES_RU.keys():
            is_enabled = cat in enabled_categories
            emoji = '✅' if is_enabled else '❌'
            
            row.append({
                'text': f"{emoji} {CATEGORY_NAMES_RU[cat]}",
                'callback_data': f"toggle_cat:{cat}"
            })
            
            if len(row) == 2:
                keyboard.append(row)
                row = []
        
        if row:
            keyboard.append(row)
        
        # Кнопка закрытия
        keyboard.append([{'text': '❌ Закрыть', 'callback_data': 'close_settings'}])
        
        return {'inline_keyboard': keyboard}


# Singleton instance
_formatter = None


def get_formatter() -> JobMessageFormatter:
    """Получение singleton-экземпляра форматтера"""
    global _formatter
    if _formatter is None:
        _formatter = JobMessageFormatter()
    return _formatter


def format_job_message(job: Dict, view_mode: str = 'compact') -> FormattedMessage:
    """Удобная функция для форматирования вакансии"""
    return get_formatter().format_job(job, view_mode)


def format_job_list_message(jobs: List[Dict], limit: int = 10) -> str:
    """Удобная функция для форматирования списка"""
    return get_formatter().format_job_list(jobs, limit)


if __name__ == '__main__':
    # Тестирование форматтера
    test_jobs = [
        {
            'title': 'Python Developer',
            'company': 'Tech Corp',
            'level': 'Junior',
            'category': 'development',
            'salary': '$3000-5000',
            'location': 'Remote',
            'description': 'We are looking for a Python developer with Django experience...',
            'tags': ['Python', 'Django', 'PostgreSQL', 'Docker'],
            'source': 'RemoteOK',
            'url': 'https://example.com/job/123',
            'hash': 'abc123',
        },
        {
            'title': 'QA Automation Engineer',
            'company': 'Test Inc',
            'level': 'Middle',
            'category': 'qa',
            'salary': 'Не указана',
            'location': 'Remote EU',
            'description': 'Looking for experienced QA engineer...',
            'tags': ['Selenium', 'Python', 'Pytest'],
            'source': 'Himalayas',
            'url': 'https://example.com/job/456',
            'hash': 'def456',
        },
    ]
    
    formatter = JobMessageFormatter()
    
    print("=" * 60)
    print("COMPACT MODE:")
    print("=" * 60)
    for job in test_jobs:
        msg = formatter.format_job(job, 'compact')
        print(f"\n{msg.text}")
        print(f"\nKeyboard: {msg.reply_markup}")
        print("-" * 40)
    
    print("\n" + "=" * 60)
    print("FULL MODE:")
    print("=" * 60)
    for job in test_jobs:
        msg = formatter.format_job(job, 'full')
        print(f"\n{msg.text}")
        print("-" * 40)
