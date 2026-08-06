# -*- coding: utf-8 -*-
"""
Хендлеры тренажёра для aiogram и интеграция в бота.

Подключение из bot.py (одна строка, ДО объявления обычных хендлеров, чтобы
тренажёр имел приоритет над консультантом при активной сессии):

    from trainer import register_trainer
    register_trainer(dp, bot, client, is_allowed)

Всё состояние тренажёра живёт здесь, отдельно от консультанта.
"""

import asyncio

from aiogram import F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import engine
from . import stats

# user_id -> сессия тренировки
# {"scenario": {...}, "transcript": [(role, text)...], "turns": int}
SESSIONS = {}


def _in_session(message: Message) -> bool:
    return message.from_user.id in SESSIONS


# ---------- Клавиатуры ----------

def menu_keyboard():
    b = InlineKeyboardBuilder()
    b.button(text="▶️ Тренироваться", callback_data="trainer_start")
    b.button(text="📊 Статистика", callback_data="trainer_stats")
    b.adjust(1)
    return b.as_markup()


def in_session_keyboard():
    b = InlineKeyboardBuilder()
    b.button(text="🚪 Выйти из тренажёра", callback_data="trainer_exit")
    b.adjust(1)
    return b.as_markup()


def after_result_keyboard(failed: bool):
    b = InlineKeyboardBuilder()
    if failed:
        b.button(text="🔄 Попробовать ещё раз", callback_data="trainer_start")
    else:
        b.button(text="▶️ Ещё клиент", callback_data="trainer_start")
    b.button(text="📊 Статистика", callback_data="trainer_stats")
    b.button(text="🚪 Выйти", callback_data="trainer_exit")
    b.adjust(1)
    return b.as_markup()


def _start_session(user_id):
    scenario = engine.new_scenario()
    SESSIONS[user_id] = {"scenario": scenario, "transcript": [], "turns": 0}
    return scenario


def register_trainer(dp, bot, client, is_allowed=None):
    """Регистрирует все хендлеры тренажёра. is_allowed(user_id)->bool — опционально."""

    stats.init_db()

    def allowed(uid):
        return True if is_allowed is None else is_allowed(uid)

    # --- Вход в меню тренажёра ---
    @dp.callback_query(F.data == "trainer_menu")
    async def open_menu(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return
        await callback.message.answer(
            "🎯 *Тренажёр по продажам*\n\n"
            "Тебе выпадет случайный клиент — со своим скрытым психотипом, ролью и запросом. "
            "Твоя задача — продать по смыслу (методология Гребенюка), а не по скрипту.\n\n"
            "Если ошибёшься — клиент сначала засомневается («я подумаю»), а потом уйдёт. "
            "Продажа засчитается, только если реально отработаешь верно.",
            parse_mode="Markdown",
            reply_markup=menu_keyboard(),
        )
        await callback.answer()

    # --- Старт тренировки / Ещё раз ---
    @dp.callback_query(F.data == "trainer_start")
    async def start_training(callback: CallbackQuery):
        uid = callback.from_user.id
        if not allowed(uid):
            return
        scenario = _start_session(uid)
        await callback.message.answer(
            engine.scenario_intro(scenario),
            parse_mode="Markdown",
            reply_markup=in_session_keyboard(),
        )
        await callback.answer()

    # --- Статистика (кнопка) ---
    @dp.callback_query(F.data == "trainer_stats")
    async def stats_cb(callback: CallbackQuery):
        if not allowed(callback.from_user.id):
            return
        await callback.message.answer(
            stats.report_for_user(callback.from_user.id),
            parse_mode="Markdown",
        )
        await callback.answer()

    # --- Выход из тренажёра ---
    @dp.callback_query(F.data == "trainer_exit")
    async def exit_cb(callback: CallbackQuery):
        SESSIONS.pop(callback.from_user.id, None)
        await callback.message.answer("🚪 Ты вышел из тренажёра. Можешь снова скидывать скрины на разбор.")
        await callback.answer()

    # --- Команда /stats (работает всегда) ---
    @dp.message(Command("stats"))
    async def stats_cmd(message: Message):
        if not allowed(message.from_user.id):
            return
        await message.answer(stats.report_for_user(message.from_user.id), parse_mode="Markdown")

    # --- /start во время сессии: выходим из тренажёра (приоритет над консультантом) ---
    @dp.message(CommandStart(), _in_session)
    async def start_in_session(message: Message):
        SESSIONS.pop(message.from_user.id, None)
        await message.answer(
            "🚪 Вышел из тренажёра.\n\nСкидывай скрины переписки — разберу по Гребенюку. "
            "Или жми «🎯 Тренажёр», чтобы снова потренироваться.",
        )

    # --- Фото/голос во время сессии: просим текст (приоритет над консультантом) ---
    @dp.message((F.photo | F.voice | F.audio), _in_session)
    async def wrong_input_in_session(message: Message):
        await message.answer("В тренажёре общайся с клиентом *текстом* 🙂", parse_mode="Markdown")

    # --- Основной ход: текст менеджера во время сессии ---
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
            await message.answer(text, parse_mode="Markdown", reply_markup=after_result_keyboard(failed=False))

        elif state == "failed":
            SESSIONS.pop(uid, None)
            stats.record_session(uid, scenario, "failed", session["turns"])
            text = f"💬 {buyer_reply}\n\n❌ *СДЕЛКА ПРОВАЛЕНА.* Клиент передумал."
            if coach:
                text += f"\n\n🧠 _Почему сорвалось: {coach}_"
            await message.answer(text, parse_mode="Markdown", reply_markup=after_result_keyboard(failed=True))

        elif state == "yellow":
            text = f"💬 {buyer_reply}\n\n🟡 _Клиент засомневался — вернись в алгоритм, это последний шанс._"
            await message.answer(text, parse_mode="Markdown", reply_markup=in_session_keyboard())

        else:  # active
            await message.answer(f"💬 {buyer_reply}", reply_markup=in_session_keyboard())
