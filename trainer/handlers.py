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
import os
import random

from aiogram import F
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile,
)
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import engine, stats, tenancy, niche_loader, brief, persona

log = logging.getLogger(__name__)

# telegram_id -> активная сессия {"scenario","profile","transcript","turns","usage"}
SESSIONS = {}

BTN_TRAINER = "🎯 Тренажёр"
BTN_NEW = "🎯 Новый клиент"
BTN_STATS = "📊 Статистика"
BTN_EXIT = "🚪 Выйти"
BTN_TEAM = "👥 Отдел"

# Нижняя клавиатура видна и во время настройки. Её нажатия приходят обычным
# текстом, поэтому мастер брифа обязан их узнавать, а не принимать за ответ.
BUTTON_LABELS = {BTN_TRAINER, BTN_NEW, BTN_STATS, BTN_EXIT, BTN_TEAM}


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


async def safe_answer(message: Message, text, **kw):
    """
    Отправить сообщение, не рискуя молчанием.

    Telegram отклоняет сообщение целиком, если разметка Markdown битая —
    достаточно одной звёздочки или подчёркивания в тексте, который написала
    модель. Исключение улетает из хендлера, и пользователь не получает НИЧЕГО.
    Поэтому при отказе повторяем тем же текстом, но без разметки.
    """
    try:
        return await message.answer(text, **kw)
    except Exception as e:
        if "parse" not in str(e).lower() and "entit" not in str(e).lower():
            raise
        log.warning("Разметка не принята Telegram, шлю без неё: %s", e)
        kw.pop("parse_mode", None)
        return await message.answer(text, **kw)


