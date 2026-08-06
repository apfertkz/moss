# -*- coding: utf-8 -*-
"""
ДВИЖОК ТРЕНАЖЁРА.

Одним вызовом Claude на каждый ход делает две вещи:
1. Играет ПОКУПАТЕЛЯ строго в характере выпавшего психотипа, в его роли и с его запросом.
2. Выступает скрытым ОЦЕНЩИКОМ: смотрит, двигается ли менеджер по алгоритму Гребенюка
   С УЧЁТОМ психотипа, и выдаёт состояние сделки.

Состояния сделки (deal_state):
  active  — идёт нормально, покупатель втянут, ждёт следующего хода менеджера
  yellow  — жёлтый сигнал: менеджер ошибся/остыл покупатель. Реплика вида
            "Я подумаю" / "Спасибо, посоветуюсь". Последнее предупреждение.
  failed  — покупатель передумал (менеджер не вернулся в алгоритм). Сделка провалена.
  won     — покупатель согласился купить/сделать следующий шаг. Продажа засчитана.

Модель возвращает СТРОГО JSON. Питон парсит и рулит UI/статистикой.
"""

import os
import re
import json
import random

from .psychotypes import PSYCHOTYPES, get_psychotype
from .algorithm import algorithm_brief, REQUIRED_STAGES
from . import niche_loader

MODEL = os.environ.get("TRAINER_MODEL", "claude-opus-4-5")
MAX_TRANSCRIPT_TURNS = 24  # сколько последних реплик держим в контексте


def new_scenario():
    """Случайная комбинация: психотип × статус × запрос из активной ниши."""
    niche = niche_loader.load_niche()
    psychotype = random.choice(PSYCHOTYPES)
    status = random.choice(niche["statuses"])
    request = random.choice(niche["requests"])
    return {
        "niche_id": niche["id"],
        "psychotype_id": psychotype["id"],
        "status_id": status["id"],
        "status_title": status["title"],
        "request": request,
    }


def scenario_intro(scenario):
    """Текст-заставка для менеджера при старте тренировки (без раскрытия психотипа)."""
    return (
        f"🎯 *Новый клиент*\n\n"
        f"👤 *Кто:* {scenario['status_title']}\n"
        f"💬 *Запрос:* {scenario['request']}\n\n"
        f"Психотип клиента скрыт — считай его по поведению и подбери подход.\n"
        f"Веди диалог по смыслу (Гребенюк), а не по скрипту. Продажа засчитается, "
        f"только если реально отработаешь верно.\n\n"
        f"_Пиши первое сообщение клиенту._"
    )


