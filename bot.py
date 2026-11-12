import asyncio
import logging
import os
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# =========================================================
# CONFIG
# =========================================================

# .env файлын автомат оқу (қосымша кітапхана қажет емес).
# Хостинг панелінде орнатылған environment variables басым болады.
def _load_local_env_file():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as exc:
        logging.warning("Could not read .env file: %s", exc)


_load_local_env_file()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_USERNAME_ENV = os.getenv("ADMIN_USERNAME", "")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_menu_banner_path():
    env = os.getenv("MENU_BANNER_PATH", "").strip()
    candidates = []
    if env:
        candidates.append(env)
    for name in (
        "menu_banner.gif",
        "menu_banner.jpg",
        "menu_banner.jpeg",
        "menu_banner.png",
        "menu_banner_anim.gif",
    ):
        candidates.append(os.path.join(_BASE_DIR, name))
        candidates.append(os.path.join(os.getcwd(), name))
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


MENU_BANNER_PATH = _resolve_menu_banner_path()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable not found. Set BOT_TOKEN in your environment.")

if not ADMIN_ID_RAW:
    raise RuntimeError("ADMIN_ID environment variable not found")

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    raise RuntimeError("ADMIN_ID must be a number")


DB_NAME = "bot_database.db"

REFERRAL_BONUS = 50
DAILY_BONUS = 12
BONUS_INTERVAL_HOURS = 12
DEFAULT_VIDEO_COST = 2
UPLOAD_BONUS = 12

UPLOAD_LIMIT = 15
UPLOAD_COOLDOWN_HOURS = 12

COIN_PRICE_NORMAL = 30
COIN_PRICE_DISCOUNT = 20
DISCOUNT_FROM = 150

# Контент автоматты өшіру (минут)
CONTENT_AUTO_DELETE_MINUTES = 30

# Рассылка қауіпсіздігі: бір хабарлама арасындағы күту (сек)
BROADCAST_DELAY = 1.0
BROADCAST_BATCH_PAUSE = 5.0
BROADCAST_BATCH_SIZE = 15

KZ_TZ = ZoneInfo("Asia/Almaty")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

router = Router()

# Жоспарланған өшіру тапсырмалары (message_id -> task)
_delete_tasks: dict[tuple[int, int], asyncio.Task] = {}


# =========================================================
# TIME
# =========================================================

def kz_now():
    return datetime.now(KZ_TZ)


def kz_date():
    return kz_now().date().isoformat()


# =========================================================
# FSM
# =========================================================

class UserUploadState(StatesGroup):
    waiting_genre = State()
    waiting_file = State()


class BroadcastState(StatesGroup):
    waiting_message = State()


class GiveCoinState(StatesGroup):
    waiting_data = State()


class GlobalCoinState(StatesGroup):
    waiting_amount = State()


class AdminContentState(StatesGroup):
    waiting_genre = State()
    waiting_file = State()


class TaskState(StatesGroup):
    waiting_name = State()
    waiting_channel = State()
    waiting_reward = State()
    waiting_link = State()
    waiting_photo = State()
    edit_choose = State()
    edit_name = State()
    edit_channel = State()
    edit_reward = State()
    edit_link = State()
    edit_photo = State()


class ChannelState(StatesGroup):
    waiting_channel = State()


class GenreState(StatesGroup):
    waiting_name = State()
    waiting_price = State()


class ReferralSettingsState(StatesGroup):
    waiting_bonus = State()


class ShopBuyState(StatesGroup):
    waiting_amount = State()


class ReferralCaptchaState(StatesGroup):
    waiting_answer = State()


class SettingsState(StatesGroup):
    waiting_value = State()


class BannerState(StatesGroup):
    waiting_media = State()
    waiting_text = State()


class BanUserState(StatesGroup):
    waiting_id = State()


class UnbanUserState(StatesGroup):
    waiting_id = State()


# =========================================================
# DATABASE HELPERS
# =========================================================

async def db_execute(query, params=(), fetch=False, fetchone=False):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        if fetchone:
            result = await cursor.fetchone()
        elif fetch:
            result = await cursor.fetchall()
        else:
            result = None
        await db.commit()
        return result


async def table_columns(db, table_name):
    cursor = await db.execute(f"PRAGMA table_info({table_name})")
    rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def add_missing_columns(db, table_name, columns):
    existing = await table_columns(db, table_name)
    for column_name, definition in columns.items():
        if column_name not in existing:
            logging.info("Migration: adding %s.%s", table_name, column_name)
            await db.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
            )


# =========================================================
# INIT DATABASE
# =========================================================

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER DEFAULT 10,
                invited_by INTEGER,
                referral_count INTEGER DEFAULT 0,
                last_bonus TEXT,
                is_blocked INTEGER DEFAULT 0,
                created_at TEXT,
                upload_count INTEGER DEFAULT 0,
                upload_date TEXT,
                upload_cooldown_until TEXT,
                last_menu_msg_id INTEGER
            )
        """)

        await add_missing_columns(db, "users", {
            "username": "TEXT",
            "first_name": "TEXT",
            "balance": "INTEGER DEFAULT 10",
            "invited_by": "INTEGER",
            "referral_count": "INTEGER DEFAULT 0",
            "last_bonus": "TEXT",
            "is_blocked": "INTEGER DEFAULT 0",
            "created_at": "TEXT",
            "upload_count": "INTEGER DEFAULT 0",
            "upload_date": "TEXT",
            "upload_cooldown_until": "TEXT",
            "last_menu_msg_id": "INTEGER",
        })

        await db.execute("""
            CREATE TABLE IF NOT EXISTS genres (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                price INTEGER DEFAULT 2,
                active INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)

        await add_missing_columns(db, "genres", {
            "name": "TEXT",
            "price": "INTEGER DEFAULT 2",
            "active": "INTEGER DEFAULT 1",
            "created_at": "TEXT",
        })

        await db.execute("""
            CREATE TABLE IF NOT EXISTS content (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_type TEXT,
                file_id TEXT,
                file_unique_id TEXT,
                genre_id INTEGER,
                added_by INTEGER,
                created_at TEXT
            )
        """)

        await add_missing_columns(db, "content", {
            "content_type": "TEXT",
            "file_id": "TEXT",
            "file_unique_id": "TEXT",
            "genre_id": "INTEGER",
            "added_by": "INTEGER",
            "created_at": "TEXT",
        })

        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uploader_id INTEGER,
                content_type TEXT,
                file_id TEXT,
                file_unique_id TEXT,
                genre_id INTEGER,
                created_at TEXT,
                submitted INTEGER DEFAULT 0
            )
        """)

        await add_missing_columns(db, "pending", {
            "uploader_id": "INTEGER",
            "content_type": "TEXT",
            "file_id": "TEXT",
            "file_unique_id": "TEXT",
            "genre_id": "INTEGER",
            "created_at": "TEXT",
            "submitted": "INTEGER DEFAULT 0",
        })

        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pending_id INTEGER,
                uploader_id INTEGER,
                content_type TEXT,
                file_unique_id TEXT,
                genre_id INTEGER,
                status TEXT,
                processed_by INTEGER,
                created_at TEXT,
                processed_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS viewed (
                user_id INTEGER,
                content_id INTEGER,
                viewed_at TEXT,
                PRIMARY KEY(user_id, content_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS content_reactions (
                user_id INTEGER,
                content_id INTEGER,
                reaction TEXT,
                created_at TEXT,
                PRIMARY KEY(user_id, content_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                invited_user_id INTEGER PRIMARY KEY,
                inviter_id INTEGER,
                created_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                channel TEXT,
                reward INTEGER DEFAULT 15,
                active INTEGER DEFAULT 1,
                created_at TEXT,
                link TEXT,
                photo_file_id TEXT,
                photo_unique_id TEXT
            )
        """)

        await add_missing_columns(db, "tasks", {
            "name": "TEXT",
            "channel": "TEXT",
            "reward": "INTEGER DEFAULT 15",
            "active": "INTEGER DEFAULT 1",
            "created_at": "TEXT",
            "link": "TEXT",
            "photo_file_id": "TEXT",
            "photo_unique_id": "TEXT",
        })

        await db.execute("""
            CREATE TABLE IF NOT EXISTS task_completions (
                user_id INTEGER,
                task_id INTEGER,
                completed_at TEXT,
                PRIMARY KEY(user_id, task_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT UNIQUE,
                active INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS referral_verifications (
                user_id INTEGER PRIMARY KEY, inviter_id INTEGER NOT NULL,
                a INTEGER NOT NULL, b INTEGER NOT NULL, answer INTEGER NOT NULL, created_at TEXT
            )
        """)

        defaults = {
            "referral_bonus": str(REFERRAL_BONUS),
            "admin_username": ADMIN_USERNAME_ENV or "",
            "shop_contact": ADMIN_USERNAME_ENV or "@admin",
            "daily_bonus": str(DAILY_BONUS),
            "upload_bonus": str(UPLOAD_BONUS),
            "coin_price_normal": str(COIN_PRICE_NORMAL),
            "coin_price_discount": str(COIN_PRICE_DISCOUNT),
            "discount_from": str(DISCOUNT_FROM),
        }

        for key, value in defaults.items():
            cursor = await db.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            )
            row = await cursor.fetchone()
            if not row:
                await db.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)",
                    (key, value)
                )

        cursor = await db.execute("SELECT COUNT(*) FROM genres")
        row = await cursor.fetchone()
        if row[0] == 0:
            await db.execute(
                "INSERT INTO genres (name, price, active, created_at) VALUES (?, ?, 1, ?)",
                ("Негізгі", DEFAULT_VIDEO_COST, kz_now().isoformat())
            )

        await db.execute(
            "UPDATE users SET created_at=? WHERE created_at IS NULL",
            (kz_now().isoformat(),)
        )

        await db.commit()

    logging.info("Database initialized successfully.")


# =========================================================
# SETTINGS
# =========================================================

async def get_setting(key: str, default: str = None):
    row = await db_execute(
        "SELECT value FROM settings WHERE key=?",
        (key,),
        fetchone=True
    )
    if row:
        return row["value"]
    return default


async def set_setting(key: str, value: str):
    await db_execute(
        """
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, value)
    )


async def get_int_setting(key, default):
    try:
        return max(0, int(await get_setting(key, str(default))))
    except (TypeError, ValueError):
        return default


async def get_shop_prices():
    return (await get_int_setting("coin_price_normal", COIN_PRICE_NORMAL),
            await get_int_setting("coin_price_discount", COIN_PRICE_DISCOUNT),
            await get_int_setting("discount_from", DISCOUNT_FROM))


async def get_referral_bonus():
    value = await get_setting("referral_bonus", str(REFERRAL_BONUS))
    try:
        return int(value)
    except (TypeError, ValueError):
        return REFERRAL_BONUS


async def get_shop_contact():
    value = await get_setting("shop_contact", ADMIN_USERNAME_ENV or "@admin")
    if value and not value.startswith("@"):
        value = "@" + value
    return value or "@admin"


# =========================================================
# USER
# =========================================================

async def get_user(user_id):
    return await db_execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,),
        fetchone=True
    )


async def is_user_blocked(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return False
    user = await get_user(user_id)
    if not user:
        return False
    try:
        return bool(int(user["is_blocked"] or 0))
    except (TypeError, ValueError, KeyError):
        return False


async def register_user(user, invited_by=None, bot: Bot = None):
    existing = await get_user(user.id)

    if existing:
        await db_execute(
            "UPDATE users SET username=?, first_name=? WHERE user_id=?",
            (user.username, user.first_name, user.id)
        )
        return False

    now = kz_now().isoformat()

    real_inviter = None
    if invited_by and invited_by != user.id:
        inviter = await get_user(invited_by)
        if inviter:
            real_inviter = invited_by

    await db_execute(
        """
        INSERT INTO users (
            user_id, username, first_name, balance, invited_by,
            referral_count, last_bonus, is_blocked, created_at,
            upload_count, upload_date, upload_cooldown_until
        )
        VALUES (?, ?, ?, 10, ?, 0, NULL, 0, ?, 0, ?, NULL)
        """,
        (user.id, user.username, user.first_name, real_inviter, now, kz_date())
    )

    a = random.randint(1, 9)
    b = random.randint(1, 9)
    await db_execute(
        "INSERT OR REPLACE INTO referral_verifications(user_id, inviter_id, a, b, answer, created_at) VALUES(?,?,?,?,?,?)",
        (user.id, real_inviter or 0, a, b, a + b, now)
    )

    return True


async def add_balance(user_id, amount):
    await db_execute(
        "UPDATE users SET balance=balance+? WHERE user_id=?",
        (amount, user_id)
    )


async def get_balance(user_id):
    user = await get_user(user_id)
    if not user:
        return 0
    return user["balance"] or 0


async def try_give_auto_bonus(bot: Bot, user_id: int) -> bool:
    """12 сағат сайын автоматты бонус береді. Берілсе True."""
    if user_id == ADMIN_ID:
        return False
    if await is_user_blocked(user_id):
        return False

    user = await get_user(user_id)
    if not user:
        return False

    now = kz_now()
    bonus = await get_int_setting("daily_bonus", DAILY_BONUS)
    interval = BONUS_INTERVAL_HOURS

    if user["last_bonus"]:
        try:
            last = datetime.fromisoformat(user["last_bonus"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=KZ_TZ)
            if (now - last).total_seconds() < interval * 3600:
                return False
        except Exception:
            pass

    await db_execute(
        "UPDATE users SET balance=balance+?, last_bonus=? WHERE user_id=?",
        (bonus, now.isoformat(), user_id)
    )

    try:
        await bot.send_message(
            user_id,
            f"🌑 <b>Қараңғы Дарвин Теориясы</b> сізге <b>+{bonus}</b> 💰 бонус берді!\n\n"
            f"⏳ Келесі бонус <b>{interval} сағаттан</b> кейін келеді.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    return True


# =========================================================
# CONTENT AUTO-DELETE + PROTECT
# =========================================================

async def schedule_message_delete(bot: Bot, chat_id: int, message_id: int, delay_minutes: int = CONTENT_AUTO_DELETE_MINUTES):
    """Контент хабарламасын N минуттан кейін автоматты өшіру."""
    key = (chat_id, message_id)

    old = _delete_tasks.pop(key, None)
    if old and not old.done():
        old.cancel()

    async def _delete_later():
        try:
            await asyncio.sleep(delay_minutes * 60)
            try:
                await bot.delete_message(chat_id, message_id)
                logging.info("Auto-deleted content msg chat=%s msg=%s", chat_id, message_id)
            except Exception as e:
                logging.debug("Auto-delete failed chat=%s msg=%s: %s", chat_id, message_id, e)
        except asyncio.CancelledError:
            pass
        finally:
            _delete_tasks.pop(key, None)

    task = asyncio.create_task(_delete_later())
    _delete_tasks[key] = task


# =========================================================
# GENRES
# =========================================================

async def get_genres(active_only=True):
    if active_only:
        return await db_execute(
            "SELECT * FROM genres WHERE active=1 ORDER BY id ASC",
            fetch=True
        )
    return await db_execute(
        "SELECT * FROM genres ORDER BY id ASC",
        fetch=True
    )


async def get_genre(genre_id):
    return await db_execute(
        "SELECT * FROM genres WHERE id=?",
        (genre_id,),
        fetchone=True
    )


# =========================================================
# CHANNEL SUBSCRIPTION
# =========================================================

def normalize_channel(channel):
    channel = (channel or "").strip()
    if not channel:
        return ""
    if channel.startswith("https://t.me/"):
        channel = channel.split("https://t.me/")[-1].strip("/")
    elif channel.startswith("http://t.me/"):
        channel = channel.split("http://t.me/")[-1].strip("/")
    elif channel.startswith("t.me/"):
        channel = channel.split("t.me/")[-1].strip("/")

    if channel.startswith("+") or channel.startswith("joinchat/"):
        return channel

    if channel.startswith("-100") or channel.lstrip("-").isdigit():
        return channel

    if not channel.startswith("@"):
        channel = "@" + channel
    return channel


def channel_url(channel):
    raw = (channel or "").strip()
    target = normalize_channel(raw)
    if not target:
        return None
    if target.startswith("@"):
        return f"https://t.me/{target[1:]}"
    if target.startswith("-100") or target.lstrip("-").isdigit():
        return None
    if target.startswith("+"):
        return f"https://t.me/{target}"
    if target.startswith("joinchat/"):
        return f"https://t.me/{target}"
    return f"https://t.me/{target.lstrip('@')}"


async def get_required_channels():
    return await db_execute(
        "SELECT * FROM channels WHERE active=1 ORDER BY id ASC",
        fetch=True
    )


async def check_user_subscriptions(bot: Bot, user_id: int):
    channels = await get_required_channels()
    missing = []
    for channel in channels:
        target = normalize_channel(channel["channel"])
        if not target:
            continue
        if target.startswith("+") or target.startswith("joinchat/"):
            logging.warning(
                "Channel %s is invite-link; store @username or -100... ID instead",
                target
            )
            missing.append(channel)
            continue

        try:
            member = await bot.get_chat_member(target, user_id)
            status = member.status
            status_val = getattr(status, "value", status)
            if isinstance(status_val, str):
                status_val = status_val.lower()

            ok_statuses = {
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.CREATOR,
                "member",
                "administrator",
                "creator",
                "restricted",
            }
            if hasattr(ChatMemberStatus, "RESTRICTED"):
                ok_statuses.add(ChatMemberStatus.RESTRICTED)

            if status not in ok_statuses and status_val not in ok_statuses:
                logging.info(
                    "User %s not subscribed to %s (status=%s)",
                    user_id, target, status
                )
                missing.append(channel)
        except Exception as e:
            logging.warning(
                "Subscription check FAILED for channel=%s user=%s error=%s",
                target, user_id, e
            )
            missing.append(channel)
    return missing


def subscription_keyboard(channels):
    builder = InlineKeyboardBuilder()
    for channel in channels:
        url = channel_url(channel["channel"])
        if url:
            builder.row(
                InlineKeyboardButton(
                    text=f"📢 {channel['channel']} — Қосылу",
                    url=url
                )
            )
    builder.row(
        InlineKeyboardButton(
            text="✅ Жазылдым / Тексеру",
            callback_data="check_subscription"
        )
    )
    return builder.as_markup()


async def require_subscription(message_or_callback, bot: Bot, user_id: int):
    if await is_user_blocked(user_id):
        return False

    missing = await check_user_subscriptions(bot, user_id)
    if not missing:
        return True

    text = (
        "🔒 Ботты пайдалану үшін алдымен міндетті каналдарға жазылыңыз.\n\n"
        "Төмендегі каналдарға қосылып, «✅ Жазылдым / Тексеру» батырмасын басыңыз."
    )
    keyboard = subscription_keyboard(missing)

    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.answer(text, reply_markup=keyboard)
        await message_or_callback.answer()
    else:
        await message_or_callback.answer(text, reply_markup=keyboard)
    return False


# =========================================================
# INLINE KEYBOARDS — ЫҢҒАЙЛАНҒАН ПАЙДАЛАНУШЫ МӘЗІРІ
# =========================================================

def user_inline_keyboard():
    """Гибрид мәзір: негізгі әрекеттер толық ені, қалғаны екі қатар."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎥 Видео көру", callback_data="menu_watch_video")],
            [InlineKeyboardButton(text="➕ Контент жіберу", callback_data="menu_upload")],
            [
                InlineKeyboardButton(text="💰 Баланс / Бонус", callback_data="menu_balance_bonus"),
                InlineKeyboardButton(text="👥 Реферал", callback_data="menu_referrals"),
            ],
            [
                InlineKeyboardButton(text="🎯 Тапсырмалар", callback_data="menu_tasks"),
                InlineKeyboardButton(text="🛒 Дүкен", callback_data="menu_shop"),
            ],
            [InlineKeyboardButton(text="ℹ️ Ақпарат", callback_data="menu_info")],
        ]
    )


