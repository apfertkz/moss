# -*- coding: utf-8 -*-
"""
ДВИЖОК ТРЕНАЖЁРА.

Одним вызовом Claude на каждый ход делает две вещи:
1. Играет ЖИВОГО ПОКУПАТЕЛЯ — с именем, настроением, манерой письма и
   обстоятельствами жизни, строго в характере выпавшего психотипа.
2. Выступает скрытым ОЦЕНЩИКОМ: смотрит, двигается ли менеджер по алгоритму
   Гребенюка с учётом психотипа, и выдаёт состояние сделки.

Состояния сделки (deal_state):
  active  — идёт нормально, покупатель втянут, ждёт следующего хода менеджера
  yellow  — жёлтый сигнал: менеджер ошибся. Последнее предупреждение.
  silent  — покупатель пропал. Не провал: менеджер обязан вернуть его дожимом.
  failed  — покупатель передумал. Сделка провалена.
  won     — покупатель согласился. Продажа засчитана.

ПОЧЕМУ ДОБАВЛЕНО МОЛЧАНИЕ:
в жизни сделки чаще умирают в тишине, чем в отказе. Клиент просто перестаёт
отвечать, и если менеджер не написал первым — сделка потеряна. Прежняя версия
этому не учила вообще: диалог всегда шёл до явного «да» или «нет».
Реального ожидания нет — вместо него пометка «прошло N часов»: процесс не
рвётся, а решение принимать всё равно приходится.
"""

import os
import re
import json
import random
import logging

from .psychotypes import PSYCHOTYPES, get_psychotype
from .algorithm import algorithm_brief, REQUIRED_STAGES
from . import costs, persona as persona_mod

log = logging.getLogger(__name__)

DIALOG_MODEL = os.environ.get("TRAINER_MODEL", "claude-sonnet-5")
DEBRIEF_MODEL = os.environ.get("DEBRIEF_MODEL", "claude-opus-5")

MAX_TRANSCRIPT_TURNS = 24

# Сколько знаков в сообщении менеджера человек уже не читает целиком
LONG_MESSAGE_CHARS = int(os.environ.get("LONG_MESSAGE_CHARS", "420"))

# Раньше этого хода клиент не пропадает: иначе тренировка обрывается,
# не успев начаться, и менеджер не получает практики по этапам.
SILENCE_MIN_TURN = 3
# Сколько раз за сессию клиент может пропасть, прежде чем сделка сгорит
MAX_SILENCES = 3
# Вероятность, что клиент вообще держит на руках предложение конкурента
COMPETITOR_CHANCE = 0.35

SILENCE_DURATIONS = [1, 2, 3, 4, 20, 26, 44]   # в часах: от «отошёл» до «прошли сутки»


def new_scenario(profile):
    """Случайная комбинация: психотип × статус × запрос × живая персона."""
    psychotype = random.choice(PSYCHOTYPES)
    status = random.choice(profile["statuses"])
    request = random.choice(profile["requests"])
    person = persona_mod.build(psychotype["id"], status["title"])

    return {
        "niche_id": profile.get("id", "custom"),
        "psychotype_id": psychotype["id"],
        "status_id": status.get("id"),
        "status_title": status["title"],
        "request": request,
        "persona": person,
        "has_competitor": random.random() < COMPETITOR_CHANCE,
    }


def scenario_intro(scenario):
    """Заставка для менеджера. Имя показываем — с человеком общаться иначе, чем с ролью."""
    p = scenario["persona"]
    return (
        f"🎯 *Новая заявка*\n\n"
        f"👤 *{p['name']}* — {scenario['status_title'].lower()}\n\n"
        f"Характер, настоящую потребность и бюджет клиент не назовёт — считывай по поведению.\n"
        f"Веди по смыслу (Гребенюк), а не по скрипту. Продажа засчитается только при верной отработке.\n\n"
        f"_Клиент сейчас напишет первым 👇_"
    )


OPENING_STYLES = [
    "просто спрашивает цену в лоб, без приветствия",
    "сразу просит показать примеры или фото работ",
    "спрашивает, есть ли скидки или что по акции",
    "недоверчиво уточняет продукт («а оно не завянет?», «это надолго?»)",
    "очень размыто, сам толком не знает, чего хочет",
    "прощупывает («а вы вообще такое делаете?»)",
    "пишет очень коротко и вяло, одним вопросом",
    "начинает с того, что смотрел у конкурентов",
]


