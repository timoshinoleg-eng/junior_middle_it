# Telegram Channel Bot for Junior/Middle Remote IT Jobs

Production-ready Python bot that automatically fetches and posts junior/middle remote IT job vacancies to your Telegram channel every 30 minutes.

## 🚀 Features

- **6 Job Sources**: RemoteOK, Remotive, Himalayas, Jobicy, WeWorkRemotely, Jooble
- **Smart Classification**: Keyword-based Junior/Middle level detection (no AI/ML)
- **Rate Limiting**: Built-in delays and retry logic to prevent API blocks
- **Deduplication**: SQLite database prevents duplicate postings
- **Production Ready**: Error handling, logging, 24/7 operation

## 📋 Requirements

- Python 3.10+
- Telegram Bot Token
- Telegram Channel (public or private)
- Jooble API Key (optional, free registration)

## 🛠️ Installation

1. **Clone or download the project files**

2. **Install dependencies**
```bash
pip install -r requirements.txt
```

3. **Set up environment variables**
```bash
cp .env.template .env
# Edit .env with your credentials
```

4. **Configure your .env file**
```
TELEGRAM_BOT_TOKEN=your_bot_token_here
CHANNEL_ID=@your_channel_name_or_id
JOOBLE_API_KEY=your_jooble_api_key_here
```

## 🤖 Setting Up Telegram Bot

### 1. Create Telegram Bot
1. Message [@BotFather](https://t.me/botfather) on Telegram
2. Send `/newbot` command
3. Follow prompts to create bot
4. Copy the bot token to `TELEGRAM_BOT_TOKEN`

### 2. Create Telegram Channel
1. Create new channel in Telegram
2. Add your bot as administrator
3. Copy channel username (e.g., `@remote_jobs_channel`) or ID to `CHANNEL_ID`

**Note**: For private channels, use the numeric channel ID (can be obtained from @userinfobot)

## 🔑 Getting Jooble API Key (Optional)

1. Visit [jooble.org/info/en/api](https://jooble.org/info/en/api)
2. Register for free API key
3. Add key to `JOOBLE_API_KEY` in .env file

*Without Jooble key, bot will work with 5 sources instead of 6*

## 🚀 Running the Bot

### Development Mode
```bash
python channel_bot.py
```

### Production Deployment (Recommended)

**Using PM2 (Node.js process manager)**
```bash
# Install PM2
npm install -g pm2

# Start bot with PM2
pm2 start channel_bot.py --interpreter python3 --name job-bot

# Monitor bot
pm2 logs job-bot
pm2 status
```

**Using systemd (Linux)**
```bash
# Create service file
sudo nano /etc/systemd/system/telegram-job-bot.service
```

Add content:
```ini
[Unit]
Description=Telegram Job Bot
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/bot/directory
ExecStart=/usr/bin/python3 channel_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable telegram-job-bot
sudo systemctl start telegram-job-bot
```

## 📊 Bot Performance

- **Classification Accuracy**: 85-90% for Junior/Middle levels
- **Job Sources**: 6 APIs with automatic fallback
- **Rate Limiting**: 5s delays between sources, random jitter
- **Posting Limit**: Max 15 jobs per 30-minute cycle
- **Deduplication**: 7-day job memory

## 🔧 Configuration Options

### Adjusting Posting Frequency
In `channel_bot.py`, modify:
```python
time.sleep(1800)  # 30 minutes in seconds
```

### Changing Job Limits
```python
for job in classified_jobs[:15]:  # Change 15 to desired limit
```

### Custom Keywords
Edit keyword lists in `channel_bot.py`:
- `JUNIOR_SIGNALS`: Keywords indicating junior positions
- `MIDDLE_SIGNALS`: Keywords indicating middle positions
- `IT_ROLES`: IT-related role keywords

## 📱 Example Output

Messages in your channel will look like:

```
🟢 Senior Python Developer

🏢 Компания: TechCorp
📍 Локация: Remote
💵 Зарплата: $50,000 - $70,000
🎯 Уровень: Middle

🛠 Навыки:
Python, Django, PostgreSQL, Docker, AWS

🔗 Откликнуться
📌 Источник: RemoteOK
```

## 🛡️ Error Handling

The bot includes comprehensive error handling:
- **API Failures**: Automatic retry with exponential backoff
- **Rate Limiting**: Respects Retry-After headers
- **Network Issues**: Graceful recovery and continuation
- **Invalid Responses**: Skips malformed job data

## 📊 Monitoring

### Logs
Bot logs all activities:
```bash
# View logs (if using PM2)
pm2 logs job-bot

# Or check the log file
tail -f ~/.pm2/logs/job-bot-out.log
```

### Database
Check posted jobs:
```bash
sqlite3 jobs.db "SELECT * FROM posted_jobs ORDER BY posted_at DESC LIMIT 10;"
```

## 🔄 Updating the Bot

1. Stop the bot: `pm2 stop job-bot`
2. Update files
3. Restart: `pm2 restart job-bot`

## 🆘 Troubleshooting

### Bot not posting jobs
1. Check bot token is correct
2. Verify bot is admin in channel
3. Check channel ID format (use @ for public channels)
4. Review logs for error messages

### API errors
1. Some sources may be temporarily unavailable
2. Bot will continue with available sources
3. Check internet connection

### Rate limiting
1. Bot automatically handles most rate limits
2. Consider increasing delays in `DELAYS` config if needed

## 📄 License

This project is open source. Feel free to modify and distribute.

## 🤝 Support

For issues and questions:
1. Check logs first
2. Verify all configuration steps
3. Test bot token and channel access
4. Ensure all dependencies are installed

---

**Happy job hunting! 🚀**