# -*- coding: utf-8 -*-
"""
Хендлеры тренажёра для aiogram и интеграция в бота.
 
Подключение из bot.py (одна строка, ДО объявления обычных хендлеров, чтобы
тренажёр имел приоритет над консультантом при активной сессии):
 
    from trainer import register_trainer
    register_trainer(dp, bot, client, is_allowed)
 
Управление — постоянной нижней клавиатурой (ReplyKeyboard): «🎯 Новый клиент»,
«📊 Статистика», «🚪 Выйти». Она не уплывает вверх, поэтому стартовать заново
можно в любой момент, в т.ч. сразу после статистики.
"""
 
import asyncio
 
from aiogram import F
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
 
from . import engine
from . import stats
 
# user_id -> сессия тренировки
# {"scenario": {...}, "transcript": [(role, text)...], "turns": int}
SESSIONS = {}
 
# Метки постоянных нижних кнопок
BTN_NEW = "🎯 Новый клиент"
BTN_STATS = "📊 Статистика"
BTN_EXIT = "🚪 Выйти"
 
 
def trainer_reply_kb():
    """Постоянная нижняя клавиатура тренажёра (не исчезает после сообщений)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NEW)],
            [KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_EXIT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Пиши сообщение клиенту…",
    )
 
 
def _in_session(message: Message) -> bool:
    return message.from_user.id in SESSIONS
 
 
def _start_session(user_id):
    scenario = engine.new_scenario()
    session = {"scenario": scenario, "transcript": [], "turns": 0}
    SESSIONS[user_id] = session
    return session
 
 
def register_trainer(dp, bot, client, is_allowed=None):
    """Регистрирует все хендлеры тренажёра. is_allowed(user_id)->bool — опционально."""
 
    stats.init_db()
 
    def allowed(uid):
        return True if is_allowed is None else is_allowed(uid)
 
    # ---------- Общие сценарии (переиспользуются кнопками и колбэками) ----------
 
    async def do_menu(message: Message, uid):
        await message.answer(
            "🎯 *Тренажёр по продажам*\n\n"
            "Тебе выпадет случайный клиент — со скрытым психотипом, ролью и запросом. "
            "Он пишет первым, ведёт себя как реальный «сложный» лид: сам не знает, чего хочет, "
            "просит примеры, прячет бюджет, возражает. Продай его по смыслу (Гребенюк), а не по скрипту.\n\n"
            "Управляй кнопками внизу 👇",
            parse_mode="Markdown",
            reply_markup=trainer_reply_kb(),
        )
 
    async def do_start(message: Message, uid):
        session = _start_session(uid)
        scenario = session["scenario"]
        await message.answer(
            engine.scenario_intro(scenario),
            parse_mode="Markdown",
            reply_markup=trainer_reply_kb(),
        )
        # Клиент пишет первым
        await bot.send_chat_action(uid, "typing")
        try:
            loop = asyncio.get_event_loop()
            opening = await loop.run_in_executor(None, engine.opening_message, client, scenario)
        except Exception:
            opening = scenario["request"]
        session["transcript"].append(("buyer", opening))
        await message.answer(f"💬 {opening}")
 
    async def do_stats(message: Message, uid):
        await message.answer(stats.report_for_user(uid), parse_mode="Markdown")
 
    async def do_exit(message: Message, uid):
        SESSIONS.pop(uid, None)
        await message.answer(
            "🚪 Вышел из тренажёра. Скидывай скрины переписки — разберу по Гребенюку.",
            reply_markup=ReplyKeyboardRemove(),
        )
 
    # ---------- Вход в тренажёр (inline-кнопка «🎯 Тренажёр» из консультанта) ----------
 
    @dp.callback_query(F.data == "trainer_menu")
    async def open_menu_cb(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return
        await do_menu(callback.message, callback.from_user.id)
        await callback.answer()
 
    # Старые inline-колбэки (на случай кнопок в истории чата) — ведут в те же сценарии
    @dp.callback_query(F.data == "trainer_start")
    async def start_cb(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return
        await do_start(callback.message, callback.from_user.id)
        await callback.answer()
 
    @dp.callback_query(F.data == "trainer_stats")
    async def stats_cb(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return
        await do_stats(callback.message, callback.from_user.id)
        await callback.answer()
 
    @dp.callback_query(F.data == "trainer_exit")
    async def exit_cb(callback: CallbackQuery):
        await do_exit(callback.message, callback.from_user.id)
        await callback.answer()
 
    # ---------- Постоянные нижние кнопки (текстовые метки) ----------
    # ВАЖНО: регистрируются ДО training_turn, чтобы перехватывать нажатия
    # даже во время активного диалога с «покупателем».
 
    @dp.message(F.text == BTN_NEW)
    async def btn_new(message: Message):
        if not allowed(message.from_user.id):
            return
        await do_start(message, message.from_user.id)
 
    @dp.message(F.text == BTN_STATS)
    async def btn_stats(message: Message):
        if not allowed(message.from_user.id):
            return
        await do_stats(message, message.from_user.id)
 
    @dp.message(F.text == BTN_EXIT)
    async def btn_exit(message: Message):
        if not allowed(message.from_user.id):
            return
        await do_exit(message, message.from_user.id)
 
    # ---------- Команда /stats ----------
 
    @dp.message(Command("stats"))
    async def stats_cmd(message: Message):
        if not allowed(message.from_user.id):
            return
        await do_stats(message, message.from_user.id)
 
    # ---------- /start во время сессии: выходим (приоритет над консультантом) ----------
 
    @dp.message(CommandStart(), _in_session)
    async def start_in_session(message: Message):
        SESSIONS.pop(message.from_user.id, None)
        await message.answer(
            "🚪 Вышел из тренажёра.\n\nСкидывай скрины переписки — разберу по Гребенюку. "
            "Или жми «🎯 Тренажёр», чтобы снова потренироваться.",
            reply_markup=ReplyKeyboardRemove(),
        )
 
    # ---------- Фото/голос во время сессии: просим текст ----------
 
    @dp.message((F.photo | F.voice | F.audio), _in_session)
    async def wrong_input_in_session(message: Message):
        await message.answer("В тренажёре общайся с клиентом *текстом* 🙂", parse_mode="Markdown")
 
    # ---------- Основной ход: текст менеджера во время сессии ----------
 
    @dp.message(F.text & ~F.text.startswith("/"), _in_session)
    async def training_turn(message: Message):
        uid = message.from_user.id
        session = SESSIONS.get(uid)
        if not session:
            return
 
        scenario = session["scenario"]
        session["transcript"].append(("manager", message.text))
        session["turns"] += 1
 
        await bot.send_chat_action(uid, "typing")
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, engine.step, client, scenario, session["transcript"], message.text
            )
        except Exception as e:
            await message.answer(f"⚠️ Ошибка тренажёра: {e}")
            return
 
        buyer_reply = result["buyer_reply"]
        state = result["deal_state"]
        coach = result.get("coach_note", "")
        session["transcript"].append(("buyer", buyer_reply))
 
        if state == "won":
            SESSIONS.pop(uid, None)
            stats.record_session(uid, scenario, "won", session["turns"])
            text = f"💬 {buyer_reply}\n\n✅ *СДЕЛКА ЗАКРЫТА!* Клиент согласился."
            if coach:
                text += f"\n\n🧠 _{coach}_"
            text += "\n\nЖми «🎯 Новый клиент», чтобы продолжить."
            await message.answer(text, parse_mode="Markdown")
 
        elif state == "failed":
            SESSIONS.pop(uid, None)
            stats.record_session(uid, scenario, "failed", session["turns"])
            text = f"💬 {buyer_reply}\n\n❌ *СДЕЛКА ПРОВАЛЕНА.* Клиент передумал."
            if coach:
                text += f"\n\n🧠 _Почему сорвалось: {coach}_"
            text += "\n\nЖми «🎯 Новый клиент» и попробуй снова."
            await message.answer(text, parse_mode="Markdown")
 
        elif state == "yellow":
            text = f"💬 {buyer_reply}\n\n🟡 _Клиент засомневался — вернись в алгоритм, это последний шанс._"
            await message.answer(text, parse_mode="Markdown")
 
        else:  # active
            await message.answer(f"💬 {buyer_reply}")
 