def back_to_main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 Басты мәзір", callback_data="menu_main")]]
    )


async def show_screen(callback: CallbackQuery, text: str, reply_markup=None, parse_mode="HTML"):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)


MAIN_MENU_CAPTION_DEFAULT = (
    "🃏 <b>JokerQZ</b> — Қазақша әлем\n\n"
    "🏠 <b>Негізгі мәзір</b>\n"
    "Төмендегі батырмаларды пайдаланыңыз."
)


async def get_menu_caption() -> str:
    value = await get_setting("menu_banner_caption", "")
    if value and value.strip():
        return value.strip()
    return MAIN_MENU_CAPTION_DEFAULT


async def _delete_old_menu(bot: Bot, chat_id: int):
    try:
        row = await db_execute(
            "SELECT last_menu_msg_id FROM users WHERE user_id=?",
            (chat_id,),
            fetchone=True
        )
        if row and row["last_menu_msg_id"]:
            try:
                await bot.delete_message(chat_id, int(row["last_menu_msg_id"]))
            except Exception:
                pass
            await db_execute(
                "UPDATE users SET last_menu_msg_id=NULL WHERE user_id=?",
                (chat_id,)
            )
    except Exception as e:
        logging.warning("delete old menu failed: %s", e)


async def _save_menu_msg(chat_id: int, message_id: int):
    try:
        await db_execute(
            "UPDATE users SET last_menu_msg_id=? WHERE user_id=?",
            (message_id, chat_id)
        )
    except Exception as e:
        logging.warning("save menu msg id failed: %s", e)


async def send_main_menu(bot: Bot, chat_id: int, caption: str = None):
    if caption is None:
        text = await get_menu_caption()
    else:
        text = caption
    keyboard = user_inline_keyboard()

    await _delete_old_menu(bot, chat_id)

    # Автоматты 12-сағаттық бонус
    try:
        await try_give_auto_bonus(bot, chat_id)
    except Exception:
        pass

    msg = None
    file_id = await get_setting("menu_banner_file_id", "")
    file_type = await get_setting("menu_banner_type", "photo")

    try:
        if file_id:
            if file_type == "animation":
                msg = await bot.send_animation(
                    chat_id, file_id, caption=text,
                    reply_markup=keyboard, parse_mode="HTML"
                )
            else:
                msg = await bot.send_photo(
                    chat_id, file_id, caption=text,
                    reply_markup=keyboard, parse_mode="HTML"
                )
            if msg:
                await _save_menu_msg(chat_id, msg.message_id)
            return
    except Exception as e:
        logging.warning("menu banner file_id failed: %s", e)
        await set_setting("menu_banner_file_id", "")

    path = _resolve_menu_banner_path()
    if path:
        logging.info("Using menu banner: %s", path)
        ext = os.path.splitext(path)[1].lower()
        if ext == ".gif":
            try:
                msg = await bot.send_animation(
                    chat_id, FSInputFile(path), caption=text,
                    reply_markup=keyboard, parse_mode="HTML"
                )
                if msg and msg.animation:
                    await set_setting("menu_banner_file_id", msg.animation.file_id)
                    await set_setting("menu_banner_type", "animation")
                    await _save_menu_msg(chat_id, msg.message_id)
                    return
            except Exception as e:
                logging.warning("send_animation failed, trying photo: %s", e)
        try:
            msg = await bot.send_photo(
                chat_id, FSInputFile(path), caption=text,
                reply_markup=keyboard, parse_mode="HTML"
            )
            if msg and msg.photo:
                await set_setting("menu_banner_file_id", msg.photo[-1].file_id)
                await set_setting("menu_banner_type", "photo")
                await _save_menu_msg(chat_id, msg.message_id)
                return
        except Exception as e:
            logging.error("menu banner send failed: %s", e)

    msg = await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
    if msg:
        await _save_menu_msg(chat_id, msg.message_id)


def admin_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="➕ Контент қосу"),
                KeyboardButton(text="📩 Жіберілгендер")
            ],
            [
                KeyboardButton(text="💰 Монета беру"),
                KeyboardButton(text="🌍 Барлығына монета")
            ],
            [
                KeyboardButton(text="📢 Рассылка"),
                KeyboardButton(text="📊 Статистика")
            ],
            [
                KeyboardButton(text="🎯 Тапсырмаларды басқару"),
                KeyboardButton(text="📢 Каналдарды басқару")
            ],
            [
                KeyboardButton(text="🎭 Жанрларды басқару"),
                KeyboardButton(text="👥 Реферал баптау")
            ],
            [
                KeyboardButton(text="🚫 Пайдаланушыны бұғаттау"),
                KeyboardButton(text="✅ Бұғаттан шығару")
            ],
            [
                KeyboardButton(text="🖼 Мәзір баннері"),
                KeyboardButton(text="⚙️ Баптаулар")
            ],
            [
                KeyboardButton(text="🔙 Қолданушы панелі")
            ]
        ],
        resize_keyboard=True
    )


def admin_only(user_id: int) -> bool:
    return user_id == ADMIN_ID


def admin_cancel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="❌ Болдырмау", callback_data="admin_cancel")
        ]]
    )


# =========================================================
# BLOCKED USER MIDDLEWARE-LIKE CHECK
# =========================================================

async def guard_blocked(event_user_id: int, answer_fn=None) -> bool:
    """True = блокталған, жауап бермеу керек."""
    if await is_user_blocked(event_user_id):
        if answer_fn:
            try:
                await answer_fn()
            except Exception:
                pass
        return True
    return False


# =========================================================
# START & MAIN MENU
# =========================================================

@router.message(CommandStart())
async def start_handler(message: Message, bot: Bot, state: FSMContext):
    if await is_user_blocked(message.from_user.id):
        return

    args = message.text.split(maxsplit=1)
    invited_by = None
    if len(args) > 1:
        try:
            invited_by = int(args[1])
        except ValueError:
            invited_by = None

    await register_user(message.from_user, invited_by, bot=bot)

    if message.from_user.id == ADMIN_ID:
        await state.clear()
        await db_execute("DELETE FROM referral_verifications WHERE user_id=?", (message.from_user.id,))
        await message.answer(
            "👑 Админ панеліне қош келдіңіз!",
            reply_markup=admin_keyboard()
        )
        return

    verification = await db_execute(
        "SELECT * FROM referral_verifications WHERE user_id=?",
        (message.from_user.id,),
        fetchone=True
    )
    if verification:
        await state.set_state(ReferralCaptchaState.waiting_answer)
        await message.answer(
            "🤖 <b>Адам екеніңізді растаңыз</b>\n\n"
            f"Есеп: <b>{verification['a']} + {verification['b']} = ?</b>\n\n"
            "Жауапты санмен жазыңыз.",
            parse_mode="HTML"
        )
        return

    await state.clear()

    subscribed = await require_subscription(message, bot, message.from_user.id)
    if not subscribed:
        return

    await send_main_menu(bot, message.chat.id)