def register_trainer(dp, bot, client, tts=None):
    """
    Регистрирует хендлеры тренажёра.

    tts — необязательная асинхронная функция (text, voice) -> bytes.
    Если передана и включён VOICE_CLIENT_CHANCE, клиент иногда присылает
    голосовое вместо текста. В переписке с реальными клиентами голосовые
    приходят постоянно, и менеджер должен уметь с ними работать.
    """

    voice_chance = float(os.environ.get("VOICE_CLIENT_CHANCE", "0"))

    async def _send_bubbles(message: Message, texts, scenario=None, allow_voice=True):
        """
        Отправить реплики клиента так, как это делает живой человек:
        несколькими короткими сообщениями, с индикатором «печатает» и
        паузой, соразмерной длине. Мгновенный ответ единым абзацем —
        главный признак, по которому тренажёр узнаётся как машина.
        """
        uid = message.from_user.id
        for i, t in enumerate(texts):
            if not t:
                continue
            delay = min(3.5, 0.7 + len(t) / 45)
            try:
                await bot.send_chat_action(uid, "typing")
            except Exception:
                pass
            await asyncio.sleep(delay)

            as_voice = (
                allow_voice and tts and scenario and voice_chance > 0
                and len(t) > 25 and random.random() < voice_chance
            )
            if as_voice:
                try:
                    await bot.send_chat_action(uid, "record_voice")
                    audio = await tts(t, persona.voice_name(scenario["persona"]))
                    await bot.send_voice(uid, BufferedInputFile(audio, filename="voice.ogg"))
                    continue
                except Exception:
                    log.warning("Голосовое не отправилось, шлю текстом", exc_info=True)
            await message.answer(t)

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
        await message.answer(
            "Собираю профиль вашей ниши. Обычно это минута-полторы, "
            "иногда до трёх — если с первого раза выйдет слабо, пересоберу сам.\n\n"
            "_Просто дождитесь ответа, писать ничего не нужно._",
            parse_mode="Markdown")
        await bot.send_chat_action(uid, "typing")

        # Всё внутри — под защитой: если здесь что-то упадёт, владелец
        # останется без единого сообщения и решит, что бот умер.
        try:
            profile, err, usage = await loop.run_in_executor(
                None, brief.generate, client, uid, remark)
        except Exception:
            log.exception("Сборка профиля упала")
            w = brief.WIZARDS.get(uid)
            if w:
                w["generating"] = False
            await message.answer(
                "⚠️ Не получилось собрать профиль — что-то сломалось на нашей стороне.\n"
                "Наберите /setup, чтобы попробовать заново.")
            return

        if usage:
            try:
                await loop.run_in_executor(None, stats.record_usage, u["company_id"], uid,
                                           "brief", brief.MODEL, usage)
            except Exception:
                log.exception("Не удалось записать расход по брифу")

        if err:
            await message.answer(f"⚠️ {err}")
            return

        await safe_answer(
            message,
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
        text = (message.text or "").strip()
        w = brief.WIZARDS.get(uid)
        if not w:
            return

        # 1. Профиль сейчас собирается — принимать ответы нельзя.
        if brief.is_generating(uid):
            await message.answer("Профиль ещё собирается — секунду, я допишу.")
            return

        # 2. Нажатие нижней кнопки. Это не ответ на вопрос: раньше такой
        #    текст уходил в мастер и ронял его.
        if text in BUTTON_LABELS:
            await message.answer(
                "Сейчас идёт настройка профиля. Закончите её или наберите /cancel — "
                "тогда кнопки снова заработают.")
            return

        # 3. Владелец нажал «Переделать» и пишет замечание.
        if w.get("awaiting_remark"):
            w["awaiting_remark"] = False
            await _build_profile(message, await _user(message), remark=text)
            return

        # 4. Черновик готов, ждём решения по кнопкам под ним.
        if brief.awaiting_confirmation(uid):
            await message.answer(
                "Профиль собран — нажмите «✅ Подходит» или «🔁 Переделать» "
                "под сообщением выше. Начать настройку заново — /setup.")
            return

        if text.lower() in ("назад", "back"):
            q = brief.back(uid)
            if not q:
                await message.answer("Это первый вопрос, назад некуда.")
                return
            await _ask(message, q)
            return

        q, err, done = brief.submit_answer(uid, text)
        if err:
            await message.answer(f"⚠️ {err}")
            return
        if done:
            await _build_profile(message, await _user(message))
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
                   "silences": 0, "awaiting_followup": False, "last_silence_hours": 0,
                   "usage": {"input_tokens": 0, "output_tokens": 0, "cache_write": 0, "cache_read": 0}}
        SESSIONS[uid] = session

        await message.answer(engine.scenario_intro(scenario),
                             parse_mode="Markdown", reply_markup=trainer_reply_kb())
        try:
            opening, usage = await loop.run_in_executor(
                None, engine.opening_message, client, scenario, profile)
        except Exception:
            log.exception("Ошибка первого сообщения")
            opening, usage = [scenario["request"]], None

        if usage:
            session["usage"] = {k: session["usage"][k] + usage[k] for k in session["usage"]}
            await loop.run_in_executor(None, stats.record_usage, u["company_id"], uid,
                                       "opening", engine.DIALOG_MODEL, usage)
        for t in opening:
            session["transcript"].append(("buyer", t))
        await _send_bubbles(message, opening, scenario)

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
        await safe_answer(message, niche_loader.describe(profile), parse_mode="Markdown")

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
        scenario = session["scenario"]
        session["transcript"].append(("manager", message.text))
        session["turns"] += 1
        was_followup = session.get("awaiting_followup", False)

        await bot.send_chat_action(uid, "typing")
        try:
            result = await loop.run_in_executor(
                None, engine.step, client, session, message.text)
        except Exception:
            log.exception("Ошибка хода тренажёра")
            await message.answer("⚠️ Тренажёр споткнулся. Напиши сообщение ещё раз.")
            return

        usage = result.get("usage")
        if usage:
            session["usage"] = {k: session["usage"][k] + usage[k] for k in session["usage"]}
            await loop.run_in_executor(None, stats.record_usage, u["company_id"], uid,
                                       "step", engine.DIALOG_MODEL, usage)

        state = result["deal_state"]
        msgs = result["buyer_messages"]

        # Клиент пропал. Реального ожидания нет — только пометка, чтобы
        # тренировка не прерывалась, а решение принимать всё равно пришлось.
        if state == "silent":
            session["silences"] = session.get("silences", 0) + 1
            session["awaiting_followup"] = True
            session["last_silence_hours"] = result["silence_hours"]
            session["transcript"].append(
                ("system", f"(клиент не отвечает {result['silence_hours']} ч.)"))

            if session["silences"] > engine.MAX_SILENCES:
                SESSIONS.pop(uid, None)
                await message.answer(
                    "⏳ *Клиент перестал отвечать окончательно.*", parse_mode="Markdown")
                await _finish(message, u, session, "failed", None)
                return

            await asyncio.sleep(1.2)
            await safe_answer(message, engine.silence_marker(result["silence_hours"]),
                              parse_mode="Markdown")
            return

        session["awaiting_followup"] = False
        for t in msgs:
            session["transcript"].append(("buyer", t))

        if state in ("won", "failed"):
            SESSIONS.pop(uid, None)
            await _send_bubbles(message, msgs, scenario)
            await _finish(message, u, session, state, None)
            return

        await _send_bubbles(message, msgs, scenario)

        if was_followup and state == "active":
            await safe_answer(message, "_Клиент вернулся — дожим сработал._",
                              parse_mode="Markdown")
        elif state == "yellow":
            await safe_answer(
                message,
                "🟡 _Клиент засомневался — вернись в алгоритм, это последний шанс._",
                parse_mode="Markdown")

    async def _finish(message, u, session, state, buyer_reply=None):
        """Завершение тренировки: вердикт, разбор на сильной модели, списание, статистика."""
        uid = message.from_user.id
        loop = asyncio.get_event_loop()

        head = ("✅ *СДЕЛКА ЗАКРЫТА!* Клиент согласился."
                if state == "won" else
                "❌ *СДЕЛКА ПРОВАЛЕНА.*")
        prefix = f"💬 {buyer_reply}\n\n" if buyer_reply else ""
        await safe_answer(message, prefix + head, parse_mode="Markdown")

        await bot.send_chat_action(uid, "typing")
        debrief, usage = await loop.run_in_executor(
            None, engine.final_debrief, client, session, state)
        if usage:
            await loop.run_in_executor(None, stats.record_usage, u["company_id"], uid,
                                       "debrief", engine.DEBRIEF_MODEL, usage)

        await loop.run_in_executor(None, stats.record_session, u, session["scenario"],
                                   state, session["turns"], session["transcript"])
        used, limit = await loop.run_in_executor(None, tenancy.consume_session, u["company_id"])

        if debrief:
            await safe_answer(message, f"🧠 *Разбор*\n\n{debrief}", parse_mode="Markdown")

        warn = tenancy.usage_warning(used, limit)
        tail = "\n\n" + warn if warn else ""
        await message.answer("Жми «🎯 Новый клиент», чтобы продолжить." + tail,
                             reply_markup=trainer_reply_kb())
