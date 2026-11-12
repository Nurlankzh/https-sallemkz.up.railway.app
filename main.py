import asyncio
import html
import logging
from io import BytesIO

from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
import database
from runner import runner_manager
from templates import TEMPLATES, get_template
from ai_helper import generate_bot_code

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

dp = Dispatcher(storage=MemoryStorage())


# ============================================================
# FSM STATES
# ============================================================

class AddBotStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_token = State()


class CodeStates(StatesGroup):
    waiting_for_code = State()


class VariableStates(StatesGroup):
    waiting_for_key = State()
    waiting_for_value = State()


class FileStates(StatesGroup):
    waiting_for_file = State()


class AIStates(StatesGroup):
    waiting_for_prompt = State()


# ============================================================
# HELPERS
# ============================================================

def is_admin(user_id: int) -> bool:
    return user_id == config.ADMIN_ID


async def admin_callback(callback: types.CallbackQuery) -> bool:
    if not is_admin(callback.from_user.id):
        try:
            await callback.answer("⛔ Рұқсат жоқ.", show_alert=True)
        except Exception:
            pass
        return False
    return True


async def owned_bot(bot_id: int, user_id: int):
    bot_data = await database.get_bot(bot_id)
    if not bot_data or bot_data["user_id"] != user_id:
        return None
    return bot_data


async def clear_child_webhook(bot_data) -> bool:
    token = None
    if isinstance(bot_data, dict):
        token = bot_data.get("token") or bot_data.get("bot_token")
    if not token:
        return False
    child_bot = Bot(token=token)
    try:
        await child_bot.delete_webhook(drop_pending_updates=True)
        return True
    except Exception:
        logger.exception("Failed to clear child webhook")
        return False
    finally:
        try:
            await child_bot.session.close()
        except Exception:
            pass


async def safe_edit(message: types.Message, text: str, reply_markup=None, parse_mode=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            try:
                await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception:
                pass
    except Exception:
        try:
            await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            pass


async def send_long_message(message: types.Message, text: str, parse_mode=None):
    limit = 3900
    if len(text) <= limit:
        await message.answer(text, parse_mode=parse_mode)
        return
    for i in range(0, len(text), limit):
        chunk = text[i : i + limit]
        try:
            await message.answer(chunk, parse_mode=parse_mode)
        except Exception:
            await message.answer(chunk)


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Жаңа бот қосу", callback_data="add_bot")],
            [InlineKeyboardButton(text="📋 Боттар тізімі", callback_data="bots")],
            [InlineKeyboardButton(text="📦 Шаблондар", callback_data="templates")],
        ]
    )


def manage_keyboard(bot_id: int, running: bool):
    if running:
        start_btn = InlineKeyboardButton(text="🟢 Іске қосылған", callback_data=f"noop_{bot_id}")
    else:
        start_btn = InlineKeyboardButton(text="🚀 Start", callback_data=f"start_{bot_id}")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [start_btn, InlineKeyboardButton(text="🛑 Stop", callback_data=f"stop_{bot_id}")],
            [InlineKeyboardButton(text="🔄 Restart", callback_data=f"restart_{bot_id}")],
            [
                InlineKeyboardButton(text="📝 Код", callback_data=f"code_{bot_id}"),
                InlineKeyboardButton(text="🤖 AI Генератор", callback_data=f"aigen_{bot_id}"),
            ],
            [
                InlineKeyboardButton(text="📦 Version", callback_data=f"versions_{bot_id}"),
                InlineKeyboardButton(text="🔐 Variables", callback_data=f"vars_{bot_id}"),
            ],
            [InlineKeyboardButton(text="📁 Файлдар", callback_data=f"files_{bot_id}")],
            [InlineKeyboardButton(text="📜 Logs", callback_data=f"logs_{bot_id}")],
            [InlineKeyboardButton(text="🗑 Ботты өшіру", callback_data=f"delete_{bot_id}")],
            [
                InlineKeyboardButton(text="⬅️ Артқа", callback_data="bots"),
                InlineKeyboardButton(text="🏠 Басты мәзір", callback_data="home"),
            ],
        ]
    )


