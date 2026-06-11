import os
import asyncio
import base64
import tempfile
import httpx
import anthropic
from openai import AsyncOpenAI
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.environ["BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ELEVENLABS_API_KEY = os.environ["ELEVENLABS_API_KEY"]
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "WTn2eCRCpoFAC50VD351")
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

SYSTEM_PROMPT = """Ты — эксперт по продажам, обученный на методологии Михаила Гребенюка ("Отдел продаж по захвату рынка").

Ты ведёшь полноценную консультацию: анализируешь скриншоты переписок с клиентами, даёшь рекомендации, отвечаешь на вопросы, помогаешь формулировать сообщения и разбираешь ситуации.

Помни контекст всего разговора — если пользователь задаёт уточняющие вопросы, отвечай с учётом предыдущих скринов и обсуждений.

КЛЮЧЕВЫЕ ПРИНЦИПЫ ГРЕБЕНЮКА:
1. Цель каждого сообщения — двигать клиента к следующему шагу воронки, не болтать
2. Всегда выявляй: есть ли боль, есть ли деньги, есть ли полномочия для решения
3. Не отвечай на вопрос о цене сразу — сначала выяви потребность
4. Используй технику "Уступ": если клиент задаёт вопрос, отвечай вопросом
5. Программируй следующий шаг в каждом сообщении ("Давайте созвонимся в среду в 15:00?")
6. Избегай стоп-слов: "наверное", "может быть", "если что", "в принципе"
7. Социальные доказательства — показывай кейсы похожих клиентов
8. Дожим: если клиент завис — используй "Что мешает принять решение прямо сейчас?"
9. Никогда не проси "подумать" — всегда назначай конкретное действие
10. Квалификация клиента — не трать время на нецелевых

При анализе скриншотов используй формат:
📊 **Анализ ситуации**
Где сейчас находится клиент в воронке, что он думает/чувствует.

⚠️ **Ошибки в диалоге** (если есть)
Что было сделано не по методологии.

✅ **Следующий шаг**
Конкретное действие — что именно написать/сказать клиенту.

💬 **Готовый текст сообщения**
Текст который можно скопировать и отправить клиенту прямо сейчас.

При текстовых вопросах — отвечай как опытный консультант по продажам, без лишнего формата."""


def new_situation_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Новая ситуация", callback_data="new_situation")
    builder.button(text="🔊 Голосовой ответ", callback_data="voice_last")
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


async def text_to_speech(text: str) -> bytes:
    # Убираем markdown символы для чистой речи
    clean = text.replace("**", "").replace("*", "").replace("#", "").replace("`", "")
    # Ограничиваем длину — ElevenLabs лучше работает с короткими текстами
    if len(clean) > 2500:
        clean = clean[:2500] + "..."

    async with httpx.AsyncClient(timeout=30) as http:
        response = await http.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "text": clean,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    "style": 0.3,
                    "use_speaker_boost": True
                }
            }
        )
        response.raise_for_status()
        return response.content


# Хранит последний ответ бота для озвучки
last_answers = {}


@dp.message(CommandStart())
async def start(message: Message):
    if not is_allowed(message.from_user.id):
        return
    conversations[message.from_user.id] = []
    await message.answer(
        "Привет! Скидывай скрины переписки с клиентом — разберу по методологии Гребенюка.\n\n"
        "Можешь писать, говорить голосовым или кидать скрины.\n"
        "Кнопка 🔊 озвучит последний ответ голосом.",
        reply_markup=new_situation_keyboard()
    )


@dp.callback_query(F.data == "new_situation")
async def new_situation(callback: CallbackQuery):
    if not is_allowed(callback.from_user.id):
        return
    conversations[callback.from_user.id] = []
    await callback.message.answer("Готов! Скидывай скрины новой ситуации.", reply_markup=new_situation_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "voice_last")