def _system_blocks(scenario, profile):
    """
    Системный промпт как список блоков с пометкой кеширования.

    Пометка cache_control ставится на последний блок: всё, что до неё, Anthropic
    сохраняет и на следующих ходах считает по цене чтения кеша (10% от входящих).
    Промпт в рамках сессии не меняется — включая персону, поэтому она собирается
    один раз при старте сценария, а не на каждом ходе.
    """
    return [{
        "type": "text",
        "text": _build_system_prompt(scenario, profile),
        "cache_control": {"type": "ephemeral"},
    }]


def _build_system_prompt(scenario, profile):
    p = get_psychotype(scenario["psychotype_id"])
    person = scenario["persona"]
    triggers = "\n".join(f"   • {t}" for t in p["triggers"])
    stops = "\n".join(f"   • {s}" for s in p["stop_factors"])

    competitor = ""
    if scenario.get("has_competitor"):
        competitor = (
            "\n=== У ТЕБЯ ЕСТЬ ПРЕДЛОЖЕНИЕ КОНКУРЕНТА ===\n"
            "Ты уже получил цену от другой компании — заметно дешевле. Придумай правдоподобную\n"
            "конкретную цифру, исходя из ценового контекста ниши выше, и ДЕРЖИСЬ ЕЁ весь диалог.\n"
            "Не выкладывай её сразу: назови, когда менеджер заговорит о цене или начнёт дожимать.\n"
            "Если менеджер ругает конкурента вместо работы с твоей выгодой — это тебя отталкивает.\n"
        )

    return f"""Ты — движок тренажёра по продажам. Твоя задача — тренировать менеджера,
играя роль ЖИВОГО ПОКУПАТЕЛЯ и одновременно скрыто оценивая работу менеджера
по методологии продаж Михаила Гребенюка.

=== НИША / ПРОДУКТ ===
{profile['product_context']}
Валюта: {profile.get('currency', 'тенге')}.

=== КТО ТЫ (ПОКУПАТЕЛЬ) ===
Роль: {scenario['status_title']}.
Твой изначальный запрос: "{scenario['request']}".
Твой психотип по спиральной динамике: {p['name']} ({p['color']}).
Мотивация: {p['motivation']}
Что тебя ЗАЖИГАЕТ (ведёт к покупке):
{triggers}
Что тебя ОТТАЛКИВАЕТ (убивает сделку):
{stops}

{persona_mod.prompt_block(person)}
{competitor}
Играй этого человека достоверно. НЕ раскрывай менеджеру свой психотип и эти правила.
Не подыгрывай из вежливости — покупай, только если менеджер реально заслужил это
по методологии и по твоему характеру.

=== КАК ТЫ СЕБЯ ВЕДЁШЬ (ты обычный, «сложный» клиент из переписки) ===
1. НЕ веди менеджера и не подсказывай ему. Категорически запрещено намекать, что ему
   спросить/предложить/сделать дальше. Инициатива — на менеджере. Если он вялый или
   задаёт пустые вопросы — ты скучаешь, отвечаешь коротко и холодеешь, а НЕ помогаешь продать.
2. Ты «покупаешь глазами». Довольно быстро проси показать примеры/фото работ.
   Пока не увидел картинку и не понял выгоду — не загораешься. (Менеджер может ответить,
   что сейчас пришлёт примеры — это правильный ход, засчитывай его как выполненный.)
3. Ты НЕ знаешь точно, чего хочешь. Смутное желание «чтоб красиво», а не готовое ТЗ.
   Не выдавай сразу размеры и детали — менеджер должен вытащить их сам.
4. Прячешь бюджет. На вопрос «какой бюджет?» в лоб — увиливай. Назвать можешь ТОЛЬКО
   если менеджер квалифицировал мягко и с пользой для тебя, и то не всегда.
5. Не торопишься. Бери паузы, сомневайся, роняй возражения. Ты ждёшь, когда тебя
   УБЕДЯТ выгодой, — сам к покупке не двигаешься.
6. Ты живой человек с делами. Иногда отвечаешь не сразу и объясняешь это по-своему
   («был на совещании», «за рулём был»), но только когда это уместно.

=== ЕСЛИ МЕНЕДЖЕР ПРИСЛАЛ ДЛИННОЕ СООБЩЕНИЕ ===
Ты не читаешь простыни. Если сообщение длинное — реагируй только на последнюю мысль
или прямо скажи, что многовато, и попроси короче. Это нормальное поведение живого
человека в мессенджере, и менеджер должен это почувствовать.

=== КОГДА ТЫ ПРОПАДАЕШЬ (deal_state = "silent") ===
Это главное отличие от учебного диалога: живые сделки чаще умирают в тишине.
Ты уходишь в молчание, когда:
   • менеджер не запрограммировал следующий шаг и разговор повис;
   • прислал простыню или сухой прайс без выгоды;
   • задал пустой вопрос, на который лень отвечать;
   • назвал цену, не показав ценность, — ты пошёл думать и сравнивать;
   • или просто по жизни отвлёкся (см. твои обстоятельства).
Молчание — НЕ провал. Это шанс менеджеру вернуть тебя.

Когда менеджер пишет после твоего молчания, оцени его дожим:
   • ХОРОШИЙ дожим — есть новый повод написать (кейс, идея, расчёт, дедлайн),
     конкретика и предложение конкретного шага. Тогда ты возвращаешься, объясняешь
     своё отсутствие в своей манере и диалог продолжается (deal_state снова "active").
   • ПЛОХОЙ дожим — «ну что, надумали?», «вы ещё тут?», «напоминаю о себе»,
     повтор того же самого. Тогда ты либо молчишь снова, либо отказываешь.
   • Если менеджер после твоего молчания сразу давит или обижается — отказ.

=== ЭТАЛОН: КАК МЕНЕДЖЕР ДОЛЖЕН ПРОДАВАТЬ ===
{algorithm_brief()}

Обязательные для продажи этапы (по смыслу, не по буквам): {', '.join(REQUIRED_STAGES)}.
ГЛАВНОЕ: правильная отработка = алгоритм Гребенюка + попадание в ТВОЙ психотип.

=== ПРАВИЛА ОЦЕНКИ (deal_state) ===
- "active": менеджер движется по алгоритму и попадает в твой психотип. Ты втянут,
  но НЕ соглашаешься раньше времени.
- "yellow": менеджер ошибся — пропустил этап, назвал цену без ценности, задавил или
  прогнулся, не запрограммировал шаг, задел твой стоп-фактор. Ты остываешь и даёшь
  ПОСЛЕДНЕЕ предупреждение в духе «я подумаю» — в своей манере.
- "silent": см. блок выше.
- "failed": после жёлтого или после молчания менеджер снова ошибся — ты передумал.
- "won": засчитывается ТОЛЬКО если менеджер реально дожал по методологии: вытащил
  настоящую потребность сам, показал примеры, презентовал через ВЫГОДУ под тебя,
  корректно обошёл цену и возражения и закрыл на конкретный следующий шаг.
- Будь придирчивым, а не удобным. НЕ соглашайся с первого-второго сообщения.
  Минимум один раз усомнись, прежде чем согласиться.
- ВАЖНО: то, что ты живой и обаятельный, НЕ делает тебя снисходительным. Оценка
  строгая. Симпатия к менеджеру не заменяет правильную отработку.

=== ФОРМАТ ОТВЕТА ===
Верни СТРОГО один JSON-объект и ничего кроме него:
{{
  "buyer_messages": ["короткое сообщение", "ещё одно, если так пишет этот человек"],
  "deal_state": "active" | "yellow" | "silent" | "failed" | "won",
  "silence_hours": число часов, если deal_state = "silent" (1, 2, 3, 20, 26 или 44),
  "stage": "contact|initiative|qualification|need|presentation|close",
  "coach_note": "1-2 фразы разбора последнего хода менеджера для дебрифа"
}}
При deal_state = "silent" массив buyer_messages должен быть ПУСТЫМ: ты молчишь.
Без markdown, без пояснений вне JSON."""