# ============================================================
# START / HOME / CANCEL
# ============================================================

@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Бұл конструкторға кіруге рұқсатыңыз жоқ.")
        return
    await message.answer(
        "🛠 <b>Telegram Bot Constructor</b>\
\
"
        "Бірнеше Telegram ботты осы жерден басқаруға болады.\
\
"
        "✨ <b>Жаңа мүмкіндіктер:</b>\
"
        "• 🤖 AI код генераторы\
"
        "• 📦 Дайын шаблондар\
"
        "• 🔄 Авто-рестарт\
"
        "• 📁 Қосымша файлдар\
\
"
        "➕ Бот қосыңыз немесе 📋 боттарыңызды басқарыңыз.",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "home")
async def home_handler(callback: types.CallbackQuery, state: FSMContext):
    if not await admin_callback(callback):
        return
    await state.clear()
    await callback.answer()
    await safe_edit(
        callback.message,
        "🛠 <b>Telegram Bot Constructor</b>\
\
Қажетті әрекетті таңдаңыз:",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


@dp.message(Command("cancel"))
@dp.message(F.text == "/cancel")
async def cancel_handler(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("❌ Әрекет тоқтатылды.", reply_markup=main_keyboard())


# ============================================================
# TEMPLATES
# ============================================================

@dp.callback_query(F.data == "templates")
async def templates_menu(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    await callback.answer()

    keyboard = []
    for key, t in TEMPLATES.items():
        keyboard.append([
            InlineKeyboardButton(
                text=f"📦 {t['name']}",
                callback_data=f"usetpl_{key}",
            )
        ])
    keyboard.append([InlineKeyboardButton(text="🏠 Басты мәзір", callback_data="home")])

    text = "📦 <b>Дайын шаблондар</b>\
\
"
    for key, t in TEMPLATES.items():
        text += f"<b>{t['name']}</b>\
{t['description']}\
\
"

    await safe_edit(
        callback.message,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("usetpl_"))
async def use_template(callback: types.CallbackQuery, state: FSMContext):
    if not await admin_callback(callback):
        return
    await callback.answer()

    key = callback.data.replace("usetpl_", "", 1)
    tpl = get_template(key)
    if not tpl:
        await callback.message.answer("❌ Шаблон табылмады.")
        return

    await state.clear()
    await state.update_data(pending_template_code=tpl["code"], pending_template_name=tpl["name"])
    await state.set_state(AddBotStates.waiting_for_name)

    await callback.message.answer(
        f"📦 Шаблон таңдалды: <b>{html.escape(tpl['name'])}</b>\
\
"
        "Енді боттың атауын енгізіңіз:\
\
❌ /cancel",
        parse_mode="HTML",
    )


# ============================================================
# ADD BOT
# ============================================================

@dp.callback_query(F.data == "add_bot")
async def add_bot_handler(callback: types.CallbackQuery, state: FSMContext):
    if not await admin_callback(callback):
        return
    await callback.answer()
    await state.set_state(AddBotStates.waiting_for_name)
    await safe_edit(
        callback.message,
        "➕ <b>Жаңа бот қосу</b>\
\
"
        "1️⃣ Боттың атауын енгізіңіз.\
\
"
        "Мысалы: <code>My Test Bot</code>\
\
❌ /cancel",
        parse_mode="HTML",
    )


@dp.message(AddBotStates.waiting_for_name)
async def receive_bot_name(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("❌ Атауы бос болмауы керек.")
        return
    if len(name) > 100:
        await message.answer("❌ Атау 100 символдан аспауы керек.")
        return

    await state.update_data(bot_name=name)
    await state.set_state(AddBotStates.waiting_for_token)
    await message.answer(
        "🔐 Енді <b>child bot токенін</b> жіберіңіз.\
\
"
        "Токенді @BotFather-ден алыңыз.\
\
"
        "⚠️ Master Constructor токенін енгізбеңіз.",
        parse_mode="HTML",
    )


@dp.message(AddBotStates.waiting_for_token)
async def receive_bot_token(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    token = (message.text or "").strip()
    if not token or ":" not in token:
        await message.answer("❌ Токен форматы дұрыс емес.")
        return
    if token == config.BOT_TOKEN:
        await message.answer("❌ Constructor токенін child bot ретінде қолдануға болмайды.")
        return

    try:
        test_bot = Bot(token=token)
        bot_info = await test_bot.get_me()
        await test_bot.session.close()
    except Exception as error:
        await message.answer(
            f"❌ Токен жарамсыз.\
\
<code>{html.escape(str(error)[:1500])}</code>",
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    bot_name = data.get("bot_name", bot_info.username or "Bot")
    template_code = data.get("pending_template_code")

    bot_id = await database.add_bot(message.from_user.id, bot_name, token)

    version_text = ""
    if template_code:
        version = await database.save_code_version(bot_id, template_code)
        version_text = f"\
📦 Шаблон коды сақталды: <b>v{version}</b>"

    await state.clear()

    await message.answer(
        "✅ <b>Бот қосылды!</b>\
\
"
        f"📛 Атауы: <b>{html.escape(bot_name)}</b>\
"
        f"🤖 Username: @{html.escape(bot_info.username or 'unknown')}\
"
        f"🆔 ID: <code>{bot_id}</code>\
"
        f"🔴 Күйі: Тоқтатылды{version_text}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🤖 Ботты басқару", callback_data=f"manage_{bot_id}")],
                [InlineKeyboardButton(text="📋 Боттар", callback_data="bots")],
            ]
        ),
        parse_mode="HTML",
    )


# ============================================================
# BOT LIST + MANAGE
# ============================================================

@dp.callback_query(F.data == "bots")
async def bots_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    await callback.answer()

    bots = await database.get_user_bots(callback.from_user.id)
    if not bots:
        await safe_edit(
            callback.message,
            "📋 <b>Боттар тізімі</b>\
\
Әзірге бот жоқ.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Бот қосу", callback_data="add_bot")],
                    [InlineKeyboardButton(text="🏠 Басты мәзір", callback_data="home")],
                ]
            ),
            parse_mode="HTML",
        )
        return

    keyboard = []
    for bot_data in bots:
        if runner_manager.is_running(bot_data["id"]):
            icon = "🟢"
        elif bot_data["status"] == "crashed":
            icon = "🔴"
        else:
            icon = "🔴"
        keyboard.append([
            InlineKeyboardButton(
                text=f"{icon} {bot_data['bot_name']} #{bot_data['id']}",
                callback_data=f"manage_{bot_data['id']}",
            )
        ])

    keyboard.extend([
        [InlineKeyboardButton(text="➕ Жаңа бот", callback_data="add_bot")],
        [
            InlineKeyboardButton(text="🔄 Жаңарту", callback_data="bots"),
            InlineKeyboardButton(text="🏠 Басты мәзір", callback_data="home"),
        ],
    ])

    await safe_edit(
        callback.message,
        "📋 <b>Боттар тізімі</b>\
\
Басқару үшін ботты таңдаңыз:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("manage_"))
async def manage_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    await callback.answer()

    try:
        bot_id = int(callback.data.split("_", 1)[1])
    except Exception:
        return

    bot_data = await owned_bot(bot_id, callback.from_user.id)
    if not bot_data:
        await callback.message.answer("⛔ Бот табылмады.")
        return

    running = runner_manager.is_running(bot_id)
    if running:
        status = "🟢 Жұмыс істеп тұр"
    elif bot_data["status"] == "crashed":
        status = "🔴 Қате арқылы тоқтаған"
    else:
        status = "🔴 Тоқтатылды"

    process_info = await runner_manager.get_process_info(bot_id)
    pid_text = ""
    if process_info and process_info.get("running"):
        pid_text = f"\
🆔 PID: <code>{process_info['pid']}</code>"

    await safe_edit(
        callback.message,
        "🤖 <b>Ботты басқару</b>\
\
"
        f"📛 Атауы: <b>{html.escape(bot_data['bot_name'])}</b>\
"
        f"🆔 ID: <code>{bot_id}</code>\
"
        f"📊 Күйі: {status}{pid_text}\
\
"
        "Қажетті әрекетті таңдаңыз:",
        reply_markup=manage_keyboard(bot_id, running),
        parse_mode="HTML",
    )


# ============================================================
# START / STOP / RESTART
# ============================================================

@dp.callback_query(F.data.startswith("start_"))
async def start_child_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    try:
        await callback.answer("🚀 Іске қосылуда...")
    except Exception:
        pass

    try:
        bot_id = int(callback.data.split("_", 1)[1])
    except Exception:
        await callback.message.answer("❌ Бот ID дұрыс емес.")
        return

    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        await callback.message.answer("⛔ Бот табылмады.")
        return

    await clear_child_webhook(data)
    success, result = await runner_manager.start_sub_bot(bot_id)
    await callback.message.answer(result)


@dp.callback_query(F.data.startswith("stop_"))
async def stop_child_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    await callback.answer("🛑 Тоқтатылуда...")

    try:
        bot_id = int(callback.data.split("_", 1)[1])
    except Exception:
        return

    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        await callback.message.answer("⛔ Бот табылмады.")
        return

    success, result = await runner_manager.stop_sub_bot(bot_id)
    await callback.message.answer(result)


@dp.callback_query(F.data.startswith("restart_"))
async def restart_child_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    await callback.answer("🔄 Restart...")

    try:
        bot_id = int(callback.data.split("_", 1)[1])
    except Exception:
        return

    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        await callback.message.answer("⛔ Бот табылмады.")
        return

    await clear_child_webhook(data)
    success, result = await runner_manager.restart_sub_bot(bot_id)
    await callback.message.answer(result)


# ============================================================
# AI CODE GENERATOR
# ============================================================

@dp.callback_query(F.data.startswith("aigen_"))
async def ai_gen_start(callback: types.CallbackQuery, state: FSMContext):
    if not await admin_callback(callback):
        return
    await callback.answer()

    try:
        bot_id = int(callback.data.split("_", 1)[1])
    except Exception:
        return

    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        await callback.message.answer("⛔ Бот табылмады.")
        return

    await state.clear()
    await state.update_data(ai_bot_id=bot_id)
    await state.set_state(AIStates.waiting_for_prompt)

    status = "✅ API кілті бар" if config.AI_API_KEY else "⚠️ API кілті жоқ (AI_API_KEY)"
    await callback.message.answer(
        f"🤖 <b>AI Код Генератор</b>\
\
"
        f"Статус: {status}\
\
"
        "Бот не істеуі керек екенін <b>сипаттап</b> жазыңыз.\
\
"
        "Мысал:\
"
        "<code>Пайдаланушыға сәлемдесіп, оның атын сұрап, кейін күнделікті мотивация жіберетін бот</code>\
\
"
        "❌ /cancel",
        parse_mode="HTML",
    )


@dp.message(AIStates.waiting_for_prompt)
async def ai_gen_receive(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    prompt = (message.text or "").strip()
    if not prompt or len(prompt) < 10:
        await message.answer("❌ Сипаттама тым қысқа. Толығырақ жазыңыз.")
        return

    data = await state.get_data()
    bot_id = data.get("ai_bot_id")
    if not bot_id:
        await state.clear()
        return

    wait_msg = await message.answer("🤖 AI код генерациялап жатыр... Күте тұрыңыз (15–60 сек).")

    success, result = await generate_bot_code(prompt)
    await state.clear()

    if not success:
        await wait_msg.edit_text(result, parse_mode="HTML")
        return

    version = await database.save_code_version(bot_id, result)

    await wait_msg.edit_text(
        f"✅ <b>AI код генерацияланды және сақталды!</b>\
\
"
        f"🤖 Bot ID: <code>{bot_id}</code>\
"
        f"📦 Version: <b>v{version}</b>\
\
"
        "🚀 Start басып іске қосыңыз.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Start", callback_data=f"start_{bot_id}")],
                [InlineKeyboardButton(text="📝 Кодты көру", callback_data=f"viewver_{bot_id}_{version}")],
                [InlineKeyboardButton(text="🤖 Басқару", callback_data=f"manage_{bot_id}")],
            ]
        ),
        parse_mode="HTML",
    )