@router.callback_query(F.data == "menu_main")
async def menu_main_callback(callback: CallbackQuery, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    if callback.from_user.id != ADMIN_ID:
        if not await require_subscription(callback, bot, callback.from_user.id):
            return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await send_main_menu(bot, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == "menu_info")
async def menu_info_callback(callback: CallbackQuery):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    await show_screen(
        callback,
        "ℹ️ <b>Ақпарат</b>\n\n"
        "Бұл бот арқылы сіз видеолар көріп, монета жинай аласыз, "
        "өз контенттеріңізді жүктеп сыйақы ала аласыз және тапсырмаларды "
        "орындап монета таба аласыз.\n\n"
        "⚠️ Боттан келген видео/фото <b>30 минуттан кейін</b> автоматты өшеді "
        "және басқаларға жіберуге болмайды.",
        back_to_main_keyboard()
    )
    await callback.answer()


# =========================================================
# CAPTCHA
# =========================================================

@router.message(ReferralCaptchaState.waiting_answer, F.text)
async def referral_captcha_answer(message: Message, state: FSMContext, bot: Bot):
    if await is_user_blocked(message.from_user.id):
        return

    row = await db_execute(
        "SELECT * FROM referral_verifications WHERE user_id=?",
        (message.from_user.id,),
        fetchone=True
    )
    if not row:
        await state.clear()
        await message.answer("⚠️ Капча мерзімі өтті. /start басыңыз.")
        return

    try:
        answer = int(message.text.strip().replace(" ", ""))
    except ValueError:
        await message.answer(
            f"❗ Тек сан енгізіңіз.\n"
            f"Есеп: <b>{row['a']} + {row['b']} = ?</b>",
            parse_mode="HTML"
        )
        return

    if answer != int(row["answer"]):
        await message.answer(
            f"❌ Қате. Қайта есептеңіз: <b>{row['a']} + {row['b']} = ?</b>",
            parse_mode="HTML"
        )
        return

    await state.clear()
    await db_execute("DELETE FROM referral_verifications WHERE user_id=?", (message.from_user.id,))

    inviter_id = row["inviter_id"]
    if inviter_id and int(inviter_id) != 0 and int(inviter_id) != message.from_user.id:
        inviter = await get_user(inviter_id)
        if inviter and not await is_user_blocked(inviter_id):
            try:
                await db_execute(
                    "INSERT INTO referrals(invited_user_id, inviter_id, created_at) VALUES(?,?,?)",
                    (message.from_user.id, inviter_id, kz_now().isoformat())
                )
                bonus = await get_referral_bonus()
                await db_execute(
                    "UPDATE users SET balance=balance+?, referral_count=referral_count+1 WHERE user_id=?",
                    (bonus, inviter_id)
                )
                try:
                    await bot.send_message(
                        inviter_id,
                        f"🎉 Жаңа реферал!\n👤 {message.from_user.first_name or message.from_user.id}\n💰 +{bonus} монета берілді!"
                    )
                except Exception:
                    pass
            except aiosqlite.IntegrityError:
                pass

    await message.answer("✅ Адам екеніңіз расталды!")

    if message.from_user.id != ADMIN_ID:
        if not await require_subscription(message, bot, message.from_user.id):
            return

    await send_main_menu(bot, message.chat.id)


@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    if callback.from_user.id == ADMIN_ID:
        await callback.answer("👑 Сіз админсіз.", show_alert=True)
        return

    missing = await check_user_subscriptions(bot, callback.from_user.id)
    if missing:
        names = []
        for ch in missing:
            names.append(normalize_channel(ch["channel"]) or ch["channel"])
        detail = ", ".join(names[:5])
        await callback.answer(
            f"❌ Әлі жазылмадыңыз немесе бот тексере алмады:\n{detail}\n\n"
            "Каналға қайта кіріп, бірнеше секунд күтіп басыңыз.",
            show_alert=True
        )
        try:
            await callback.message.edit_reply_markup(
                reply_markup=subscription_keyboard(missing)
            )
        except Exception:
            pass
        return

    try:
        await callback.message.delete()
    except Exception:
        pass
    await bot.send_message(callback.from_user.id, "✅ Жазылым тексерілді!")
    await send_main_menu(bot, callback.from_user.id)
    await callback.answer()


# =========================================================
# BALANCE + AUTO BONUS (12 сағат сайын)
# =========================================================

@router.callback_query(F.data == "menu_balance_bonus")
async def balance_bonus_callback(callback: CallbackQuery, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    await register_user(callback.from_user, bot=bot)

    if callback.from_user.id != ADMIN_ID:
        if not await require_subscription(callback, bot, callback.from_user.id):
            return

    # Автоматты бонус тексеру
    await try_give_auto_bonus(bot, callback.from_user.id)

    balance = await get_balance(callback.from_user.id)
    user = await get_user(callback.from_user.id)
    now = kz_now()
    bonus = await get_int_setting("daily_bonus", DAILY_BONUS)

    remaining_text = ""
    if user and user["last_bonus"]:
        try:
            last = datetime.fromisoformat(user["last_bonus"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=KZ_TZ)
            remaining = timedelta(hours=BONUS_INTERVAL_HOURS) - (now - last)
            if remaining.total_seconds() > 0:
                hours = int(remaining.total_seconds() // 3600)
                minutes = int((remaining.total_seconds() % 3600) // 60)
                remaining_text = f"\n⏳ Келесі бонус: <b>{hours} сағ. {minutes} мин.</b>"
            else:
                remaining_text = "\n✅ Бонус жақында беріледі"
        except Exception:
            pass

    await show_screen(
        callback,
        f"💳 <b>Баланс / Бонус</b>\n\n"
        f"💰 Сіздің балансыңыз: <b>{balance}</b>\n"
        f"🌑 Авто-бонус: <b>+{bonus}</b> / {BONUS_INTERVAL_HOURS} сағ.{remaining_text}",
        back_to_main_keyboard()
    )
    await callback.answer()


# =========================================================
# REFERRALS
# =========================================================

@router.callback_query(F.data == "menu_referrals")
async def referral_handler_callback(callback: CallbackQuery, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    await register_user(callback.from_user, bot=bot)

    if callback.from_user.id != ADMIN_ID:
        if not await require_subscription(callback, bot, callback.from_user.id):
            return

    me = await bot.get_me()
    user = await get_user(callback.from_user.id)
    link = f"https://t.me/{me.username}?start={callback.from_user.id}"
    count = user["referral_count"] or 0
    bonus = await get_referral_bonus()

    await show_screen(
        callback,
        "👥 <b>Реферал жүйесі</b>\n\n"
        f"💰 Бір адам шақырсаңыз: <b>+{bonus}</b> 💰\n"
        f"👤 Сіз шақырғандар: <b>{count}</b>\n\n"
        "🔗 Сіздің сілтемеңіз:\n"
        f"<code>{link}</code>",
        back_to_main_keyboard()
    )
    await callback.answer()


# =========================================================
# SHOP
# =========================================================

@router.callback_query(F.data == "menu_shop")
async def shop_start_callback(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    await register_user(callback.from_user, bot=bot)

    if callback.from_user.id != ADMIN_ID:
        if not await require_subscription(callback, bot, callback.from_user.id):
            return

    await state.set_state(ShopBuyState.waiting_amount)

    price_n, price_d, disc_from = await get_shop_prices()
    await show_screen(
        callback,
        "🛒 <b>МОНЕТА ДҮКЕНІ</b>\n\n"
        f"• 1 монета = <b>{price_n} ₸</b>\n"
        f"• {disc_from}+ монета → <b>{price_d} ₸</b> (скидка)\n\n"
        "Қанша монета керек?\n"
        "Санын жазыңыз (мысалы: <code>50</code> немесе <code>200</code>)",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ Болдырмау", callback_data="shop_cancel")]
            ]
        )
    )
    await callback.answer()


@router.callback_query(F.data == "shop_cancel")
async def shop_cancel(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await send_main_menu(bot, callback.from_user.id)
    await callback.answer()


@router.message(ShopBuyState.waiting_amount, F.text)
async def shop_amount_handler(message: Message, state: FSMContext, bot: Bot):
    if await is_user_blocked(message.from_user.id):
        return

    text = message.text.strip().replace(" ", "")
    try:
        coins = int(text)
        if coins <= 0 or coins > 100000:
            raise ValueError
    except ValueError:
        await message.answer(
            "❗ Дұрыс сан енгізіңіз.\n"
            "Мысалы: <code>50</code> немесе <code>200</code>",
            parse_mode="HTML"
        )
        return

    price_n, price_d, disc_from = await get_shop_prices()
    if coins >= disc_from:
        price_per = price_d
        total = coins * price_per
        discount_note = (
            f"\n🎉 <b>Скидка қолданылды!</b>\n"
            f"{disc_from}+ монетадан бастап 1 монета = {price_d} ₸"
        )
    else:
        price_per = price_n
        total = coins * price_per
        discount_note = ""

    contact = await get_shop_contact()
    await state.clear()

    await message.answer(
        "🛒 <b>ЕСЕПТЕУ</b>\n\n"
        f"🪙 Монета саны: <b>{coins}</b>\n"
        f"💵 1 монета: <b>{price_per} ₸</b>\n"
        f"💰 Жалпы сома: <b>{total:,} ₸</b>"
        f"{discount_note}\n\n"
        "━━━━━━━━━━━━━━\n"
        f"Төлем жасау үшін осы адамға жазыңыз:\n"
        f"👉 <b>{contact}</b>\n\n"
        "Оған монета санын және төлемді растаңыз.\n"
        "Админ тексергеннен кейін монетаңызды қосады.",
        parse_mode="HTML"
    )
    await send_main_menu(bot, message.chat.id)


# =========================================================
# UPLOAD LIMIT / COOLDOWN
# =========================================================

async def get_upload_status(user_id):
    user = await get_user(user_id)
    if not user:
        return {"count": 0, "cooldown_until": None}

    cooldown = user["upload_cooldown_until"]
    if cooldown:
        try:
            cooldown_dt = datetime.fromisoformat(cooldown)
            if cooldown_dt.tzinfo is None:
                cooldown_dt = cooldown_dt.replace(tzinfo=KZ_TZ)
            if cooldown_dt > kz_now():
                return {"count": user["upload_count"] or 0, "cooldown_until": cooldown_dt}

            await db_execute(
                "UPDATE users SET upload_count=0, upload_date=?, upload_cooldown_until=NULL WHERE user_id=?",
                (kz_date(), user_id)
            )
            return {"count": 0, "cooldown_until": None}
        except Exception:
            pass

    if user["upload_date"] != kz_date():
        await db_execute(
            "UPDATE users SET upload_count=0, upload_date=? WHERE user_id=?",
            (kz_date(), user_id)
        )
        return {"count": 0, "cooldown_until": None}

    return {"count": user["upload_count"] or 0, "cooldown_until": None}


async def get_upload_count(user_id):
    status = await get_upload_status(user_id)
    return status["count"]


async def get_upload_cooldown(user_id):
    status = await get_upload_status(user_id)
    return status["cooldown_until"]


async def increment_upload_count(user_id):
    status = await get_upload_status(user_id)
    new_count = status["count"] + 1

    if new_count >= UPLOAD_LIMIT:
        cooldown_until = kz_now() + timedelta(hours=UPLOAD_COOLDOWN_HOURS)
        await db_execute(
            "UPDATE users SET upload_count=?, upload_date=?, upload_cooldown_until=? WHERE user_id=?",
            (new_count, kz_date(), cooldown_until.isoformat(), user_id)
        )
        return cooldown_until

    await db_execute(
        "UPDATE users SET upload_count=?, upload_date=? WHERE user_id=?",
        (new_count, kz_date(), user_id)
    )
    return None


def format_remaining_time(target):
    if not target:
        return ""
    seconds = int((target - kz_now()).total_seconds())
    if seconds <= 0:
        return ""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours} сағ. {minutes} мин."


# =========================================================
# USER UPLOAD
# =========================================================

@router.callback_query(F.data == "menu_upload")
async def upload_start_callback(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    await register_user(callback.from_user, bot=bot)

    if callback.from_user.id != ADMIN_ID:
        if not await require_subscription(callback, bot, callback.from_user.id):
            return

    cooldown = await get_upload_cooldown(callback.from_user.id)
    if cooldown:
        await show_screen(
            callback,
            "🚫 Сіз 15 файл лимитіне жеттіңіз.\n\n"
            f"⏳ Қалған уақыт: {format_remaining_time(cooldown)}",
            back_to_main_keyboard()
        )
        await callback.answer()
        return

    count = await get_upload_count(callback.from_user.id)
    genres = await get_genres()

    if not genres:
        await show_screen(callback, "❌ Қазір ешқандай жанр жоқ.", back_to_main_keyboard())
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    for genre in genres:
        builder.button(
            text=f"🎭 {genre['name']} — {genre['price']} 💰",
            callback_data=f"upload_genre_{genre['id']}"
        )
    builder.button(text="❌ Болдырмау", callback_data="upload_cancel")
    builder.adjust(1)

    await state.set_state(UserUploadState.waiting_genre)
    await show_screen(
        callback,
        "🎭 Видеоны/фотоны қай жанрға қосқыңыз келеді?\n\n"
        f"📤 Жіберілген файл: {count}/{UPLOAD_LIMIT}",
        builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("upload_genre_"))
async def upload_genre_selected(callback: CallbackQuery, state: FSMContext):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    genre_id = int(callback.data.split("_")[-1])
    genre = await get_genre(genre_id)

    if not genre or not genre["active"]:
        await callback.answer("❌ Бұл жанр қолжетімсіз.", show_alert=True)
        return

    cooldown = await get_upload_cooldown(callback.from_user.id)
    if cooldown:
        await callback.answer("🚫 15 файл лимитіне жеттіңіз.", show_alert=True)
        await state.clear()
        return

    count = await get_upload_count(callback.from_user.id)
    await state.update_data(genre_id=genre_id)
    await state.set_state(UserUploadState.waiting_file)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📤 Жіберу", callback_data="submit_pending"),
                InlineKeyboardButton(text="🚫 Жібергім келмейді", callback_data="cancel_pending")
            ],
            [
                InlineKeyboardButton(text="🔄 Жанр өзгерту", callback_data="change_upload_genre")
            ]
        ]
    )

    await callback.message.edit_text(
        f"🎭 Таңдалған жанр: <b>{genre['name']}</b>\n\n"
        "📤 Енді видео, фото немесе файл жіберіңіз.\n\n"
        f"📊 Лимит: {count}/{UPLOAD_LIMIT}\n\n"
        "⚠️ Файл бірден админге жіберілмейді.\n"
        "Файлдарды жинап болған соң «📤 Жіберу» батырмасын басыңыз.",
        parse_mode="HTML"
    )
    await callback.message.answer(
        "👇 Файл жіберуге дайынмын.\n\n"
        "Бірнеше файл жібере аласыз. Соңында «📤 Жіберу» батырмасын басыңыз.",
        reply_markup=keyboard
    )
    await callback.answer()


@router.callback_query(F.data == "change_upload_genre")
async def change_upload_genre(callback: CallbackQuery, state: FSMContext):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    genres = await get_genres()
    if not genres:
        await callback.answer("❌ Жанр жоқ.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for genre in genres:
        builder.button(
            text=f"🎭 {genre['name']} — {genre['price']} 💰",
            callback_data=f"upload_genre_{genre['id']}"
        )
    builder.button(text="❌ Болдырмау", callback_data="upload_cancel")
    builder.adjust(1)

    await state.set_state(UserUploadState.waiting_genre)
    await callback.message.answer("🎭 Жаңа жанр таңдаңыз:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "upload_cancel")
async def upload_cancel(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await send_main_menu(bot, callback.from_user.id)
    await callback.answer()


@router.message(UserUploadState.waiting_file)
async def upload_file_handler(message: Message, state: FSMContext, bot: Bot):
    if await is_user_blocked(message.from_user.id):
        return

    await register_user(message.from_user, bot=bot)

    cooldown = await get_upload_cooldown(message.from_user.id)
    if cooldown:
        await message.answer(
            f"🚫 15 файл лимитіне жеттіңіз.\n\n⏳ Қалған уақыт: {format_remaining_time(cooldown)}"
        )
        return

    count = await get_upload_count(message.from_user.id)
    if count >= UPLOAD_LIMIT:
        await message.answer(f"🚫 15 файл лимитіне жеттіңіз.\n⏳ Cooldown: {UPLOAD_COOLDOWN_HOURS} сағат.")
        return

    data = await state.get_data()
    genre_id = data.get("genre_id")
    genre = await get_genre(genre_id)

    if not genre:
        await message.answer("❌ Жанр табылмады.")
        await state.clear()
        return

    content_type = None
    file_id = None
    unique_id = None

    if message.video:
        content_type = "video"
        file_id = message.video.file_id
        unique_id = message.video.file_unique_id
    elif message.photo:
        content_type = "photo"
        file_id = message.photo[-1].file_id
        unique_id = message.photo[-1].file_unique_id
    elif message.document:
        content_type = "document"
        file_id = message.document.file_id
        unique_id = message.document.file_unique_id
    else:
        await message.answer("❌ Видео, фото немесе файл жіберіңіз.")
        return

    duplicate = await db_execute(
        "SELECT id FROM content WHERE file_unique_id=? LIMIT 1",
        (unique_id,),
        fetchone=True
    )
    if duplicate:
        await message.answer("⚠️ Бұл файл бұрын қабылданған.")
        return

    duplicate_pending = await db_execute(
        "SELECT id FROM pending WHERE file_unique_id=? LIMIT 1",
        (unique_id,),
        fetchone=True
    )
    if duplicate_pending:
        await message.answer("⚠️ Бұл файл қазірдің өзінде жіберу кезегінде тұр.")
        return

    await db_execute(
        """
        INSERT INTO pending (uploader_id, content_type, file_id, file_unique_id, genre_id, created_at, submitted)
        VALUES (?, ?, ?, ?, ?, ?, 0)
        """,
        (message.from_user.id, content_type, file_id, unique_id, genre_id, kz_now().isoformat())
    )

    pending = await db_execute(
        "SELECT * FROM pending WHERE uploader_id=? AND submitted=0 ORDER BY id DESC LIMIT 1",
        (message.from_user.id,),
        fetchone=True
    )
    pending_id = pending["id"]

    cooldown_created = await increment_upload_count(message.from_user.id)
    current_count = await get_upload_count(message.from_user.id)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Жіберу", callback_data="submit_pending")],
            [InlineKeyboardButton(text="🚫 Жібергім келмейді", callback_data="cancel_pending")],
            [InlineKeyboardButton(text="➕ Тағы файл қосу", callback_data="continue_upload")]
        ]
    )

    text = (
        "✅ Файл дайын!\n\n"
        f"🎭 Жанр: {genre['name']}\n"
        f"📦 Түрі: {content_type}\n"
        f"🆔 Файл ID: {pending_id}\n\n"
        f"📊 Жалпы лимит: {current_count}/{UPLOAD_LIMIT}\n\n"
        "⚠️ Файл әзірге админге жіберілген жоқ.\n"
        "Барлық дайын файлдарды бірден жіберу үшін «📤 Жіберу» батырмасын басыңыз."
    )

    if cooldown_created:
        text += f"\n\n🚫 Сіз 15 файл лимитіне жеттіңіз.\n⏳ {UPLOAD_COOLDOWN_HOURS} сағат бойы жаңа файл қабылданбайды."

    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "continue_upload")
async def continue_upload(callback: CallbackQuery, state: FSMContext):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    cooldown = await get_upload_cooldown(callback.from_user.id)
    if cooldown:
        await callback.answer(f"🚫 Лимитке жеттіңіз. {format_remaining_time(cooldown)}", show_alert=True)
        return

    count = await get_upload_count(callback.from_user.id)
    if count >= UPLOAD_LIMIT:
        await callback.answer("🚫 15 файл лимитіне жеттіңіз.", show_alert=True)
        return

    genres = await get_genres()
    if not genres:
        await callback.answer("❌ Жанр жоқ.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for genre in genres:
        builder.button(
            text=f"🎭 {genre['name']} — {genre['price']} 💰",
            callback_data=f"upload_genre_{genre['id']}"
        )
    builder.adjust(1)

    await state.set_state(UserUploadState.waiting_genre)
    await callback.message.answer(
        f"➕ Тағы файл қосу\n\n📊 Қазір: {count}/{UPLOAD_LIMIT}\n\nЖанр таңдаңыз:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data == "submit_pending")
async def submit_pending(callback: CallbackQuery, bot: Bot, state: FSMContext):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    user_id = callback.from_user.id

    rows = await db_execute(
        """
        SELECT p.*, g.name AS genre_name, g.price AS genre_price
        FROM pending p
        LEFT JOIN genres g ON g.id=p.genre_id
        WHERE p.uploader_id=? AND p.submitted=0
        ORDER BY p.id ASC
        """,
        (user_id,),
        fetch=True
    )

    if not rows:
        await callback.answer("📭 Жіберілетін файл жоқ.", show_alert=True)
        return

    ids = [row["id"] for row in rows]
    placeholders = ",".join("?" for _ in ids)
    await db_execute(
        f"UPDATE pending SET submitted=1 WHERE id IN ({placeholders})",
        tuple(ids)
    )

    try:
        await bot.send_message(
            ADMIN_ID,
            f"📩 <b>Жаңа контент кезекке қосылды</b>\n\n"
            f"👤 User ID: <code>{user_id}</code>\n"
            f"👤 Username: @{callback.from_user.username or 'жоқ'}\n"
            f"📦 Файл саны: <b>{len(rows)}</b>\n\n"
            "Көру үшін: <b>📩 Жіберілгендер</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.warning("Admin notify failed: %s", e)

    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        f"📤 Файлдарыңыз модерация кезегіне қосылды!\n"
        f"✅ Жіберілді: <b>{len(rows)}</b>\n"
        "⏳ Админ тексергеннен кейін нәтиже хабарланады.",
        parse_mode="HTML"
    )
    await send_main_menu(bot, callback.from_user.id)
    await callback.answer("📤 Жіберілді!")


@router.callback_query(F.data == "cancel_pending")
async def cancel_pending(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    user_id = callback.from_user.id
    rows = await db_execute(
        "SELECT id FROM pending WHERE uploader_id=? AND submitted=0",
        (user_id,),
        fetch=True
    )
    if not rows:
        await callback.answer("📭 Өшірілетін файл жоқ.", show_alert=True)
        return

    await db_execute(
        "DELETE FROM pending WHERE uploader_id=? AND submitted=0",
        (user_id,)
    )
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await send_main_menu(bot, callback.from_user.id)
    await callback.answer("🚫 Файлдар жойылды.")


# =========================================================
# APPROVE / REJECT
# =========================================================

@router.callback_query(F.data.startswith("approve_"))
async def approve_content(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return

    pending_id = int(callback.data.split("_")[1])
    pending = await db_execute(
        "SELECT * FROM pending WHERE id=?",
        (pending_id,),
        fetchone=True
    )
    if not pending:
        await callback.answer("⚠️ Бұл файл бұрын өңделген.", show_alert=True)
        return

    genre = await get_genre(pending["genre_id"])
    genre_name = genre["name"] if genre else "Белгісіз"

    duplicate = await db_execute(
        "SELECT id FROM content WHERE file_unique_id=?",
        (pending["file_unique_id"],),
        fetchone=True
    )
    if not duplicate:
        await db_execute(
            """
            INSERT INTO content (content_type, file_id, file_unique_id, genre_id, added_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (pending["content_type"], pending["file_id"], pending["file_unique_id"],
             pending["genre_id"], pending["uploader_id"], kz_now().isoformat())
        )

    await db_execute(
        """
        INSERT INTO pending_history
        (pending_id, uploader_id, content_type, file_unique_id, genre_id, status, processed_by, created_at, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (pending_id, pending["uploader_id"], pending["content_type"], pending["file_unique_id"],
         pending["genre_id"], "approved", ADMIN_ID, pending["created_at"], kz_now().isoformat())
    )

    await db_execute("DELETE FROM pending WHERE id=?", (pending_id,))
    upload_bonus = await get_int_setting("upload_bonus", UPLOAD_BONUS)
    await add_balance(pending["uploader_id"], upload_bonus)
    balance = await get_balance(pending["uploader_id"])

    try:
        await bot.send_message(
            pending["uploader_id"],
            f"🎉 <b>КОНТЕНТ ҚАБЫЛДАНДЫ!</b>\n\n"
            f"🎭 Жанр: {genre_name}\n"
            f"💰 Сізге +{upload_bonus} монета берілді.\n"
            f"💳 Қазіргі баланс: <b>{balance}</b> 💰",
            parse_mode="HTML"
        )
    except Exception:
        pass

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer("✅ Қабылданды!")


@router.callback_query(F.data.startswith("reject_"))
async def reject_content(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return

    pending_id = int(callback.data.split("_")[1])
    pending = await db_execute(
        "SELECT * FROM pending WHERE id=?",
        (pending_id,),
        fetchone=True
    )
    if not pending:
        await callback.answer("⚠️ Бұл файл бұрын өңделген.", show_alert=True)
        return

    genre = await get_genre(pending["genre_id"])
    genre_name = genre["name"] if genre else "Белгісіз"

    await db_execute(
        """
        INSERT INTO pending_history
        (pending_id, uploader_id, content_type, file_unique_id, genre_id, status, processed_by, created_at, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (pending_id, pending["uploader_id"], pending["content_type"], pending["file_unique_id"],
         pending["genre_id"], "rejected", ADMIN_ID, pending["created_at"], kz_now().isoformat())
    )
    await db_execute("DELETE FROM pending WHERE id=?", (pending_id,))

    try:
        await bot.send_message(
            pending["uploader_id"],
            f"❌ <b>КОНТЕНТ ҚАБЫЛДАНБАДЫ.</b>\n\n"
            f"🎭 Жанр: {genre_name}\n"
            "💰 Монета берілмейді.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer("❌ Қабылданбады.")


# =========================================================
# REACTIONS + VIDEO WATCH (protect_content + auto-delete)
# =========================================================

async def get_reaction_counts(content_id):
    row = await db_execute(
        """
        SELECT
            SUM(CASE WHEN reaction='like' THEN 1 ELSE 0 END) AS likes,
            SUM(CASE WHEN reaction='dislike' THEN 1 ELSE 0 END) AS dislikes
        FROM content_reactions
        WHERE content_id=?
        """,
        (content_id,),
        fetchone=True
    )
    return (row["likes"] or 0), (row["dislikes"] or 0)


async def get_user_reaction(user_id, content_id):
    row = await db_execute(
        "SELECT reaction FROM content_reactions WHERE user_id=? AND content_id=?",
        (user_id, content_id),
        fetchone=True
    )
    return row["reaction"] if row else None


async def set_reaction(user_id, content_id, reaction):
    current = await get_user_reaction(user_id, content_id)
    if current == reaction:
        await db_execute(
            "DELETE FROM content_reactions WHERE user_id=? AND content_id=?",
            (user_id, content_id)
        )
        return None

    await db_execute(
        """
        INSERT INTO content_reactions (user_id, content_id, reaction, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, content_id) DO UPDATE SET
            reaction=excluded.reaction, created_at=excluded.created_at
        """,
        (user_id, content_id, reaction, kz_now().isoformat())
    )
    return reaction


def reaction_keyboard(content_id, user_reaction=None, genre_id=None):
    like_text = "❤️ Лайк" + (" ✓" if user_reaction == "like" else "")
    dislike_text = "👎 Дизлайк" + (" ✓" if user_reaction == "dislike" else "")

    rows = [[
        InlineKeyboardButton(text=like_text, callback_data=f"like_{content_id}"),
        InlineKeyboardButton(text=dislike_text, callback_data=f"dislike_{content_id}")
    ]]
    if genre_id is not None:
        rows.append([InlineKeyboardButton(text="➡️ Келесі", callback_data=f"next_{genre_id}")])
        rows.append([
            InlineKeyboardButton(text="🔙 Жанрларға", callback_data="back_genres"),
            InlineKeyboardButton(text="🏠 Басты мәзір", callback_data="menu_main_from_video"),
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def build_content_caption(content, genre, price, balance, user_id):
    likes, dislikes = await get_reaction_counts(content["id"])
    user_reaction = await get_user_reaction(user_id, content["id"])
    caption = (
        f"🎭 Жанр: {genre['name']}\n"
        f"💰 Видео бағасы: {price}\n"
        f"💳 Қалған монета: {balance}\n\n"
        f"❤️ Лайк: {likes}\n"
        f"👎 Дизлайк: {dislikes}\n\n"
        f"⏳ {CONTENT_AUTO_DELETE_MINUTES} мин. кейін автоматты өшеді"
    )
    return caption, user_reaction


async def send_content_to_user(bot, user_id, content, genre, price):
    """Контент жіберу: protect_content=True + 30 мин кейін өшіру."""
    balance = await get_balance(user_id)
    caption, user_reaction = await build_content_caption(content, genre, price, balance, user_id)
    keyboard = reaction_keyboard(content["id"], user_reaction, genre["id"])

    try:
        msg = None
        if content["content_type"] == "video":
            msg = await bot.send_video(
                user_id, content["file_id"],
                caption=caption, reply_markup=keyboard,
                protect_content=True
            )
        elif content["content_type"] == "photo":
            msg = await bot.send_photo(
                user_id, content["file_id"],
                caption=caption, reply_markup=keyboard,
                protect_content=True
            )
        elif content["content_type"] == "document":
            msg = await bot.send_document(
                user_id, content["file_id"],
                caption=caption, reply_markup=keyboard,
                protect_content=True
            )
        if msg:
            await schedule_message_delete(bot, user_id, msg.message_id, CONTENT_AUTO_DELETE_MINUTES)
            return True
        return False
    except Exception as e:
        logging.error("Content send error: %s", e)
        return False


@router.callback_query(F.data.startswith("like_"))
async def like_content(callback: CallbackQuery, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    content_id = int(callback.data.split("_")[1])
    content = await db_execute("SELECT * FROM content WHERE id=?", (content_id,), fetchone=True)
    if not content:
        await callback.answer("❌ Контент табылмады.", show_alert=True)
        return

    await set_reaction(callback.from_user.id, content_id, "like")
    likes, dislikes = await get_reaction_counts(content_id)
    await callback.answer(f"❤️ {likes}  |  👎 {dislikes}")

    if callback.message:
        genre = await get_genre(content["genre_id"])
        if genre:
            balance = await get_balance(callback.from_user.id)
            user_reaction = await get_user_reaction(callback.from_user.id, content_id)
            caption = (
                f"🎭 Жанр: {genre['name']}\n"
                f"💰 Видео бағасы: {genre['price']}\n"
                f"💳 Қалған монета: {balance}\n\n"
                f"❤️ Лайк: {likes}\n"
                f"👎 Дизлайк: {dislikes}\n\n"
                f"⏳ {CONTENT_AUTO_DELETE_MINUTES} мин. кейін автоматты өшеді"
            )
            keyboard = reaction_keyboard(content_id, user_reaction, genre["id"])
            try:
                await bot.edit_message_caption(
                    chat_id=callback.from_user.id,
                    message_id=callback.message.message_id,
                    caption=caption,
                    reply_markup=keyboard
                )
            except Exception:
                pass


@router.callback_query(F.data.startswith("dislike_"))
async def dislike_content(callback: CallbackQuery, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    content_id = int(callback.data.split("_")[1])
    content = await db_execute("SELECT * FROM content WHERE id=?", (content_id,), fetchone=True)
    if not content:
        await callback.answer("❌ Контент табылмады.", show_alert=True)
        return

    await set_reaction(callback.from_user.id, content_id, "dislike")
    likes, dislikes = await get_reaction_counts(content_id)
    await callback.answer(f"❤️ {likes}  |  👎 {dislikes}")

    if callback.message:
        genre = await get_genre(content["genre_id"])
        if genre:
            balance = await get_balance(callback.from_user.id)
            user_reaction = await get_user_reaction(callback.from_user.id, content_id)
            caption = (
                f"🎭 Жанр: {genre['name']}\n"
                f"💰 Видео бағасы: {genre['price']}\n"
                f"💳 Қалған монета: {balance}\n\n"
                f"❤️ Лайк: {likes}\n"
                f"👎 Дизлайк: {dislikes}\n\n"
                f"⏳ {CONTENT_AUTO_DELETE_MINUTES} мин. кейін автоматты өшеді"
            )
            keyboard = reaction_keyboard(content_id, user_reaction, genre["id"])
            try:
                await bot.edit_message_caption(
                    chat_id=callback.from_user.id,
                    message_id=callback.message.message_id,
                    caption=caption,
                    reply_markup=keyboard
                )
            except Exception:
                pass


@router.callback_query(F.data == "menu_watch_video")
async def video_genres_callback(callback: CallbackQuery, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    await register_user(callback.from_user, bot=bot)

    if callback.from_user.id != ADMIN_ID:
        if not await require_subscription(callback, bot, callback.from_user.id):
            return

    genres = await get_genres()
    if not genres:
        await show_screen(callback, "📭 Қазір жанрлар жоқ.", back_to_main_keyboard())
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    for genre in genres:
        count = await db_execute(
            "SELECT COUNT(*) AS count FROM content WHERE genre_id=?",
            (genre["id"],),
            fetchone=True
        )
        builder.button(
            text=f"🎭 {genre['name']} • {genre['price']} 💰 • {count['count']} видео",
            callback_data=f"watch_genre_{genre['id']}"
        )
    builder.row(InlineKeyboardButton(text="🏠 Басты мәзір", callback_data="menu_main"))
    builder.adjust(1)

    await show_screen(
        callback,
        "🎭 <b>ЖАНР ТАҢДАҢЫЗ</b>\n\n"
        "Әр жанрдың көру бағасы әртүрлі болуы мүмкін.\n"
        f"⚠️ Контент {CONTENT_AUTO_DELETE_MINUTES} мин. кейін өшеді және жіберуге болмайды.",
        builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("watch_genre_"))
async def watch_genre(callback: CallbackQuery, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    if callback.from_user.id != ADMIN_ID:
        if not await require_subscription(callback, bot, callback.from_user.id):
            return

    genre_id = int(callback.data.split("_")[-1])
    genre = await get_genre(genre_id)
    if not genre or not genre["active"]:
        await callback.answer("❌ Жанр табылмады.", show_alert=True)
        return

    balance = await get_balance(callback.from_user.id)
    price = genre["price"]

    if balance < price:
        await callback.answer(
            f"❌ Баланс жеткіліксіз.\nҚажет: {price} 💰\nСізде: {balance} 💰",
            show_alert=True
        )
        return

    content = await db_execute(
        """
        SELECT * FROM content
        WHERE genre_id=? AND id NOT IN (
            SELECT content_id FROM viewed WHERE user_id=?
        )
        ORDER BY id ASC LIMIT 1
        """,
        (genre_id, callback.from_user.id),
        fetchone=True
    )

    if not content:
        await callback.answer("📭 Бұл жанрда көрмеген видео жоқ.", show_alert=True)
        return

    await db_execute(
        "UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?",
        (price, callback.from_user.id, price)
    )
    await db_execute(
        "INSERT OR IGNORE INTO viewed (user_id, content_id, viewed_at) VALUES (?, ?, ?)",
        (callback.from_user.id, content["id"], kz_now().isoformat())
    )

    success = await send_content_to_user(bot, callback.from_user.id, content, genre, price)
    if not success:
        await add_balance(callback.from_user.id, price)
        await db_execute(
            "DELETE FROM viewed WHERE user_id=? AND content_id=?",
            (callback.from_user.id, content["id"])
        )
        await callback.answer("⚠️ Видео жіберілмеді. Монетаңыз қайтарылды.", show_alert=True)
        return

    try:
        await callback.message.edit_text(
            f"🎭 <b>{genre['name']}</b>\n\n⬇️ Видео төменде жіберілді.\n"
            f"⏳ {CONTENT_AUTO_DELETE_MINUTES} мин. кейін өшеді.",
            parse_mode="HTML",
            reply_markup=None
        )
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    await callback.answer()


@router.callback_query(F.data.startswith("next_"))
async def next_video(callback: CallbackQuery, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    genre_id = int(callback.data.split("_")[-1])
    genre = await get_genre(genre_id)
    if not genre:
        await callback.answer("❌ Жанр табылмады.", show_alert=True)
        return

    if callback.from_user.id != ADMIN_ID:
        if not await require_subscription(callback, bot, callback.from_user.id):
            return

    price = genre["price"]
    balance = await get_balance(callback.from_user.id)

    if balance < price:
        await callback.answer(
            f"❌ Баланс жеткіліксіз.\nҚажет: {price} 💰\nСізде: {balance} 💰",
            show_alert=True
        )
        return

    content = await db_execute(
        """
        SELECT * FROM content
        WHERE genre_id=? AND id NOT IN (
            SELECT content_id FROM viewed WHERE user_id=?
        )
        ORDER BY id ASC LIMIT 1
        """,
        (genre_id, callback.from_user.id),
        fetchone=True
    )

    if not content:
        await callback.answer("📭 Бұл жанрдағы барлық видеоны көрдіңіз.", show_alert=True)
        return

    await db_execute(
        "UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?",
        (price, callback.from_user.id, price)
    )
    await db_execute(
        "INSERT OR IGNORE INTO viewed (user_id, content_id, viewed_at) VALUES (?, ?, ?)",
        (callback.from_user.id, content["id"], kz_now().isoformat())
    )

    success = await send_content_to_user(bot, callback.from_user.id, content, genre, price)
    if not success:
        await add_balance(callback.from_user.id, price)
        await db_execute(
            "DELETE FROM viewed WHERE user_id=? AND content_id=?",
            (callback.from_user.id, content["id"])
        )
        await callback.answer("⚠️ Видео жіберілмеді. Монетаңыз қайтарылды.", show_alert=True)
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await callback.answer()


async def _build_genres_keyboard():
    genres = await get_genres()
    builder = InlineKeyboardBuilder()
    for genre in genres:
        count = await db_execute(
            "SELECT COUNT(*) AS count FROM content WHERE genre_id=?",
            (genre["id"],),
            fetchone=True
        )
        builder.button(
            text=f"🎭 {genre['name']} • {genre['price']} 💰 • {count['count']}",
            callback_data=f"watch_genre_{genre['id']}"
        )
    builder.row(InlineKeyboardButton(text="🏠 Басты мәзір", callback_data="menu_main"))
    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(F.data == "back_genres")
async def back_genres(callback: CallbackQuery, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    kb = await _build_genres_keyboard()
    await bot.send_message(
        callback.from_user.id,
        "🎭 <b>ЖАНРЛАР</b>\n\nЖанр таңдаңыз:",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "menu_main_from_video")
async def menu_main_from_video(callback: CallbackQuery, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await send_main_menu(bot, callback.from_user.id)
    await callback.answer()


# =========================================================
# TASKS (USER)
# =========================================================

@router.callback_query(F.data == "menu_tasks")
async def tasks_list_callback(callback: CallbackQuery, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    if callback.from_user.id != ADMIN_ID and not await require_subscription(callback, bot, callback.from_user.id):
        return

    rows = await db_execute(
        "SELECT * FROM tasks WHERE active=1 ORDER BY id ASC",
        fetch=True
    )
    if not rows:
        await show_screen(callback, "📭 Қазір белсенді тапсырма жоқ.", back_to_main_keyboard())
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    for row in rows:
        done = await db_execute(
            "SELECT 1 FROM task_completions WHERE user_id=? AND task_id=?",
            (callback.from_user.id, row["id"]),
            fetchone=True
        )
        text = f"✅ {row['name']} (+{row['reward']})" if done else f"🎯 {row['name']} (+{row['reward']})"
        builder.button(text=text, callback_data=f"task_{row['id']}")
    builder.row(InlineKeyboardButton(text="🏠 Басты мәзір", callback_data="menu_main"))
    builder.adjust(1)

    await show_screen(
        callback,
        "🎯 <b>ТАПСЫРМАЛАР</b>\n\nТапсырманы таңдап, көрсетілген сілтемеге өтіңіз.",
        builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task_"), ~F.data.startswith("task_check_"), ~F.data.startswith("admintask_"))
async def task_open(callback: CallbackQuery, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    task_id = int(callback.data.split("_", 1)[1])
    task = await db_execute(
        "SELECT * FROM tasks WHERE id=? AND active=1",
        (task_id,),
        fetchone=True
    )
    if not task:
        await callback.answer("❌ Тапсырма табылмады.", show_alert=True)
        return

    done = await db_execute(
        "SELECT 1 FROM task_completions WHERE user_id=? AND task_id=?",
        (callback.from_user.id, task_id),
        fetchone=True
    )
    if done:
        await callback.answer("✅ Бұл тапсырма бұрын орындалған.", show_alert=True)
        return

    url = task["link"] or channel_url(task["channel"])
    rows = []
    if url:
        rows.append([InlineKeyboardButton(text="🔗 Тапсырманы орындау", url=url)])
    rows.append([InlineKeyboardButton(text="✅ Тексеру", callback_data=f"task_check_{task_id}")])
    rows.append([InlineKeyboardButton(text="🔙 Тапсырмаларға", callback_data="menu_tasks")])

    text = f"🎯 <b>{task['name']}</b>\n\n💰 Сыйақы: +{task['reward']} монета"
    if task["channel"]:
        text += f"\n📢 Канал: {task['channel']}"

    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if task["photo_file_id"]:
        try:
            await callback.message.answer_photo(task["photo_file_id"], caption=text, reply_markup=markup, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=markup, parse_mode="HTML")
    else:
        await callback.message.answer(text, reply_markup=markup, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("task_check_"))
async def task_check(callback: CallbackQuery, bot: Bot):
    if await is_user_blocked(callback.from_user.id):
        await callback.answer()
        return

    task_id = int(callback.data.split("_", 2)[2])
    task = await db_execute(
        "SELECT * FROM tasks WHERE id=? AND active=1",
        (task_id,),
        fetchone=True
    )
    if not task:
        await callback.answer("❌ Тапсырма табылмады.", show_alert=True)
        return

    done = await db_execute(
        "SELECT 1 FROM task_completions WHERE user_id=? AND task_id=?",
        (callback.from_user.id, task_id),
        fetchone=True
    )
    if done:
        await callback.answer("✅ Бұрын орындалған.", show_alert=True)
        return

    if task["channel"]:
        try:
            target = normalize_channel(task["channel"])
            member = await bot.get_chat_member(target, callback.from_user.id)
            if member.status not in (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.CREATOR,
            ):
                await callback.answer("❌ Алдымен тапсырмадағы каналға жазылыңыз.", show_alert=True)
                return
        except Exception:
            await callback.answer("⚠️ Канал жазылымын тексеру мүмкін болмады.", show_alert=True)
            return

    await db_execute(
        "INSERT INTO task_completions(user_id, task_id, completed_at) VALUES(?,?,?)",
        (callback.from_user.id, task_id, kz_now().isoformat())
    )
    await add_balance(callback.from_user.id, int(task["reward"]))
    await callback.answer(f"🎉 +{task['reward']} монета!", show_alert=True)
    await callback.message.answer(f"✅ Тапсырма орындалды!\n💰 +{task['reward']} монета қосылды.")


# =========================================================
# ADMIN TASKS
# =========================================================

@router.message(F.text == "🎯 Тапсырмаларды басқару")
async def admin_tasks_menu(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    await state.clear()

    rows = await db_execute("SELECT * FROM tasks ORDER BY id DESC", fetch=True)

    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Жаңа тапсырма қосу", callback_data="admintask_add")
    if rows:
        for r in rows:
            status = "🟢" if r["active"] == 1 else "🔴"
            builder.button(text=f"{status} {r['name']} ({r['reward']} 💰)", callback_data=f"admintask_view_{r['id']}")
    builder.adjust(1)

    await message.answer(
        "🎯 <b>Тапсырмаларды басқару панелі</b>\n\n"
        "Төменнен тізімді көріп, өңдей аласыз немесе жаңасын қоса аласыз:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admintask_add")
async def admintask_add_start(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    await state.set_state(TaskState.waiting_name)
    await callback.message.edit_text(
        "➕ <b>Жаңа тапсырма қосу</b>\n\n1️⃣ Тапсырманың атауын жазыңыз (мысалы: <i>Арнаға жазылу</i>):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Болдырмау", callback_data="admintask_cancel")]])
    )
    await callback.answer()


@router.message(TaskState.waiting_name, F.text)
async def admintask_get_name(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    await state.update_data(name=message.text.strip())
    await state.set_state(TaskState.waiting_channel)
    await message.answer(
        "2️⃣ Тапсырмаға қатысты канал username немесе сілтемесін жазыңыз (жоқ болса <code>жоқ</code> деп жіберіңіз):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Болдырмау", callback_data="admintask_cancel")]])
    )


@router.message(TaskState.waiting_channel, F.text)
async def admintask_get_channel(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    text = message.text.strip()
    channel = "" if text.lower() == "жоқ" else text
    await state.update_data(channel=channel)
    await state.set_state(TaskState.waiting_reward)
    await message.answer(
        "3️⃣ Тапсырма үшін берілетін сыйақы мөлшерін (монета санын) санмен жазыңыз (мысалы: <code>15</code>):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Болдырмау", callback_data="admintask_cancel")]])
    )


@router.message(TaskState.waiting_reward, F.text)
async def admintask_get_reward(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    try:
        reward = int(message.text.strip())
        if reward < 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Тек оң сан енгізіңіз (мысалы: 15).")
        return

    await state.update_data(reward=reward)
    await state.set_state(TaskState.waiting_link)
    await message.answer(
        "4️⃣ Пайдаланушы өтетін сілтемені (URL) жазыңыз (мысалы: <code>https://t.me/...</code> немесе канал болса <code>жоқ</code> деңіз):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Болдырмау", callback_data="admintask_cancel")]])
    )


@router.message(TaskState.waiting_link, F.text)
async def admintask_get_link(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    text = message.text.strip()
    link = "" if text.lower() == "жоқ" else text
    await state.update_data(link=link)
    await state.set_state(TaskState.waiting_photo)
    await message.answer(
        "5️⃣ Тапсырмаға фото қосу керек пе?\n\nҚажет болса фото жіберіңіз, қажет болмаса <code>жоқ</code> деп жазыңыз.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Болдырмау", callback_data="admintask_cancel")]])
    )


@router.message(TaskState.waiting_photo)
async def admintask_get_photo(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return

    data = await state.get_data()
    name = data.get("name")
    channel = data.get("channel")
    reward = data.get("reward")
    link = data.get("link")

    photo_file_id = None
    if message.photo:
        photo_file_id = message.photo[-1].file_id

    await db_execute(
        """
        INSERT INTO tasks (name, channel, reward, active, created_at, link, photo_file_id)
        VALUES (?, ?, ?, 1, ?, ?, ?)
        """,
        (name, channel, reward, kz_now().isoformat(), link, photo_file_id)
    )

    await state.clear()
    await message.answer(
        "✅ <b>Тапсырма сәтті қосылды!</b>",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


@router.callback_query(F.data.startswith("admintask_view_"))
async def admintask_view_callback(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return

    task_id = int(callback.data.split("_")[2])
    task = await db_execute("SELECT * FROM tasks WHERE id=?", (task_id,), fetchone=True)
    if not task:
        await callback.answer("❌ Тапсырма табылмады.", show_alert=True)
        return

    status_str = "🟢 Белсенді" if task["active"] == 1 else "🔴 Өшірулі"
    text = (
        f"🎯 <b>Тапсырма мәліметі</b> (#ID {task['id']})\n\n"
        f"📌 Атауы: <b>{task['name']}</b>\n"
        f"📢 Канал: {task['channel'] or 'Жоқ'}\n"
        f"🔗 Сілтеме: {task['link'] or 'Жоқ'}\n"
        f"💰 Сыйақы: {task['reward']} монета\n"
        f"⚡ Күйі: {status_str}"
    )

    toggle_text = "🔴 Өшіру" if task["active"] == 1 else "🟢 Қосу"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data=f"admintask_toggle_{task['id']}")],
            [InlineKeyboardButton(text="🗑 Жою", callback_data=f"admintask_delete_{task['id']}")],
            [InlineKeyboardButton(text="⬅️ Тізімге қайту", callback_data="admintask_back")]
        ]
    )

    if task["photo_file_id"]:
        try:
            await callback.message.answer_photo(task["photo_file_id"], caption=text, reply_markup=keyboard, parse_mode="HTML")
            await callback.message.delete()
        except Exception:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admintask_toggle_"))
async def admintask_toggle_callback(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return

    task_id = int(callback.data.split("_")[2])
    task = await db_execute("SELECT * FROM tasks WHERE id=?", (task_id,), fetchone=True)
    if not task:
        await callback.answer("❌ Тапсырма табылмады.", show_alert=True)
        return

    new_active = 0 if task["active"] == 1 else 1
    await db_execute("UPDATE tasks SET active=? WHERE id=?", (new_active, task_id))

    await callback.answer("✅ Күйі өзгертілді!", show_alert=True)

    task = await db_execute("SELECT * FROM tasks WHERE id=?", (task_id,), fetchone=True)
    status_str = "🟢 Белсенді" if task["active"] == 1 else "🔴 Өшірулі"
    text = (
        f"🎯 <b>Тапсырма мәліметі</b> (#ID {task['id']})\n\n"
        f"📌 Атауы: <b>{task['name']}</b>\n"
        f"📢 Канал: {task['channel'] or 'Жоқ'}\n"
        f"🔗 Сілтеме: {task['link'] or 'Жоқ'}\n"
        f"💰 Сыйақы: {task['reward']} монета\n"
        f"⚡ Күйі: {status_str}"
    )
    toggle_text = "🔴 Өшіру" if task["active"] == 1 else "🟢 Қосу"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data=f"admintask_toggle_{task['id']}")],
            [InlineKeyboardButton(text="🗑 Жою", callback_data=f"admintask_delete_{task['id']}")],
            [InlineKeyboardButton(text="⬅️ Тізімге қайту", callback_data="admintask_back")]
        ]
    )
    try:
        await callback.message.edit_caption(caption=text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            pass


@router.callback_query(F.data.startswith("admintask_delete_"))
async def admintask_delete_callback(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return

    task_id = int(callback.data.split("_")[2])
    await db_execute("DELETE FROM tasks WHERE id=?", (task_id,))
    await db_execute("DELETE FROM task_completions WHERE task_id=?", (task_id,))

    await callback.answer("🗑 Тапсырма жойылды!", show_alert=True)

    rows = await db_execute("SELECT * FROM tasks ORDER BY id DESC", fetch=True)
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Жаңа тапсырма қосу", callback_data="admintask_add")
    if rows:
        for r in rows:
            status = "🟢" if r["active"] == 1 else "🔴"
            builder.button(text=f"{status} {r['name']} ({r['reward']} 💰)", callback_data=f"admintask_view_{r['id']}")
    builder.adjust(1)

    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        "🎯 <b>Тапсырмаларды басқару панелі</b>\n\nТапсырма сәтті жойылды:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admintask_back")
async def admintask_back_callback(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    await state.clear()

    rows = await db_execute("SELECT * FROM tasks ORDER BY id DESC", fetch=True)
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Жаңа тапсырма қосу", callback_data="admintask_add")
    if rows:
        for r in rows:
            status = "🟢" if r["active"] == 1 else "🔴"
            builder.button(text=f"{status} {r['name']} ({r['reward']} 💰)", callback_data=f"admintask_view_{r['id']}")
    builder.adjust(1)

    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        "🎯 <b>Тапсырмаларды басқару панелі</b>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "admintask_cancel")
async def admintask_cancel_callback(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("❌ Әрекет тоқтатылды.", reply_markup=admin_keyboard())
    await callback.answer()


# =========================================================
# ADMIN PANEL
# =========================================================

@router.message(F.text == "👑 Админ панелі")
async def switch_to_admin(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    await state.clear()
    await message.answer("👑 Админ панелі", reply_markup=admin_keyboard())


@router.message(F.text == "🔙 Қолданушы панелі")
async def switch_to_user_panel(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "👤 Қолданушы панелі\n\n🏠 <b>Негізгі мәзір</b>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )
    await message.answer(
        "Төмендегі батырмаларды пайдаланыңыз:",
        reply_markup=user_inline_keyboard()
    )


@router.callback_query(F.data == "admin_cancel")
async def admin_cancel(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    await state.clear()
    await callback.message.answer("❌ Әрекет тоқтатылды.", reply_markup=admin_keyboard())
    await callback.answer()


# -------------------------
# BAN / UNBAN USER
# -------------------------

@router.message(F.text == "🚫 Пайдаланушыны бұғаттау")
async def admin_ban_start(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    await state.set_state(BanUserState.waiting_id)
    await message.answer(
        "🚫 <b>Пайдаланушыны бұғаттау</b>\n\n"
        "User ID-ні жіберіңіз (мысалы: <code>123456789</code>).\n"
        "Бұғатталған адамға бот ешқандай жауап бермейді.",
        parse_mode="HTML",
        reply_markup=admin_cancel_keyboard()
    )


@router.message(BanUserState.waiting_id, F.text)
async def admin_ban_user(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❗ Тек сан (User ID) енгізіңіз.")
        return

    if uid == ADMIN_ID:
        await message.answer("❌ Өзіңізді бұғаттай алмайсыз.")
        return

    user = await get_user(uid)
    if not user:
        await message.answer("❌ Бұл user_id базаға тіркелмеген.")
        return

    await db_execute("UPDATE users SET is_blocked=1 WHERE user_id=?", (uid,))
    await state.clear()
    await message.answer(
        f"🚫 <b>{uid}</b> бұғатталды.\n"
        "Бот оған енді жауап бермейді.",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


@router.message(F.text == "✅ Бұғаттан шығару")
async def admin_unban_start(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    await state.set_state(UnbanUserState.waiting_id)
    await message.answer(
        "✅ <b>Бұғаттан шығару</b>\n\n"
        "User ID-ні жіберіңіз:",
        parse_mode="HTML",
        reply_markup=admin_cancel_keyboard()
    )


@router.message(UnbanUserState.waiting_id, F.text)
async def admin_unban_user(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❗ Тек сан (User ID) енгізіңіз.")
        return

    user = await get_user(uid)
    if not user:
        await message.answer("❌ Бұл user_id базаға тіркелмеген.")
        return

    await db_execute("UPDATE users SET is_blocked=0 WHERE user_id=?", (uid,))
    await state.clear()
    await message.answer(
        f"✅ <b>{uid}</b> бұғаттан шығарылды.",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


# -------------------------
# GIVE COIN
# -------------------------

@router.message(F.text == "💰 Монета беру")
async def admin_give_coin_start(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    await state.set_state(GiveCoinState.waiting_data)
    await message.answer(
        "💰 <b>Монета беру</b>\n\n"
        "Мына форматта жіберіңіз:\n"
        "<code>USER_ID AMOUNT</code>\n\n"
        "Мысалы: <code>123456789 50</code>",
        parse_mode="HTML",
        reply_markup=admin_cancel_keyboard()
    )


@router.message(GiveCoinState.waiting_data, F.text)
async def admin_give_coin(message: Message, state: FSMContext, bot: Bot):
    if not admin_only(message.from_user.id):
        return
    parts = message.text.strip().split()
    if len(parts) != 2:
        await message.answer("❗ Дұрыс формат: <code>USER_ID AMOUNT</code>", parse_mode="HTML")
        return
    try:
        user_id, amount = int(parts[0]), int(parts[1])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ ID және монета саны сан болуы керек.")
        return

    user = await get_user(user_id)
    if not user:
        await message.answer("❌ Бұл user_id базаға тіркелмеген.")
        return

    await add_balance(user_id, amount)
    new_balance = await get_balance(user_id)
    await state.clear()

    try:
        await bot.send_message(
            user_id,
            f"🌑 <b>Қараңғы Дарвин Теориясы</b> сізге <b>+{amount}</b> 💰 берді!\n\n"
            f"💳 Қазіргі баланс: <b>{new_balance}</b>",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await message.answer(
        f"✅ <b>{user_id}</b> пайдаланушысына +{amount} 💰 берілді.\n"
        f"💳 Жаңа баланс: <b>{new_balance}</b>",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


# -------------------------
# GLOBAL COIN
# -------------------------

@router.message(F.text == "🌍 Барлығына монета")
async def admin_global_coin_start(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    await state.set_state(GlobalCoinState.waiting_amount)
    await message.answer(
        "🌍 Барлық қолданушыға бірдей қанша монета берілсін?\n\n"
        "Тек сан жіберіңіз. Мысалы: <code>10</code>",
        parse_mode="HTML",
        reply_markup=admin_cancel_keyboard()
    )


@router.message(GlobalCoinState.waiting_amount, F.text)
async def admin_global_coin(message: Message, state: FSMContext, bot: Bot):
    if not admin_only(message.from_user.id):
        return
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Оң сан енгізіңіз.")
        return

    users = await db_execute(
        "SELECT user_id FROM users WHERE is_blocked=0",
        fetch=True
    )
    count = len(users) if users else 0

    await db_execute(
        "UPDATE users SET balance=balance+? WHERE is_blocked=0",
        (amount,)
    )
    await state.clear()

    sent = 0
    for row in (users or []):
        try:
            await bot.send_message(
                row["user_id"],
                f"🌑 <b>Қараңғы Дарвин Теориясы</b> сізге <b>+{amount}</b> 💰 берді!",
                parse_mode="HTML"
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await message.answer(
        f"✅ {count} қолданушыға +{amount} 💰 берілді.\n"
        f"📨 Хабарлама жіберілді: {sent}",
        reply_markup=admin_keyboard()
    )


# -------------------------
# BROADCAST (келісімсіз, қауіпсіз жылдамдық)
# -------------------------

@router.message(F.text == "📢 Рассылка")
async def admin_broadcast_start(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return

    row = await db_execute(
        "SELECT COUNT(*) AS c FROM users WHERE is_blocked=0",
        fetchone=True
    )
    active_count = int(row["c"]) if row else 0

    if active_count == 0:
        await message.answer(
            "📢 <b>Рассылка</b>\n\n"
            "⚠️ Белсенді қолданушы жоқ.",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )
        return

    await state.set_state(BroadcastState.waiting_message)
    await message.answer(
        "📢 <b>Рассылка</b>\n\n"
        f"👥 Белсенді қолданушы: <b>{active_count}</b>\n\n"
        "Енді жіберілетін хабарламаны осы чатқа жіберіңіз.\n"
        "Мәтін, фото, видео немесе құжат қабылданады.\n\n"
        "⚠️ Хабарлама <b>барлық белсенді</b> қолданушыларға жіберіледі.\n"
        f"🛡 Қауіпсіздік: әр хабарлама арасында ~{BROADCAST_DELAY}с күту, "
        f"әр {BROADCAST_BATCH_SIZE} хабарламадан кейін {BROADCAST_BATCH_PAUSE}с үзіліс.\n"
        "Бұл Telegram бан қаупін азайтады.",
        parse_mode="HTML",
        reply_markup=admin_cancel_keyboard()
    )


@router.message(BroadcastState.waiting_message)
async def admin_broadcast_send(message: Message, state: FSMContext, bot: Bot):
    if not admin_only(message.from_user.id):
        return

    users = await db_execute(
        "SELECT user_id FROM users WHERE is_blocked=0",
        fetch=True
    )
    total = len(users)
    if total == 0:
        await state.clear()
        await message.answer(
            "📭 Белсенді қолданушы жоқ. Рассылка тоқтатылды.",
            reply_markup=admin_keyboard()
        )
        return

    progress_msg = await message.answer(
        f"📢 <b>Рассылка басталды...</b>\n\n"
        f"Жіберілуде: 0 / {total}\n"
        f"🛡 Қауіпсіз режим (баяу жіберу)",
        parse_mode="HTML"
    )

    sent = 0
    failed = 0
    blocked_marked = 0

    for i, row in enumerate(users, 1):
        try:
            await bot.copy_message(
                chat_id=row["user_id"],
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
            sent += 1
        except Exception as e:
            # Telegram flood control: respect RetryAfter instead of retrying rapidly.
            retry_after = getattr(e, "retry_after", None)
            if retry_after is not None:
                wait_seconds = max(1, int(retry_after) + 1)
                logging.warning("Telegram rate limit: waiting %s seconds", wait_seconds)
                await asyncio.sleep(wait_seconds)
                try:
                    await bot.copy_message(
                        chat_id=row["user_id"],
                        from_chat_id=message.chat.id,
                        message_id=message.message_id
                    )
                    sent += 1
                    e = None
                except Exception as retry_error:
                    e = retry_error
            if e is not None:
                failed += 1
                err = str(e).lower()
                logging.warning("Broadcast failed for %s: %s", row["user_id"], e)
                if any(x in err for x in (
                    "blocked", "deactivated", "chat not found",
                    "user is deactivated", "bot was blocked",
                    "forbidden", "peer_id_invalid"
                )):
                    try:
                        await db_execute(
                            "UPDATE users SET is_blocked=1 WHERE user_id=?",
                            (row["user_id"],)
                        )
                        blocked_marked += 1
                    except Exception:
                        pass

        if i % 15 == 0 or i == total:
            try:
                await progress_msg.edit_text(
                    f"📢 <b>Рассылка жүріп жатыр...</b>\n\n"
                    f"Жіберілуде: <b>{i} / {total}</b>\n"
                    f"✅ Сәтті: {sent}\n"
                    f"❌ Қате: {failed}\n"
                    f"🚫 Блок/өшірілген: {blocked_marked}",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        # Қауіпсіз жылдамдық
        await asyncio.sleep(BROADCAST_DELAY)
        if i % BROADCAST_BATCH_SIZE == 0 and i < total:
            await asyncio.sleep(BROADCAST_BATCH_PAUSE)

    await state.clear()

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Тағы рассылка", callback_data="broadcast_again")],
            [InlineKeyboardButton(text="⬅️ Админ панелі", callback_data="admin_menu_back")]
        ]
    )

    await progress_msg.edit_text(
        f"✅ <b>Рассылка аяқталды!</b>\n\n"
        f"👥 Барлығы: {total}\n"
        f"✅ Жіберілді: {sent}\n"
        f"❌ Қате: {failed}\n"
        f"🚫 Блок деп белгіленді: {blocked_marked}",
        parse_mode="HTML",
        reply_markup=keyboard
    )


@router.callback_query(F.data == "broadcast_again")
async def broadcast_again(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return

    row = await db_execute(
        "SELECT COUNT(*) AS c FROM users WHERE is_blocked=0",
        fetchone=True
    )
    active = int(row["c"]) if row else 0
    if active == 0:
        await callback.message.answer(
            "⚠️ Белсенді қолданушы жоқ.",
            reply_markup=admin_keyboard()
        )
        await callback.answer()
        return

    await state.set_state(BroadcastState.waiting_message)
    await callback.message.answer(
        f"📢 <b>Жаңа рассылка</b>\n\n"
        f"👥 Белсенді: <b>{active}</b>\n\n"
        "Жіберілетін хабарламаны осы чатқа жіберіңіз.",
        parse_mode="HTML",
        reply_markup=admin_cancel_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "admin_menu_back")
async def admin_menu_back(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    await state.clear()
    await callback.message.answer("👑 Админ панелі", reply_markup=admin_keyboard())
    await callback.answer()


# -------------------------
# STATISTICS (+ total likes)
# -------------------------

@router.message(F.text == "📊 Статистика")
async def admin_statistics(message: Message):
    if not admin_only(message.from_user.id):
        return

    users = await db_execute("SELECT COUNT(*) AS c FROM users", fetchone=True)
    active_users = await db_execute(
        "SELECT COUNT(*) AS c FROM users WHERE is_blocked=0",
        fetchone=True
    )
    blocked_users = await db_execute(
        "SELECT COUNT(*) AS c FROM users WHERE is_blocked=1",
        fetchone=True
    )
    content = await db_execute("SELECT COUNT(*) AS c FROM content", fetchone=True)
    pending = await db_execute(
        "SELECT COUNT(*) AS c FROM pending WHERE submitted=1",
        fetchone=True
    )
    tasks = await db_execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE active=1",
        fetchone=True
    )
    channels = await db_execute(
        "SELECT COUNT(*) AS c FROM channels WHERE active=1",
        fetchone=True
    )
    total_likes = await db_execute(
        "SELECT COUNT(*) AS c FROM content_reactions WHERE reaction='like'",
        fetchone=True
    )
    total_dislikes = await db_execute(
        "SELECT COUNT(*) AS c FROM content_reactions WHERE reaction='dislike'",
        fetchone=True
    )

    await message.answer(
        "📊 <b>СТАТИСТИКА</b>\n\n"
        f"👥 Барлық қолданушы: <b>{users['c']}</b>\n"
        f"🟢 Белсенді: <b>{active_users['c']}</b>\n"
        f"🚫 Бұғатталған: <b>{blocked_users['c']}</b>\n"
        f"🎥 Контент: <b>{content['c']}</b>\n"
        f"📩 Күтілген контент: <b>{pending['c']}</b>\n"
        f"🎯 Белсенді тапсырма: <b>{tasks['c']}</b>\n"
        f"📢 Міндетті канал: <b>{channels['c']}</b>\n\n"
        f"❤️ Барлық лайк: <b>{total_likes['c']}</b>\n"
        f"👎 Барлық дизлайк: <b>{total_dislikes['c']}</b>",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


# -------------------------
# ADMIN CONTENT ADD
# -------------------------

@router.message(F.text == "➕ Контент қосу")
async def admin_add_content_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    genres = await get_genres(active_only=True)
    if not genres:
        await message.answer("❌ Алдымен жанр қосыңыз.")
        return

    builder = InlineKeyboardBuilder()
    for genre in genres:
        builder.button(
            text=f"🎭 {genre['name']} — {genre['price']} 💰",
            callback_data=f"admin_content_genre_{genre['id']}"
        )
    builder.adjust(1)

    await state.set_state(AdminContentState.waiting_genre)
    await message.answer("🎭 Контент қай жанрға қосылсын?", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("admin_content_genre_"))
async def admin_content_genre(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return

    genre_id = int(callback.data.split("_")[-1])
    genre = await get_genre(genre_id)
    if not genre:
        await callback.answer("❌ Жанр жоқ.", show_alert=True)
        return

    await state.update_data(genre_id=genre_id)
    await state.set_state(AdminContentState.waiting_file)
    await callback.message.edit_text(
        f"🎭 Жанр: <b>{genre['name']}</b>\n\n"
        "📤 Енді видео/фото/файл жіберіңіз.",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(AdminContentState.waiting_file)
async def admin_add_content(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    data = await state.get_data()
    genre_id = data.get("genre_id")
    genre = await get_genre(genre_id)
    if not genre:
        await message.answer("❌ Жанр табылмады.")
        await state.clear()
        return

    content_type = None
    file_id = None
    unique_id = None

    if message.video:
        content_type = "video"
        file_id = message.video.file_id
        unique_id = message.video.file_unique_id
    elif message.photo:
        content_type = "photo"
        file_id = message.photo[-1].file_id
        unique_id = message.photo[-1].file_unique_id
    elif message.document:
        content_type = "document"
        file_id = message.document.file_id
        unique_id = message.document.file_unique_id
    else:
        await message.answer("❌ Видео, фото немесе файл жіберіңіз.")
        return

    exists = await db_execute(
        "SELECT id FROM content WHERE file_unique_id=?",
        (unique_id,),
        fetchone=True
    )
    if exists:
        await message.answer("⚠️ Бұл контент базаға бұрын қосылған.")
        return

    await db_execute(
        """
        INSERT INTO content (content_type, file_id, file_unique_id, genre_id, added_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (content_type, file_id, unique_id, genre_id, ADMIN_ID, kz_now().isoformat())
    )

    await message.answer(
        f"✅ Контент қосылды!\n\n"
        f"🎭 Жанр: {genre['name']}\n"
        f"💰 Көру бағасы: {genre['price']} 💰",
        reply_markup=admin_keyboard()
    )


@router.message(F.text == "📩 Жіберілгендер")
async def pending_list(message: Message, bot: Bot):
    if message.from_user.id != ADMIN_ID:
        return

    rows = await db_execute(
        """
        SELECT p.*, g.name AS genre_name, g.price AS genre_price,
               u.username AS uploader_username, u.first_name AS uploader_name
        FROM pending p
        LEFT JOIN genres g ON g.id=p.genre_id
        LEFT JOIN users u ON u.user_id=p.uploader_id
        WHERE p.submitted=1
        ORDER BY p.id ASC
        """,
        fetch=True
    )
    if not rows:
        await message.answer(
            "📭 Қазір модераторға жіберілген материал жоқ.\n"
            "Жаңа материал түскен кезде осы жерде көрсетіледі.",
            reply_markup=admin_keyboard()
        )
        return

    await message.answer(
        f"📩 Күтілген материалдар: <b>{len(rows)}</b>\n"
        "Әрқайсысын төменде жеке хабарламада көресіз.",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )

    for row in rows:
        uploader_name = row["uploader_name"] or "—"
        uploader_username = f"@{row['uploader_username']}" if row["uploader_username"] else "жоқ"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="✅ Қабылдау", callback_data=f"approve_{row['id']}"),
                InlineKeyboardButton(text="❌ Қабылдамау", callback_data=f"reject_{row['id']}")
            ]]
        )
        caption = (
            "📩 <b>МОДЕРАЦИЯ КЕЗЕГІ</b>\n\n"
            f"👤 User ID: <code>{row['uploader_id']}</code>\n"
            f"👤 Аты: {uploader_name}\n"
            f"👤 Username: {uploader_username}\n"
            f"📦 Түрі: {row['content_type']}\n"
            f"🎭 Жанр: {row['genre_name'] or 'Белгісіз'}\n"
            f"💰 Жанр бағасы: {row['genre_price'] or 0} монета\n"
            f"🆔 Pending ID: {row['id']}"
        )
        try:
            if row["content_type"] == "video":
                await bot.send_video(
                    message.chat.id, row["file_id"],
                    caption=caption, reply_markup=keyboard, parse_mode="HTML"
                )
            elif row["content_type"] == "photo":
                await bot.send_photo(
                    message.chat.id, row["file_id"],
                    caption=caption, reply_markup=keyboard, parse_mode="HTML"
                )
            elif row["content_type"] == "document":
                await bot.send_document(
                    message.chat.id, row["file_id"],
                    caption=caption, reply_markup=keyboard, parse_mode="HTML"
                )
            else:
                await message.answer(
                    caption + "\n\n⚠️ Файл түрі белгісіз, жіберілмеді.",
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
        except Exception as e:
            logging.error("Pending list send error id=%s: %s", row["id"], e)
            await message.answer(
                f"⚠️ Pending #{row['id']} жіберілмеді: {e}\n"
                f"User: <code>{row['uploader_id']}</code>",
                parse_mode="HTML",
                reply_markup=keyboard
            )
        await asyncio.sleep(0.05)


# =========================================================
# ADMIN: CHANNELS
# =========================================================

@router.message(F.text == "📢 Каналдарды басқару")
async def admin_channels_menu(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    await state.clear()

    rows = await db_execute("SELECT * FROM channels ORDER BY id ASC", fetch=True)
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Канал қосу", callback_data="adminch_add")
    if rows:
        for r in rows:
            status = "🟢" if r["active"] == 1 else "🔴"
            builder.button(
                text=f"{status} {r['channel']}",
                callback_data=f"adminch_view_{r['id']}"
            )
    builder.adjust(1)
    await message.answer(
        "📢 <b>Міндетті каналдар</b>\n\n"
        "Пайдаланушы ботты пайдалану үшін осы каналдарға жазылуы керек.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "adminch_add")
async def adminch_add_start(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    await state.set_state(ChannelState.waiting_channel)
    await callback.message.edit_text(
        "➕ <b>Канал қосу</b>\n\n"
        "Канал username немесе сілтемесін жіберіңіз.\n"
        "Мысалы: <code>@mychannel</code> немесе <code>https://t.me/mychannel</code>\n\n"
        "⚠️ Бот каналға админ болуы керек!",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Болдырмау", callback_data="adminch_cancel")
        ]])
    )
    await callback.answer()


@router.message(ChannelState.waiting_channel, F.text)
async def adminch_get_channel(message: Message, state: FSMContext, bot: Bot):
    if not admin_only(message.from_user.id):
        return
    raw = message.text.strip()
    channel = normalize_channel(raw)
    if not channel:
        await message.answer("❗ Дұрыс канал енгізіңіз.")
        return

    if channel.startswith("+") or channel.startswith("joinchat/"):
        await message.answer(
            "❗ Invite-сілтеме емес, каналдың <b>@username</b> немесе "
            "<b>-100...</b> ID жіберіңіз.\n\n"
            "Жеке канал болса: ботты админ етіп, канал ID-сін "
            "(@userinfobot немесе getUpdates арқылы) алыңыз.",
            parse_mode="HTML"
        )
        return

    try:
        chat = await bot.get_chat(channel)
        if chat.username:
            channel = f"@{chat.username}"
        else:
            channel = str(chat.id)
        me = await bot.get_me()
        bot_member = await bot.get_chat_member(channel, me.id)
        bot_status = getattr(bot_member.status, "value", bot_member.status)
        if str(bot_status).lower() not in ("administrator", "creator"):
            await message.answer(
                "⚠️ Бот каналға <b>админ емес</b>.\n"
                "Канал → Админдер → ботты қосыңыз "
                "(кемінде «Participants-ты көру» құқығы).\n\n"
                "Қазір қосылмайды — алдымен админ етіңіз.",
                parse_mode="HTML"
            )
            return
    except Exception as e:
        logging.error("Channel verify failed: %s", e)
        await message.answer(
            f"❌ Каналды тексеру сәтсіз.\n\n"
            f"<code>{e}</code>\n\n"
            "Тексеріңіз:\n"
            "1) Username дұрыс па? (@channel)\n"
            "2) Бот каналға админ бе?\n"
            "3) Канал публичный ма?",
            parse_mode="HTML"
        )
        return

    exists = await db_execute(
        "SELECT id FROM channels WHERE channel=?",
        (channel,),
        fetchone=True
    )
    if exists:
        await message.answer("⚠️ Бұл канал бұрын қосылған.")
        return

    await db_execute(
        "INSERT INTO channels (channel, active, created_at) VALUES (?, 1, ?)",
        (channel, kz_now().isoformat())
    )
    await state.clear()
    await message.answer(
        f"✅ Канал қосылды: <b>{channel}</b>\n"
        "Енді жазылым тексерілуі керек.",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


@router.callback_query(F.data.startswith("adminch_view_"))
async def adminch_view(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    ch_id = int(callback.data.split("_")[2])
    row = await db_execute("SELECT * FROM channels WHERE id=?", (ch_id,), fetchone=True)
    if not row:
        await callback.answer("❌ Табылмады.", show_alert=True)
        return

    status = "🟢 Белсенді" if row["active"] == 1 else "🔴 Өшірулі"
    toggle = "🔴 Өшіру" if row["active"] == 1 else "🟢 Қосу"
    text = (
        f"📢 <b>Канал</b> (#ID {row['id']})\n\n"
        f"🔗 {row['channel']}\n"
        f"⚡ Күйі: {status}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle, callback_data=f"adminch_toggle_{row['id']}")],
        [InlineKeyboardButton(text="🗑 Жою", callback_data=f"adminch_delete_{row['id']}")],
        [InlineKeyboardButton(text="⬅️ Тізімге", callback_data="adminch_back")],
    ])
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("adminch_toggle_"))
async def adminch_toggle(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    ch_id = int(callback.data.split("_")[2])
    row = await db_execute("SELECT * FROM channels WHERE id=?", (ch_id,), fetchone=True)
    if not row:
        await callback.answer("❌ Табылмады.", show_alert=True)
        return
    new_active = 0 if row["active"] == 1 else 1
    await db_execute("UPDATE channels SET active=? WHERE id=?", (new_active, ch_id))
    await callback.answer("✅ Күйі өзгертілді!")
    row = await db_execute("SELECT * FROM channels WHERE id=?", (ch_id,), fetchone=True)
    status = "🟢 Белсенді" if row["active"] == 1 else "🔴 Өшірулі"
    toggle = "🔴 Өшіру" if row["active"] == 1 else "🟢 Қосу"
    text = (
        f"📢 <b>Канал</b> (#ID {row['id']})\n\n"
        f"🔗 {row['channel']}\n"
        f"⚡ Күйі: {status}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle, callback_data=f"adminch_toggle_{row['id']}")],
        [InlineKeyboardButton(text="🗑 Жою", callback_data=f"adminch_delete_{row['id']}")],
        [InlineKeyboardButton(text="⬅️ Тізімге", callback_data="adminch_back")],
    ])
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data.startswith("adminch_delete_"))
async def adminch_delete(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    ch_id = int(callback.data.split("_")[2])
    await db_execute("DELETE FROM channels WHERE id=?", (ch_id,))
    await callback.answer("🗑 Жойылды!")
    rows = await db_execute("SELECT * FROM channels ORDER BY id ASC", fetch=True)
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Канал қосу", callback_data="adminch_add")
    if rows:
        for r in rows:
            status = "🟢" if r["active"] == 1 else "🔴"
            builder.button(text=f"{status} {r['channel']}", callback_data=f"adminch_view_{r['id']}")
    builder.adjust(1)
    try:
        await callback.message.edit_text(
            "📢 <b>Міндетті каналдар</b>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "📢 <b>Міндетті каналдар</b>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )


@router.callback_query(F.data == "adminch_back")
async def adminch_back(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        return
    await state.clear()
    rows = await db_execute("SELECT * FROM channels ORDER BY id ASC", fetch=True)
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Канал қосу", callback_data="adminch_add")
    if rows:
        for r in rows:
            status = "🟢" if r["active"] == 1 else "🔴"
            builder.button(text=f"{status} {r['channel']}", callback_data=f"adminch_view_{r['id']}")
    builder.adjust(1)
    try:
        await callback.message.edit_text(
            "📢 <b>Міндетті каналдар</b>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "📢 <b>Міндетті каналдар</b>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data == "adminch_cancel")
async def adminch_cancel(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        return
    await state.clear()
    await callback.message.answer("❌ Тоқтатылды.", reply_markup=admin_keyboard())
    await callback.answer()


# =========================================================
# ADMIN: GENRES
# =========================================================

@router.message(F.text == "🎭 Жанрларды басқару")
async def admin_genres_menu(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    await state.clear()

    rows = await db_execute("SELECT * FROM genres ORDER BY id ASC", fetch=True)
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Жанр қосу", callback_data="admingenre_add")
    if rows:
        for r in rows:
            status = "🟢" if r["active"] == 1 else "🔴"
            builder.button(
                text=f"{status} {r['name']} — {r['price']} 💰",
                callback_data=f"admingenre_view_{r['id']}"
            )
    builder.adjust(1)
    await message.answer(
        "🎭 <b>Жанрларды басқару</b>\n\n"
        "Жанр қосу, бағасын өзгерту, қосу/өшіру.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admingenre_add")
async def admingenre_add_start(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    await state.set_state(GenreState.waiting_name)
    await callback.message.edit_text(
        "➕ <b>Жаңа жанр</b>\n\n1️⃣ Жанр атауын жазыңыз:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Болдырмау", callback_data="admingenre_cancel")
        ]])
    )
    await callback.answer()


@router.message(GenreState.waiting_name, F.text)
async def admingenre_get_name(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    name = message.text.strip()
    if not name or len(name) > 64:
        await message.answer("❗ Атау бос болмауы және 64 таңбадан аспауы керек.")
        return
    await state.update_data(name=name)
    await state.set_state(GenreState.waiting_price)
    await message.answer(
        "2️⃣ Көру бағасын (монета) санмен жазыңыз (мысалы: <code>2</code>):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Болдырмау", callback_data="admingenre_cancel")
        ]])
    )


@router.callback_query(F.data.startswith("admingenre_view_"))
async def admingenre_view(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    genre_id = int(callback.data.split("_")[2])
    row = await db_execute("SELECT * FROM genres WHERE id=?", (genre_id,), fetchone=True)
    if not row:
        await callback.answer("❌ Табылмады.", show_alert=True)
        return

    status = "🟢 Белсенді" if row["active"] == 1 else "🔴 Өшірулі"
    toggle = "🔴 Өшіру" if row["active"] == 1 else "🟢 Қосу"
    text = (
        f"🎭 <b>Жанр</b> (#ID {row['id']})\n\n"
        f"📌 Атауы: <b>{row['name']}</b>\n"
        f"💰 Бағасы: {row['price']} монета\n"
        f"⚡ Күйі: {status}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Бағаны өзгерту", callback_data=f"admingenre_price_{row['id']}")],
        [InlineKeyboardButton(text=toggle, callback_data=f"admingenre_toggle_{row['id']}")],
        [InlineKeyboardButton(text="🗑 Жою", callback_data=f"admingenre_delete_{row['id']}")],
        [InlineKeyboardButton(text="⬅️ Тізімге", callback_data="admingenre_back")],
    ])
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admingenre_price_"))
async def admingenre_price_start(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    genre_id = int(callback.data.split("_")[2])
    await state.set_state(GenreState.waiting_price)
    await state.update_data(edit_genre_id=genre_id)
    await callback.message.answer(
        "💰 Жаңа бағаны санмен жіберіңіз:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Болдырмау", callback_data="admingenre_cancel")
        ]])
    )
    await callback.answer()


