# -*- coding: utf-8 -*-
"""
Динамикалық Telegram бот (aiogram 3)
- Қазақша / орысша тіл таңдау
- Каналдар, сатып алу, алдын ала қарау (бірнеше фото/видео)
- Тегін қолжетімділік (реферал)
- Қазақша админ панелі
- Рассылкада мәтін + фото/видео/альбом
- /start басқан сайын ескі мәзір өшіріледі
- Бұғаттау жүйесі
- 🛡 Профессионалды қауіпсіздік қабаты (10/10)
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
import traceback
from collections import defaultdict, deque
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable, Awaitable
from urllib.parse import quote

from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, TelegramObject,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramRetryAfter,
    TelegramBadRequest,
    TelegramAPIError,
)

# =========================================================
# КОНФИГУРАЦИЯ (Variables қажет емес)
# =========================================================
BOT_TOKEN = "9037365082:AAEh4z9EEXCL9mexLY9FuoJuP9xTpD6iREE"
ADMIN_IDS = [8942851703]
BUY_USERNAME = "JOKWRHUB18"

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_BASE_DIR, "bot_database.db")

# =========================================================
# 🛡 ПРОФЕССИОНАЛДЫ ҚАУІПСІЗДІК (10/10)
# =========================================================
USER_COOLDOWN = 1.3
GLOBAL_MSG_PER_SECOND = 18
BROADCAST_BASE_DELAY = 0.09
BROADCAST_BATCH = 20
BROADCAST_BATCH_PAUSE = 2.2
MAX_ACTIONS_PER_MINUTE = 25
AUTO_BAN_ON_BLOCK = True
ADMIN_ALERT_ON_FLOOD = True

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
router = Router()


# =========================================================
# 🛡 SECURITY CORE
# =========================================================
class SecurityCore:
    def __init__(self):
        self.user_last = defaultdict(float)
        self.user_actions = defaultdict(lambda: deque(maxlen=60))
        self.global_times = deque(maxlen=100)
        self.suspicious = set()
        self.flood_hits = 0

    def check_user(self, user_id: int) -> bool:
        now = time.time()
        if now - self.user_last[user_id] < USER_COOLDOWN:
            return False
        self.user_last[user_id] = now

        actions = self.user_actions[user_id]
        actions.append(now)
        while actions and now - actions[0] > 60:
            actions.popleft()

        if len(actions) > MAX_ACTIONS_PER_MINUTE:
            self.suspicious.add(user_id)
            return False

        self.global_times.append(now)
        while self.global_times and now - self.global_times[0] > 1.0:
            self.global_times.popleft()
        if len(self.global_times) > GLOBAL_MSG_PER_SECOND:
            return False
        return True

    def is_suspicious(self, user_id: int) -> bool:
        return user_id in self.suspicious


security = SecurityCore()


async def safe_send(bot: Bot, user_id: int, method, *args, **kwargs):
    try:
        return await method(*args, **kwargs)
    except TelegramForbiddenError:
        logger.info(f"[SECURITY] User {user_id} blocked bot → auto-ban")
        if AUTO_BAN_ON_BLOCK:
            set_banned(user_id, True)
        return None
    except TelegramRetryAfter as e:
        wait = e.retry_after + 1.5
        security.flood_hits += 1
        logger.warning(f"[FLOOD] RetryAfter {wait}s for {user_id}")
        if ADMIN_ALERT_ON_FLOOD and security.flood_hits % 5 == 0:
            for admin in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin,
                        f"⚠️ Flood control! Кідіріс: {wait:.1f}с\nЖалпы flood: {security.flood_hits}"
                    )
                except Exception:
                    pass
        await asyncio.sleep(wait)
        try:
            return await method(*args, **kwargs)
        except Exception:
            return None
    except TelegramBadRequest as e:
        err = str(e).lower()
        if any(x in err for x in ["chat not found", "user is deactivated", "bot was blocked"]):
            if AUTO_BAN_ON_BLOCK:
                set_banned(user_id, True)
        logger.warning(f"[BADREQUEST] {user_id}: {e}")
        return None
    except TelegramAPIError as e:
        logger.error(f"[API] {user_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"[UNEXPECTED] {user_id}: {e}\n{traceback.format_exc()}")
        return None


# =========================================================
# БҰҒАТТАУ + SECURITY MIDDLEWARE
# =========================================================
class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is not None:
            if not is_admin(user.id) and is_banned(user.id):
                return None
        return await handler(event, data)


class SecurityMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and not is_admin(user.id):
            if not security.check_user(user.id):
                return None
            if security.is_suspicious(user.id):
                await asyncio.sleep(0.8)
        return await handler(event, data)


# =========================================================
# ДЕРЕКҚОР
# =========================================================
def db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
    except sqlite3.OperationalError as e:
        fallback = os.path.join("/tmp", os.path.basename(DB_PATH) or "bot_database.db")
        logger.warning("DB ашылмады (%s): %s → fallback: %s", DB_PATH, e, fallback)
        conn = sqlite3.connect(fallback, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        language TEXT,
        referrer_id INTEGER,
        invited_count INTEGER DEFAULT 0,
        has_free_access INTEGER DEFAULT 0,
        has_paid_access INTEGER DEFAULT 0,
        joined_at TEXT,
        last_active TEXT,
        last_menu_msg_id INTEGER
    )""")

    for col, typedef in [
        ("last_menu_msg_id", "INTEGER"),
        ("is_banned", "INTEGER DEFAULT 0"),
        ("banned_at", "TEXT"),
        ("bot_msg_ids", "TEXT"),
    ]:
        try:
            cur.execute(f"ALTER TABLE users ADD COLUMN {col} {typedef}")
        except Exception:
            pass

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS texts (
        key TEXT PRIMARY KEY,
        text_kk TEXT,
        text_ru TEXT
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS buttons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        menu TEXT NOT NULL,
        text_kk TEXT,
        text_ru TEXT,
        callback_data TEXT,
        url TEXT,
        row INTEGER DEFAULT 0,
        position INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name_kk TEXT,
        name_ru TEXT,
        description_kk TEXT,
        description_ru TEXT,
        price INTEGER DEFAULT 1500,
        invite_link TEXT,
        preview_link TEXT,
        preview_text_kk TEXT,
        preview_text_ru TEXT,
        preview_photo_id TEXT,
        preview_media TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TEXT
    )""")

    for col in ("preview_text_kk", "preview_text_ru", "preview_photo_id", "preview_media"):
        try:
            cur.execute(f"ALTER TABLE channels ADD COLUMN {col} TEXT")
        except Exception:
            pass

    cur.execute("""
    CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER UNIQUE,
        created_at TEXT
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        channel_id INTEGER,
        amount INTEGER,
        status TEXT DEFAULT 'pending',
        created_at TEXT,
        paid_at TEXT
    )""")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_banned ON users(is_banned)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_active ON users(last_active)")

    defaults = {
        "referral_needed": "3",
        "preview_minutes": "2",
        "support_username": "@JOKWRHUB18",
        "welcome_kk": "🔥 Қош келдіңіз, тәттім 😈💋\n\nМенің VIP әлеміме қош келдің...\nБөлімді таңдап, ләззат ал 🔥💦",
        "welcome_ru": "🔥 Добро пожаловать, сладкий 😈💋\n\nДобро пожаловать в мой VIP мир...\nВыбери раздел и получи удовольствие 🔥💦",
        "free_access_reward_link": "",
        "bot_username": "",
    }
    for key, value in defaults.items():
        cur.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))

    cur.execute("SELECT value FROM settings WHERE key='welcome_kk'")
    row = cur.fetchone()
    if row and "тәттім" not in (row["value"] or ""):
        cur.execute("UPDATE settings SET value=? WHERE key='welcome_kk'", (defaults["welcome_kk"],))
        cur.execute("UPDATE settings SET value=? WHERE key='welcome_ru'", (defaults["welcome_ru"],))

    default_texts = [
        ("main_menu", "Бөлімді таңдаңыз ⬇️", "Выберите раздел ⬇️"),
        ("channels_title", "📁 Қажетті каналды таңдаңыз:", "📁 Выберите канал:"),
        ("back", "◀️ Артқа", "◀️ Назад"),
        ("buy", "🛒 Сатып алу", "🛒 Купить"),
        ("preview", "👁 Алдын ала қарау", "👁 Предпросмотр"),
        ("free_channel", "🎁 Тегін қолжетімділік", "🎁 Бесплатный доступ"),
        ("referral_info", "Достарыңызды шақырып, тегін қолжетімділік алыңыз!", "Пригласите друзей и получите бесплатный доступ!"),
        ("invited", "Шақырылғандар: {count}/{needed}", "Приглашено: {count}/{needed}"),
        ("your_link", "Сіздің сілтемеңіз:\n{link}", "Ваша ссылка:\n{link}"),
        ("access_granted", "✅ Тегін қолжетімділік берілді!", "✅ Бесплатный доступ предоставлен!"),
    ]
    for item in default_texts:
        cur.execute("INSERT OR IGNORE INTO texts(key,text_kk,text_ru) VALUES(?,?,?)", item)

    cur.execute("SELECT COUNT(*) FROM buttons WHERE menu='start'")
    if cur.fetchone()[0] == 0:
        defaults_buttons = [
            ("start", "📁 Каналдар", "📁 Каналы", "menu_channels", None, 0, 0),
            ("start", "🎁 Тегін канал", "🎁 Бесплатный канал", "menu_free", None, 1, 0),
            ("start", "🤖 Көмек", "🤖 Помощь", "menu_help", None, 2, 0),
            ("start", "✉️ Админге жазу", "✉️ Написать админу", "menu_support", None, 3, 0),
        ]
        cur.executemany("""
            INSERT INTO buttons(menu,text_kk,text_ru,callback_data,url,row,position)
            VALUES(?,?,?,?,?,?,?)
        """, defaults_buttons)

    cur.execute("SELECT COUNT(*) FROM channels")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO channels
            (name_kk,name_ru,description_kk,description_ru,price,invite_link,created_at)
            VALUES(?,?,?,?,?,?,?)
        """, (
            "VIP канал", "VIP канал",
            "⭐ VIP канал\n\n18+ контент. Қолжетімділік алу үшін «Сатып алу» батырмасын басыңыз.",
            "⭐ VIP канал\n\nКонтент 18+. Для получения доступа нажмите «Купить».",
            1500, "", datetime.now().isoformat()
        ))

    conn.commit()
    conn.close()


