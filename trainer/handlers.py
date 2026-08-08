# -*- coding: utf-8 -*-
"""
Хендлеры тренажёра для aiogram.

Подключение из bot.py (одна строка, ДО обычных хендлеров, чтобы тренажёр
имел приоритет над консультантом при активной сессии):

    from trainer import register_trainer
    register_trainer(dp, bot, client)

ЧТО ИЗМЕНИЛОСЬ В МУЛЬТИАРЕНДНОЙ ВЕРСИИ:
  • Вместо общего списка разрешённых id — компания пользователя из базы.
  • Перед каждой тренировкой проверяются статус подписки, срок и лимит.
  • Профиль ниши берётся из базы, свой у каждой компании.
  • Для владельца доступны команды управления отделом.
  • Расход токенов пишется в usage_log на каждом вызове модели.
"""

import asyncio
import csv
import io
import logging

from aiogram import F
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile,
)
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import engine, stats, tenancy, niche_loader, brief

log = logging.getLogger(__name__)

# telegram_id -> активная сессия {"scenario","profile","transcript","turns","usage"}
SESSIONS = {}

BTN_TRAINER = "🎯 Тренажёр"
BTN_NEW = "🎯 Новый клиент"
BTN_STATS = "📊 Статистика"
BTN_EXIT = "🚪 Выйти"
BTN_TEAM = "👥 Отдел"


def main_reply_kb(is_owner=False):
    rows = [[KeyboardButton(text=BTN_TRAINER), KeyboardButton(text=BTN_STATS)]]
    if is_owner:
        rows.append([KeyboardButton(text=BTN_TEAM)])
    return ReplyKeyboardMarkup(
        keyboard=rows, resize_keyboard=True,
        input_field_placeholder="Скинь скрин переписки или жми «Тренажёр»…",
    )


def trainer_reply_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NEW), KeyboardButton(text=BTN_EXIT)],
            [KeyboardButton(text=BTN_STATS)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Пиши сообщение клиенту…",
    )


def _in_session(message: Message) -> bool:
    return message.from_user.id in SESSIONS


def _in_brief(message: Message) -> bool:
    return brief.is_active(message.from_user.id)