# ============================================================
# CODE (text / file)
# ============================================================

@dp.callback_query(F.data.startswith("code_"))
async def code_menu_handler(callback: types.CallbackQuery, state: FSMContext):
    if not await admin_callback(callback):
        return
    await callback.answer()

    try:
        bot_id = int(callback.data.split("_", 1)[1])
    except Exception:
        return

    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        await callback.message.answer("⛔ Бот табылмады.")
        return

    await state.clear()
    await state.update_data(code_bot_id=bot_id)
    await state.set_state(CodeStates.waiting_for_code)

    await callback.message.answer(
        "📝 <b>Python код енгізу</b>\
\
"
        "1️⃣ Кодты хабарлама ретінде жіберіңіз\
"
        "2️⃣ Немесе <code>.py</code> файл жіберіңіз\
\
"
        "Код сақталғанда жаңа Version жасалады.\
\
❌ /cancel",
        parse_mode="HTML",
    )


@dp.message(CodeStates.waiting_for_code, F.text)
async def receive_code_text(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    code = message.text or ""
    data = await state.get_data()
    bot_id = data.get("code_bot_id")
    if not bot_id:
        await state.clear()
        return

    if len(code.encode("utf-8")) > config.MAX_CODE_SIZE:
        await message.answer("❌ Код тым үлкен.")
        return

    version = await database.save_code_version(bot_id, code)
    await state.clear()

    await message.answer(
        f"✅ <b>Код сақталды!</b>\
\
🤖 Bot ID: <code>{bot_id}</code>\
📦 Version: <b>v{version}</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Start", callback_data=f"start_{bot_id}")],
                [InlineKeyboardButton(text="📦 Version", callback_data=f"versions_{bot_id}")],
                [InlineKeyboardButton(text="🤖 Басқару", callback_data=f"manage_{bot_id}")],
            ]
        ),
        parse_mode="HTML",
    )


