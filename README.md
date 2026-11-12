# Telegram Bot Constructor (Enhanced)

Railway-ге арналған күшті Telegram бот конструкторы.

## Railway орнату

### 1. Variables (міндетті)
```
BOT_TOKEN=master_бот_токені
ADMIN_ID=сіздің_telegram_id
```

### 2. AI Генератор үшін (опционалды)
```
AI_API_KEY=sk-...
AI_API_BASE=https://api.openai.com/v1
AI_MODEL=gpt-4o-mini
```

xAI Grok үшін:
```
AI_API_BASE=https://api.x.ai/v1
AI_MODEL=grok-2-latest
```

### 3. Deploy баптаулары
- **Build Command** → бос қалдырыңыз
- **Start Command** → `python main.py`

## Мүмкіндіктер

- ✅ Бірнеше ботты басқару (Start/Stop/Restart)
- ✅ Код версиялау
- ✅ Environment Variables
- ✅ Қосымша файлдар
- ✅ Авто-рестарт (құлағанда)
- ✅ Логтар
- ✅ 🤖 AI код генераторы
- ✅ 📦 Дайын шаблондар (Echo, Admin, AI Chat, Channel)

## Қолдану

1. `/start`
2. Бот қосу немесе Шаблон таңдау
3. Код жүктеу / AI генератор
4. Start