def register_trainer(dp, bot, client):
    """Регистрирует хендлеры тренажёра."""

    async def _user(message: Message):
        """Достать пользователя из базы. None — если не привязан к компании."""
        loop = asyncio.get_event_loop()
        u = await loop.run_in_executor(None, tenancy.get_user, message.from_user.id)
        if u:
            await loop.run_in_executor(None, tenancy.touch, message.from_user.id)
        return u

    def _is_owner(u):
        return u and u["role"] == tenancy.ROLE_OWNER

    # ======================================================================
    # МАСТЕР БРИФА
    # Регистрируется ПЕРВЫМ: пока владелец отвечает на вопросы, его текст
    # не должен попадать ни в тренажёр, ни в разбор скринов.
    # ======================================================================

    def _draft_kb():
        b = InlineKeyboardBuilder()
        b.button(text="✅ Подходит", callback_data="brief_ok")
        b.button(text="🔁 Переделать", callback_data="brief_redo")
        b.adjust(2)
        return b.as_markup()

    async def _ask(message: Message, q):
        w = brief.WIZARDS.get(message.from_user.id)
        step = w["step"] + 1
        total = len(brief.QUESTIONS)
        await message.answer(
            f"{brief.progress_bar(w['step'], total)}\n\n"
            f"{brief.question_text(q, step, total)}",
            parse_mode="Markdown",
        )

    async def _build_profile(message: Message, u, remark=None):
        uid = message.from_user.id
        loop = asyncio.get_event_loop()
        await message.answer("Собираю профиль вашей ниши — это займёт полминуты…")
        await bot.send_chat_action(uid, "typing")

        profile, err, usage = await loop.run_in_executor(
            None, brief.generate, client, uid, remark)

        if usage:
            await loop.run_in_executor(None, stats.record_usage, u["company_id"], uid,
                                       "brief", brief.MODEL, usage)
        if err:
            await message.answer(f"⚠️ {err}")
            return

        await message.answer(
            "Вот что получилось. Проверьте — по этому описанию бот будет "
            "играть ваших клиентов.\n\n" + niche_loader.describe(profile),
            parse_mode="Markdown", reply_markup=_draft_kb(),
        )

    @dp.message(Command("setup"))
    async def setup_cmd(message: Message):
        u = await _user(message)
        if not _is_owner(u):
            await message.answer("Настройку профиля запускает руководитель компании.")
            return

        loop = asyncio.get_event_loop()
        existing = await loop.run_in_executor(None, niche_loader.active_profile, u["company_id"])
        warn = ("\n\n_У вас уже настроен профиль. Новый заменит его, "
                "старый останется в истории._" if existing else "")

        q = brief.start(message.from_user.id, u["company_id"])
        await message.answer(
            f"*Настройка под ваш бизнес*\n\n"
            f"Восемь вопросов, примерно пять минут. По вашим ответам бот соберёт "
            f"портреты клиентов и их запросы — и дальше будет играть именно ваших "
            f"покупателей, а не абстрактных.\n\n"
            f"Отвечайте развёрнуто: чем подробнее, тем достовернее тренировка.\n"
            f"«назад» — вернуться на шаг, /cancel — выйти.{warn}",
            parse_mode="Markdown",
        )
        await _ask(message, q)

    @dp.message(Command("cancel"))
    async def cancel_cmd(message: Message):
        if brief.cancel(message.from_user.id):
            await message.answer("Настройка отменена. Запустить заново — /setup")
        else:
            await message.answer("Нечего отменять.")

    @dp.message(F.text & ~F.text.startswith("/"), _in_brief)
    async def brief_answer(message: Message):
        uid = message.from_user.id
        u = await _user(message)
        w = brief.WIZARDS.get(uid)

        # Владелец нажал «Переделать» и теперь пишет замечание
        if w and w.get("awaiting_remark"):
            w["awaiting_remark"] = False
            await _build_profile(message, u, remark=message.text)
            return

        if message.text.strip().lower() in ("назад", "back"):
            q = brief.back(uid)
            if not q:
                await message.answer("Это первый вопрос, назад некуда.")
                return
            await _ask(message, q)
            return

        q, err, done = brief.submit_answer(uid, message.text)
        if err:
            await message.answer(f"⚠️ {err}")
            return
        if done:
            await _build_profile(message, u)
            return
        await _ask(message, q)

    @dp.message((F.photo | F.voice | F.audio), _in_brief)
    async def brief_wrong_input(message: Message):
        await message.answer("Сейчас идёт настройка — ответьте, пожалуйста, текстом.")

    @dp.callback_query(F.data == "brief_ok")
    async def brief_ok_cb(callback: CallbackQuery):
        uid = callback.from_user.id
        loop = asyncio.get_event_loop()
        u = await loop.run_in_executor(None, tenancy.get_user, uid)
        if not u:
            await callback.answer()
            return

        version, err = brief.confirm(uid)
        if err:
            await callback.message.answer(f"⚠️ {err}")
            await callback.answer()
            return

        await loop.run_in_executor(None, tenancy.set_status, u["company_id"], tenancy.STATUS_ACTIVE)
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start=join_{u['invite_code']}"
        await callback.message.answer(
            f"✅ *Профиль сохранён* (версия {version}). Компания готова к работе.\n\n"
            f"Осталось позвать менеджеров — отправьте им эту ссылку:\n{link}\n\n"
            f"Свободных мест: *{u['seats'] - tenancy.seats_taken(u['company_id'])}* из {u['seats']}.\n\n"
            f"Сами можете попробовать прямо сейчас — кнопка «🎯 Тренажёр» внизу.",
            parse_mode="Markdown", disable_web_page_preview=True,
            reply_markup=main_reply_kb(True),
        )
        await callback.answer("Готово")

    @dp.callback_query(F.data == "brief_redo")
    async def brief_redo_cb(callback: CallbackQuery):
        uid = callback.from_user.id
        w = brief.WIZARDS.get(uid)
        if not w:
            await callback.answer("Черновик уже неактуален, запустите /setup", show_alert=True)
            return
        w["awaiting_remark"] = True
        await callback.message.answer(
            "Что поправить? Напишите замечание одним сообщением — "
            "например «добавь корпоративных клиентов» или «убери частных лиц, "
            "мы работаем только с бизнесом»."
        )
        await callback.answer()

    # ---------- Сценарии ----------

    async def do_menu(message: Message, u):
        await message.answer(
            "🎯 *Тренажёр по продажам*\n\n"
            "Тебе выпадет случайный клиент — со скрытым психотипом, ролью и запросом. "
            "Он пишет первым, ведёт себя как реальный «сложный» лид: сам не знает, чего хочет, "
            "просит примеры, прячет бюджет, возражает. Продай его по смыслу (Гребенюк), а не по скрипту.\n\n"
            "Управляй кнопками внизу 👇",
            parse_mode="Markdown", reply_markup=trainer_reply_kb(),
        )

    async def do_start(message: Message, u):
        uid = message.from_user.id
        loop = asyncio.get_event_loop()

        try:
            tenancy.check_can_train(u)
        except tenancy.Denied as d:
            await message.answer(str(d), reply_markup=main_reply_kb(_is_owner(u)))
            return

        profile = await loop.run_in_executor(None, niche_loader.active_profile, u["company_id"])
        if not profile:
            await message.answer(
                "Профиль вашей компании ещё не настроен.\n"
                "Руководителю нужно заполнить бриф — команда /setup."
            )
            return

        scenario = engine.new_scenario(profile)
        session = {"scenario": scenario, "profile": profile,
                   "transcript": [], "turns": 0,
                   "usage": {"input_tokens": 0, "output_tokens": 0, "cache_write": 0, "cache_read": 0}}
        SESSIONS[uid] = session

        await message.answer(engine.scenario_intro(scenario),
                             parse_mode="Markdown", reply_markup=trainer_reply_kb())
        await bot.send_chat_action(uid, "typing")
        try:
            opening, usage = await loop.run_in_executor(
                None, engine.opening_message, client, scenario, profile)
        except Exception:
            log.exception("Ошибка первого сообщения")
            opening, usage = scenario["request"], None

        if usage:
            session["usage"] = {k: session["usage"][k] + usage[k] for k in session["usage"]}
            await loop.run_in_executor(None, stats.record_usage, u["company_id"], uid,
                                       "opening", engine.DIALOG_MODEL, usage)
        session["transcript"].append(("buyer", opening))
        await message.answer(f"💬 {opening}")

    async def do_stats(message: Message, u):
        if not u:
            await message.answer(
                "Вы пока не привязаны к компании. Попросите руководителя прислать "
                "ссылку-приглашение."
            )
            return
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, stats.report_for_user, u)
        await message.answer(text, parse_mode="Markdown")

    async def do_exit(message: Message, u):
        was_in = SESSIONS.pop(message.from_user.id, None) is not None
        text = ("🚪 Вышел из тренажёра. Скидывай скрины переписки — разберу по Гребенюку."
                if was_in else
                "Ты сейчас в режиме разбора. Скидывай скрины переписки — разберу по Гребенюку.")
        await message.answer(text, reply_markup=main_reply_kb(_is_owner(u)))

    # ---------- Кнопки ----------

    @dp.callback_query(F.data == "trainer_menu")
    async def open_menu_cb(callback: CallbackQuery):
        u = await _user(callback)
        await do_menu(callback.message, u)
        await callback.answer()

    @dp.message(F.text.in_({BTN_TRAINER, BTN_NEW}))
    async def btn_new(message: Message):
        await do_start(message, await _user(message))

    @dp.message(F.text == BTN_STATS)
    async def btn_stats(message: Message):
        await do_stats(message, await _user(message))

    @dp.message(F.text == BTN_EXIT)
    async def btn_exit(message: Message):
        await do_exit(message, await _user(message))

    @dp.message(Command("stats"))
    async def stats_cmd(message: Message):
        await do_stats(message, await _user(message))

    # ---------- Команды владельца ----------

    async def _owner_only(message: Message):
        u = await _user(message)
        if not _is_owner(u):
            await message.answer("Эта команда доступна только руководителю.")
            return None
        return u

    @dp.message(F.text == BTN_TEAM)
    @dp.message(Command("dashboard"))
    async def dashboard_cmd(message: Message):
        u = await _owner_only(message)
        if not u:
            return
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, stats.report_for_company, u["company_id"])
        await message.answer(text, parse_mode="Markdown")

    @dp.message(Command("team"))
    async def team_cmd(message: Message):
        u = await _owner_only(message)
        if not u:
            return
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, tenancy.team, u["company_id"])
        taken = len(rows)
        lines = [f"👥 *Отдел* — {taken} из {u['seats']} мест", ""]
        for m in rows:
            name = m["full_name"] or (f"@{m['username']}" if m["username"] else str(m["telegram_id"]))
            role = "руководитель" if m["role"] == tenancy.ROLE_OWNER else "менеджер"
            mark = "" if m["active"] else " _(отключён)_"
            seen = m["last_seen_at"].strftime("%d.%m") if m["last_seen_at"] else "не заходил"
            lines.append(f"• {name} — {role}, тренировок {m['total']}, был {seen}{mark}")
        lines += ["", "Пригласить ещё — /invite"]
        await message.answer("\n".join(lines), parse_mode="Markdown")

    @dp.message(Command("invite"))
    async def invite_cmd(message: Message):
        u = await _owner_only(message)
        if not u:
            return
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start=join_{u['invite_code']}"
        free = u["seats"] - tenancy.seats_taken(u["company_id"])
        await message.answer(
            f"👥 *Ссылка для менеджеров*\n\n{link}\n\n"
            f"Отправьте её в рабочий чат. Свободных мест: *{free}* из {u['seats']}.\n\n"
            f"_Если ссылка утекла за пределы компании — /revoke выдаст новую, старая перестанет работать._",
            parse_mode="Markdown", disable_web_page_preview=True,
        )

    @dp.message(Command("revoke"))
    async def revoke_cmd(message: Message):
        u = await _owner_only(message)
        if not u:
            return
        loop = asyncio.get_event_loop()
        code = await loop.run_in_executor(None, tenancy.rotate_invite_code, u["company_id"])
        me = await bot.get_me()
        await message.answer(
            f"Старая ссылка отозвана. Новая:\n\nhttps://t.me/{me.username}?start=join_{code}",
            disable_web_page_preview=True,
        )

    @dp.message(Command("limits"))
    async def limits_cmd(message: Message):
        u = await _owner_only(message)
        if not u:
            return
        plan = tenancy.PLANS[u["plan"]]
        used, limit = u["sessions_used"], u["session_limit"]
        pct = round(used / limit * 100) if limit else 0
        exp = u["expires_at"].strftime("%d.%m.%Y") if u["expires_at"] else "—"
        await message.answer(
            f"📦 *Тариф «{plan['title']}»*\n\n"
            f"Тренировок: *{used}* из *{limit}* ({pct}%)\n"
            f"Мест: *{tenancy.seats_taken(u['company_id'])}* из *{u['seats']}*\n"
            f"Оплачено до: *{exp}*",
            parse_mode="Markdown",
        )

    @dp.message(Command("profile"))
    async def profile_cmd(message: Message):
        u = await _owner_only(message)
        if not u:
            return
        loop = asyncio.get_event_loop()
        profile = await loop.run_in_executor(None, niche_loader.active_profile, u["company_id"])
        if not profile:
            await message.answer("Профиль ещё не настроен. Заполните бриф — /setup.")
            return
        await message.answer(niche_loader.describe(profile), parse_mode="Markdown")

    @dp.message(Command("export"))
    async def export_cmd(message: Message):
        u = await _owner_only(message)
        if not u:
            return
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, stats.export_rows, u["company_id"])
        if not rows:
            await message.answer("Пока нечего выгружать.")
            return
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Дата", "Менеджер", "Клиент", "Психотип", "Итог", "Ходов"])
        for r in rows:
            w.writerow([
                r["finished_at"].strftime("%d.%m.%Y %H:%M"),
                r["full_name"] or r["username"] or r["telegram_id"],
                r["status_title"], r["psychotype_id"],
                "продано" if r["result"] == "won" else "провал", r["turns"],
            ])
        data = buf.getvalue().encode("utf-8-sig")
        await message.answer_document(
            BufferedInputFile(data, filename="moss_sale_statistika.csv"),
            caption=f"Выгрузка: {len(rows)} тренировок",
        )

    # ---------- Во время сессии ----------

    @dp.message((F.photo | F.voice | F.audio), _in_session)
    async def wrong_input_in_session(message: Message):
        await message.answer("В тренажёре общайся с клиентом *текстом* 🙂", parse_mode="Markdown")

    @dp.message(F.text & ~F.text.startswith("/"), _in_session)
    async def training_turn(message: Message):
        uid = message.from_user.id
        session = SESSIONS.get(uid)
        if not session:
            return
        u = await _user(message)
        if not u:
            SESSIONS.pop(uid, None)
            return

        loop = asyncio.get_event_loop()
        scenario, profile = session["scenario"], session["profile"]
        session["transcript"].append(("manager", message.text))
        session["turns"] += 1

        await bot.send_chat_action(uid, "typing")
        try:
            result = await loop.run_in_executor(
                None, engine.step, client, scenario, profile,
                session["transcript"], message.text)
        except Exception:
            log.exception("Ошибка хода тренажёра")
            await message.answer("⚠️ Тренажёр споткнулся. Напиши сообщение ещё раз.")
            return

        usage = result.get("usage")
        if usage:
            session["usage"] = {k: session["usage"][k] + usage[k] for k in session["usage"]}
            await loop.run_in_executor(None, stats.record_usage, u["company_id"], uid,
                                       "step", engine.DIALOG_MODEL, usage)

        buyer_reply = result["buyer_reply"]
        state = result["deal_state"]
        session["transcript"].append(("buyer", buyer_reply))

        if state in ("won", "failed"):
            SESSIONS.pop(uid, None)
            await _finish(message, u, session, state, buyer_reply)
        elif state == "yellow":
            await message.answer(
                f"💬 {buyer_reply}\n\n🟡 _Клиент засомневался — вернись в алгоритм, это последний шанс._",
                parse_mode="Markdown")
        else:
            await message.answer(f"💬 {buyer_reply}")

    async def _finish(message, u, session, state, buyer_reply):
        """Завершение тренировки: вердикт, разбор на сильной модели, списание, статистика."""
        uid = message.from_user.id
        loop = asyncio.get_event_loop()

        head = ("✅ *СДЕЛКА ЗАКРЫТА!* Клиент согласился."
                if state == "won" else
                "❌ *СДЕЛКА ПРОВАЛЕНА.* Клиент передумал.")
        await message.answer(f"💬 {buyer_reply}\n\n{head}", parse_mode="Markdown")

        await bot.send_chat_action(uid, "typing")
        debrief, usage = await loop.run_in_executor(
            None, engine.final_debrief, client, session["scenario"],
            session["profile"], session["transcript"], state)
        if usage:
            await loop.run_in_executor(None, stats.record_usage, u["company_id"], uid,
                                       "debrief", engine.DEBRIEF_MODEL, usage)

        await loop.run_in_executor(None, stats.record_session, u, session["scenario"],
                                   state, session["turns"], session["transcript"])
        used, limit = await loop.run_in_executor(None, tenancy.consume_session, u["company_id"])

        if debrief:
            await message.answer(f"🧠 *Разбор*\n\n{debrief}", parse_mode="Markdown")

        warn = tenancy.usage_warning(used, limit)
        tail = "\n\n" + warn if warn else ""
        await message.answer("Жми «🎯 Новый клиент», чтобы продолжить." + tail,
                             reply_markup=trainer_reply_kb())