@dp.message(CodeStates.waiting_for_code, F.document)
async def receive_code_file(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    document = message.document
    if not document:
        return

    filename = document.file_name or ""
    if not filename.lower().endswith(".py"):
        await message.answer("❌ Тек <code>.py</code> файл қабылданады.", parse_mode="HTML")
        return

    if document.file_size and document.file_size > config.MAX_CODE_SIZE:
        await message.answer("❌ Файл тым үлкен.")
        return

    data = await state.get_data()
    bot_id = data.get("code_bot_id")
    if not bot_id:
        await state.clear()
        return

    try:
        buffer = BytesIO()
        await message.bot.download(document, destination=buffer)
        raw = buffer.getvalue()

        try:
            code = raw.decode("utf-8")
        except UnicodeDecodeError:
            code = raw.decode("utf-8-sig")

        if not code.strip():
            await message.answer("❌ Файл бос.")
            return

        version = await database.save_code_version(bot_id, code)
        await state.clear()

        await message.answer(
            f"✅ <b>Файл сақталды!</b>\
\
"
            f"📄 {html.escape(filename)}\
"
            f"📦 Version: <b>v{version}</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🚀 Start", callback_data=f"start_{bot_id}")],
                    [InlineKeyboardButton(text="🤖 Басқару", callback_data=f"manage_{bot_id}")],
                ]
            ),
            parse_mode="HTML",
        )
    except Exception as error:
        logger.exception("FILE ERROR")
        await message.answer(f"❌ Қате: <code>{html.escape(str(error)[:2000])}</code>", parse_mode="HTML")


