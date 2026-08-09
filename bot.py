# -*- coding: utf-8 -*-
"""
MOSS SALE — точка входа.

Один экземпляр бота обслуживает все компании-клиенты. Доступ определяется
привязкой telegram_id к компании в базе, а не списком разрешённых id.

Переменные окружения:
  BOT_TOKEN           — токен бота от BotFather                (обязательно)
  ANTHROPIC_API_KEY   — ключ Anthropic                          (обязательно)
  DATABASE_URL        — Postgres, Railway подставляет сам        (обязательно)
  OPENAI_API_KEY      — для распознавания и озвучки              (необязательно)
  TRAINER_MODEL       — модель диалога, по умолчанию claude-sonnet-5
  DEBRIEF_MODEL       — модель разбора, по умолчанию claude-opus-5
  ADMIN_IDS           — telegram_id владельца продукта, через запятую
  VOICE_CLIENT_CHANCE — доля голосовых от клиента в тренажёре, 0..1 (по умолчанию 0)
  LONG_MESSAGE_CHARS  — с какой длины сообщение менеджера считается простынёй (420)
"""

import os
import io
import asyncio
import base64
import logging
import tempfile

import anthropic
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder

from trainer import register_trainer, main_reply_kb, db, tenancy, onboarding, stats

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("moss")

BOT_TOKEN = os.environ["BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x}

CONSULTANT_MODEL = os.environ.get("CONSULTANT_MODEL", "claude-opus-5")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Голос и распознавание — необязательный блок: без ключа OpenAI бот работает,
# просто не озвучивает и не расшифровывает голосовые.
openai_async = openai_sync = None
if OPENAI_API_KEY:
    from openai import AsyncOpenAI, OpenAI
    openai_async = AsyncOpenAI(api_key=OPENAI_API_KEY)
    openai_sync = OpenAI(api_key=OPENAI_API_KEY)

conversations = {}
media_groups = {}
media_group_timers = {}
last_answers = {}

SYSTEM_PROMPT = """Ты — эксперт по продажам, обученный на методологии Михаила Гребенюка ("Отдел продаж по захвату рынка").

Ты ведёшь полноценную консультацию: анализируешь скриншоты переписок с клиентами, даёшь рекомендации, отвечаешь на вопросы, помогаешь формулировать сообщения и разбираешь ситуации.

КЛЮЧЕВЫЕ ПРИНЦИПЫ ГРЕБЕНЮКА:
1. Цель каждого сообщения — двигать клиента к следующему шагу воронки
2. Выявляй: боль, деньги, полномочия
3. Не отвечай на вопрос о цене сразу — сначала выяви потребность
4. Техника "Уступ": отвечай вопросом на вопрос
5. Программируй следующий шаг в каждом сообщении
6. Избегай стоп-слов: "наверное", "может быть", "если что"
7. Социальные доказательства — показывай кейсы
8. Дожим: "Что мешает принять решение прямо сейчас?"
9. Никогда не проси "подумать" — назначай конкретное действие
10. Квалификация — не трать время на нецелевых

При анализе скриншотов:
📊 Анализ ситуации
⚠️ Ошибки в диалоге
✅ Следующий шаг
💬 Готовый текст сообщения"""


def new_situation_keyboard():
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Новая ситуация", callback_data="new_situation")
    if openai_sync:
        b.button(text="🔊 Озвучить", callback_data="voice_last")
    b.button(text="🎯 Тренажёр", callback_data="trainer_menu")
    b.adjust(2, 1)
    return b.as_markup()


async def current_user(tg_id):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, tenancy.get_user, tg_id)


async def guard(message: Message):
    """Пускать только привязанных к компании. Возвращает пользователя либо None."""
    u = await current_user(message.from_user.id)
    if not u:
        await message.answer(onboarding.welcome_unbound())
        return None
    if not u["active"]:
        await message.answer("Ваш доступ отключён руководителем.")
        return None
    return u