# =========================================================
# КӨМЕКШІ ФУНКЦИЯЛАР
# =========================================================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_banned(user_id: int) -> bool:
    user = get_user(user_id)
    if not user:
        return False
    return bool(user.get("is_banned"))


def set_banned(user_id: int, banned: bool):
    conn = db()
    if banned:
        conn.execute(
            "UPDATE users SET is_banned=1, banned_at=? WHERE user_id=?",
            (datetime.now().isoformat(), user_id)
        )
    else:
        conn.execute(
            "UPDATE users SET is_banned=0, banned_at=NULL WHERE user_id=?",
            (user_id,)
        )
    conn.commit()
    conn.close()


def get_banned_users() -> List[dict]:
    conn = db()
    rows = conn.execute(
        "SELECT * FROM users WHERE is_banned=1 ORDER BY banned_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def setting(key: str, default: str = "") -> str:
    conn = db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    conn = db()
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))
    conn.commit()
    conn.close()


def tr(key: str, lang: str = "kk") -> str:
    conn = db()
    row = conn.execute("SELECT text_kk,text_ru FROM texts WHERE key=?", (key,)).fetchone()
    conn.close()
    if not row:
        return key
    return row["text_kk"] if lang == "kk" else row["text_ru"]


def save_text(key: str, kk: str, ru: str):
    conn = db()
    conn.execute("INSERT OR REPLACE INTO texts(key,text_kk,text_ru) VALUES(?,?,?)", (key, kk, ru))
    conn.commit()
    conn.close()


def get_user(user_id: int) -> Optional[dict]:
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def ensure_user(user_id: int, username=None, full_name=None, referrer_id=None):
    conn = db()
    cur = conn.cursor()
    old = cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
    now = datetime.now().isoformat()
    if not old:
        if referrer_id == user_id:
            referrer_id = None
        cur.execute("""
            INSERT INTO users(user_id,username,full_name,referrer_id,joined_at,last_active)
            VALUES(?,?,?,?,?,?)
        """, (user_id, username, full_name, referrer_id, now, now))
        if referrer_id:
            ref_exists = cur.execute("SELECT user_id FROM users WHERE user_id=?", (referrer_id,)).fetchone()
            if ref_exists:
                cur.execute("""
                    INSERT OR IGNORE INTO referrals(referrer_id,referred_id,created_at)
                    VALUES(?,?,?)
                """, (referrer_id, user_id, now))
                if cur.rowcount:
                    cur.execute("UPDATE users SET invited_count=invited_count+1 WHERE user_id=?", (referrer_id,))
    else:
        cur.execute("""
            UPDATE users SET username=?,full_name=?,last_active=?
            WHERE user_id=?
        """, (username, full_name, now, user_id))
    conn.commit()
    conn.close()


def user_lang(user_id: int) -> str:
    user = get_user(user_id)
    return user.get("language") if user and user.get("language") else "kk"


def set_user_lang(user_id: int, lang: str):
    conn = db()
    conn.execute("UPDATE users SET language=? WHERE user_id=?", (lang, user_id))
    conn.commit()
    conn.close()


def get_bot_msg_ids(user_id: int) -> List[int]:
    user = get_user(user_id)
    if not user:
        return []
    ids: List[int] = []
    raw = user.get("bot_msg_ids")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                ids.extend(int(x) for x in data if x)
        except Exception:
            pass
    old = user.get("last_menu_msg_id")
    if old:
        try:
            oid = int(old)
            if oid and oid not in ids:
                ids.append(oid)
        except Exception:
            pass
    seen = set()
    unique: List[int] = []
    for x in ids:
        if x not in seen:
            seen.add(x)
            unique.append(x)
    return unique[-50:]


def set_bot_msg_ids(user_id: int, msg_ids: List[int]):
    clean = []
    seen = set()
    for x in msg_ids:
        try:
            i = int(x)
        except Exception:
            continue
        if i and i not in seen:
            seen.add(i)
            clean.append(i)
    clean = clean[-50:]
    conn = db()
    conn.execute(
        "UPDATE users SET bot_msg_ids=?, last_menu_msg_id=? WHERE user_id=?",
        (json.dumps(clean, ensure_ascii=False), clean[-1] if clean else None, user_id)
    )
    conn.commit()
    conn.close()


def append_bot_msg_ids(user_id: int, *msg_ids: int):
    current = get_bot_msg_ids(user_id)
    for mid in msg_ids:
        if mid:
            try:
                current.append(int(mid))
            except Exception:
                pass
    set_bot_msg_ids(user_id, current)


def set_last_menu_msg(user_id: int, msg_id: int):
    if msg_id:
        set_bot_msg_ids(user_id, [msg_id])
    else:
        set_bot_msg_ids(user_id, [])


def get_last_menu_msg(user_id: int) -> Optional[int]:
    ids = get_bot_msg_ids(user_id)
    return ids[-1] if ids else None