# ============================================================
# VERSIONS
# ============================================================

@dp.callback_query(F.data.startswith("versions_"))
async def versions_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    await callback.answer()

    try:
        bot_id = int(callback.data.split("_", 1)[1])
    except Exception:
        return

    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        return

    versions = await database.get_code_versions(bot_id)
    if not versions:
        await safe_edit(
            callback.message,
            "📦 <b>Versions</b>\
\
❌ Әзірге код жоқ.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📝 Код қосу", callback_data=f"code_{bot_id}")],
                    [InlineKeyboardButton(text="⬅️ Артқа", callback_data=f"manage_{bot_id}")],
                ]
            ),
            parse_mode="HTML",
        )
        return

    keyboard = []
    for v in versions:
        n = v["version"]
        keyboard.append([
            InlineKeyboardButton(text=f"👁 v{n}", callback_data=f"viewver_{bot_id}_{n}"),
            InlineKeyboardButton(text=f"🗑 v{n}", callback_data=f"delver_{bot_id}_{n}"),
        ])
    keyboard.append([InlineKeyboardButton(text="⬅️ Артқа", callback_data=f"manage_{bot_id}")])

    await safe_edit(
        callback.message,
        f"📦 <b>Код Versions</b>\
\
🤖 Bot ID: <code>{bot_id}</code>\
📊 Барлығы: <b>{len(versions)}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("viewver_"))
async def view_version_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    await callback.answer()

    try:
        parts = callback.data.split("_")
        bot_id, version = int(parts[1]), int(parts[2])
    except Exception:
        return

    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        return

    version_data = await database.get_code_version(bot_id, version)
    if not version_data:
        await callback.message.answer("❌ Version табылмады.")
        return

    code = version_data["code"]
    header = f"📦 <b>v{version}</b> | {len(code)} символ\
\
"
    preview = html.escape(code[:3500])
    await send_long_message(callback.message, header + f"<pre>{preview}</pre>", parse_mode="HTML")
    await callback.message.answer(
        "Version:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Өшіру", callback_data=f"delver_{bot_id}_{version}")],
                [InlineKeyboardButton(text="⬅️ Артқа", callback_data=f"versions_{bot_id}")],
            ]
        ),
    )


