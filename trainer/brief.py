# -*- coding: utf-8 -*-
"""
Мастер брифа: превращает ответы владельца в профиль ниши.

Это тот самый блок, ради которого затевалась вся мультиарендность. Раньше
профиль под каждого клиента писал человек руками; теперь владелец отвечает
на восемь вопросов в боте, модель собирает профиль по схеме, он проходит
валидацию и сразу становится действующим.

Ход мастера хранится в памяти процесса: если бот перезапустится посреди
брифа, владелец начнёт заново. Это осознанный размен — бриф занимает
пять минут, а хранить недозаполненное состояние в базе означает
поддерживать его миграции ради редкого случая.
"""

import json
import logging
import re

from . import niche_loader, costs

log = logging.getLogger(__name__)

MODEL = "claude-opus-5"   # профиль пишется один раз на компанию — берём лучшую модель
MAX_ATTEMPTS = 3

# Жёсткий предел на один вызов. Без него зависший запрос держит мастер
# минутами: у SDK таймаут по умолчанию — десять минут, и всё это время
# владелец видит «собираю профиль…» и думает, что бот умер.
REQUEST_TIMEOUT = 120.0

# telegram_id -> {"company_id", "step", "answers": {...}, "draft": {...}}
WIZARDS = {}

SKIP_WORDS = {"-", "–", "—", "пропустить", "нет", "не знаю", "далее"}


QUESTIONS = [
    {
        "key": "product",
        "title": "Что вы продаёте",
        "text": ("Опишите продукт своими словами: что это, из чего состоит, "
                 "чем отличается от аналогов.\n\n"
                 "_Пример: изготавливаем панно из стабилизированного мха на заказ — "
                 "не требует ухода, служит 8–10 лет, делаем под любой размер и с логотипом._"),
        "required": True,
        "min_len": 40,
    },
    {
        "key": "audience",
        "title": "Кто у вас покупает",
        "text": ("Перечислите типы клиентов — чем конкретнее, тем лучше бот их отыграет.\n\n"
                 "_Пример: рестораны и кофейни, бутик-отели, дизайнеры интерьеров, "
                 "владельцы квартир, офисы под логотип._"),
        "required": True,
        "min_len": 25,
    },
    {
        "key": "price",
        "title": "Средний чек и цикл сделки",
        "text": ("Сколько в среднем стоит заказ и сколько времени проходит от заявки до оплаты?\n\n"
                 "_Пример: средний чек 350 тысяч тенге, от заявки до предоплаты обычно 1–2 недели._"),
        "required": True,
        "min_len": 10,
    },
    {
        "key": "channels",
        "title": "Откуда приходят заявки",
        "text": ("Через какие каналы с вами связываются?\n\n"
                 "_Пример: Instagram Direct, WhatsApp, звонки с сайта, рекомендации дизайнеров._"),
        "required": False,
        "min_len": 5,
    },
    {
        "key": "first_messages",
        "title": "Первые сообщения клиентов",
        "text": ("Напишите 3–5 реальных первых сообщений, с которых начинаются диалоги. "
                 "Дословно, как пишут люди — с опечатками и без вежливости.\n\n"
                 "_Пример:_\n"
                 "_Почём панно?_\n"
                 "_Здравствуйте, а примеры работ есть?_\n"
                 "_Нужно оформить зону ресепшн, что можете предложить?_"),
        "required": True,
        "min_len": 30,
    },
    {
        "key": "objections",
        "title": "Частые возражения",
        "text": ("Что клиенты говорят, когда сомневаются или отказываются?\n\n"
                 "_Пример: дорого · надо подумать · а оно не завянет · "
                 "посоветуюсь с мужем · сравниваю с другими._"),
        "required": True,
        "min_len": 20,
    },
    {
        "key": "advantages",
        "title": "Чем вы лучше конкурентов",
        "text": ("Почему выбирают вас, а не соседнее предложение?\n\n"
                 "_Пример: свой цех и монтажники, гарантия 3 года, делаем сложные формы, "
                 "которые другие не берут._"),
        "required": False,
        "min_len": 15,
    },
    {
        "key": "must_ask",
        "title": "Что менеджер обязан выяснить до цены",
        "text": ("Какую информацию продавец должен вытащить из клиента, "
                 "прежде чем называть стоимость?\n\n"
                 "_Пример: площадь и место размещения, срок, для чего нужно — "
                 "интерьер или фотозона, кто принимает решение._"),
        "required": True,
        "min_len": 15,
    },
]