def get_buttons(menu: str, lang: str):
    conn = db()
    rows = conn.execute("""
        SELECT * FROM buttons
        WHERE menu=? AND is_active=1 ORDER BY row,position,id
    """, (menu,)).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "text": r["text_kk"] if lang == "kk" else r["text_ru"],
            "callback": r["callback_data"],
            "url": r["url"]
        }
        for r in rows
    ]


def get_channels(active=True):
    conn = db()
    if active:
        rows = conn.execute("SELECT * FROM channels WHERE is_active=1 ORDER BY id").fetchall()
    else:
        rows = conn.execute("SELECT * FROM channels ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_channel(channel_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_preview_media(channel: dict) -> List[Dict[str, str]]:
    media = []
    raw = channel.get("preview_media")
    if raw:
        try:
            media = json.loads(raw)
            if isinstance(media, list):
                return media
        except Exception:
            pass
    if channel.get("preview_photo_id"):
        media.append({"type": "photo", "file_id": channel["preview_photo_id"]})
    return media


async def delete_old_menu(bot: Bot, chat_id: int, user_id: int):
    ids = get_bot_msg_ids(user_id)
    for mid in ids:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass
        await asyncio.sleep(0.03)
    set_bot_msg_ids(user_id, [])


async def wipe_bot_messages(bot: Bot, chat_id: int, user_id: int) -> int:
    ids = get_bot_msg_ids(user_id)
    deleted = 0
    for mid in ids:
        try:
            await bot.delete_message(chat_id, mid)
            deleted += 1
        except Exception:
            pass
        await asyncio.sleep(0.03)
    set_bot_msg_ids(user_id, [])
    return deleted


async def send_main_menu(bot: Bot, chat_id: int, user_id: int, edit_message: Message = None):
    lang = user_lang(user_id)
    welcome = setting("welcome_kk") if lang == "kk" else setting("welcome_ru")
    kb = main_keyboard(user_id)
    await delete_old_menu(bot, chat_id, user_id)
    msg = await bot.send_message(chat_id, welcome, reply_markup=kb)
    set_bot_msg_ids(user_id, [msg.message_id])
    return msg


# =========================================================
# КЛАВИАТУРАЛАР
# =========================================================
def language_keyboard():
    b = InlineKeyboardBuilder()
    b.button(text="🇰🇿 Қазақша", callback_data="set_lang:kk")
    b.button(text="🇷🇺 Русский", callback_data="set_lang:ru")
    b.adjust(1)
    return b.as_markup()


def main_keyboard(user_id: int):
    lang = user_lang(user_id)
    b = InlineKeyboardBuilder()
    for item in get_buttons("start", lang):
        if item["url"]:
            b.button(text=item["text"], url=item["url"])
        else:
            b.button(text=item["text"], callback_data=item["callback"] or f"button:{item['id']}")
    b.adjust(1)
    return b.as_markup()


def back_keyboard(user_id: int, target="back_main"):
    b = InlineKeyboardBuilder()
    b.button(text=tr("back", user_lang(user_id)), callback_data=target)
    return b.as_markup()


def channels_keyboard(user_id: int):
    lang = user_lang(user_id)
    b = InlineKeyboardBuilder()
    for ch in get_channels():
        title = ch["name_kk"] if lang == "kk" else ch["name_ru"]
        b.button(text=title, callback_data=f"channel:{ch['id']}")
    b.button(text=tr("back", lang), callback_data="back_main")
    b.adjust(1)
    return b.as_markup()


def get_buy_url(lang: str) -> str:
    purchase_text = (
        "Сәлеметсіз бе! Сатып алу туралы ақпарат беріңіз."
        if lang == "kk"
        else "Здравствуйте! Хочу приобрести доступ. Подскажите, пожалуйста."
    )
    return f"https://t.me/{BUY_USERNAME}?text={quote(purchase_text)}"


def channel_keyboard(channel_id: int, user_id: int):
    lang = user_lang(user_id)
    b = InlineKeyboardBuilder()
    b.button(text=tr("buy", lang), url=get_buy_url(lang))
    b.button(text=tr("preview", lang), callback_data=f"preview:{channel_id}")
    b.button(text=tr("back", lang), callback_data="menu_channels")
    b.adjust(1)
    return b.as_markup()


def admin_keyboard():
    b = InlineKeyboardBuilder()
    items = [
        ("📊 Статистика", "admin_stats"),
        ("📝 Мәтіндерді басқару", "admin_texts"),
        ("🔘 Батырмаларды басқару", "admin_buttons"),
        ("📁 Каналдарды басқару", "admin_channels"),
        ("🎁 Реферал жүйесін баптау", "admin_referral"),
        ("📢 Барлығына хабарлама жіберу", "admin_broadcast"),
        ("🚫 Бұғаттау / Бұғаттан шығару", "admin_ban"),
        ("⚙️ Жалпы баптаулар", "admin_settings"),
    ]
    for title, data in items:
        b.button(text=title, callback_data=data)
    b.adjust(1)
    return b.as_markup()


def broadcast_keyboard(user_id: int = None):
    lang = user_lang(user_id) if user_id else "kk"
    b = InlineKeyboardBuilder()
    b.button(text=tr("buy", lang), url=get_buy_url(lang))
    b.button(text="🏠 Меню" if lang == "kk" else "🏠 Меню", callback_data="back_main")
    b.adjust(1)
    return b.as_markup()


# =========================================================
# FSM КҮЙЛЕРІ
# =========================================================
class AdminStates(StatesGroup):
    text_key = State()
    text_kk = State()
    text_ru = State()
    button_menu = State()
    button_kk = State()
    button_ru = State()
    button_action = State()
    channel_name_kk = State()
    channel_name_ru = State()
    channel_desc_kk = State()
    channel_desc_ru = State()
    channel_price = State()
    channel_link = State()
    referral_count = State()
    referral_link = State()
    broadcast = State()
    setting_value = State()
    support = State()
    set_preview_content = State()
    ban_user_id = State()


# =========================================================
# ПАЙДАЛАНУШЫ БӨЛІМІ
# =========================================================
@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    args = (message.text or "").split()
    referrer = int(args[1]) if len(args) > 1 and args[1].isdigit() else None

    ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
        referrer
    )

    user = get_user(message.from_user.id)
    if not user or not user["language"]:
        await delete_old_menu(message.bot, message.chat.id, message.from_user.id)
        msg = await message.answer(
            "🇰🇿 Тілді таңдаңыз\n🇷🇺 Выберите язык",
            reply_markup=language_keyboard()
        )
        set_last_menu_msg(message.from_user.id, msg.message_id)
        return

    await send_main_menu(message.bot, message.chat.id, message.from_user.id)


@router.callback_query(F.data.startswith("set_lang:"))
async def set_language(call: CallbackQuery):
    lang = call.data.split(":")[1]
    ensure_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    set_user_lang(call.from_user.id, lang)
    await send_main_menu(call.bot, call.message.chat.id, call.from_user.id, edit_message=call.message)
    await call.answer()


@router.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    user = get_user(call.from_user.id)
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    await delete_old_menu(call.bot, chat_id, user_id)
    try:
        await call.message.delete()
    except Exception:
        pass

    if not user or not user["language"]:
        msg = await call.bot.send_message(
            chat_id,
            "🇰🇿 Тілді таңдаңыз\n🇷🇺 Выберите язык",
            reply_markup=language_keyboard(),
        )
        set_bot_msg_ids(user_id, [msg.message_id])
    else:
        await send_main_menu(call.bot, chat_id, user_id)
    await call.answer()