@dp.callback_query(F.data.startswith("delver_"))
async def delete_version_confirm(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    await callback.answer()
    try:
        parts = callback.data.split("_")
        bot_id, version = int(parts[1]), int(parts[2])
    except Exception:
        return

    await callback.message.answer(
        f"⚠️ Version v{version} өшірілсін бе?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Иә", callback_data=f"confirmdelver_{bot_id}_{version}")],
                [InlineKeyboardButton(text="❌ Жоқ", callback_data=f"versions_{bot_id}")],
            ]
        ),
    )


@dp.callback_query(F.data.startswith("confirmdelver_"))
async def delete_version_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    await callback.answer("🗑...")
    try:
        parts = callback.data.split("_")
        bot_id, version = int(parts[1]), int(parts[2])
    except Exception:
        return

    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        return

    deleted = await database.delete_code_version(bot_id, version)
    await callback.message.answer("✅ Өшірілді." if deleted else "❌ Табылмады.")
    callback.data = f"versions_{bot_id}"
    await versions_handler(callback)


# ============================================================
# VARIABLES
# ============================================================

@dp.callback_query(F.data.startswith("vars_"))
async def variables_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    await callback.answer()

    try:
        bot_id = int(callback.data.split("_", 1)[1])
    except Exception:
        return

    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        return

    variables = await database.get_env_vars(bot_id)
    text = f"🔐 <b>Variables</b>\
\
🤖 Bot ID: <code>{bot_id}</code>\
\
"
    if not variables:
        text += "❌ Variable жоқ.\
"
    else:
        for key, value in variables.items():
            safe = html.escape(str(value))
            if len(safe) > 80:
                safe = safe[:80] + "..."
            text += f"🔑 <code>{html.escape(key)}</code>\
💾 <code>{safe}</code>\
\
"

    keyboard = [[InlineKeyboardButton(text=f"🗑 {k}", callback_data=f"delvar_{bot_id}_{k}")] for k in variables]
    keyboard.append([InlineKeyboardButton(text="➕ Variable қосу", callback_data=f"addvar_{bot_id}")])
    keyboard.append([InlineKeyboardButton(text="⬅️ Артқа", callback_data=f"manage_{bot_id}")])

    await safe_edit(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")


@dp.callback_query(F.data.startswith("addvar_"))
async def add_variable_handler(callback: types.CallbackQuery, state: FSMContext):
    if not await admin_callback(callback):
        return
    await callback.answer()
    try:
        bot_id = int(callback.data.split("_", 1)[1])
    except Exception:
        return
    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        return
    await state.clear()
    await state.update_data(variable_bot_id=bot_id)
    await state.set_state(VariableStates.waiting_for_key)
    await callback.message.answer("🔐 Variable атауын енгізіңіз (мысалы: <code>API_KEY</code>):", parse_mode="HTML")


@dp.message(VariableStates.waiting_for_key)
async def receive_variable_key(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    key = (message.text or "").strip()
    if not key or len(key) > 100:
        await message.answer("❌ Key дұрыс емес.")
        return
    if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_" for c in key):
        await message.answer("❌ Тек A-Z, a-z, 0-9, _ рұқсат.")
        return
    await state.update_data(variable_key=key)
    await state.set_state(VariableStates.waiting_for_value)
    await message.answer(f"🔑 <code>{html.escape(key)}</code>\
\
Енді Value енгізіңіз:", parse_mode="HTML")


@dp.message(VariableStates.waiting_for_value)
async def receive_variable_value(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    value = message.text or ""
    data = await state.get_data()
    bot_id = data.get("variable_bot_id")
    key = data.get("variable_key")
    if not bot_id or not key:
        await state.clear()
        return
    await database.set_env_var(bot_id, key, value)
    await state.clear()
    await message.answer(
        f"✅ Variable сақталды: <code>{html.escape(key)}</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔐 Variables", callback_data=f"vars_{bot_id}")]]
        ),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("delvar_"))