async def voice_last(callback: CallbackQuery):
    if not is_allowed(callback.from_user.id):
        return
    user_id = callback.from_user.id
    text = last_answers.get(user_id)
    if not text:
        await callback.answer("Нет последнего ответа для озвучки", show_alert=True)
        return
    await callback.answer("Генерирую голос...")
    try:
        audio_bytes = await text_to_speech(text)
        await bot.send_voice(
            user_id,
            BufferedInputFile(audio_bytes, filename="answer.mp3")
        )
    except Exception as e:
        await bot.send_message(user_id, f"Ошибка голоса: {str(e)}")


async def send_answer(user_id: int, text: str, voice: bool = False):
    last_answers[user_id] = text
    await bot.send_message(user_id, text, parse_mode="Markdown", reply_markup=new_situation_keyboard())
    if voice:
        try:
            audio_bytes = await text_to_speech(text)
            await bot.send_voice(user_id, BufferedInputFile(audio_bytes, filename="answer.mp3"))
        except Exception as e:
            await bot.send_message(user_id, f"(Голос недоступен: {str(e)})")


async def process_media_group(user_id, group_id, caption):
    images = media_groups.pop(group_id, [])
    if not images:
        return
    await bot.send_message(user_id, f"Анализирую {len(images)} скринов...")
    content = []
    for image_data in images:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data},
        })
    text = f"Вот скриншоты переписки с клиентом ({len(images)} шт.).{' Контекст: ' + caption if caption else ''} Что делать дальше?"
    content.append({"type": "text", "text": text})
    try:
        answer = await ask_claude(user_id, content)
        await send_answer(user_id, answer)
    except Exception as e:
        await bot.send_message(user_id, f"Ошибка: {str(e)}")


@dp.message(F.photo)
async def handle_photo(message: Message):
    if not is_allowed(message.from_user.id):
        return
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file.file_path)
    image_data = base64.standard_b64encode(file_bytes.read()).decode("utf-8")

    if message.media_group_id:
        group_id = message.media_group_id
        user_id = message.from_user.id
        caption = message.caption or ""
        if group_id not in media_groups:
            media_groups[group_id] = []
        media_groups[group_id].append(image_data)
        if group_id in media_group_timers:
            media_group_timers[group_id].cancel()
        async def delayed():
            await asyncio.sleep(1.5)
            await process_media_group(user_id, group_id, caption)
        task = asyncio.create_task(delayed())
        media_group_timers[group_id] = task
    else:
        await message.answer("Анализирую...")
        caption = message.caption or ""
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
            {"type": "text", "text": f"Вот скриншот переписки с клиентом.{' Контекст: ' + caption if caption else ''} Что делать дальше?"}
        ]
        try:
            answer = await ask_claude(message.from_user.id, content)
            await send_answer(message.from_user.id, answer)
        except Exception as e:
            await message.answer(f"Ошибка: {str(e)}")


@dp.message(F.voice | F.audio)
async def handle_voice(message: Message):
    if not is_allowed(message.from_user.id):
        return
    await message.answer("Распознаю голосовое...")
    try:
        voice = message.voice or message.audio
        file = await bot.get_file(voice.file_id)
        file_bytes = await bot.download_file(file.file_path)
        text = await transcribe_voice(file_bytes.read())
        if not text.strip():
            await message.answer("Не удалось распознать речь.")
            return
        await message.answer(f"🎤 _«{text}»_", parse_mode="Markdown")
        history = get_history(message.from_user.id)
        if not history:
            await message.answer("Скидывай скриншот переписки — разберём ситуацию.")
            return
        answer = await ask_claude(message.from_user.id, text)
        await send_answer(message.from_user.id, answer, voice=True)
    except Exception as e:
        await message.answer(f"Ошибка: {str(e)}")


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    if not is_allowed(message.from_user.id):
        return
    history = get_history(message.from_user.id)
    if not history:
        await message.answer("Скидывай скриншот переписки — разберём ситуацию.")
        return
    await message.answer("Думаю...")
    try:
        answer = await ask_claude(message.from_user.id, message.text)
        await send_answer(message.from_user.id, answer)
    except Exception as e:
        await message.answer(f"Ошибка: {str(e)}")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