@router.message(GenreState.waiting_price, F.text)
async def admingenre_price_or_create(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    data = await state.get_data()
    edit_id = data.get("edit_genre_id")

    try:
        price = int(message.text.strip())
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Тек оң сан немесе 0 енгізіңіз.")
        return

    if edit_id:
        await db_execute("UPDATE genres SET price=? WHERE id=?", (price, edit_id))
        await state.clear()
        await message.answer(
            f"✅ Баға жаңартылды: <b>{price}</b> 💰",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )
        return

    name = data.get("name")
    if not name:
        await state.clear()
        await message.answer("❌ Қате. Қайтадан бастаңыз.", reply_markup=admin_keyboard())
        return
    try:
        await db_execute(
            "INSERT INTO genres (name, price, active, created_at) VALUES (?, ?, 1, ?)",
            (name, price, kz_now().isoformat())
        )
    except Exception:
        await message.answer("❌ Мүмкін мұндай атау бар.")
        await state.clear()
        return
    await state.clear()
    await message.answer(
        f"✅ Жанр қосылды: <b>{name}</b> — {price} 💰",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


@router.callback_query(F.data.startswith("admingenre_toggle_"))
async def admingenre_toggle(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    genre_id = int(callback.data.split("_")[2])
    row = await db_execute("SELECT * FROM genres WHERE id=?", (genre_id,), fetchone=True)
    if not row:
        await callback.answer("❌ Табылмады.", show_alert=True)
        return
    new_active = 0 if row["active"] == 1 else 1
    await db_execute("UPDATE genres SET active=? WHERE id=?", (new_active, genre_id))
    await callback.answer("✅ Күйі өзгертілді!")
    row = await db_execute("SELECT * FROM genres WHERE id=?", (genre_id,), fetchone=True)
    status = "🟢 Белсенді" if row["active"] == 1 else "🔴 Өшірулі"
    toggle = "🔴 Өшіру" if row["active"] == 1 else "🟢 Қосу"
    text = (
        f"🎭 <b>Жанр</b> (#ID {row['id']})\n\n"
        f"📌 Атауы: <b>{row['name']}</b>\n"
        f"💰 Бағасы: {row['price']} монета\n"
        f"⚡ Күйі: {status}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Бағаны өзгерту", callback_data=f"admingenre_price_{row['id']}")],
        [InlineKeyboardButton(text=toggle, callback_data=f"admingenre_toggle_{row['id']}")],
        [InlineKeyboardButton(text="🗑 Жою", callback_data=f"admingenre_delete_{row['id']}")],
        [InlineKeyboardButton(text="⬅️ Тізімге", callback_data="admingenre_back")],
    ])
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data.startswith("admingenre_delete_"))
async def admingenre_delete(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    genre_id = int(callback.data.split("_")[2])
    await db_execute("DELETE FROM genres WHERE id=?", (genre_id,))
    await callback.answer("🗑 Жойылды!")
    rows = await db_execute("SELECT * FROM genres ORDER BY id ASC", fetch=True)
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Жанр қосу", callback_data="admingenre_add")
    if rows:
        for r in rows:
            status = "🟢" if r["active"] == 1 else "🔴"
            builder.button(
                text=f"{status} {r['name']} — {r['price']} 💰",
                callback_data=f"admingenre_view_{r['id']}"
            )
    builder.adjust(1)
    try:
        await callback.message.edit_text(
            "🎭 <b>Жанрларды басқару</b>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "🎭 <b>Жанрларды басқару</b>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )


@router.callback_query(F.data == "admingenre_back")
async def admingenre_back(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        return
    await state.clear()
    rows = await db_execute("SELECT * FROM genres ORDER BY id ASC", fetch=True)
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Жанр қосу", callback_data="admingenre_add")
    if rows:
        for r in rows:
            status = "🟢" if r["active"] == 1 else "🔴"
            builder.button(
                text=f"{status} {r['name']} — {r['price']} 💰",
                callback_data=f"admingenre_view_{r['id']}"
            )
    builder.adjust(1)
    try:
        await callback.message.edit_text(
            "🎭 <b>Жанрларды басқару</b>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "🎭 <b>Жанрларды басқару</b>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data == "admingenre_cancel")
async def admingenre_cancel(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        return
    await state.clear()
    await callback.message.answer("❌ Тоқтатылды.", reply_markup=admin_keyboard())
    await callback.answer()


# =========================================================
# ADMIN: REFERRAL SETTINGS
# =========================================================

@router.message(F.text == "👥 Реферал баптау")
async def admin_referral_settings(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    await state.clear()
    bonus = await get_referral_bonus()
    await state.set_state(ReferralSettingsState.waiting_bonus)
    await message.answer(
        "👥 <b>Реферал баптау</b>\n\n"
        f"Қазіргі бонус: <b>{bonus}</b> 💰\n\n"
        "Жаңа бонус мөлшерін санмен жіберіңіз:",
        parse_mode="HTML",
        reply_markup=admin_cancel_keyboard()
    )


@router.message(ReferralSettingsState.waiting_bonus, F.text)
async def admin_referral_set_bonus(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    try:
        value = int(message.text.strip())
        if value < 0:
            raise ValueError
    except ValueError:
        await message.answer("❗ Тек оң сан немесе 0 енгізіңіз.")
        return
    await set_setting("referral_bonus", str(value))
    await state.clear()
    await message.answer(
        f"✅ Реферал бонусы: <b>{value}</b> 💰",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


# =========================================================
# ADMIN: SETTINGS
# =========================================================

@router.message(F.text == "⚙️ Баптаулар")
async def admin_settings_menu(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    await state.clear()

    daily = await get_int_setting("daily_bonus", DAILY_BONUS)
    upload = await get_int_setting("upload_bonus", UPLOAD_BONUS)
    price_n, price_d, disc_from = await get_shop_prices()
    contact = await get_shop_contact()
    admin_user = await get_setting("admin_username", ADMIN_USERNAME_ENV or "")

    text = (
        "⚙️ <b>БАПТАУЛАР</b>\n\n"
        f"🌑 Авто-бонус ({BONUS_INTERVAL_HOURS} сағ): <b>{daily}</b>\n"
        f"📤 Жүктеу сыйақысы: <b>{upload}</b>\n"
        f"🛒 1 монета (қалыпты): <b>{price_n}</b> ₸\n"
        f"🛒 1 монета (скидка): <b>{price_d}</b> ₸\n"
        f"🛒 Скидка басталуы: <b>{disc_from}</b> монета\n"
        f"📞 Дүкен контакті: <b>{contact}</b>\n"
        f"👤 Admin username: <b>{admin_user or '—'}</b>\n\n"
        "Өзгерткіңіз келетін параметрді таңдаңыз:"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌑 Авто-бонус мөлшері", callback_data="settings_daily_bonus")],
        [InlineKeyboardButton(text="📤 Жүктеу сыйақысы", callback_data="settings_upload_bonus")],
        [InlineKeyboardButton(text="🛒 Қалыпты баға (₸)", callback_data="settings_coin_normal")],
        [InlineKeyboardButton(text="🛒 Скидка бағасы (₸)", callback_data="settings_coin_discount")],
        [InlineKeyboardButton(text="🛒 Скидка шегі", callback_data="settings_discount_from")],
        [InlineKeyboardButton(text="📞 Дүкен контакті", callback_data="settings_shop_contact")],
        [InlineKeyboardButton(text="👤 Admin username", callback_data="settings_admin_username")],
        [InlineKeyboardButton(text="⬅️ Админ панелі", callback_data="admin_menu_back")],
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("settings_"))
async def admin_settings_choose(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return

    key_map = {
        "settings_daily_bonus": ("daily_bonus", f"Авто-бонус мөлшері ({BONUS_INTERVAL_HOURS} сағ, монета)"),
        "settings_upload_bonus": ("upload_bonus", "Жүктеу сыйақысы (монета)"),
        "settings_coin_normal": ("coin_price_normal", "1 монета қалыпты бағасы (₸)"),
        "settings_coin_discount": ("coin_price_discount", "1 монета скидка бағасы (₸)"),
        "settings_discount_from": ("discount_from", "Скидка басталатын монета саны"),
        "settings_shop_contact": ("shop_contact", "Дүкен контакті (@username)"),
        "settings_admin_username": ("admin_username", "Admin username (@username)"),
    }
    data = key_map.get(callback.data)
    if not data:
        await callback.answer()
        return

    setting_key, title = data
    current = await get_setting(setting_key, "")
    await state.set_state(SettingsState.waiting_value)
    await state.update_data(setting_key=setting_key)
    await callback.message.answer(
        f"⚙️ <b>{title}</b>\n\n"
        f"Қазіргі мән: <code>{current}</code>\n\n"
        "Жаңа мәнді жіберіңіз:",
        parse_mode="HTML",
        reply_markup=admin_cancel_keyboard()
    )
    await callback.answer()


@router.message(SettingsState.waiting_value, F.text)
async def admin_settings_set_value(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    data = await state.get_data()
    key = data.get("setting_key")
    if not key:
        await state.clear()
        return

    value = message.text.strip()
    numeric_keys = {
        "daily_bonus", "upload_bonus", "coin_price_normal",
        "coin_price_discount", "discount_from"
    }
    if key in numeric_keys:
        try:
            num = int(value)
            if num < 0:
                raise ValueError
            value = str(num)
        except ValueError:
            await message.answer("❗ Тек оң сан немесе 0 енгізіңіз.")
            return

    if key in ("shop_contact", "admin_username"):
        if value and not value.startswith("@"):
            value = "@" + value.lstrip("@")

    await set_setting(key, value)
    await state.clear()
    await message.answer(
        f"✅ <b>{key}</b> = <code>{value}</code>",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


# =========================================================
# ADMIN: МӘЗІР БАННЕРІ
# =========================================================

async def _banner_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 GIF/Фото қою", callback_data="banner_upload")],
        [InlineKeyboardButton(text="✏️ Мәтінді өзгерту", callback_data="banner_edit_text")],
        [InlineKeyboardButton(text="👁 Қазіргісін көру", callback_data="banner_preview")],
        [
            InlineKeyboardButton(text="🗑 Суретті өшіру", callback_data="banner_clear_media"),
            InlineKeyboardButton(text="🗑 Мәтінді өшіру", callback_data="banner_clear_text"),
        ],
        [InlineKeyboardButton(text="🗑 Бәрін өшіру", callback_data="banner_clear_all")],
        [InlineKeyboardButton(text="⬅️ Админ панелі", callback_data="admin_menu_back")],
    ])


@router.message(F.text == "🖼 Мәзір баннері")
async def admin_banner_menu(message: Message, state: FSMContext, bot: Bot):
    if not admin_only(message.from_user.id):
        return
    await state.clear()

    file_id = await get_setting("menu_banner_file_id", "")
    file_type = await get_setting("menu_banner_type", "")
    caption = await get_setting("menu_banner_caption", "")
    media_status = "🟢 Қойылған" if file_id else "🔴 Жоқ"
    text_status = "🟢 Өз мәтін" if (caption and caption.strip()) else "⚪ Әдепкі мәтін"
    preview_text = (caption.strip()[:200] + "…") if caption and len(caption.strip()) > 200 else (caption.strip() if caption else "—")

    await message.answer(
        "🖼 <b>Мәзір баннері</b>\n\n"
        f"🖼 Сурет/GIF: <b>{media_status}</b> (<code>{file_type or '—'}</code>)\n"
        f"✏️ Мәтін: <b>{text_status}</b>\n\n"
        f"📝 Қазіргі мәтін:\n<code>{preview_text}</code>\n\n"
        "Сурет, мәтін — бәрін өзгертуге / өшіруге болады.",
        parse_mode="HTML",
        reply_markup=await _banner_admin_keyboard()
    )


@router.callback_query(F.data == "banner_upload")
async def banner_upload_start(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    await state.set_state(BannerState.waiting_media)
    await callback.message.answer(
        "🖼 <b>Баннер суреті</b>\n\n"
        "Енді <b>фото</b> немесе <b>GIF</b> жіберіңіз.\n"
        "Ол мәзірдің үстінде тұрады.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Болдырмау", callback_data="banner_cancel")
        ]])
    )
    await callback.answer()


@router.callback_query(F.data == "banner_edit_text")
async def banner_edit_text_start(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    current = await get_menu_caption()
    await state.set_state(BannerState.waiting_text)
    await callback.message.answer(
        "✏️ <b>Мәзір мәтіні</b> (баннер асты)\n\n"
        f"Қазіргі:\n<code>{current[:500]}</code>\n\n"
        "Жаңа мәтінді жіберіңіз.\n"
        "HTML қолдануға болады: <code>&lt;b&gt;қалың&lt;/b&gt;</code>, "
        "<code>&lt;i&gt;курсив&lt;/i&gt;</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Болдырмау", callback_data="banner_cancel")
        ]])
    )
    await callback.answer()


@router.callback_query(F.data == "banner_cancel")
async def banner_cancel(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        return
    await state.clear()
    await callback.message.answer("❌ Тоқтатылды.", reply_markup=admin_keyboard())
    await callback.answer()


@router.message(BannerState.waiting_media)
async def banner_receive_media(message: Message, state: FSMContext, bot: Bot):
    if not admin_only(message.from_user.id):
        return

    file_id = None
    file_type = None

    if message.animation:
        file_id = message.animation.file_id
        file_type = "animation"
    elif message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
    elif message.document:
        mime = (message.document.mime_type or "").lower()
        name = (message.document.file_name or "").lower()
        if "gif" in mime or name.endswith(".gif"):
            file_id = message.document.file_id
            file_type = "animation"
        elif mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp")):
            file_id = message.document.file_id
            file_type = "photo"

    if not file_id:
        await message.answer("❗ Фото немесе GIF жіберіңіз.")
        return

    await set_setting("menu_banner_file_id", file_id)
    await set_setting("menu_banner_type", file_type)
    await state.clear()

    await message.answer(
        f"✅ <b>Сурет/GIF сақталды!</b> (<code>{file_type}</code>)",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )
    await send_main_menu(bot, message.chat.id)


@router.message(BannerState.waiting_text, F.text)
async def banner_receive_text(message: Message, state: FSMContext, bot: Bot):
    if not admin_only(message.from_user.id):
        return

    text = (message.text or "").strip()
    if not text:
        await message.answer("❗ Бос мәтін болмауы керек.")
        return
    if len(text) > 1000:
        await message.answer("❗ Мәтін тым ұзын (макс. 1000 таңба).")
        return

    await set_setting("menu_banner_caption", text)
    await state.clear()
    await message.answer(
        "✅ <b>Мәтін сақталды!</b>\n\nҚалай көрінетіні:",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )
    await send_main_menu(bot, message.chat.id)


@router.callback_query(F.data == "banner_preview")
async def banner_preview(callback: CallbackQuery, bot: Bot):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    await callback.answer()
    await send_main_menu(bot, callback.from_user.id)


@router.callback_query(F.data == "banner_clear_media")
async def banner_clear_media(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    await set_setting("menu_banner_file_id", "")
    await set_setting("menu_banner_type", "")
    await callback.answer("🗑 Сурет өшірілді", show_alert=True)
    await callback.message.answer(
        "🗑 Баннер суреті өшірілді. Мәтін қалды.",
        reply_markup=admin_keyboard()
    )


@router.callback_query(F.data == "banner_clear_text")
async def banner_clear_text(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    await set_setting("menu_banner_caption", "")
    await callback.answer("🗑 Мәтін әдепкіге қайтарылды", show_alert=True)
    await callback.message.answer(
        "🗑 Арнайы мәтін өшірілді. Әдепкі мәтін қолданылады.",
        reply_markup=admin_keyboard()
    )


@router.callback_query(F.data == "banner_clear_all")
async def banner_clear_all(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("❌ Рұқсат жоқ.", show_alert=True)
        return
    await set_setting("menu_banner_file_id", "")
    await set_setting("menu_banner_type", "")
    await set_setting("menu_banner_caption", "")
    await callback.answer("🗑 Бәрі өшірілді", show_alert=True)
    await callback.message.answer(
        "🗑 Сурет пен мәтін толық өшірілді.\n"
        "Мәзір әдепкі мәтінмен (суретсіз) көрсетіледі.",
        reply_markup=admin_keyboard()
    )


# =========================================================
# HELP + FALLBACK
# =========================================================

HELP_TEXT = (
    "ℹ️ <b>Көмек / Қалай қолдану керек</b>\n\n"
    "🎥 <b>Видео көру</b> — жанр таңдап, монетаға контент көресіз\n"
    "➕ <b>Контент жіберу</b> — өз файлыңызды жіберіп сыйақы аласыз\n"
    f"💰 <b>Баланс / Бонус</b> — монета саны және авто-бонус ({BONUS_INTERVAL_HOURS} сағ сайын)\n"
    "👥 <b>Реферал</b> — достарды шақырып монета табасыз\n"
    "🎯 <b>Тапсырмалар</b> — тапсырма орындап сыйақы аласыз\n"
    "🛒 <b>Дүкен</b> — монета сатып алу\n\n"
    f"⚠️ Боттан келген видео/фото <b>{CONTENT_AUTO_DELETE_MINUTES} минуттан кейін</b> "
    "автоматты өшеді және басқаларға жіберуге болмайды.\n\n"
    "Төмендегі мәзір батырмаларын басыңыз."
)

HELP_KEYWORDS = {
    "көмек", "help", "помощ", "помощь", "не істеу", "не істеймін",
    "қалай", "калай", "start", "бастау", "меню", "мәзір", "menu",
    "бот", "не істейді", "функция", "нұсқау", "инструкция",
}


@router.message(Command("help"))
async def help_command(message: Message, bot: Bot, state: FSMContext):
    if await is_user_blocked(message.from_user.id):
        return

    await state.clear()
    await register_user(message.from_user, bot=bot)

    if message.from_user.id == ADMIN_ID:
        await message.answer(
            HELP_TEXT + "\n\n👑 Сіз админсіз — төменде админ панелі.",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )
        return

    if not await require_subscription(message, bot, message.from_user.id):
        return

    await message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=user_inline_keyboard())


@router.message(F.text)
async def fallback_text_handler(message: Message, bot: Bot, state: FSMContext):
    if await is_user_blocked(message.from_user.id):
        return

    current = await state.get_state()
    if current is not None:
        await message.answer(
            "⏳ Қазір бот сізден нақты жауап күтіп тұр.\n"
            "Көрсетілген форматта жазыңыз немесе «❌ Болдырмау» басыңыз.\n\n"
            "Басты мәзірге оралу үшін /start немесе /help жіберіңіз."
        )
        return

    await register_user(message.from_user, bot=bot)
    text = (message.text or "").strip().lower()

    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "❓ Түсінбедім.\n\n"
            "Админ панелі батырмаларын қолданыңыз немесе /help жіберіңіз.",
            reply_markup=admin_keyboard()
        )
        return

    if not await require_subscription(message, bot, message.from_user.id):
        return

    if text in HELP_KEYWORDS or any(k in text for k in ("көмек", "help", "қалай", "меню", "мәзір")):
        await message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=user_inline_keyboard())
        return

    if any(k in text for k in ("баланс", "монета", "ақша", "balance")):
        balance = await get_balance(message.from_user.id)
        await message.answer(
            f"💳 Сіздің балансыңызда: <b>{balance}</b> 💰\n\n"
            "Толық мәзір төменде:",
            parse_mode="HTML",
            reply_markup=user_inline_keyboard()
        )
        return

    if any(k in text for k in ("реферал", "шақыру", "ссылка", "сілтеме", "invite")):
        me = await bot.get_me()
        user = await get_user(message.from_user.id)
        link = f"https://t.me/{me.username}?start={message.from_user.id}"
        count = (user["referral_count"] or 0) if user else 0
        bonus = await get_referral_bonus()
        await message.answer(
            "👥 <b>Реферал</b>\n\n"
            f"💰 Бір адам: +{bonus} 💰\n"
            f"👤 Шақырғандар: {count}\n\n"
            f"🔗 <code>{link}</code>",
            parse_mode="HTML",
            reply_markup=user_inline_keyboard()
        )
        return

    await message.answer(
        "👋 Сәлем! Мен мәзір арқылы жұмыс істеймін.\n\n"
        "Төмендегі батырмаларды басыңыз немесе /help жіберіңіз.\n"
        "Егер сұрағыңыз болса: <b>көмек</b> деп жазыңыз.",
        parse_mode="HTML",
        reply_markup=user_inline_keyboard()
    )


# =========================================================
# BACKGROUND: AUTO BONUS CHECKER
# =========================================================

async def auto_bonus_loop(bot: Bot):
    """Әр 10 минут сайын 12 сағат өткен қолданушыларға бонус береді."""
    while True:
        try:
            await asyncio.sleep(600)  # 10 мин
            users = await db_execute(
                "SELECT user_id, last_bonus FROM users WHERE is_blocked=0",
                fetch=True
            )
            if not users:
                continue
            now = kz_now()
            for row in users:
                uid = row["user_id"]
                if uid == ADMIN_ID:
                    continue
                try:
                    await try_give_auto_bonus(bot, uid)
                except Exception as e:
                    logging.debug("auto_bonus user %s: %s", uid, e)
                await asyncio.sleep(0.03)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.warning("auto_bonus_loop error: %s", e)
            await asyncio.sleep(60)


# =========================================================
# MAIN
# =========================================================

async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot is starting...")

    # Фондық авто-бонус
    asyncio.create_task(auto_bonus_loop(bot))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())