async def delete_variable_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    try:
        parts = callback.data.split("_", 2)
        bot_id, key = int(parts[1]), parts[2]
    except Exception:
        await callback.answer("❌ Қате", show_alert=True)
        return
    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        return
    await database.delete_env_var(bot_id, key)
    await callback.answer("🗑 Өшірілді")
    await variables_handler(callback)


# ============================================================
# FILES
# ============================================================

@dp.callback_query(F.data.startswith("files_"))
async def files_handler(callback: types.CallbackQuery, state: FSMContext = None):
    if not await admin_callback(callback):
        return
    await callback.answer()
    try:
        bot_id = int(callback.data.split("_", 1)[1])
    except Exception:
        return
    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        return

    files = await database.get_bot_files(bot_id)
    text = f"📁 <b>Файлдар</b>\
\
🤖 Bot ID: <code>{bot_id}</code>\
\
"
    if not files:
        text += "❌ Файл жоқ.\
"
    else:
        for f in files:
            text += f"📄 <code>{html.escape(f['filename'])}</code>\
"

    keyboard = [[InlineKeyboardButton(text=f"🗑 {f['filename']}", callback_data=f"delfile_{bot_id}_{f['filename']}")] for f in files]
    keyboard.append([InlineKeyboardButton(text="➕ Файл жүктеу", callback_data=f"addfile_{bot_id}")])
    keyboard.append([InlineKeyboardButton(text="⬅️ Артқа", callback_data=f"manage_{bot_id}")])

    await safe_edit(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")


@dp.callback_query(F.data.startswith("addfile_"))
async def add_file_handler(callback: types.CallbackQuery, state: FSMContext):
    if not await admin_callback(callback):
        return
    await callback.answer()
    try:
        bot_id = int(callback.data.split("_", 1)[1])
    except Exception:
        return
    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        return
    await state.clear()
    await state.update_data(file_bot_id=bot_id)
    await state.set_state(FileStates.waiting_for_file)
    await callback.message.answer("📁 Файлды жіберіңіз (.json, .txt, .db т.б.)\
\
❌ /cancel")


@dp.message(FileStates.waiting_for_file, F.document)
async def receive_extra_file(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    document = message.document
    data = await state.get_data()
    bot_id = data.get("file_bot_id")
    if not bot_id or not document:
        return
    if document.file_size and document.file_size > 5 * 1024 * 1024:
        await message.answer("❌ Максимум 5 MB.")
        return
    try:
        buffer = BytesIO()
        await message.bot.download(document, destination=buffer)
        content = buffer.getvalue()
        filename = document.file_name or "file"
        await database.save_bot_file(bot_id, filename, content)
        await state.clear()
        await message.answer(
            f"✅ Файл сақталды: <code>{html.escape(filename)}</code>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="📁 Файлдар", callback_data=f"files_{bot_id}")]]
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(f"❌ Қате: {e}")


@dp.callback_query(F.data.startswith("delfile_"))
async def delete_file_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    try:
        parts = callback.data.split("_", 2)
        bot_id, filename = int(parts[1]), parts[2]
    except Exception:
        return
    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        return
    await database.delete_bot_file(bot_id, filename)
    await callback.answer("🗑 Өшірілді")
    await files_handler(callback)


# ============================================================
# LOGS
# ============================================================

@dp.callback_query(F.data.startswith("logs_"))
async def logs_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    await callback.answer()
    try:
        bot_id = int(callback.data.split("_", 1)[1])
    except Exception:
        return
    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        return

    logs = await database.get_logs(bot_id, limit=80)
    if not logs:
        text = "📜 <b>Logs</b>\
\
Логтар жоқ."
    else:
        lines = ["📜 <b>Logs</b>", ""]
        for log in logs:
            lines.append(
                f"<b>{html.escape(str(log['level']))}</b> [{html.escape(str(log['created_at']))}]\
"
                f"{html.escape(str(log['message']))}"
            )
        text = "\
".join(lines)
    if len(text) > 3800:
        text = text[-3800:]

    await safe_edit(
        callback.message,
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Жаңарту", callback_data=f"logs_{bot_id}")],
                [InlineKeyboardButton(text="⬅️ Артқа", callback_data=f"manage_{bot_id}")],
            ]
        ),
        parse_mode="HTML",
    )


# ============================================================
# DELETE BOT
# ============================================================

@dp.callback_query(F.data.startswith("delete_"))
async def delete_bot_confirm_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    await callback.answer()
    try:
        bot_id = int(callback.data.split("_", 1)[1])
    except Exception:
        return
    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        return
    await callback.message.answer(
        "⚠️ <b>Ботты толық өшіру керек пе?</b>\
\
"
        "• Бот тоқтайды\
• Барлық версия, variable, файл, лог өшеді",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Иә, өшіру", callback_data=f"confirmdelete_{bot_id}")],
                [InlineKeyboardButton(text="❌ Жоқ", callback_data=f"manage_{bot_id}")],
            ]
        ),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("confirmdelete_"))