GENERATOR_SYSTEM = """Ты собираешь профиль ниши для тренажёра отдела продаж.
Тренажёр играет роль живого покупателя, и качество отыгрыша целиком зависит
от того, насколько точно ты опишешь этот бизнес.

Тебе дают ответы владельца компании на бриф. Верни СТРОГО один JSON-объект:

{
  "id": "латиницей, коротко, по названию ниши, например moss_decor",
  "title": "человекочитаемое название ниши, до 60 знаков",
  "product_context": "СЛИТНЫЙ текст 600-1100 знаков: что продаёт компания, кому,
     ключевые свойства продукта, чем отличается от конкурентов, ценовой контекст,
     как обычно ведут себя клиенты и почему берут паузу. Пиши так, чтобы человек,
     прочитавший только этот абзац, смог достоверно сыграть покупателя этой компании.
     Не выдумывай фактов, которых нет в брифе, но связывай и разворачивай сказанное.",
  "currency": "валюта из ответов, по умолчанию тенге",
  "statuses": [
     {"id": "латиницей", "title": "кто это, 2-4 слова",
      "context": "что для него важно, чего боится, как принимает решение — 1-2 предложения"}
  ],
  "requests": ["типовой запрос клиента от первого лица, как он его формулирует"]
}

Требования, которые проверяются автоматически:
- statuses: НЕ МЕНЕЕ 8 разных типов клиентов. Бери их из ответа про аудиторию,
  разворачивай до конкретных ролей. Если владелец назвал «рестораны» — это
  «директор ресторана», «управляющий сетью кофеен», «шеф-повар нового заведения».
  Типы должны отличаться мотивацией и бюджетом, а не только названием.
- requests: НЕ МЕНЕЕ 10 запросов. Половину возьми из реальных первых сообщений
  владельца, сохранив его манеру. Остальные дострой по смыслу: разные бюджеты,
  разные поводы, разная степень определённости. Обязательно добавь пару
  размытых («хочу что-то красивое, сам не знаю что») и пару с возражением
  внутри («интересует, но переживаю за качество»).
- product_context не короче 600 знаков.

Пиши по-русски, живым языком отрасли. Без markdown, без пояснений вне JSON."""


def is_active(telegram_id):
    return telegram_id in WIZARDS


def start(telegram_id, company_id):
    WIZARDS[telegram_id] = {"company_id": company_id, "step": 0, "answers": {}, "draft": None}
    return QUESTIONS[0]


def cancel(telegram_id):
    return WIZARDS.pop(telegram_id, None) is not None


def current_question(telegram_id):
    w = WIZARDS.get(telegram_id)
    if not w or w["step"] >= len(QUESTIONS):
        return None
    return QUESTIONS[w["step"]]


def question_text(q, index, total):
    req = "" if q["required"] else "\n\n_Необязательно — можно ответить «пропустить»._"
    return f"*Вопрос {index} из {total}. {q['title']}*\n\n{q['text']}{req}"


def back(telegram_id):
    """Вернуться на шаг назад. Возвращает вопрос либо None."""
    w = WIZARDS.get(telegram_id)
    if not w or w["step"] == 0:
        return None
    w["step"] -= 1
    q = QUESTIONS[w["step"]]
    w["answers"].pop(q["key"], None)
    return q


def awaiting_confirmation(telegram_id):
    """Все вопросы отвечены — мастер ждёт решения по черновику."""
    w = WIZARDS.get(telegram_id)
    return bool(w) and w["step"] >= len(QUESTIONS)


def is_generating(telegram_id):
    w = WIZARDS.get(telegram_id)
    return bool(w) and w.get("generating", False)


def submit_answer(telegram_id, text):
    """
    Принять ответ. Возвращает (следующий_вопрос, ошибка, готово).
    Если готово=True — все вопросы отвечены, пора генерировать.
    """
    w = WIZARDS.get(telegram_id)
    if not w:
        return None, "Мастер не запущен. Наберите /setup.", False

    # Страховка: после последнего вопроса мастер остаётся активным, пока
    # владелец не подтвердит профиль. Любое сообщение в этот момент раньше
    # обращалось к QUESTIONS[8] и роняло хендлер — бот замолкал целиком.
    if w["step"] >= len(QUESTIONS):
        return None, None, True

    q = QUESTIONS[w["step"]]
    answer = (text or "").strip()
    skipped = answer.lower() in SKIP_WORDS

    if skipped and q["required"]:
        return q, "Этот вопрос пропустить нельзя — без него бот не сможет играть ваших клиентов.", False

    if not skipped and len(answer) < q["min_len"]:
        return q, (f"Слишком коротко. Нужно хотя бы {q['min_len']} знаков — "
                   f"чем подробнее ответ, тем достовернее бот отыграет вашего покупателя."), False

    w["answers"][q["key"]] = "" if skipped else answer
    w["step"] += 1

    if w["step"] >= len(QUESTIONS):
        return None, None, True
    return QUESTIONS[w["step"]], None, False


def _brief_text(answers):
    lines = []
    for q in QUESTIONS:
        val = answers.get(q["key"], "")
        if val:
            lines.append(f"{q['title'].upper()}:\n{val}")
    return "\n\n".join(lines)


