"""AI Code Generator helper (OpenAI-compatible API)."""

import logging
from typing import Optional

import aiohttp

import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Сен кәсіби Python Telegram бот әзірлеушісісің.
Пайдаланушының сипаттамасы бойынша толық, жұмыс істейтін aiogram 3.x бот кодын жаз.

Талаптар:
- Тек Python кодты қайтар (markdown қоршауынсыз, түсініктемесіз)
- aiogram 3.x қолдану
- BOT_TOKEN-ді os.getenv("BOT_TOKEN") арқылы алу
- asyncio + dp.start_polling қолдану
- Қазақша немесе орысша хабарламалар
- Қате өңдеуді қосу
- Код бір файлда болуы керек (bot.py)
"""


async def generate_bot_code(user_prompt: str) -> tuple[bool, str]:
    """
    Generate bot code using AI.
    Returns (success, code_or_error_message)
    """
    if not config.AI_API_KEY:
        return False, (
            "❌ AI генератор үшін API кілті орнатылмаған.\n\n"
            "Railway Variables-қа қосыңыз:\n"
            "<code>AI_API_KEY</code> = сіздің API кілтіңіз\n\n"
            "Қосымша (опционалды):\n"
            "<code>AI_API_BASE</code> = https://api.openai.com/v1\n"
            "<code>AI_MODEL</code> = gpt-4o-mini\n\n"
            "xAI Grok үшін:\n"
            "AI_API_BASE = https://api.x.ai/v1\n"
            "AI_MODEL = grok-2-latest"
        )

    url = f"{config.AI_API_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.AI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.AI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Мына ботты жаса:\n\n{user_prompt}",
            },
        ],
        "temperature": 0.3,
        "max_tokens": 4000,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=90),
            ) as resp:
                data = await resp.json()

                if resp.status != 200:
                    err = data.get("error", data)
                    return False, f"❌ AI API қатесі ({resp.status}):\n{err}"

                content = data["choices"][0]["message"]["content"]
                # Strip markdown code fences if present
                code = content.strip()
                if code.startswith("```"):
                    lines = code.split("\n")
                    # remove first and last fence
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    code = "\n".join(lines)

                if not code.strip():
                    return False, "❌ AI бос жауап қайтарды."

                return True, code

    except asyncio.TimeoutError:
        return False, "❌ AI жауап беру уақыты аяқталды (timeout)."
    except Exception as e:
        logger.exception("AI generate error")
        return False, f"❌ AI қатесі: {e}"


# Need asyncio for TimeoutError reference
import asyncio