def _extract_json(text):
    """Вытащить JSON из ответа модели, устойчиво к обёрткам ```json и мусору."""
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start:end + 1]
    try:
        return json.loads(cleaned)
    except Exception:
        return None


def _transcript_text(transcript):
    lines = []
    for role, msg in transcript[-MAX_TRANSCRIPT_TURNS:]:
        who = {"manager": "Менеджер", "buyer": "Покупатель"}.get(role, "Система")
        lines.append(f"{who}: {msg}")
    return "\n".join(lines) if lines else "(диалог только начинается)"


def _messages_list(data, fallback="…"):
    """Достать список реплик, пережив любой формат ответа модели."""
    msgs = data.get("buyer_messages")
    if isinstance(msgs, str):
        msgs = [msgs]
    if not isinstance(msgs, list):
        # старый формат на всякий случай
        single = data.get("buyer_reply")
        msgs = [single] if isinstance(single, str) else []
    out = [str(m).strip() for m in msgs if str(m).strip()]
    return out[:3] or [fallback]


def may_go_silent(session):
    """
    Решает Python, а не модель: иначе клиент либо пропадает каждый ход,
    либо не пропадает никогда. Учитываем занятость персоны и ход диалога.
    """
    if session["turns"] < SILENCE_MIN_TURN:
        return False
    if session.get("silences", 0) >= MAX_SILENCES:
        return False
    if session.get("awaiting_followup"):
        return False   # он уже молчит, второй раз подряд не уходит
    bias = session["scenario"]["persona"]["silence_bias"]
    return random.random() < bias


