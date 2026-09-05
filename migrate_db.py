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

import db_backend

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def migrate_database(db_path: str = 'jobs.db'):
    """Выполнение миграции базы данных (SQLite или DATABASE_URL).

    Production now initializes the complete schema through ``DatabaseConnection``.
    This standalone command remains useful for the historical SQLite file and
    also supports Neon/PostgreSQL: when ``DATABASE_URL`` (or the test override)
    is present, it applies the backend's complete dialect-specific DDL instead
    of sending SQLite-only ``AUTOINCREMENT``/``PRAGMA`` statements to Postgres.
    """
    database_url = db_backend.database_url()
    if database_url:
        backend = db_backend.get_backend(database_url)
        conn = backend.connect(database_url)
        cursor = conn.cursor()
        try:
            for statement in backend.ddl():
                cursor.execute(statement)
            conn.commit()
            cursor.execute("SELECT COUNT(*) FROM posted_jobs")
            total_jobs = cursor.fetchone()[0]
            logger.info("✅ PostgreSQL schema migration successfully completed!")
            logger.info(f"📊 Всего вакансий в базе: {total_jobs}")
            if total_jobs > 0:
                cursor.execute(
                    "SELECT COUNT(*) FROM posted_jobs WHERE category = %s"
                    if db_backend.is_postgres(backend)
                    else "SELECT COUNT(*) FROM posted_jobs WHERE category = ?",
                    ('other',),
                )
                logger.info(f"📊 Вакансий без категории: {cursor.fetchone()[0]}")
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"❌ Ошибка PostgreSQL-миграции: {e}")
            return False
        finally:
            conn.close()

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


def migrate_sqlite_to_postgres(sqlite_path: str, postgres_url: str) -> dict:
    """Copy durable ledgers from SQLite into an initialized PostgreSQL backend.

    This is intentionally an explicit cutover command, not automatic startup
    behaviour: the owner pauses publishing, runs it once against the live Neon
    URL, verifies counts, and only then enables the Render publisher. Rows are
    inserted idempotently with ``ON CONFLICT DO NOTHING``; source data is never
    deleted or modified. The source SQLite file is opened read-only.
    """
    if not os.path.exists(sqlite_path):
        raise FileNotFoundError(sqlite_path)
    source = sqlite3.connect(f"file:{Path(sqlite_path).resolve()}?mode=ro", uri=True)
    backend = db_backend.get_backend(postgres_url)
    target = backend.connect(postgres_url)
    copied = {}
    tables = (
        'posted_jobs', 'user_favorites', 'user_settings',
        'telegram_content_hashes', 'events', 'referrals', 'job_payloads',
        'deliveries', 'source_runs', 'meta', 'bot_state',
    )
    try:
        target_cursor = target.cursor()
        for statement in backend.ddl():
            target_cursor.execute(statement)
        # Match DatabaseConnection's compatibility migrations before copying;
        # otherwise legacy observability/profile columns would be silently
        # omitted from the target column intersection below.
        compatibility = (
            "ALTER TABLE posted_jobs ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'other'",
            "ALTER TABLE posted_jobs ADD COLUMN IF NOT EXISTS fingerprint TEXT DEFAULT ''",
            "ALTER TABLE posted_jobs ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP",
            "ALTER TABLE user_favorites ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'saved'",
            "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS skills TEXT DEFAULT ''",
            "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS digest_enabled SMALLINT DEFAULT 0",
            "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS onboarding_done SMALLINT DEFAULT 0",
            "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS alerts_enabled SMALLINT DEFAULT 0",
            "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS premium_unlocked SMALLINT DEFAULT 0",
        )
        for statement in compatibility:
            target_cursor.execute(statement)
        target.commit()
        for table in tables:
            source_cursor = source.execute(f"PRAGMA table_info({table})")
            source_columns = [row[1] for row in source_cursor.fetchall()]
            if not source_columns:
                copied[table] = 0
                continue
            if not all(col.replace("_", "").isalnum() for col in source_columns):
                raise ValueError(f"unsafe SQLite column metadata in {table!r}")
            target_columns = backend.columns(target_cursor, table)
            columns = [col for col in source_columns if col in target_columns]
            if not columns:
                copied[table] = 0
                continue
            quoted = ", ".join(columns)
            placeholders = ", ".join(["%s"] * len(columns))
            # Table/column names come only from sqlite PRAGMA metadata and are
            # constrained to identifier characters; values remain parameters.
            insert = (
                f"INSERT INTO {table} ({quoted}) VALUES ({placeholders}) "
                "ON CONFLICT DO NOTHING"  # nosec B608
            )
            rows = source.execute(f"SELECT {quoted} FROM {table}").fetchall()  # nosec B608
            for row in rows:
                target_cursor.execute(insert, row)
            copied[table] = len(rows)
        # Keep BIGSERIAL ids above all imported values for future inserts.
        for table in ('user_favorites', 'events', 'source_runs'):
            target_cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s "
                "AND column_name = 'id'",
                (table,),
            )
            if target_cursor.fetchone():
                target_cursor.execute(
                    "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                    "COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM " + table,  # nosec B608 (allowlisted table)
                    (table,),
                )
        target.commit()
        return copied
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


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
        # v7 (B13): posted_jobs has no `description` column — backfill must run
        # on the columns that actually exist (title carries the signal).
        cursor.execute("""
            SELECT hash, title, url, source
            FROM posted_jobs
            WHERE category = 'other' OR category IS NULL
        """)
        jobs = cursor.fetchall()

        if not jobs:
            logger.info("✅ Нет вакансий для категоризации")
            return True

        logger.info(f"🔄 Категоризация {len(jobs)} вакансий...")

        updated = 0
        for job_hash, title, url, source in jobs:
            job_data = {
                'title': title or '',
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
    parser.add_argument('--import-sqlite', metavar='PATH', help='Copy SQLite ledgers into DATABASE_URL')
    
    args = parser.parse_args()
    
    if args.import_sqlite:
        if not db_backend.database_url():
            parser.error('--import-sqlite requires DATABASE_URL or TEST_DATABASE_URL')
        try:
            result = migrate_sqlite_to_postgres(args.import_sqlite, db_backend.database_url())
            logger.info(f"✅ SQLite -> PostgreSQL import complete: {result}")
            sys.exit(0)
        except Exception as exc:
            logger.error(f"❌ SQLite -> PostgreSQL import failed: {exc}")
            sys.exit(1)
    elif args.schema:
        print_schema(args.db)
    elif args.backfill:
        # v7 (B13): a failed backfill must not exit 0.
        success = backfill_categories(args.db)
        sys.exit(0 if success else 1)
    else:
        success = migrate_database(args.db)
        sys.exit(0 if success else 1)