@router.callback_query(F.data == "menu_channels")
async def menu_channels(call: CallbackQuery):
    lang = user_lang(call.from_user.id)
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    text = tr("channels_title", lang)
    kb = channels_keyboard(user_id)

    tracked = get_bot_msg_ids(user_id)
    if len(tracked) > 1:
        await delete_old_menu(call.bot, chat_id, user_id)
        try:
            await call.message.delete()
        except Exception:
            pass
        msg = await call.bot.send_message(chat_id, text, reply_markup=kb)
        set_bot_msg_ids(user_id, [msg.message_id])
    else:
        try:
            await call.message.edit_text(text, reply_markup=kb)
            set_last_menu_msg(user_id, call.message.message_id)
        except Exception:
            await delete_old_menu(call.bot, chat_id, user_id)
            msg = await call.bot.send_message(chat_id, text, reply_markup=kb)
            set_bot_msg_ids(user_id, [msg.message_id])
    await call.answer()


@router.callback_query(F.data.startswith("channel:"))
async def show_channel(call: CallbackQuery):
    try:
        channel_id = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        await call.answer("Қате канал нөмірі", show_alert=True)
        return
    ch = get_channel(channel_id)
    if not ch or not ch["is_active"]:
        await call.answer("Канал табылмады немесе уақытша жабық.", show_alert=True)
        return
    lang = user_lang(call.from_user.id)
    desc = ch["description_kk"] if lang == "kk" else ch["description_ru"]
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    await delete_old_menu(call.bot, chat_id, user_id)
    try:
        await call.message.delete()
    except Exception:
        pass

    msg = await call.bot.send_message(
        chat_id, desc, reply_markup=channel_keyboard(channel_id, user_id)
    )
    set_bot_msg_ids(user_id, [msg.message_id])
    await call.answer()


@router.callback_query(F.data.startswith("preview:"))
async def preview(call: CallbackQuery):
    try:
        channel_id = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        await call.answer("Қате", show_alert=True)
        return

    ch = get_channel(channel_id)
    if not ch:
        await call.answer("Канал табылмады", show_alert=True)
        return

    lang = user_lang(call.from_user.id)
    minutes = setting("preview_minutes", "2")

    custom_text = ch.get("preview_text_kk") if lang == "kk" else ch.get("preview_text_ru")
    if custom_text and custom_text.strip():
        text = custom_text
    else:
        if lang == "kk":
            text = f"👁 Алдын ала қарау\n\nҚарау уақыты: {minutes} минут."
        else:
            text = f"👁 Предпросмотр\n\nВремя просмотра: {minutes} мин."

    b = InlineKeyboardBuilder()
    b.button(text=tr("buy", lang), url=get_buy_url(lang))
    if ch.get("preview_link"):
        watch = "👁 Қарау" if lang == "kk" else "👁 Смотреть"
        b.button(text=watch, url=ch["preview_link"])
    b.button(text=tr("back", lang), callback_data=f"channel:{channel_id}")
    b.adjust(1)
    kb = b.as_markup()

    media_list = get_preview_media(ch)
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    await delete_old_menu(call.bot, chat_id, user_id)
    try:
        await call.message.delete()
    except Exception:
        pass

    tracked: List[int] = []

    if not media_list:
        msg = await call.bot.send_message(chat_id, text, reply_markup=kb)
        tracked.append(msg.message_id)
        set_bot_msg_ids(user_id, tracked)
        await call.answer()
        return

    if len(media_list) == 1:
        item = media_list[0]
        try:
            if item["type"] == "photo":
                msg = await call.bot.send_photo(chat_id, photo=item["file_id"], caption=text, reply_markup=kb)
            elif item["type"] == "video":
                msg = await call.bot.send_video(chat_id, video=item["file_id"], caption=text, reply_markup=kb)
            else:
                msg = await call.bot.send_document(chat_id, document=item["file_id"], caption=text, reply_markup=kb)
            tracked.append(msg.message_id)
        except Exception as e:
            logger.warning("Preview media error: %s", e)
            msg = await call.bot.send_message(chat_id, text + "\n\n⚠️ Медиа жүктелмеді.", reply_markup=kb)
            tracked.append(msg.message_id)
        set_bot_msg_ids(user_id, tracked)
        await call.answer()
        return

    input_media = []
    for i, item in enumerate(media_list[:10]):
        caption = text if i == 0 else None
        if item["type"] == "photo":
            input_media.append(InputMediaPhoto(media=item["file_id"], caption=caption))
        elif item["type"] == "video":
            input_media.append(InputMediaVideo(media=item["file_id"], caption=caption))
        else:
            input_media.append(InputMediaDocument(media=item["file_id"], caption=caption))

    try:
        msgs = await call.bot.send_media_group(chat_id, media=input_media)
        for m in msgs:
            tracked.append(m.message_id)
        btn_msg = await call.bot.send_message(chat_id, "⬇️", reply_markup=kb)
        tracked.append(btn_msg.message_id)
    except Exception as e:
        logger.warning("Preview album error: %s", e)
        for i, item in enumerate(media_list[:10]):
            cap = text if i == 0 else None
            try:
                if item["type"] == "photo":
                    m = await call.bot.send_photo(chat_id, photo=item["file_id"], caption=cap)
                elif item["type"] == "video":
                    m = await call.bot.send_video(chat_id, video=item["file_id"], caption=cap)
                else:
                    m = await call.bot.send_document(chat_id, document=item["file_id"], caption=cap)
                tracked.append(m.message_id)
            except Exception:
                pass
        btn_msg = await call.bot.send_message(chat_id, "⬇️", reply_markup=kb)
        tracked.append(btn_msg.message_id)

    set_bot_msg_ids(user_id, tracked)
    await call.answer()


