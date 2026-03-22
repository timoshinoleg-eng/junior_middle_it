#!/usr/bin/env python3
"""
Telegram Authorization Utility - Утилита для первоначальной авторизации

Использование:
    python telegram_auth.py

Требуется запустить один раз для создания файла сессии (job_parser_session.session).
После этого бот сможет работать в автоматическом режиме через cron.
"""
import os
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

try:
    from telethon import TelegramClient
except ImportError:
    print("❌ Telethon не установлен. Установите: pip install telethon")
    exit(1)


async def main():
    """Main authorization flow"""
    print("=" * 60)
    print("🔐 Telegram Authorization Utility")
    print("=" * 60)
    
    # Get credentials
    api_id = os.getenv('TELEGRAM_API_ID')
    api_hash = os.getenv('TELEGRAM_API_HASH')
    
    if not api_id or not api_hash:
        print("\n❌ Ошибка: TELEGRAM_API_ID и TELEGRAM_API_HASH не найдены")
        print("\nПолучите их на https://my.telegram.org/apps:")
        print("  1. Войдите в свой аккаунт Telegram")
        print("  2. Заполните форму создания приложения")
        print("  3. Скопируйте api_id и api_hash")
        print("\nЗатем добавьте их в файл .env:")
        print("  TELEGRAM_API_ID=12345678")
        print("  TELEGRAM_API_HASH=your_hash_here")
        return
    
    print(f"\n📱 API ID: {api_id}")
    print(f"🔑 API Hash: {api_hash[:10]}...")
    
    session_file = 'job_parser_session.session'
    
    # Check for existing session
    if Path(session_file).exists():
        print(f"\n⚠️  Файл сессии уже существует: {session_file}")
        response = input("Пересоздать сессию? (y/N): ").strip().lower()
        if response != 'y':
            print("❌ Отменено")
            return
        # Remove old session
        Path(session_file).unlink()
        print("🗑️  Старый файл сессии удален")
    
    print("\n🔄 Подключение к Telegram...")
    
    client = TelegramClient('job_parser_session', int(api_id), api_hash)
    
    try:
        await client.connect()
        
        if await client.is_user_authorized():
            print("✅ Вы уже авторизованы!")
            me = await client.get_me()
            print(f"\n👤 Пользователь: {me.first_name} (@{me.username})")
            print(f"📞 Телефон: {me.phone}")
        else:
            print("\n📲 Требуется авторизация")
            print("-" * 60)
            
            # Phone number input
            phone = input("Введите номер телефона (с кодом страны, например +79123456789): ").strip()
            
            if not phone.startswith('+'):
                print("❌ Номер должен начинаться с '+' и кода страны")
                return
            
            # Send code request
            await client.send_code_request(phone)
            print("📨 Код подтверждения отправлен в Telegram")
            
            # Code input
            code = input("Введите код подтверждения: ").strip()
            
            try:
                await client.sign_in(phone, code)
                print("✅ Авторизация успешна!")
            except Exception as e:
                if "2FA" in str(e) or "password" in str(e).lower():
                    # 2FA required
                    password = input("Введите пароль двухфакторной аутентификации: ").strip()
                    await client.sign_in(password=password)
                    print("✅ Авторизация с 2FA успешна!")
                else:
                    raise
        
        # Get user info
        me = await client.get_me()
        print("\n" + "=" * 60)
        print("✅ Авторизация завершена успешно!")
        print("=" * 60)
        print(f"\n👤 Имя: {me.first_name} {me.last_name or ''}")
        print(f"🔖 Username: @{me.username or 'не указан'}")
        print(f"📞 Телефон: {me.phone}")
        print(f"🆔 ID: {me.id}")
        print(f"\n💾 Файл сессии создан: {session_file}")
        print("\nТеперь бот может работать в автоматическом режиме!")
        
        # Test channel access
        print("\n🧪 Проверка доступа к каналам...")
        test_channels = ['remote_developers', 'programmer_remote']
        
        for channel in test_channels:
            try:
                entity = await client.get_entity(channel)
                print(f"  ✅ @{channel} - доступен")
            except Exception as e:
                print(f"  ⚠️  @{channel} - {e}")
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
    finally:
        await client.disconnect()
        print("\n🔌 Отключение от Telegram")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 Прервано пользователем")