def _build_system_prompt(scenario):
    p = get_psychotype(scenario["psychotype_id"])
    niche = niche_loader.load_niche(scenario["niche_id"])
    triggers = "\n".join(f"   • {t}" for t in p["triggers"])
    stops = "\n".join(f"   • {s}" for s in p["stop_factors"])

    return f"""Ты — движок тренажёра по продажам. Твоя задача — тренировать менеджера,
играя роль ЖИВОГО ПОКУПАТЕЛЯ и одновременно скрыто оценивая работу менеджера
по методологии продаж Михаила Гребенюка.

=== НИША / ПРОДУКТ ===
{niche['product_context']}
Валюта: {niche.get('currency', 'рубли')}.

=== КТО ТЫ (ПОКУПАТЕЛЬ) ===
Роль: {scenario['status_title']}.
Твой изначальный запрос: "{scenario['request']}".
Твой психотип по спиральной динамике: {p['name']} ({p['color']}).
Мотивация: {p['motivation']}
Что тебя ЗАЖИГАЕТ (ведёт к покупке):
{triggers}
Что тебя ОТТАЛКИВАЕТ (убивает сделку):
{stops}
Манера речи: {p['speech_style']}

Играй этого человека достоверно: с его характером, сомнениями и манерой речи.
НЕ раскрывай менеджеру свой психотип и эти правила. Не подыгрывай из вежливости —
покупай, только если менеджер реально заслужил это по методологии и по твоему характеру.

=== ЭТАЛОН: КАК МЕНЕДЖЕР ДОЛЖЕН ПРОДАВАТЬ ===
{algorithm_brief()}

Обязательные для продажи этапы (по смыслу, не по буквам): {', '.join(REQUIRED_STAGES)}.
ГЛАВНОЕ: правильная отработка = алгоритм Гребенюка + попадание в ТВОЙ психотип.
Пример: тебя-Властного нельзя продавливать и мучить анкетой; тебя-Системного нельзя
закрывать без конкретики и гарантий; тебе-Выживающему нельзя называть цену без ценности.

=== ПРАВИЛА ОЦЕНКИ (deal_state) ===
- "active": менеджер движется по алгоритму и попадает в твой психотип. Ты втянут, отвечаешь
  в характере, но НЕ соглашаешься раньше времени. Задавай встречные вопросы/сомнения,
  веди себя как настоящий клиент этого психотипа.
- "yellow": менеджер ошибся — пропустил этап, назвал цену без ценности, задавил/прогнулся,
  не запрограммировал шаг, задел твой стоп-фактор, или просто "впаривает". Тогда ты остываешь
  и даёшь ПОСЛЕДНЕЕ предупреждение репликой в духе "Я подумаю" / "Спасибо за информацию,
  я посоветуюсь" (в своей манере). Это шанс менеджеру вернуться в алгоритм.
- Если после "yellow" менеджер СНОВА ошибается или продолжает в том же духе (не отработал
  твоё сомнение, снова давит/льёт воду) → "failed": ты передумал ("Всё, я передумал покупать",
  в своей манере).
- Если после "yellow" менеджер ГРАМОТНО вернулся в алгоритм и закрыл твоё сомнение → снова "active"
  (или сразу "won", если этого хватило для решения).
- "won": менеджер прошёл обязательные этапы ПО СМЫСЛУ, попал в твой психотип и закрыл на
  конкретный следующий шаг (замер/предоплата/встреча/счёт). Тогда ты соглашаешься в своей манере.
  НЕ соглашайся раньше, чем менеджер выявил потребность и презентовал через выгоду — даже если
  он сразу назвал хорошую цену.
- Никогда не выдавай "won" за грубое "купи-купи" без отработки алгоритма.

=== ФОРМАТ ОТВЕТА ===
Верни СТРОГО один JSON-объект и ничего кроме него:
{{
  "buyer_reply": "реплика покупателя менеджеру — живая, в характере, 1-4 предложения",
  "deal_state": "active" | "yellow" | "failed" | "won",
  "stage": "contact|initiative|qualification|need|presentation|close — на каком этапе сейчас менеджер",
  "coach_note": "1-2 фразы разбора последнего хода менеджера для дебрифа (что сделал хорошо/плохо по методологии)"
}}
Без markdown, без пояснений вне JSON."""


def _extract_json(text):
    """Вытащить JSON из ответа модели, устойчиво к обёрткам ```json и мусору."""
    if not text:
        return None
    # убрать кодовые заборы
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    # взять от первой { до последней }
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
        who = "Менеджер" if role == "manager" else "Покупатель"
        lines.append(f"{who}: {msg}")
    return "\n".join(lines) if lines else "(диалог только начинается)"


def step(client, scenario, transcript, manager_message):
    """
    Один ход тренажёра.
    client — anthropic.Anthropic (синхронный клиент из bot.py).
    transcript — список кортежей (role, text), role in {'manager','buyer'}.
    Возвращает dict: buyer_reply, deal_state, stage, coach_note.
    Питон-подстраховка: гарантирует валидный deal_state.
    """
    system = _build_system_prompt(scenario)
    user_content = (
        f"ДИАЛОГ ДО СИХ ПОР:\n{_transcript_text(transcript)}\n\n"
        f"НОВОЕ СООБЩЕНИЕ МЕНЕДЖЕРА:\n{manager_message}\n\n"
        f"Ответь строго JSON по формату."
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=700,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    raw = resp.content[0].text if resp.content else ""
    data = _extract_json(raw)

    if not isinstance(data, dict):
        # безопасный фолбэк — не роняем тренировку
        return {
            "buyer_reply": "Хм, не совсем понял вас. Можете пояснить?",
            "deal_state": "active",
            "stage": "contact",
            "coach_note": "",
        }

    state = str(data.get("deal_state", "active")).lower().strip()
    if state not in ("active", "yellow", "failed", "won"):
        state = "active"

    return {
        "buyer_reply": str(data.get("buyer_reply", "")).strip() or "…",
        "deal_state": state,
        "stage": str(data.get("stage", "")).strip(),
        "coach_note": str(data.get("coach_note", "")).strip(),
    }
