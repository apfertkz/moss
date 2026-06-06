import os
import asyncio
import base64
import anthropic
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart

BOT_TOKEN = os.environ["BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """Ты — эксперт по продажам, обученный на методологии Михаила Гребенюка ("Отдел продаж по захвату рынка").

Твоя задача: анализировать скриншоты переписок с клиентами и давать конкретные рекомендации по следующему шагу в диалоге.

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

ФОРМАТ ОТВЕТА:
📊 **Анализ ситуации**
Где сейчас находится клиент в воронке, что он думает/чувствует.

⚠️ **Ошибки в диалоге** (если есть)
Что было сделано не по методологии.

✅ **Следующий шаг**
Конкретное действие — что именно написать/сказать клиенту.

💬 **Готовый текст сообщения**
Текст который можно скопировать и отправить клиенту прямо сейчас.

Будь конкретным, практичным, без воды. Давай готовые формулировки."""


@dp.message(CommandStart())
async def start(message: Message):
    if ALLOWED_USER_ID and message.from_user.id != ALLOWED_USER_ID:
        return
    await message.answer(
        "Привет! Скидывай скрин переписки с клиентом — разберу по методологии Гребенюка и скажу что писать дальше."
    )


@dp.message(F.photo)
async def handle_photo(message: Message):
    if ALLOWED_USER_ID and message.from_user.id != ALLOWED_USER_ID:
        return

    await message.answer("Анализирую диалог...")

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file.file_path)
    image_data = base64.standard_b64encode(file_bytes.read()).decode("utf-8")

    caption = message.caption or ""
    user_text = f"Вот скриншот переписки с клиентом.{' Контекст: ' + caption if caption else ''} Что делать дальше?"

    try:
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_data,
                            },
                        },
                        {"type": "text", "text": user_text},
                    ],
                }
            ],
        )

        answer = response.content[0].text
        await message.answer(answer, parse_mode="Markdown")

    except Exception as e:
        await message.answer(f"Ошибка: {str(e)}")


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    if ALLOWED_USER_ID and message.from_user.id != ALLOWED_USER_ID:
        return

    await message.answer("Скидывай скриншот переписки — разберу ситуацию.")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