@router.callback_query(F.data == "menu_free")
async def menu_free(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    user = get_user(uid)
    needed = int(setting("referral_needed", "3"))
    invited = user["invited_count"] if user else 0
    bot_username = setting("bot_username", "").strip().lstrip("@")

    if not bot_username:
        me = await call.bot.get_me()
        bot_username = me.username

    link = f"https://t.me/{bot_username}?start={uid}"
    if invited >= needed or (user and user["has_free_access"]):
        reward = setting("free_access_reward_link", "")
        text = tr("access_granted", lang)
        if reward:
            text += f"\n\n{reward}"
        else:
            text += "\n\nАдмин сыйлық сілтемесін әлі орнатпаған."
        conn = db()
        conn.execute("UPDATE users SET has_free_access=1 WHERE user_id=?", (uid,))
        conn.commit()
        conn.close()
    else:
        text = (
            f"{tr('free_channel', lang)}\n\n"
            f"{tr('referral_info', lang)}\n\n"
            f"{tr('invited', lang).format(count=invited, needed=needed)}\n\n"
            f"{tr('your_link', lang).format(link=link)}"
        )

    b = InlineKeyboardBuilder()
    if invited < needed:
        b.button(
            text="📨 Достарға жіберу" if lang == "kk" else "📨 Пригласить друзей",
            url=f"https://t.me/share/url?url={link}"
        )
    b.button(text=tr("back", lang), callback_data="back_main")
    b.adjust(1)
    await call.message.edit_text(text, reply_markup=b.as_markup())
    set_last_menu_msg(call.from_user.id, call.message.message_id)
    await call.answer()


@router.callback_query(F.data == "menu_help")
async def menu_help(call: CallbackQuery):
    lang = user_lang(call.from_user.id)
    b = InlineKeyboardBuilder()
    b.button(text="🛒 Сатып алу жолы" if lang == "kk" else "🛒 Как купить", callback_data="help_buy")
    b.button(text="🎁 Тегін алу жолы" if lang == "kk" else "🎁 Как получить бесплатно", callback_data="help_free")
    b.button(text=tr("back", lang), callback_data="back_main")
    b.adjust(1)
    await call.message.edit_text(
        "Керек бөлімді таңдаңыз:" if lang == "kk" else "Выберите нужный раздел:",
        reply_markup=b.as_markup()
    )
    set_last_menu_msg(call.from_user.id, call.message.message_id)
    await call.answer()


@router.callback_query(F.data == "help_buy")
async def help_buy(call: CallbackQuery):
    lang = user_lang(call.from_user.id)
    if lang == "kk":
        text = (
            "🛒 Сатып алу тәртібі:\n\n"
            "1. «Каналдар» бөліміне кіріңіз.\n"
            "2. Қажетті каналды таңдаңыз.\n"
            "3. «Сатып алу» батырмасын басыңыз.\n"
            "4. Telegram сізді @JOKWRHUB18 чатына апарады."
        )
    else:
        text = (
            "🛒 Как купить:\n\n"
            "1. Откройте раздел «Каналы».\n"
            "2. Выберите нужный канал.\n"
            "3. Нажмите «Купить».\n"
            "4. Telegram откроет чат с @JOKWRHUB18."
        )
    await call.message.edit_text(text, reply_markup=back_keyboard(call.from_user.id, "menu_help"))
    set_last_menu_msg(call.from_user.id, call.message.message_id)
    await call.answer()


@router.callback_query(F.data == "help_free")
async def help_free(call: CallbackQuery):
    lang = user_lang(call.from_user.id)
    needed = setting("referral_needed", "3")
    if lang == "kk":
        text = (
            "🎁 Тегін қолжетімділік алу:\n\n"
            "1. «Тегін канал» бөліміне кіріңіз.\n"
            "2. Реферал сілтемеңізді достарыңызға жіберіңіз.\n"
            f"3. {needed} адам ботқа сіздің сілтемеңіз арқылы тіркелуі керек.\n"
            "4. Шарт орындалған соң тегін қолжетімділік ашылады."
        )
    else:
        text = (
            "🎁 Как получить бесплатный доступ:\n\n"
            "1. Откройте раздел «Бесплатный канал».\n"
            "2. Отправьте друзьям свою реферальную ссылку.\n"
            f"3. {needed} человек должны запустить бота по вашей ссылке.\n"
            "4. После выполнения условия откроется бесплатный доступ."
        )
    await call.message.edit_text(text, reply_markup=back_keyboard(call.from_user.id, "menu_help"))
    set_last_menu_msg(call.from_user.id, call.message.message_id)
    await call.answer()


@router.callback_query(F.data == "menu_support")
async def menu_support(call: CallbackQuery):
    lang = user_lang(call.from_user.id)
    text = (
        f"Админге жазыңыз: @{BUY_USERNAME}"
        if lang == "kk"
        else f"Напишите администратору: @{BUY_USERNAME}"
    )
    b = InlineKeyboardBuilder()
    b.button(text="✉️ Хабарлама жазу" if lang == "kk" else "✉️ Написать", url=f"https://t.me/{BUY_USERNAME}")
    b.button(text=tr("back", lang), callback_data="back_main")
    b.adjust(1)
    await call.message.edit_text(text, reply_markup=b.as_markup())
    set_last_menu_msg(call.from_user.id, call.message.message_id)
    await call.answer()


# =========================================================
# АДМИН: НЕГІЗГІ ПАНЕЛЬ
# =========================================================
@router.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Бұл бөлім тек әкімшіге арналған.")
        return
    await message.answer("🛠 ӘКІМШІ БАСҚАРУ ПАНЕЛІ", reply_markup=admin_keyboard())


@router.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    conn = db()
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    paid = conn.execute("SELECT COUNT(*) FROM users WHERE has_paid_access=1").fetchone()[0]
    free = conn.execute("SELECT COUNT(*) FROM users WHERE has_free_access=1").fetchone()[0]
    orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
    conn.close()
    text = (
        "📊 БОТ СТАТИСТИКАСЫ\n\n"
        f"👥 Барлық қолданушы: {total}\n"
        f"💳 Ақылы қолжетімділігі бар: {paid}\n"
        f"🎁 Тегін қолжетімділігі бар: {free}\n"
        f"📦 Барлық өтініш: {orders}\n"
        f"⏳ Күтілудегі өтініш: {pending}"
    )
    await call.message.edit_text(text, reply_markup=admin_keyboard())
    await call.answer()


# =========================================================
# АДМИН: МӘТІНДЕР
# =========================================================
TEXT_KEY_NAMES = {
    "main_menu": "Негізгі мәзір мәтіні",
    "channels_title": "Каналдар бөлімінің тақырыбы",
    "back": "«Артқа» батырмасы",
    "buy": "«Сатып алу» батырмасы",
    "preview": "«Алдын ала қарау» батырмасы",
    "free_channel": "Тегін канал тақырыбы",
    "referral_info": "Реферал ақпараты",
    "invited": "Шақырылғандар саны",
    "your_link": "Реферал сілтемесі",
    "access_granted": "Тегін қолжетімділік берілді",
}


@router.callback_query(F.data == "admin_texts")
async def admin_texts(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    conn = db()
    rows = conn.execute("SELECT key FROM texts ORDER BY key").fetchall()
    conn.close()
    b = InlineKeyboardBuilder()
    for row in rows:
        key = row["key"]
        name = TEXT_KEY_NAMES.get(key, key)
        b.button(text=f"✏️ {name}", callback_data=f"edit_text:{key}")
    b.button(text="➕ Жаңа мәтін қосу", callback_data="add_text")
    b.button(text="◀️ Әкімші панелі", callback_data="admin_home")
    b.adjust(1)
    await call.message.edit_text("📝 Өзгертетін мәтінді таңдаңыз:", reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data == "admin_home")
async def admin_home(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    await state.clear()
    await call.message.edit_text("🛠 ӘКІМШІ БАСҚАРУ ПАНЕЛІ", reply_markup=admin_keyboard())
    await call.answer()


@router.callback_query(F.data.startswith("edit_text:"))
async def edit_text(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    key = call.data.split(":", 1)[1]
    await state.update_data(text_key=key)
    await state.set_state(AdminStates.text_kk)
    name = TEXT_KEY_NAMES.get(key, key)
    await call.message.edit_text(f"«{name}» мәтінінің қазақша нұсқасын жіберіңіз:")
    await call.answer()


@router.callback_query(F.data == "add_text")
async def add_text(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    await state.set_state(AdminStates.text_key)
    await call.message.edit_text("Жаңа мәтінге латын әріптерімен кілт атауын беріңіз (мысалы: my_text):")
    await call.answer()


@router.message(AdminStates.text_key)
async def text_key(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    key = (message.text or "").strip()
    if not key or len(key) > 60:
        await message.answer("Кілт атауы бос болмауы керек және 60 таңбадан аспасын.")
        return
    await state.update_data(text_key=key)
    await state.set_state(AdminStates.text_kk)
    await message.answer("Қазақша мәтінді жіберіңіз:")


@router.message(AdminStates.text_kk)
async def text_kk(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(text_kk=message.html_text or message.text or "")
    await state.set_state(AdminStates.text_ru)
    await message.answer("Енді орысша мәтінді жіберіңіз:")


@router.message(AdminStates.text_ru)
async def text_ru(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    save_text(data["text_key"], data.get("text_kk", ""), message.html_text or message.text or "")
    await state.clear()
    await message.answer("✅ Екі тілдегі мәтін сақталды.", reply_markup=admin_keyboard())


# =========================================================
# АДМИН: БАТЫРМАЛАР
# =========================================================
@router.callback_query(F.data == "admin_buttons")
async def admin_buttons(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    conn = db()
    rows = conn.execute("SELECT * FROM buttons ORDER BY menu,row,position,id").fetchall()
    conn.close()
    lines = ["🔘 БАТЫРМАЛАР ТІЗІМІ\n"]
    b = InlineKeyboardBuilder()
    for row in rows:
        status = "🟢" if row["is_active"] else "🔴"
        lines.append(f"{status} ID:{row['id']} | {row['menu']} | {row['text_kk']} / {row['text_ru']}")
        b.button(text=f"🗑 Өшіру: {row['text_kk'][:25]}", callback_data=f"delete_button:{row['id']}")
    b.button(text="➕ Жаңа батырма қосу", callback_data="add_button")
    b.button(text="◀️ Әкімші панелі", callback_data="admin_home")
    b.adjust(1)
    await call.message.edit_text("\n".join(lines)[:4000], reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data == "add_button")
async def add_button(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    await state.set_state(AdminStates.button_menu)
    await call.message.edit_text(
        "➕ ЖАҢА БАТЫРМА ҚОСУ\n\n"
        "Қай бөлімге орналастырасыз?\n\n"
        "start — Негізгі мәзір\n"
        "channels — Каналдар бөлімі\n"
        "help — Көмек бөлімі\n\n"
        "Бөлім атауын жазыңыз:"
    )
    await call.answer()


@router.message(AdminStates.button_menu)
async def button_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    menu = (message.text or "").strip()
    if not menu:
        await message.answer("Бөлім атауын жазыңыз:")
        return
    await state.update_data(button_menu=menu)
    await state.set_state(AdminStates.button_kk)
    await message.answer("Батырманың қазақша атауын жазыңыз:")


@router.message(AdminStates.button_kk)
async def button_kk(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(button_kk=message.text or "")
    await state.set_state(AdminStates.button_ru)
    await message.answer("Батырманың орысша атауын жазыңыз:")


@router.message(AdminStates.button_ru)
async def button_ru(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(button_ru=message.text or "")
    await state.set_state(AdminStates.button_action)
    await message.answer(
        "🔗 Батырма басылғанда орындалатын әрекетті енгізіңіз:\n\n"
        "Ішкі бөлімге өту: menu_channels\n"
        "Сілтемеге өту: https://t.me/username"
    )


@router.message(AdminStates.button_action)
async def button_action(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    raw = (message.text or "").strip()
    if raw.startswith("https://") or raw.startswith("http://"):
        url, callback_data = raw, None
    else:
        url, callback_data = None, raw
    conn = db()
    conn.execute("""
        INSERT INTO buttons(menu,text_kk,text_ru,callback_data,url,row,position)
        VALUES(?,?,?,?,?,0,0)
    """, (data["button_menu"], data["button_kk"], data["button_ru"], callback_data, url))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("✅ Батырма қосылды.", reply_markup=admin_keyboard())


@router.callback_query(F.data.startswith("delete_button:"))
async def delete_button(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    button_id = int(call.data.split(":")[1])
    conn = db()
    conn.execute("DELETE FROM buttons WHERE id=?", (button_id,))
    conn.commit()
    conn.close()
    await call.answer("Батырма өшірілді.")
    await admin_buttons(call)


# =========================================================
# АДМИН: КАНАЛДАР + PREVIEW
# =========================================================
@router.callback_query(F.data == "admin_channels")
async def admin_channels(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    channels = get_channels(active=False)
    lines = ["📁 КАНАЛДАРДЫ БАСҚАРУ\n"]
    b = InlineKeyboardBuilder()
    for ch in channels:
        status = "🟢" if ch["is_active"] else "🔴"
        media = get_preview_media(ch)
        has_preview = f"✅{len(media)}" if media or ch.get("preview_text_kk") or ch.get("preview_link") else "❌"
        lines.append(f"{status} ID:{ch['id']} | {ch['name_kk']} | {ch['price']} ₸ | Preview:{has_preview}")
        b.button(text=f"Қосу/өшіру: {ch['name_kk'][:18]}", callback_data=f"toggle_channel:{ch['id']}")
        b.button(text=f"👁 Preview орнату: {ch['name_kk'][:15]}", callback_data=f"set_preview:{ch['id']}")
    b.button(text="➕ Канал қосу", callback_data="add_channel")
    b.button(text="◀️ Әкімші панелі", callback_data="admin_home")
    b.adjust(1)
    await call.message.edit_text("\n".join(lines)[:4000], reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("toggle_channel:"))
async def toggle_channel(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    channel_id = int(call.data.split(":")[1])
    conn = db()
    conn.execute(
        "UPDATE channels SET is_active=CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?",
        (channel_id,)
    )
    conn.commit()
    conn.close()
    await call.answer("Каналдың күйі өзгертілді.")
    await admin_channels(call)


@router.callback_query(F.data.startswith("set_preview:"))
async def set_preview_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    channel_id = int(call.data.split(":")[1])
    ch = get_channel(channel_id)
    if not ch:
        await call.answer("Канал табылмады", show_alert=True)
        return

    await state.update_data(preview_channel_id=channel_id, preview_media_list=[], preview_caption="")
    await state.set_state(AdminStates.set_preview_content)

    await call.message.edit_text(
        f"👁 <b>«{ch['name_kk']}»</b> каналына алдын ала қарау орнату\n\n"
        "📷 <b>Фото</b> немесе 🎬 <b>видео</b> жіберіңіз (қалағанша көп).\n"
        "Мәтінді суретке/видеоға caption ретінде қосыңыз.\n\n"
        "Немесе жай мәтін / сілтеме (https://...) жіберіңіз.\n\n"
        "✅ Барлығын жіберіп болған соң <b>/done</b> деп жазыңыз.\n"
        "❌ Болдырмау: /cancel"
    )
    await call.answer()


@router.message(AdminStates.set_preview_content, F.photo | F.video | F.document)
async def set_preview_media(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    media_list = data.get("preview_media_list") or []

    item = None
    if message.photo:
        item = {"type": "photo", "file_id": message.photo[-1].file_id}
    elif message.video:
        item = {"type": "video", "file_id": message.video.file_id}
    elif message.document:
        item = {"type": "document", "file_id": message.document.file_id}

    if item:
        media_list.append(item)
        if message.caption and not data.get("preview_caption"):
            await state.update_data(preview_caption=message.caption)
        await state.update_data(preview_media_list=media_list)
        await message.answer(f"✅ Медиа қосылды ({len(media_list)} дана).\nТағы жіберіңіз немесе <b>/done</b> деп аяқтаңыз.")


@router.message(AdminStates.set_preview_content, F.text)
async def set_preview_text_or_done(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    text = (message.text or "").strip()
    data = await state.get_data()
    channel_id = data.get("preview_channel_id")

    if not channel_id:
        await state.clear()
        await message.answer("Қате. Қайтадан бастаңыз.", reply_markup=admin_keyboard())
        return

    if text.lower() in ("/done", "done", "дайын", "сақтау", "готово"):
        media_list = data.get("preview_media_list") or []
        caption = data.get("preview_caption") or ""

        conn = db()
        if media_list:
            conn.execute(
                "UPDATE channels SET preview_media=?, preview_text_kk=?, preview_text_ru=?, preview_photo_id=? WHERE id=?",
                (json.dumps(media_list, ensure_ascii=False), caption, caption, media_list[0]["file_id"] if media_list else None, channel_id)
            )
        elif caption:
            conn.execute(
                "UPDATE channels SET preview_text_kk=?, preview_text_ru=? WHERE id=?",
                (caption, caption, channel_id)
            )
        conn.commit()
        conn.close()

        await state.clear()
        await message.answer(
            f"✅ Алдын ала қарау сақталды!\nМедиа саны: {len(media_list)}\nМәтін: {'бар' if caption else 'жоқ'}",
            reply_markup=admin_keyboard()
        )
        return

    if text.startswith("http://") or text.startswith("https://"):
        conn = db()
        conn.execute("UPDATE channels SET preview_link=? WHERE id=?", (text, channel_id))
        conn.commit()
        conn.close()
        await message.answer("✅ Сілтеме сақталды. Тағы медиа жіберіңіз немесе /done деп аяқтаңыз.")
        return

    await state.update_data(preview_caption=message.html_text or text)
    await message.answer("✅ Мәтін сақталды.\nЕнді фото/видео жіберіңіз немесе /done деп аяқтаңыз.")


@router.callback_query(F.data == "add_channel")
async def add_channel(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    await state.set_state(AdminStates.channel_name_kk)
    await call.message.edit_text("Каналдың қазақша атауын жазыңыз:")
    await call.answer()


@router.message(AdminStates.channel_name_kk)
async def channel_name_kk(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(ch_name_kk=message.text or "")
    await state.set_state(AdminStates.channel_name_ru)
    await message.answer("Каналдың орысша атауын жазыңыз:")


@router.message(AdminStates.channel_name_ru)
async def channel_name_ru(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(ch_name_ru=message.text or "")
    await state.set_state(AdminStates.channel_desc_kk)
    await message.answer("Каналдың қазақша сипаттамасын жіберіңіз:")


@router.message(AdminStates.channel_desc_kk)
async def channel_desc_kk(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(ch_desc_kk=message.html_text or message.text or "")
    await state.set_state(AdminStates.channel_desc_ru)
    await message.answer("Каналдың орысша сипаттамасын жіберіңіз:")


@router.message(AdminStates.channel_desc_ru)
async def channel_desc_ru(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(ch_desc_ru=message.html_text or message.text or "")
    await state.set_state(AdminStates.channel_price)
    await message.answer("Бағасын теңгемен жазыңыз (тек сан):")


@router.message(AdminStates.channel_price)
async def channel_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        price = int((message.text or "").strip())
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Бағаны тек оң санмен жазыңыз:")
        return
    await state.update_data(ch_price=price)
    await state.set_state(AdminStates.channel_link)
    await message.answer("Жабық каналға шақыру сілтемесін жіберіңіз.\nСілтеме жоқ болса, - деп жазыңыз:")


@router.message(AdminStates.channel_link)
async def channel_link(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    link = (message.text or "").strip()
    if link == "-":
        link = ""
    conn = db()
    conn.execute("""
        INSERT INTO channels
        (name_kk,name_ru,description_kk,description_ru,price,invite_link,created_at)
        VALUES(?,?,?,?,?,?,?)
    """, (
        data["ch_name_kk"], data["ch_name_ru"],
        data["ch_desc_kk"], data["ch_desc_ru"],
        data["ch_price"], link, datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer(
        "✅ Жаңа канал қосылды.\n\nЕнді «Каналдарды басқару» → «Preview орнату» арқылы фото/видео қосыңыз.",
        reply_markup=admin_keyboard()
    )


# =========================================================
# АДМИН: РЕФЕРАЛ
# =========================================================
@router.callback_query(F.data == "admin_referral")
async def admin_referral(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    needed = setting("referral_needed", "3")
    link = setting("free_access_reward_link", "") or "Орнатылмаған"
    b = InlineKeyboardBuilder()
    b.button(text="🔢 Шақыру санын өзгерту", callback_data="set_ref_count")
    b.button(text="🔗 Сыйлық сілтемесін өзгерту", callback_data="set_ref_link")
    b.button(text="◀️ Әкімші панелі", callback_data="admin_home")
    b.adjust(1)
    await call.message.edit_text(
        f"🎁 РЕФЕРАЛ ЖҮЙЕСІ\n\nҚажетті шақыру саны: {needed}\nСыйлық сілтемесі: {link}",
        reply_markup=b.as_markup()
    )
    await call.answer()


@router.callback_query(F.data == "set_ref_count")
async def set_ref_count(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    await state.set_state(AdminStates.referral_count)
    await call.message.edit_text("Тегін қолжетімділік үшін қажет адам санын жазыңыз:")
    await call.answer()


@router.message(AdminStates.referral_count)
async def save_ref_count(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        count = int((message.text or "").strip())
        if count < 1:
            raise ValueError
    except ValueError:
        await message.answer("1 немесе одан үлкен сан жазыңыз:")
        return
    set_setting("referral_needed", str(count))
    await state.clear()
    await message.answer("✅ Шақыру саны сақталды.", reply_markup=admin_keyboard())


@router.callback_query(F.data == "set_ref_link")
async def set_ref_link(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    await state.set_state(AdminStates.referral_link)
    await call.message.edit_text("Тегін қолжетімділік берілгенде көрсетілетін сілтемені жіберіңіз:")
    await call.answer()


@router.message(AdminStates.referral_link)
async def save_ref_link(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    set_setting("free_access_reward_link", (message.text or "").strip())
    await state.clear()
    await message.answer("✅ Сыйлық сілтемесі сақталды.", reply_markup=admin_keyboard())


# =========================================================
# АДМИН: ЖАППАЙ ХАБАРЛАМА (қауіпсіз)
# =========================================================
@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    await state.set_state(AdminStates.broadcast)
    await call.message.edit_text(
        "📢 Барлық қолданушыға жіберілетін хабарламаны жіберіңіз.\n\n"
        "Қолдау көрсетіледі:\n"
        "• Мәтін\n"
        "• Фото (+ caption)\n"
        "• Видео (+ caption)\n"
        "• Құжат\n\n"
        "Астында автоматты түрде:\n"
        "🛒 Сатып алу\n"
        "🏠 Меню\n"
        "батырмалары қосылады.\n\n"
        "Таратуды тоқтату: /cancel"
    )
    await call.answer()


@router.message(Command("cancel"))
async def cancel_admin_action(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("Әрекет тоқтатылды.", reply_markup=admin_keyboard())


@router.message(AdminStates.broadcast)
async def send_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    has_content = bool(
        message.text or message.caption or message.photo
        or message.video or message.document or message.animation
    )
    if not has_content:
        await message.answer("Мәтін немесе медиа жіберіңіз.\n/cancel — тоқтату")
        return

    conn = db()
    ids = [
        r["user_id"]
        for r in conn.execute(
            "SELECT user_id FROM users WHERE IFNULL(is_banned,0)=0"
        ).fetchall()
    ]
    conn.close()

    total = len(ids)
    success = failed = blocked = 0
    status = await message.answer(f"🛡 Қауіпсіз тарату басталды...\nАдам саны: {total}")

    caption = message.html_text or message.caption or message.text or ""

    for i, uid in enumerate(ids, 1):
        try:
            kb = broadcast_keyboard(uid)
            sent = None

            if message.photo:
                sent = await safe_send(
                    message.bot, uid,
                    message.bot.send_photo,
                    uid, photo=message.photo[-1].file_id,
                    caption=caption, reply_markup=kb
                )
            elif message.video:
                sent = await safe_send(
                    message.bot, uid,
                    message.bot.send_video,
                    uid, video=message.video.file_id,
                    caption=caption, reply_markup=kb
                )
            elif message.animation:
                sent = await safe_send(
                    message.bot, uid,
                    message.bot.send_animation,
                    uid, animation=message.animation.file_id,
                    caption=caption, reply_markup=kb
                )
            elif message.document:
                sent = await safe_send(
                    message.bot, uid,
                    message.bot.send_document,
                    uid, document=message.document.file_id,
                    caption=caption, reply_markup=kb
                )
            else:
                sent = await safe_send(
                    message.bot, uid,
                    message.bot.send_message,
                    uid, caption, reply_markup=kb
                )

            if sent:
                append_bot_msg_ids(uid, sent.message_id)
                success += 1
            else:
                blocked += 1
                failed += 1

        except Exception as e:
            failed += 1
            logger.info(f"Broadcast fail {uid}: {e}")

        await asyncio.sleep(BROADCAST_BASE_DELAY)

        if i % BROADCAST_BATCH == 0:
            await asyncio.sleep(BROADCAST_BATCH_PAUSE)
            try:
                await status.edit_text(
                    f"🛡 Тарату жүріп жатыр...\n"
                    f"✅ {success} | ❌ {failed} | 🚫 {blocked}\n"
                    f"Прогресс: {i}/{total}"
                )
            except Exception:
                pass

    await state.clear()
    await status.edit_text(
        f"✅ Қауіпсіз тарату аяқталды\n\n"
        f"Жеткізілді: {success}\n"
        f"Қате: {failed}\n"
        f"Бұғаттағандар: {blocked}",
        reply_markup=admin_keyboard()
    )


# =========================================================
# АДМИН: БҰҒАТТАУ
# =========================================================
@router.callback_query(F.data == "admin_ban")
async def admin_ban_menu(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    await state.clear()
    banned = get_banned_users()
    lines = [
        "🚫 <b>БҰҒАТТАУ ЖҮЙЕСІ</b>\n",
        "Бұғатталған адамға бот мүлдем жауап бермейді.\n",
    ]
    if banned:
        lines.append(f"<b>Бұғатталғандар ({len(banned)}):</b>")
        for u in banned[:30]:
            uname = f"@{u['username']}" if u.get("username") else "—"
            name = u.get("full_name") or "—"
            when = (u.get("banned_at") or "")[:16].replace("T", " ")
            lines.append(f"• <code>{u['user_id']}</code> | {name} | {uname} | {when}")
    else:
        lines.append("Бұғатталған адам жоқ.")

    b = InlineKeyboardBuilder()
    b.button(text="➕ Пайдаланушыны бұғаттау", callback_data="ban_start")
    for u in banned[:15]:
        label = u.get("username") or u.get("full_name") or str(u["user_id"])
        b.button(text=f"✅ Бұғаттан шығару: {str(label)[:20]}", callback_data=f"unban:{u['user_id']}")
    b.button(text="◀️ Әкімші панелі", callback_data="admin_home")
    b.adjust(1)
    await call.message.edit_text("\n".join(lines)[:4000], reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data == "ban_start")
async def ban_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    await state.set_state(AdminStates.ban_user_id)
    await call.message.edit_text(
        "🚫 Бұғаттайтын пайдаланушының <b>Telegram ID</b>-ін жіберіңіз.\n\n"
        "Мысалы: <code>6303091468</code>\n\n"
        "Болдырмау: /cancel"
    )
    await call.answer()


@router.message(AdminStates.ban_user_id)
async def ban_user_by_id(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Тек сандық ID жіберіңіз (мысалы: 6303091468):")
        return
    target_id = int(raw)
    if is_admin(target_id):
        await message.answer("⚠️ Админді бұғаттауға болмайды.")
        return

    ensure_user(target_id)
    set_banned(target_id, True)

    deleted = 0
    try:
        deleted = await wipe_bot_messages(message.bot, target_id, target_id)
    except Exception as e:
        logger.warning("Ban wipe messages error: %s", e)

    await state.clear()
    user = get_user(target_id)
    name = (user or {}).get("full_name") or "—"
    uname = (user or {}).get("username")
    uname_s = f"@{uname}" if uname else "—"
    await message.answer(
        f"✅ Пайдаланушы бұғатталды.\n\n"
        f"ID: <code>{target_id}</code>\n"
        f"Аты: {name}\n"
        f"Username: {uname_s}\n"
        f"🗑 Өшірілген бот хабарламалары: {deleted}\n\n"
        f"Енді бот оған еш жауап бермейді.",
        reply_markup=admin_keyboard()
    )


@router.callback_query(F.data.startswith("unban:"))
async def unban_user_handler(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    try:
        target_id = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        await call.answer("Қате ID", show_alert=True)
        return
    set_banned(target_id, False)
    await call.answer("✅ Бұғаттан шығарылды", show_alert=True)
    await admin_ban_menu(call, state)


# =========================================================
# АДМИН: ЖАЛПЫ БАПТАУЛАР
# =========================================================
@router.callback_query(F.data == "admin_settings")
async def admin_settings(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    support = setting("support_username", "@JOKWRHUB18")
    minutes = setting("preview_minutes", "2")
    b = InlineKeyboardBuilder()
    b.button(text="✉️ Админ аккаунтын өзгерту", callback_data="change_support")
    b.button(text="⏱ Алдын ала қарау уақытын өзгерту", callback_data="change_preview")
    b.button(text="🇰🇿 Қазақша қарсы алу мәтінін өзгерту", callback_data="welcome_kk")
    b.button(text="🇷🇺 Орысша қарсы алу мәтінін өзгерту", callback_data="welcome_ru")
    b.button(text="◀️ Әкімші панелі", callback_data="admin_home")
    b.adjust(1)
    await call.message.edit_text(
        f"⚙️ ЖАЛПЫ БАПТАУЛАР\n\nАдмин: {support}\nАлдын ала қарау: {minutes} минут",
        reply_markup=b.as_markup()
    )
    await call.answer()


@router.callback_query(F.data == "change_support")
async def change_support(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    await state.set_state(AdminStates.support)
    await call.message.edit_text("Админнің Telegram атын жіберіңіз (мысалы: @username):")
    await call.answer()


@router.message(AdminStates.support)
async def save_support(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    value = (message.text or "").strip()
    if not value.startswith("@"):
        await message.answer("Атау @ белгісінен басталуы керек. Қайта жіберіңіз:")
        return
    set_setting("support_username", value)
    await state.clear()
    await message.answer("✅ Админ аккаунты сақталды.", reply_markup=admin_keyboard())


@router.callback_query(F.data == "change_preview")
async def change_preview(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    await state.update_data(setting_key="preview_minutes")
    await state.set_state(AdminStates.setting_value)
    await call.message.edit_text("Алдын ала қарау уақытын минутпен жазыңыз:")
    await call.answer()


@router.callback_query(F.data.in_({"welcome_kk", "welcome_ru"}))
async def change_welcome(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Рұқсат жоқ!", show_alert=True)
        return
    key = "welcome_kk" if call.data == "welcome_kk" else "welcome_ru"
    await state.update_data(setting_key=key)
    await state.set_state(AdminStates.setting_value)
    await call.message.edit_text(
        "Жаңа қарсы алу мәтінін жіберіңіз:" if key == "welcome_kk" else "Новое приветственное сообщение отправьте:"
    )
    await call.answer()


@router.message(AdminStates.setting_value)
async def save_setting_value(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    key = data.get("setting_key")
    value = (message.text or "").strip()
    if key == "preview_minutes":
        try:
            if int(value) < 1:
                raise ValueError
        except ValueError:
            await message.answer("1 немесе одан үлкен минут санын жазыңыз:")
            return
    if key:
        set_setting(key, value)
    await state.clear()
    await message.answer("✅ Баптау сақталды.", reply_markup=admin_keyboard())


# =========================================================
# БОТТЫ ІСКЕ ҚОСУ
# =========================================================
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN табылмады.")
    if not ADMIN_IDS:
        raise RuntimeError("ADMIN_IDS табылмады.")

    logger.info("DB_PATH = %s", DB_PATH)
    init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Қорғаныс қабаттары
    dp.message.middleware(BanMiddleware())
    dp.callback_query.middleware(BanMiddleware())
    dp.message.middleware(SecurityMiddleware())
    dp.callback_query.middleware(SecurityMiddleware())

    dp.include_router(router)

    logger.info("🛡 Security layers loaded. Бот іске қосылды.")
    try:
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот тоқтатылды.")