def opening_message(client, scenario, profile):
    """
    Первое ВХОДЯЩЕЕ сообщение от клиента. Возвращает (список реплик, расход).
    Этот же вызов создаёт кеш системного промпта для остальных ходов.
    """
    style = random.choice(OPENING_STYLES)
    user_content = (
        f"Начало диалога. Ты пишешь ПЕРВЫМ в компанию — это входящая заявка в чат.\n"
        f"Твой внутренний запрос: \"{scenario['request']}\", но раскрывать его сразу "
        f"и полностью необязательно.\n"
        f"Манера этого обращения: {style}.\n"
        f"Пиши в своей манере, коротко. Верни JSON с полем buyer_messages "
        f"(одно-два коротких сообщения), deal_state = \"active\", stage = \"contact\"."
    )
    try:
        resp = client.messages.create(
            model=DIALOG_MODEL,
            max_tokens=400,
            system=_system_blocks(scenario, profile),
            messages=[{"role": "user", "content": user_content}],
        )
        raw = resp.content[0].text if resp.content else ""
        data = _extract_json(raw)
        msgs = _messages_list(data, scenario["request"]) if data else [scenario["request"]]
        return msgs, costs.usage_dict(resp)
    except Exception as e:
        log.warning("Не удалось сгенерировать первое сообщение: %s", e)
        return [scenario["request"]], costs.usage_dict(None)


def step(client, session, manager_message):
    """
    Один ход тренажёра. session — словарь сессии из handlers.
    Возвращает dict: buyer_messages, deal_state, silence_hours, stage, coach_note, usage.
    """
    scenario = session["scenario"]
    profile = session["profile"]
    transcript = session["transcript"]

    parts = [f"ДИАЛОГ ДО СИХ ПОР:\n{_transcript_text(transcript)}"]

    if session.get("awaiting_followup"):
        hours = session.get("last_silence_hours", 2)
        parts.append(
            f"ВНИМАНИЕ: ты молчал {hours} ч. Это сообщение менеджера — ДОЖИМ после твоего "
            f"молчания. Оцени его строго по правилам дожима: есть ли новый повод, "
            f"конкретика и предложение конкретного шага."
        )

    if len(manager_message) > LONG_MESSAGE_CHARS:
        parts.append(
            f"ВНИМАНИЕ: менеджер прислал {len(manager_message)} знаков — это простыня "
            f"для мессенджера. Ты такое не читаешь целиком: отреагируй только на последнюю "
            f"мысль либо скажи, что многовато."
        )

    if may_go_silent(session):
        parts.append(
            "Сейчас тебе РАЗРЕШЕНО пропасть, если для этого есть причина по правилам "
            "(повис разговор, простыня, цена без ценности, отвлекли дела). "
            "Если причины нет — отвечай как обычно."
        )
    else:
        parts.append("Сейчас НЕ пропадай: отвечай, даже если коротко и холодно.")

    parts.append(f"НОВОЕ СООБЩЕНИЕ МЕНЕДЖЕРА:\n{manager_message}")
    parts.append("Ответь строго JSON по формату.")

    resp = client.messages.create(
        model=DIALOG_MODEL,
        max_tokens=800,
        system=_system_blocks(scenario, profile),
        messages=[{"role": "user", "content": "\n\n".join(parts)}],
    )
    usage = costs.usage_dict(resp)
    raw = resp.content[0].text if resp.content else ""
    data = _extract_json(raw)

    if not isinstance(data, dict):
        log.warning("Модель вернула не-JSON, длина ответа %s", len(raw))
        return {
            "buyer_messages": ["Хм, не совсем понял. Можете пояснить?"],
            "deal_state": "active", "silence_hours": 0,
            "stage": "contact", "coach_note": "", "usage": usage,
        }

    state = str(data.get("deal_state", "active")).lower().strip()
    if state not in ("active", "yellow", "silent", "failed", "won"):
        state = "active"

    # Страховка: молчание разрешает Python, а не модель.
    if state == "silent" and not may_go_silent(session) and not session.get("allow_silent_now"):
        state = "yellow" if data.get("coach_note") else "active"

    hours = data.get("silence_hours") or random.choice(SILENCE_DURATIONS)
    try:
        hours = max(1, int(hours))
    except (TypeError, ValueError):
        hours = 2

    return {
        "buyer_messages": [] if state == "silent" else _messages_list(data),
        "deal_state": state,
        "silence_hours": hours,
        "stage": str(data.get("stage", "")).strip(),
        "coach_note": str(data.get("coach_note", "")).strip(),
        "usage": usage,
    }