async def delete_bot_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    await callback.answer("🗑...")
    try:
        bot_id = int(callback.data.split("_", 1)[1])
    except Exception:
        return
    data = await owned_bot(bot_id, callback.from_user.id)
    if not data:
        return
    try:
        if runner_manager.is_running(bot_id):
            await runner_manager.stop_sub_bot(bot_id)
        await runner_manager.delete_workspace(bot_id)
        await database.delete_bot(bot_id)
        await callback.message.answer(
            "✅ Бот толық өшірілді.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📋 Боттар", callback_data="bots")],
                    [InlineKeyboardButton(text="🏠 Басты мәзір", callback_data="home")],
                ]
            ),
        )
    except Exception as error:
        logger.exception("DELETE ERROR")
        await callback.message.answer(f"❌ Қате: <code>{html.escape(str(error)[:2000])}</code>", parse_mode="HTML")


# ============================================================
# NOOP + UNKNOWN
# ============================================================

@dp.callback_query(F.data.startswith("noop_"))
async def noop_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    await callback.answer("🟢 Бот жұмыс істеп тұр.")


@dp.callback_query()
async def unknown_callback_handler(callback: types.CallbackQuery):
    if not await admin_callback(callback):
        return
    try:
        await callback.answer("⚠️ Батырма жарамсыз.", show_alert=True)
    except Exception:
        pass


# ============================================================
# STARTUP
# ============================================================

async def main():
    print("========================================")
    print("🛠 TELEGRAM BOT CONSTRUCTOR")
    print("========================================")
    await database.init_db()
    print("✅ Database дайын")
    print("🚀 Master bot іске қосылуда...")

    bot = Bot(token=config.BOT_TOKEN)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, skip_updates=True)
    finally:
        print("🛑 Тоқтатылуда...")
        try:
            await runner_manager.stop_all()
        except Exception as e:
            print(f"STOP ALL ERROR: {e}")
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