def _extract_json(text):
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?", "", text.strip()).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    start_i, end_i = cleaned.find("{"), cleaned.rfind("}")
    if start_i != -1 and end_i > start_i:
        cleaned = cleaned[start_i:end_i + 1]
    try:
        return json.loads(cleaned)
    except Exception:
        return None


def generate(client, telegram_id, remark=None):
    """
    Собрать профиль из ответов. Возвращает (профиль, ошибка, расход).
    При провале валидации переспрашивает модель, до MAX_ATTEMPTS раз.
    """
    w = WIZARDS.get(telegram_id)
    if not w:
        return None, "Мастер не запущен.", None

    user_content = f"ОТВЕТЫ ВЛАДЕЛЬЦА:\n\n{_brief_text(w['answers'])}"
    if remark:
        user_content += (f"\n\nВЛАДЕЛЕЦ ПОПРОСИЛ ПЕРЕДЕЛАТЬ. Его замечание:\n{remark}\n"
                         f"Учти его и собери профиль заново.")

    total_usage = {"input_tokens": 0, "output_tokens": 0, "cache_write": 0, "cache_read": 0}
    last_error = None
    w["generating"] = True

    try:
        return _generate_loop(client, w, user_content, total_usage)
    finally:
        w["generating"] = False


def _generate_loop(client, w, user_content, total_usage):
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=4000,
                system=GENERATOR_SYSTEM,
                messages=[{"role": "user", "content": user_content}],
                timeout=REQUEST_TIMEOUT,
            )
        except TypeError:
            # Заглушки в тестах не знают про timeout — повторяем без него.
            resp = client.messages.create(
                model=MODEL,
                max_tokens=4000,
                system=GENERATOR_SYSTEM,
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception:
            log.exception("Ошибка обращения к модели при генерации профиля")
            return None, ("Модель не ответила вовремя. Наберите /setup ещё раз — "
                          "ответы придётся ввести заново."), total_usage

        total_usage = costs.add(total_usage, costs.usage_dict(resp))
        raw = resp.content[0].text if resp.content else ""
        data = _extract_json(raw)

        if data is None:
            last_error = "модель вернула не JSON"
            user_content += "\n\nПРЕДЫДУЩАЯ ПОПЫТКА: ответ не был корректным JSON. Верни только JSON."
            continue

        # Требования тренажёра выше минимальных в схеме: мало типов клиентов —
        # и сценарии начнут повторяться уже на второй день.
        if len(data.get("statuses") or []) < 8:
            last_error = "мало типов клиентов"
            user_content += (f"\n\nПРЕДЫДУЩАЯ ПОПЫТКА: было "
                             f"{len(data.get('statuses') or [])} типов клиентов, нужно минимум 8. "
                             f"Разверни аудиторию до конкретных ролей.")
            continue
        if len(data.get("requests") or []) < 10:
            last_error = "мало запросов"
            user_content += (f"\n\nПРЕДЫДУЩАЯ ПОПЫТКА: было "
                             f"{len(data.get('requests') or [])} запросов, нужно минимум 10.")
            continue

        try:
            niche_loader.validate(data)
        except niche_loader.InvalidProfile as e:
            last_error = str(e)
            user_content += f"\n\nПРЕДЫДУЩАЯ ПОПЫТКА не прошла проверку: {e}. Исправь."
            log.warning("Профиль не прошёл валидацию (попытка %s): %s", attempt, e)
            continue
        except Exception as e:
            # Модель может вернуть структуру неожиданного вида — например,
            # строки вместо объектов в statuses. Валидатор на этом упадёт
            # не своим исключением; молча ронять мастер нельзя.
            last_error = f"неожиданная структура ответа ({type(e).__name__})"
            user_content += ("\n\nПРЕДЫДУЩАЯ ПОПЫТКА имела неверную структуру. "
                            "Строго соблюдай формат полей.")
            log.warning("Профиль неожиданной структуры (попытка %s): %s", attempt, e)
            continue

        w["draft"] = data
        log.info("Профиль для компании %s собран с попытки %s", w["company_id"], attempt)
        return data, None, total_usage

    return None, (f"Не получилось собрать профиль за {MAX_ATTEMPTS} попытки "
                  f"({last_error}). Попробуйте /setup ещё раз и опишите продукт подробнее."), total_usage


def confirm(telegram_id):
    """Сохранить черновик как действующий профиль. Возвращает (версия, ошибка)."""
    w = WIZARDS.get(telegram_id)
    if not w or not w["draft"]:
        return None, "Нечего сохранять — сначала соберите профиль через /setup."
    try:
        version = niche_loader.save_profile(w["company_id"], w["draft"], brief=w["answers"])
    except niche_loader.InvalidProfile as e:
        return None, f"Профиль не прошёл финальную проверку: {e}"
    WIZARDS.pop(telegram_id, None)
    return version, None


def progress_bar(step, total):
    done = "▰" * step
    left = "▱" * (total - step)
    return f"{done}{left}"
