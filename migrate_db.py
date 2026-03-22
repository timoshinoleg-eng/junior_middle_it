"""
Database Migration Script - Миграция базы данных для новых функций
Версия: 1.0.0

Выполняет:
1. Добавление поля category в таблицу posted_jobs
2. Создание индекса по category
3. Создание таблицы user_favorites
4. Создание таблицы user_settings
5. Бэкфилл существующих записей (опционально)
"""
import os
import sys
import sqlite3
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def migrate_database(db_path: str = 'jobs.db'):
    """Выполнение миграции базы данных"""
    
    if not os.path.exists(db_path):
        logger.warning(f"⚠️ База данных {db_path} не найдена. Будет создана новая.")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # 1. Проверяем существование таблицы posted_jobs
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='posted_jobs'")
        if not cursor.fetchone():
            logger.info("📋 Создание таблицы posted_jobs...")
            cursor.execute("""
                CREATE TABLE posted_jobs (
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
            cursor.execute("CREATE INDEX idx_jobs_category ON posted_jobs(category)")
            cursor.execute("CREATE INDEX idx_jobs_posted_at ON posted_jobs(posted_at)")
        else:
            # 2. Добавляем поле category если его нет
            cursor.execute("PRAGMA table_info(posted_jobs)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if 'category' not in columns:
                logger.info("➕ Добавление поля category...")
                cursor.execute("ALTER TABLE posted_jobs ADD COLUMN category TEXT DEFAULT 'other'")
            else:
                logger.info("✅ Поле category уже существует")
            
            # 3. Создаем индексы
            logger.info("📇 Создание индексов...")
            try:
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_category ON posted_jobs(category)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON posted_jobs(posted_at)")
            except sqlite3.OperationalError as e:
                logger.warning(f"⚠️ Ошибка создания индекса: {e}")
        
        # 4. Создаем таблицу user_favorites
        logger.info("📋 Создание таблицы user_favorites...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_hash TEXT NOT NULL,
                saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, job_hash)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_favorites_user ON user_favorites(user_id)")
        
        # 5. Создаем таблицу user_settings
        logger.info("📋 Создание таблицы user_settings...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                enabled_categories TEXT DEFAULT 'development,qa,devops,data,marketing,sales,pm,design,other',
                hide_senior BOOLEAN DEFAULT 1,
                min_salary_filter INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 6. Создаем таблицу для дедупликации Telegram
        logger.info("📋 Создание таблицы telegram_content_hashes...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telegram_content_hashes (
                hash TEXT PRIMARY KEY,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tg_hashes_created ON telegram_content_hashes(created_at)")
        
        # 7. Создаем таблицу bot_state для хранения состояния
        logger.info("📋 Создание таблицы bot_state...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        logger.info("✅ Миграция успешно завершена!")
        
        # 8. Выводим статистику
        cursor.execute("SELECT COUNT(*) FROM posted_jobs")
        total_jobs = cursor.fetchone()[0]
        logger.info(f"📊 Всего вакансий в базе: {total_jobs}")
        
        if total_jobs > 0:
            cursor.execute("SELECT COUNT(*) FROM posted_jobs WHERE category = 'other'")
            uncategorized = cursor.fetchone()[0]
            logger.info(f"📊 Вакансий без категории: {uncategorized}")
        
        return True
        
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Ошибка миграции: {e}")
        return False
    finally:
        conn.close()


def backfill_categories(db_path: str = 'jobs.db'):
    """
    Бэкфилл существующих вакансий с помощью классификатора.
    Опционально: можно запустить отдельно.
    """
    try:
        from job_classifier import classify_job
    except ImportError:
        logger.error("❌ Модуль job_classifier не найден. Установите его перед бэкфиллом.")
        return False
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT hash, title, description, url, source 
            FROM posted_jobs 
            WHERE category = 'other' OR category IS NULL
        """)
        jobs = cursor.fetchall()
        
        if not jobs:
            logger.info("✅ Нет вакансий для категоризации")
            return True
        
        logger.info(f"🔄 Категоризация {len(jobs)} вакансий...")
        
        updated = 0
        for job_hash, title, description, url, source in jobs:
            job_data = {
                'title': title or '',
                'description': description or '',
                'url': url or '',
                'source': source or '',
            }
            category = classify_job(job_data)
            
            cursor.execute(
                "UPDATE posted_jobs SET category = ? WHERE hash = ?",
                (category, job_hash)
            )
            updated += 1
            
            if updated % 100 == 0:
                conn.commit()
                logger.info(f"  Обработано {updated}/{len(jobs)}")
        
        conn.commit()
        logger.info(f"✅ Обновлено {updated} вакансий")
        return True
        
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Ошибка бэкфилла: {e}")
        return False
    finally:
        conn.close()


def print_schema(db_path: str = 'jobs.db'):
    """Вывод схемы базы данных"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("\n📋 Схема базы данных:")
    print("=" * 60)
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = cursor.fetchall()
    
    for table in tables:
        table_name = table[0]
        print(f"\nТаблица: {table_name}")
        print("-" * 40)
        
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
        for col in columns:
            print(f"  {col[1]:<20} {col[2]:<10} {'NOT NULL' if col[3] else ''}")
        
        # Индексы
        cursor.execute(f"PRAGMA index_list({table_name})")
        indexes = cursor.fetchall()
        for idx in indexes:
            print(f"  [INDEX] {idx[1]}")
    
    conn.close()


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Database migration for IT Job Bot')
    parser.add_argument('--db', default='jobs.db', help='Path to database file')
    parser.add_argument('--backfill', action='store_true', help='Run category backfill')
    parser.add_argument('--schema', action='store_true', help='Print database schema')
    
    args = parser.parse_args()
    
    if args.schema:
        print_schema(args.db)
    elif args.backfill:
        backfill_categories(args.db)
    else:
        success = migrate_database(args.db)
        sys.exit(0 if success else 1)