def get_history(user_id):
    return conversations.setdefault(user_id, [])


def add_to_history(user_id, role, content):
    h = get_history(user_id)
    h.append({"role": role, "content": content})
    if len(h) > 20:
        conversations[user_id] = h[-20:]


def _ask_claude_sync(user_id, content):
    add_to_history(user_id, "user", content)
    resp = client.messages.create(
        model=CONSULTANT_MODEL,
        max_tokens=2000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=get_history(user_id),
    )
    answer = resp.content[0].text
    add_to_history(user_id, "assistant", answer)
    return answer, resp


async def ask_claude(user, content):
    loop = asyncio.get_event_loop()
    answer, resp = await loop.run_in_executor(None, _ask_claude_sync, user["telegram_id"], content)
    from trainer import costs
    await loop.run_in_executor(
        None, stats.record_usage, user["company_id"], user["telegram_id"],
        "consultant", CONSULTANT_MODEL, costs.usage_dict(resp))
    return answer


# --- Голос ------------------------------------------------------------------

async def transcribe_voice(file_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp.write(file_bytes)
        path = tmp.name
    try:
        with open(path, "rb") as f:
            tr = await openai_async.audio.transcriptions.create(
                model="whisper-1", file=("voice.ogg", f, "audio/ogg"), language="ru")
        return tr.text
    finally:
        os.unlink(path)


def _tts_sync(text: str, voice: str = "onyx") -> bytes:
    clean = text
    for ch in ("**", "*", "#", "`", "_"):
        clean = clean.replace(ch, "")
    resp = openai_sync.audio.speech.create(model="tts-1", voice=voice, input=clean[:2000])
    buf = io.BytesIO()
    for chunk in resp.iter_bytes():
        buf.write(chunk)
    return buf.getvalue()


async def text_to_speech(text: str, voice: str = "onyx") -> bytes:
    return await asyncio.get_event_loop().run_in_executor(None, _tts_sync, text, voice)


async def send_answer(user_id: int, text: str, with_voice: bool = False):
    last_answers[user_id] = text
    await bot.send_message(user_id, text, parse_mode="Markdown",
                           reply_markup=new_situation_keyboard())
    if with_voice and openai_sync:
        try:
            audio = await text_to_speech(text)
            await bot.send_voice(user_id, BufferedInputFile(audio, filename="answer.mp3"))
        except Exception as e:
            log.warning("Голос недоступен: %s", e)


# --- Тренажёр регистрируется ДО хендлеров консультанта: при активной
# --- тренировке его хендлеры должны перехватывать текст первыми.
# Голос передаём только если есть ключ OpenAI — без него клиент пишет текстом.
register_trainer(dp, bot, client, tts=text_to_speech if openai_sync else None)


# --- Вход по ссылке ---------------------------------------------------------

@dp.message(CommandStart(deep_link=True))
async def start_deeplink(message: Message, command: CommandObject):
    """t.me/BOT?start=<код> — активация владельца или вход менеджера."""
    loop = asyncio.get_event_loop()
    user, text, ok = await loop.run_in_executor(
        None, onboarding.redeem, command.args, message.from_user.id,
        message.from_user.full_name, message.from_user.username)

    if not ok:
        await message.answer(text or onboarding.welcome_unbound())
        return

    conversations[message.from_user.id] = []
    await message.answer(
        text, parse_mode="Markdown",
        reply_markup=main_reply_kb(user["role"] == tenancy.ROLE_OWNER))


@dp.message(CommandStart())
async def start(message: Message):
    u = await guard(message)
    if not u:
        return
    conversations[message.from_user.id] = []
    is_owner = u["role"] == tenancy.ROLE_OWNER
    hint = ("\n\n👥 Кнопка «Отдел» — сводка по менеджерам. Пригласить сотрудников — /invite."
            if is_owner else "")
    await message.answer(
        f"С возвращением, {u['company_title']}.\n\n"
        f"🎯 «Тренажёр» — потренироваться на живом клиенте.\n"
        f"📸 Или скидывай скрины переписки — разберу по Гребенюку.{hint}",
        reply_markup=main_reply_kb(is_owner))


@dp.message(Command("help"))
async def help_cmd(message: Message):
    u = await guard(message)
    if not u:
        return
    lines = ["*Что умеет бот*", "",
             "🎯 Тренажёр — диалог с живым клиентом и разбор после",
             "📸 Скрин переписки — разбор реальной ситуации",
             "📊 /stats — ваша конверсия"]
    if u["role"] == tenancy.ROLE_OWNER:
        lines += ["", "*Для руководителя:*",
                  "/dashboard — сводка по отделу",
                  "/team — состав и активность",
                  "/invite — ссылка для менеджеров",
                  "/revoke — отозвать ссылку",
                  "/limits — тариф и остаток",
                  "/profile — профиль вашей ниши",
                  "/export — выгрузка в таблицу"]
    await message.answer("\n".join(lines), parse_mode="Markdown")


# --- Служебные команды владельца продукта -----------------------------------

@dp.message(Command("newcompany"))
async def newcompany_cmd(message: Message, command: CommandObject):
    """Создать компанию и получить ссылку активации. Только для ADMIN_IDS.

    /newcompany Название | тариф | почта
    """
    if message.from_user.id not in ADMIN_IDS:
        return
    args = (command.args or "").split("|")
    title = args[0].strip() if args and args[0].strip() else None
    if not title:
        await message.answer("Формат: /newcompany Название | тариф | почта\n"
                             f"Тарифы: {', '.join(tenancy.PLANS)}")
        return
    plan = args[1].strip() if len(args) > 1 and args[1].strip() else tenancy.DEFAULT_PLAN
    email = args[2].strip() if len(args) > 2 and args[2].strip() else None
    if plan not in tenancy.PLANS:
        await message.answer(f"Неизвестный тариф. Доступны: {', '.join(tenancy.PLANS)}")
        return

    loop = asyncio.get_event_loop()
    company = await loop.run_in_executor(None, tenancy.create_company, title, plan, email)
    me = await bot.get_me()
    await message.answer(
        f"Компания *{title}* создана, тариф «{tenancy.PLANS[plan]['title']}».\n\n"
        f"Ссылка для владельца:\nhttps://t.me/{me.username}?start={company['activation_code']}",
        parse_mode="Markdown", disable_web_page_preview=True)


@dp.message(Command("spend"))
async def spend_cmd(message: Message):
    """Сколько компании стоили нам за 30 дней. Только для ADMIN_IDS."""
    if message.from_user.id not in ADMIN_IDS:
        return
    rows = db.query(
        """SELECT c.title, c.plan, c.sessions_used, c.session_limit,
                  COALESCE(SUM(u.cost_usd),0) AS usd
           FROM companies c LEFT JOIN usage_log u
             ON u.company_id=c.id AND u.created_at > now() - interval '30 days'
           GROUP BY c.id ORDER BY usd DESC""")
    if not rows:
        await message.answer("Компаний пока нет.")
        return
    lines = ["*Расход за 30 дней*", ""]
    total = 0
    for r in rows:
        usd = float(r["usd"])
        total += usd
        lines.append(f"• {r['title']} ({r['plan']}): {r['sessions_used']}/{r['session_limit']} "
                     f"тренировок, ${usd:.2f}")
    lines += ["", f"*Итого: ${total:.2f}*"]
    await message.answer("\n".join(lines), parse_mode="Markdown")


# --- Консультант ------------------------------------------------------------

@dp.callback_query(F.data == "new_situation")
async def new_situation(callback: CallbackQuery):
    conversations[callback.from_user.id] = []
    await callback.message.answer("Готов! Скидывай скрины.", reply_markup=new_situation_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "voice_last")
async def voice_last(callback: CallbackQuery):
    uid = callback.from_user.id
    text = last_answers.get(uid)
    if not text or not openai_sync:
        await callback.answer("Нет ответа для озвучки", show_alert=True)
        return
    await callback.answer("Генерирую…")
    try:
        audio = await text_to_speech(text)
        await bot.send_voice(uid, BufferedInputFile(audio, filename="answer.mp3"))
    except Exception as e:
        log.warning("Ошибка озвучки: %s", e)


async def process_media_group(user, group_id, caption):
    images = media_groups.pop(group_id, [])
    if not images:
        return
    uid = user["telegram_id"]
    await bot.send_message(uid, f"Анализирую {len(images)} скринов…")
    content = [{"type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": img}}
               for img in images]
    content.append({"type": "text",
                    "text": f"Скриншоты переписки ({len(images)} шт.)."
                            f"{' ' + caption if caption else ''} Что делать дальше?"})
    try:
        answer = await ask_claude(user, content)
        await send_answer(uid, answer)
    except Exception as e:
        log.exception("Ошибка разбора группы скринов")
        await bot.send_message(uid, "Не смог разобрать эти скрины. Попробуй ещё раз.")