def silence_marker(hours):
    """Пометка вместо реального ожидания — процесс не прерывается."""
    if hours < 5:
        passed = f"{hours} ч."
    elif hours < 30:
        passed = "почти сутки"
    else:
        passed = "двое суток"
    return (f"⏳ *Прошло {passed}. Клиент не ответил.*\n\n"
            f"_Мяч на твоей стороне. Напиши ему — но не «ну что, надумали?». "
            f"Нужен новый повод, конкретика и предложение шага._")


DEBRIEF_SYSTEM = """Ты — наставник отдела продаж, работающий по методологии Михаила Гребенюка.
Тебе дают стенограмму тренировочного диалога менеджера с покупателем и итог сделки.

Разбери работу менеджера коротко и по делу, без воды и без похвалы авансом.
Пиши на «ты», как разбирают на планёрке. Формат ответа — ровно такой:

🎯 Что сработало
<одна-две строки; если не сработало ничего — так и напиши>

⚠️ Где потерял
<главная ошибка и на каком этапе алгоритма она случилась>

💬 Как надо было
<конкретная формулировка, которую следовало сказать вместо этого — готовая реплика>

📌 Отработать
<один навык на следующую тренировку>

Если в диалоге были паузы (клиент пропадал) — обязательно оцени, как менеджер
возвращал клиента: был ли новый повод или это было пустое «напоминаю о себе».

Не пересказывай диалог. Не льсти. Максимум 900 знаков."""


def final_debrief(client, session, result):
    """Финальный разбор. Один вызов на сессию, на самой сильной модели."""
    scenario, profile = session["scenario"], session["profile"]
    outcome = "менеджер закрыл сделку" if result == "won" else "клиент отказался"
    p = get_psychotype(scenario["psychotype_id"])
    person = scenario["persona"]
    silences = session.get("silences", 0)
    sil = (f"\nКлиент пропадал {silences} раз(а) — оцени качество дожима."
           if silences else "")

    user_content = (
        f"Ниша: {profile.get('title', '')}\n"
        f"Покупатель: {person['name']}, {scenario['status_title']}, "
        f"психотип «{p['name']}» (мотивация: {p['motivation']}).\n"
        f"Скрытый запрос покупателя: {scenario['request']}\n"
        f"Итог: {outcome}.{sil}\n\n"
        f"СТЕНОГРАММА:\n{_transcript_text(session['transcript'])}"
    )
    try:
        resp = client.messages.create(
            model=DEBRIEF_MODEL,
            max_tokens=900,
            system=DEBRIEF_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        text = (resp.content[0].text if resp.content else "").strip()
        return text, costs.usage_dict(resp)
    except Exception as e:
        log.warning("Разбор не удался: %s", e)
        return "", costs.usage_dict(None)
