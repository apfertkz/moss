import os
import asyncio
import base64
import anthropic
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.environ["BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# История диалогов: user_id -> list of messages
conversations = {}

# Медиагруппы: media_group_id -> list of images (для нескольких скринов)
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
    return builder.as_markup()


def get_history(user_id):
    if user_id not in conversations:
        conversations[user_id] = []
    return conversations[user_id]


def add_to_history(user_id, role, content):
    history = get_history(user_id)
    history.append({"role": role, "content": content})
    # Храним последние 20 сообщений
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


@dp.message(CommandStart())
async def start(message: Message):
    if ALLOWED_USER_ID and message.from_user.id != ALLOWED_USER_ID:
        return
    conversations[message.from_user.id] = []
    await message.answer(
        "Привет! Скидывай скрины переписки с клиентом — разберу по методологии Гребенюка.\n\n"
        "Можешь кидать сразу несколько скринов, задавать уточняющие вопросы и вести диалог.\n"
        "Когда начнёшь новую ситуацию — нажми кнопку ниже.",
        reply_markup=new_situation_keyboard()
    )


@dp.callback_query(F.data == "new_situation")
async def new_situation(callback: CallbackQuery):
    if ALLOWED_USER_ID and callback.from_user.id != ALLOWED_USER_ID:
        return
    conversations[callback.from_user.id] = []
    await callback.message.answer(
        "Готов! Скидывай скрины новой ситуации.",
        reply_markup=new_situation_keyboard()
    )
    await callback.answer()


async def process_media_group(user_id, group_id, caption):
    images = media_groups.pop(group_id, [])
    if not images:
        return

    await bot.send_message(user_id, f"Анализирую {len(images)} скринов...")

    content = []
    for image_data in images:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": image_data,
            },
        })

    text = f"Вот скриншоты переписки с клиентом ({len(images)} шт.).{' Контекст: ' + caption if caption else ''} Что делать дальше?"
    content.append({"type": "text", "text": text})

    try:
        answer = await ask_claude(user_id, content)
        await bot.send_message(user_id, answer, parse_mode="Markdown", reply_markup=new_situation_keyboard())
    except Exception as e:
        await bot.send_message(user_id, f"Ошибка: {str(e)}")


@dp.message(F.photo)
async def handle_photo(message: Message):
    if ALLOWED_USER_ID and message.from_user.id != ALLOWED_USER_ID:
        return

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file.file_path)
    image_data = base64.standard_b64encode(file_bytes.read()).decode("utf-8")

    # Если это группа фото
    if message.media_group_id:
        group_id = message.media_group_id
        user_id = message.from_user.id
        caption = message.caption or ""

        if group_id not in media_groups:
            media_groups[group_id] = []

        media_groups[group_id].append(image_data)

        # Отменяем предыдущий таймер если есть
        if group_id in media_group_timers:
            media_group_timers[group_id].cancel()

        # Запускаем таймер — через 1.5 сек обрабатываем группу
        async def delayed():
            await asyncio.sleep(1.5)
            await process_media_group(user_id, group_id, caption)

        task = asyncio.create_task(delayed())
        media_group_timers[group_id] = task

    else:
        # Одиночное фото
        await message.answer("Анализирую...")
        caption = message.caption or ""
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": image_data,
                },
            },
            {"type": "text", "text": f"Вот скриншот переписки с клиентом.{' Контекст: ' + caption if caption else ''} Что делать дальше?"}
        ]

        try:
            answer = await ask_claude(message.from_user.id, content)
            await message.answer(answer, parse_mode="Markdown", reply_markup=new_situation_keyboard())
        except Exception as e:
            await message.answer(f"Ошибка: {str(e)}")


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    if ALLOWED_USER_ID and message.from_user.id != ALLOWED_USER_ID:
        return

    history = get_history(message.from_user.id)
    if not history:
        await message.answer("Скидывай скриншот переписки — разберём ситуацию.")
        return

    await message.answer("Думаю...")

    try:
        answer = await ask_claude(message.from_user.id, message.text)
        await message.answer(answer, parse_mode="Markdown", reply_markup=new_situation_keyboard())
    except Exception as e:
        await message.answer(f"Ошибка: {str(e)}")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
