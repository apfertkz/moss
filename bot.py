import os
import asyncio
import base64
import tempfile
import urllib.request

import anthropic
from openai import AsyncOpenAI
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.environ["BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ALLOWED_USER_IDS = set(
    int(x.strip()) for x in os.environ.get("ALLOWED_USER_ID", "0").split(",") if x.strip()
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

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
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Новая ситуация", callback_data="new_situation")
    builder.button(text="🔊 Озвучить", callback_data="voice_last")
    builder.adjust(2)
    return builder.as_markup()


def is_allowed(user_id):
    return not (ALLOWED_USER_IDS - {0}) or user_id in ALLOWED_USER_IDS


def get_history(user_id):
    if user_id not in conversations:
        conversations[user_id] = []
    return conversations[user_id]


def add_to_history(user_id, role, content):
    history = get_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > 20:
        conversations[user_id] = history[-20:]


async def ask_claude(user_id, content):
    add_to_history(user_id, "user", content)
    history = get_history(user_id)
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=history,
    )
    answer = response.content[0].text
    add_to_history(user_id, "assistant", answer)
    return answer


async def transcribe_voice(file_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    with open(tmp_path, "rb") as audio_file:
        transcript = await openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=("voice.ogg", audio_file, "audio/ogg"),
            language="ru"
        )
    os.unlink(tmp_path)
    return transcript.text


def text_to_speech_sync(text: str) -> bytes:
    from gtts import gTTS
    import io
    clean = text.replace("**", "").replace("*", "").replace("#", "").replace("`", "").replace("_", "")
    if len(clean) > 1000:
        clean = clean[:1000]

    params = urllib.parse.urlencode({
        "ie": "UTF-8",
        "q": clean,
        "tl": "ru",
        "client": "tw-ob",
    })
    url = f"https://translate.google.com/translate_tts?{params}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


async def text_to_speech(text: str) -> bytes:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, text_to_speech_sync, text)


async def send_answer(user_id: int, text: str, with_voice: bool = False):
    last_answers[user_id] = text
    await bot.send_message(user_id, text, parse_mode="Markdown", reply_markup=new_situation_keyboard())
    if with_voice:
        try:
            audio = await text_to_speech(text)
            await bot.send_voice(user_id, BufferedInputFile(audio, filename="answer.mp3"))
        except Exception as e:
            await bot.send_message(user_id, f"⚠️ Голос недоступен: {e}")


@dp.message(CommandStart())
async def start(message: Message):
    if not is_allowed(message.from_user.id):
        return
    conversations[message.from_user.id] = []
    await message.answer(
        "Привет! Скидывай скрины переписки — разберу по Гребенюку.\n\n"
        "🔊 Кнопка озвучит последний ответ голосом.",
        reply_markup=new_situation_keyboard()
    )


@dp.callback_query(F.data == "new_situation")
async def new_situation(callback: CallbackQuery):
    if not is_allowed(callback.from_user.id):
        return
    conversations[callback.from_user.id] = []
    await callback.message.answer("Готов! Скидывай скрины.", reply_markup=new_situation_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "voice_last")
async def voice_last(callback: CallbackQuery):
    if not is_allowed(callback.from_user.id):
        return
    user_id = callback.from_user.id
    text = last_answers.get(user_id)
    if not text:
        await callback.answer("Нет ответа для озвучки", show_alert=True)
        return
    await callback.answer("Генерирую...")
    try:
        audio = await text_to_speech(text)
        await bot.send_voice(user_id, BufferedInputFile(audio, filename="answer.mp3"))
    except Exception as e:
        await bot.send_message(user_id, f"⚠️ Ошибка голоса: {e}")


async def process_media_group(user_id, group_id, caption):
    images = media_groups.pop(group_id, [])
    if not images:
        return
    await bot.send_message(user_id, f"Анализирую {len(images)} скринов...")
    content = []
    for img in images:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img}})
    content.append({"type": "text", "text": f"Скриншоты переписки ({len(images)} шт.).{' ' + caption if caption else ''} Что делать дальше?"})
    try:
        answer = await ask_claude(user_id, content)
        await send_answer(user_id, answer)
    except Exception as e:
        await bot.send_message(user_id, f"Ошибка: {e}")


@dp.message(F.photo)
async def handle_photo(message: Message):
    if not is_allowed(message.from_user.id):
        return
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file.file_path)
    image_data = base64.standard_b64encode(file_bytes.read()).decode("utf-8")

    if message.media_group_id:
        gid = message.media_group_id
        uid = message.from_user.id
        cap = message.caption or ""
        if gid not in media_groups:
            media_groups[gid] = []
        media_groups[gid].append(image_data)
        if gid in media_group_timers:
            media_group_timers[gid].cancel()
        async def delayed():
            await asyncio.sleep(1.5)
            await process_media_group(uid, gid, cap)
        media_group_timers[gid] = asyncio.create_task(delayed())
    else:
        await message.answer("Анализирую...")
        cap = message.caption or ""
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
            {"type": "text", "text": f"Скриншот переписки.{' ' + cap if cap else ''} Что делать дальше?"}
        ]
        try:
            answer = await ask_claude(message.from_user.id, content)
            await send_answer(message.from_user.id, answer)
        except Exception as e:
            await message.answer(f"Ошибка: {e}")


@dp.message(F.voice | F.audio)
async def handle_voice(message: Message):
    if not is_allowed(message.from_user.id):
        return
    await message.answer("Распознаю...")
    try:
        voice = message.voice or message.audio
        file = await bot.get_file(voice.file_id)
        file_bytes = await bot.download_file(file.file_path)
        text = await transcribe_voice(file_bytes.read())
        if not text.strip():
            await message.answer("Не удалось распознать речь.")
            return
        await message.answer(f"🎤 _{text}_", parse_mode="Markdown")
        if not get_history(message.from_user.id):
            await message.answer("Сначала скинь скриншот переписки.")
            return
        answer = await ask_claude(message.from_user.id, text)
        await send_answer(message.from_user.id, answer, with_voice=True)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    if not is_allowed(message.from_user.id):
        return
    if not get_history(message.from_user.id):
        await message.answer("Скидывай скриншот переписки — разберём.")
        return
    await message.answer("Думаю...")
    try:
        answer = await ask_claude(message.from_user.id, message.text)
        await send_answer(message.from_user.id, answer)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