@dp.message(F.photo)
async def handle_photo(message: Message):
    u = await guard(message)
    if not u:
        return
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    raw = await bot.download_file(file.file_path)
    image_data = base64.standard_b64encode(raw.read()).decode("utf-8")

    if message.media_group_id:
        gid = message.media_group_id
        cap = message.caption or ""
        media_groups.setdefault(gid, []).append(image_data)
        if gid in media_group_timers:
            media_group_timers[gid].cancel()

        async def delayed():
            await asyncio.sleep(1.5)
            await process_media_group(u, gid, cap)

        media_group_timers[gid] = asyncio.create_task(delayed())
    else:
        await message.answer("Анализирую…")
        cap = message.caption or ""
        content = [
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
            {"type": "text",
             "text": f"Скриншот переписки.{' ' + cap if cap else ''} Что делать дальше?"},
        ]
        try:
            answer = await ask_claude(u, content)
            await send_answer(message.from_user.id, answer)
        except Exception:
            log.exception("Ошибка разбора скрина")
            await message.answer("Не смог разобрать скрин. Попробуй ещё раз.")


@dp.message(F.voice | F.audio)
async def handle_voice(message: Message):
    u = await guard(message)
    if not u:
        return
    if not openai_async:
        await message.answer("Распознавание голоса сейчас недоступно — напиши текстом.")
        return
    await message.answer("Распознаю…")
    try:
        voice = message.voice or message.audio
        file = await bot.get_file(voice.file_id)
        raw = await bot.download_file(file.file_path)
        text = await transcribe_voice(raw.read())
        if not text.strip():
            await message.answer("Не удалось распознать речь.")
            return
        await message.answer(f"🎤 _{text}_", parse_mode="Markdown")
        if not get_history(message.from_user.id):
            await message.answer("Сначала скинь скриншот переписки.")
            return
        answer = await ask_claude(u, text)
        await send_answer(message.from_user.id, answer, with_voice=True)
    except Exception:
        log.exception("Ошибка обработки голоса")
        await message.answer("Не получилось обработать голосовое.")


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    u = await guard(message)
    if not u:
        return
    if not get_history(message.from_user.id):
        await message.answer("Скидывай скриншот переписки — разберём. "
                             "Или жми «🎯 Тренажёр», чтобы потренироваться.")
        return
    await message.answer("Думаю…")
    try:
        answer = await ask_claude(u, message.text)
        await send_answer(message.from_user.id, answer)
    except Exception:
        log.exception("Ошибка ответа консультанта")
        await message.answer("Что-то пошло не так. Попробуй ещё раз.")


async def main():
    db.init_db()
    if not db.healthcheck():
        raise SystemExit("База недоступна — проверь DATABASE_URL")
    me = await bot.get_me()
    log.info("Бот @%s запущен. Модель диалога: %s", me.username,
             os.environ.get("TRAINER_MODEL", "claude-sonnet-5"))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
