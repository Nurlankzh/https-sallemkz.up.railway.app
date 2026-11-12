"""Ready-made bot templates."""

TEMPLATES = {
    "echo": {
        "name": "Echo Bot",
        "description": "Жіберілген хабарламаны қайталайды",
        "code": '''import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
import os

logging.basicConfig(level=logging.INFO)
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()

@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer("👋 Сәлем! Мен Echo ботпын. Маған бірдеңе жаз.")

@dp.message()
async def echo(message: types.Message):
    await message.answer(message.text or "📝")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
'''
    },
    "admin": {
        "name": "Admin Panel Bot",
        "description": "Админ панелі бар қарапайым бот",
        "code": '''import asyncio
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import os

logging.basicConfig(level=logging.INFO)
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()

ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

@dp.message(CommandStart())
async def start(message: types.Message):
    if is_admin(message.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
            [InlineKeyboardButton(text="📢 Хабарлама жіберу", callback_data="broadcast")],
        ])
        await message.answer("🛠 Админ панелі", reply_markup=kb)
    else:
        await message.answer("👋 Сәлем!")

@dp.callback_query(F.data == "stats")
async def stats(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    await callback.message.answer("📊 Статистика: бот жұмыс істеп тұр.")

@dp.message(Command("id"))
async def my_id(message: types.Message):
    await message.answer(f"Сіздің ID: `{message.from_user.id}`", parse_mode="Markdown")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
'''
    },
    "ai_chat": {
        "name": "AI Chat Bot",
        "description": "AI-мен сөйлесетін бот (OpenAI/xAI)",
        "code": '''import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
import aiohttp

logging.basicConfig(level=logging.INFO)
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()

AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_API_BASE = os.getenv("AI_API_BASE", "https://api.openai.com/v1")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")

@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer("🤖 AI Chat Bot\\nМаған сұрақ қойыңыз!")

@dp.message()
async def chat(message: types.Message):
    if not AI_API_KEY:
        await message.answer("❌ AI_API_KEY орнатылмаған.")
        return
    await message.bot.send_chat_action(message.chat.id, "typing")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{AI_API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": AI_MODEL,
                    "messages": [
                        {"role": "system", "content": "Сен пайдалы AI көмекшісісің. Қазақша жауап бер."},
                        {"role": "user", "content": message.text or ""},
                    ],
                    "max_tokens": 1000,
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    await message.answer(f"❌ API қатесі: {data}")
                    return
                answer = data["choices"][0]["message"]["content"]
                await message.answer(answer)
    except Exception as e:
        await message.answer(f"❌ Қате: {e}")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
'''
    },
    "channel": {
        "name": "Channel Poster",
        "description": "Каналға пост жіберетін бот",
        "code": '''import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command

logging.basicConfig(level=logging.INFO)
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()

CHANNEL_ID = os.getenv("CHANNEL_ID", "")  # -100xxxxxxxxxx
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

@dp.message(CommandStart())
async def start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("📢 Channel Poster\\n/post <мәтін> — каналға жіберу")

@dp.message(Command("post"))
async def post(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    text = message.text.replace("/post", "", 1).strip()
    if not text or not CHANNEL_ID:
        await message.answer("Қолдану: /post мәтін\\nCHANNEL_ID орнатыңыз.")
        return
    try:
        await bot.send_message(CHANNEL_ID, text)
        await message.answer("✅ Жіберілді!")
    except Exception as e:
        await message.answer(f"❌ Қате: {e}")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
'''
    },
}


def get_template_list():
    return list(TEMPLATES.keys())


def get_template(key: str) -> dict | None:
    return TEMPLATES.get(key)
