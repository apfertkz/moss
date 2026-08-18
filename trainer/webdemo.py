# -*- coding: utf-8 -*-
"""
Живое демо на сайте: гость проходит короткий опрос, получает клиента под
свою нишу, ведёт с ним переписку и в конце — разбор своей работы.

Зачем оно вообще: продукт продаётся ощущением «он и правда живой», а сайт
может об этом только рассказывать. Каждый переход в мессенджер теряет
половину пришедших, поэтому демо должно работать прямо на странице.

Чем отличается от демо в боте: там гость опознан телеграм-аккаунтом и на
человека приходится две попытки. Здесь личности нет вообще, и любой со
скриптом способен за час сжечь больше, чем приносит клиент за год. Поэтому
три независимых рубежа:

  1. Дневной потолок расхода. Считается по базе, а не по счётчику в памяти:
     перезапуск процесса иначе обнулял бы защиту.
  2. Лимит попыток с одного адреса за сутки.
  3. Проверка Cloudflare Turnstile при старте.

Если рубеж сработал, демо не ломается, а мягко отказывает и ведёт в бота:
отказ — тоже часть воронки.

Диалог и разбор берутся из движка тренажёра без изменений. Отдельная копия
логики означала бы, что демо на сайте со временем начнёт врать про продукт.
"""

import asyncio
import datetime
import json
import logging
import os
import re
import secrets
import time

import aiohttp
from aiohttp import web

from . import db, costs, llm, engine, niche_loader

log = logging.getLogger(__name__)

# --- Настройки ---------------------------------------------------------------

# Сколько реплик менеджера даём. Десять — компромисс: этапы методологии
# успевают проявиться, разбор получается содержательным, а переписка
# укладывается в пару минут и до конца доходит большинство.
MAX_TURNS = int(os.environ.get("DEMO_TURNS", "10"))

# Потолок расхода на демо в сутки, в долларах.
DAILY_USD = float(os.environ.get("DEMO_DAILY_USD", "8"))

# Сколько попыток с одного адреса в сутки.
PER_IP_DAY = int(os.environ.get("DEMO_PER_IP", "3"))

# Профиль ниши пишется один раз за демо — берём модель побыстрее.
# Диалог тоже: на странице пауза в пять секунд читается как поломка,
# а на десяти репликах разница в качестве почти не видна.
PROFILE_MODEL = os.environ.get("DEMO_PROFILE_MODEL", "claude-sonnet-5")
DIALOG_MODEL = os.environ.get("DEMO_DIALOG_MODEL", "claude-sonnet-5")

TURNSTILE_SECRET = os.environ.get("TURNSTILE_SECRET", "")

# Живые сессии демо. В памяти намеренно: они живут минуты, переживать
# перезапуск им незачем, а в базе от них остался бы мусор.
_live = {}
SESSION_TTL = 45 * 60


# --- Опрос -------------------------------------------------------------------

QUESTIONS = [
    {
        "key": "product",
        "label": "Что вы продаёте",
        "placeholder": "Панно из стабилизированного мха на заказ — не требует ухода, служит 8–10 лет",
        "min": 15,
    },
    {
        "key": "audience",
        "label": "Кто у вас покупает",
        "placeholder": "Рестораны, бутик-отели, дизайнеры интерьеров, офисы",
        "min": 8,
    },
    {
        "key": "price",
        "label": "Средний чек",
        "placeholder": "350 000 ₸",
        "min": 2,
    },
    {
        "key": "pain",
        "label": "Где чаще всего срывается сделка",
        "placeholder": "Спрашивают цену и пропадают",
        "min": 8,
    },
]


PROFILE_SYSTEM = """Ты собираешь профиль ниши для тренажёра отдела продаж.
По коротким ответам предпринимателя составь профиль, по которому нейросеть
сможет достоверно отыгрывать его покупателя.

Верни СТРОГО JSON без пояснений:
{
  "id": "краткий_идентификатор_латиницей",
  "title": "Название ниши по-русски",
  "product_context": "Развёрнутое описание: что продаётся, кому, ключевые свойства, из чего складывается цена, типичные возражения. Не меньше 400 знаков.",
  "currency": "тенге",
  "statuses": [{"id": "...", "title": "Тип клиента", "context": "Кто это, что для него важно, чего боится"}],
  "requests": ["Короткий запрос покупателя своими словами"]
}

Требования: минимум 4 типа клиентов и минимум 6 запросов.
Запросы пиши так, как их пишет живой человек в переписке — коротко, без
канцелярита, иногда с опечаткой или без знаков препинания.
Валюту и порядок цен бери из ответа про средний чек.
Если ответы скупые — достраивай сам по здравому смыслу отрасли."""


def _fallback_profile(answers):
    """
    Профиль на случай, если модель не ответила или ответила мусором.

    Демо не должно падать из-за одного неудачного вызова: человек уже
    заполнил опрос и ждёт. Профиль получается беднее, но рабочий.
    """
    product = (answers.get("product") or "продукт").strip()
    audience = (answers.get("audience") or "клиенты").strip()
    price = (answers.get("price") or "").strip()
    pain = (answers.get("pain") or "").strip()

    return {
        "id": "custom",
        "title": product[:60],
        "product_context": (
            f"Продаём: {product}. Покупатели: {audience}. Средний чек: {price or 'не указан'}. "
            f"Чаще всего сделка срывается так: {pain or 'клиент пропадает после вопроса о цене'}. "
            f"Клиенты сравнивают предложения, торгуются и легко уходят, если менеджер "
            f"отвечает ценой без ценности. Решение почти никогда не принимается в первом "
            f"же сообщении: нужен разговор, выяснение задачи и предложение следующего шага."
        ),
        "currency": "тенге",
        "statuses": [
            {"id": "cold", "title": "Пришёл сравнить цены",
             "context": "Пишет сразу про стоимость, задачу не объясняет, легко уходит"},
            {"id": "warm", "title": "Уже присматривался",
             "context": "Смотрел у других, есть ожидания по цене и срокам"},
            {"id": "expert", "title": "Разбирается в вопросе",
             "context": "Задаёт точные вопросы, не терпит общих слов"},
            {"id": "busy", "title": "Занятой руководитель",
             "context": "Пишет коротко, ценит время, не читает длинных сообщений"},
        ],
        "requests": [
            "Сколько стоит?",
            "Здравствуйте, интересует ваш продукт",
            "А что по срокам?",
            "Скиньте прайс пожалуйста",
            "Видел у вас работы, хочу похожее",
            "Насколько это вообще надёжно?",
        ],
    }


def build_profile(client, answers):
    """Собрать профиль ниши из ответов опроса. Возвращает (профиль, расход)."""
    text = "\n".join(
        f"{q['label']}: {answers.get(q['key'], '').strip()}" for q in QUESTIONS
    )
    try:
        resp = llm.create(
            client,
            model=PROFILE_MODEL,
            max_tokens=2200,
            system=PROFILE_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
        usage = costs.usage_dict(resp)
        raw = costs.text_of(resp)
        match = re.search(r"\{.*\}", raw, re.S)
        profile = json.loads(match.group(0)) if match else None
        niche_loader.validate(profile)
        return profile, usage
    except Exception as e:
        log.warning("Профиль для демо не собрался (%s) — берём запасной", e)
        return _fallback_profile(answers), costs.usage_dict(None)


# --- Рубежи защиты -----------------------------------------------------------

def spent_today():
    """Сколько долларов демо съело с начала суток. Считаем по базе."""
    row = db.query(
        """SELECT COALESCE(SUM(cost_usd), 0) AS s FROM web_demo
            WHERE created_at > date_trunc('day', now())""",
        one=True,
    ) or {}
    return float(row.get("s") or 0)


def tries_from(ip):
    row = db.query(
        """SELECT COUNT(*) AS n FROM web_demo
            WHERE ip = %s AND created_at > now() - interval '24 hours'""",
        (ip,), one=True,
    ) or {}
    return int(row.get("n") or 0)


async def turnstile_ok(token, ip):
    """
    Проверка «вы не робот». Без заданного секрета проверка пропускается —
    иначе на машине разработчика демо было бы не запустить вовсе.
    """
    if not TURNSTILE_SECRET:
        return True
    if not token:
        return False
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={"secret": TURNSTILE_SECRET, "response": token, "remoteip": ip or ""},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                data = await r.json()
                return bool(data.get("success"))
    except Exception:
        # Недоступность Cloudflare не должна ронять демо: остаются
        # дневной потолок и лимит по адресу.
        log.exception("Turnstile недоступен — пропускаем проверку")
        return True


# --- Учёт --------------------------------------------------------------------

def _charge(sid, model, usage):
    """Записать расход сессии. Считаем в долларах, как и везде в проекте."""
    s = _live.get(sid)
    if not s:
        return
    s["cost"] += costs.cost_usd(model, usage)


def _persist(sid, **fields):
    keys = ", ".join(f"{k} = %s" for k in fields)
    db.execute(f"UPDATE web_demo SET {keys} WHERE token = %s",
               tuple(fields.values()) + (sid,))


def cleanup():
    now = time.time()
    for sid in [k for k, v in _live.items() if now - v["at"] > SESSION_TTL]:
        _live.pop(sid, None)


# --- Сценарий ----------------------------------------------------------------

async def start(client, answers, ip):
    """
    Собрать клиента и получить его первое сообщение.
    Возвращает словарь для браузера либо бросает ValueError с причиной отказа.
    """
    cleanup()

    loop = asyncio.get_event_loop()
    if await loop.run_in_executor(None, spent_today) >= DAILY_USD:
        raise ValueError(
            "Сегодня демо разобрали до конца — лимит на сутки исчерпан. "
            "Полная версия без ограничений ждёт в боте."
        )
    if await loop.run_in_executor(None, tries_from, ip) >= PER_IP_DAY:
        raise ValueError(
            "Вы уже проходили демо сегодня. Дальше интереснее в боте: "
            "там клиенты не кончаются."
        )

    profile, usage = await loop.run_in_executor(None, build_profile, client, answers)
    scenario = engine.new_scenario(profile)

    sid = secrets.token_urlsafe(16)
    db.execute(
        """INSERT INTO web_demo (token, ip, niche, answers) VALUES (%s,%s,%s,%s)""",
        (sid, ip, profile.get("title"), json.dumps(answers, ensure_ascii=False)),
    )

    _live[sid] = {
        "scenario": scenario, "profile": profile, "transcript": [],
        "turns": 0, "cost": 0.0, "at": time.time(),
        "silences": 0, "awaiting_followup": False,
    }
    _charge(sid, PROFILE_MODEL, usage)

    # Движок тренажёра берёт модель из своей переменной. Подменяем на время
    # вызова: демо на странице живёт по другим требованиям к скорости.
    msgs, usage = await loop.run_in_executor(
        None, _with_model, engine.opening_message, client, scenario, profile)
    _charge(sid, DIALOG_MODEL, usage)

    person = scenario["persona"]
    return {
        "sid": sid,
        "client": {
            "name": person["name"],
            "status": scenario["status_title"],
            "niche": profile.get("title"),
        },
        "messages": msgs,
        "left": MAX_TURNS,
    }


def _with_model(fn, *args):
    """Выполнить вызов движка на модели демо, вернув прежнюю после."""
    was = engine.DIALOG_MODEL
    engine.DIALOG_MODEL = DIALOG_MODEL
    try:
        return fn(*args)
    finally:
        engine.DIALOG_MODEL = was


async def say(client, sid, text):
    """Ход менеджера. Возвращает ответ клиента и остаток попыток."""
    s = _live.get(sid)
    if not s:
        raise ValueError("Демо устарело — начните заново")
    if s.get("state") in ("won", "failed"):
        # Сделка закончилась — дальше разговаривать не с кем. Без этой проверки
        # с закрытым клиентом можно было переписываться сколько угодно.
        raise ValueError("Сделка завершена — посмотрите разбор")
    if s["turns"] >= MAX_TURNS:
        raise ValueError("Лимит демо исчерпан")

    text = (text or "").strip()[:1500]
    if not text:
        raise ValueError("Пустое сообщение")

    s["at"] = time.time()
    s["transcript"].append({"role": "manager", "text": text})
    # Движок решает, может ли клиент пропасть, по этому полю. В демо оно
    # всегда ноль: пауза «клиент молчит два часа» на странице читается как
    # поломка, а объяснять её здесь негде.
    s["silences"] = engine.MAX_SILENCES

    loop = asyncio.get_event_loop()
    out = await loop.run_in_executor(None, _with_model, engine.step, client, s, text)
    _charge(sid, DIALOG_MODEL, out["usage"])

    for m in out["buyer_messages"]:
        s["transcript"].append({"role": "buyer", "text": m})

    s["turns"] += 1
    left = MAX_TURNS - s["turns"]

    state = out["deal_state"]
    # Молчание клиента в демо не отыгрываем: на странице человек не станет
    # ждать и не поймёт, что произошло.
    if state == "silent":
        state = "yellow"

    s["state"] = state
    over = left <= 0 or state in ("won", "failed")
    return {
        "messages": out["buyer_messages"],
        "state": state,
        "left": left,
        "over": over,
    }


async def finish(client, sid, contact=None, name=None):
    """Разбор работы менеджера. Он же — момент, ради которого всё затевалось."""
    s = _live.get(sid)
    if not s:
        raise ValueError("Демо устарело — начните заново")

    # Исход берём из последнего хода движка. Если человек просто упёрся в
    # лимит реплик, сделка не закрыта и не провалена — разбираем как есть.
    result = "won" if s.get("state") == "won" else "lost"

    loop = asyncio.get_event_loop()
    text, usage = await loop.run_in_executor(
        None, engine.final_debrief, client, s, result)
    _charge(sid, engine.DEBRIEF_MODEL, usage)

    await loop.run_in_executor(
        None, lambda: _persist(
            sid,
            turns=s["turns"],
            cost_usd=round(s["cost"], 6),
            result=result,
            contact=contact,
            contact_name=name,
            verdict=text,
            finished_at=datetime.datetime.now(datetime.timezone.utc),
        ))

    _live.pop(sid, None)
    return {"verdict": text, "result": result}


# --- HTTP --------------------------------------------------------------------

def client_ip(request):
    """
    Адрес гостя. За обратным прокси Railway настоящий адрес приходит
    заголовком, а request.remote — это адрес самого прокси.
    """
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote) or "?"


def attach(app, anthropic_client, origins):
    """
    Подключить маршруты демо к приложению панели.

    Демо живёт в том же процессе: база и ключ модели уже рядом, а отдельный
    сервис пришлось бы отдельно и деплоить, и охранять.
    """

    def cors(resp, request):
        origin = request.headers.get("Origin", "")
        if origin and (origin in origins or "*" in origins):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            resp.headers["Vary"] = "Origin"
        return resp

    def ok(request, data, status=200):
        return cors(
            web.json_response(
                text=json.dumps(data, ensure_ascii=False, default=str),
                status=status, content_type="application/json"),
            request)

    async def preflight(request):
        return cors(web.Response(status=204), request)

    async def body(request):
        try:
            return await request.json()
        except Exception:
            return {}

    async def h_config(request):
        return ok(request, {
            "questions": QUESTIONS,
            "turns": MAX_TURNS,
            "sitekey": os.environ.get("TURNSTILE_SITEKEY", ""),
        })

    async def h_start(request):
        d = await body(request)
        ip = client_ip(request)

        if not await turnstile_ok(d.get("captcha"), ip):
            return ok(request, {"error": "Не удалось убедиться, что вы не робот"}, 403)

        answers = {q["key"]: str(d.get(q["key"], "")).strip()[:600] for q in QUESTIONS}
        short = [q["label"] for q in QUESTIONS if len(answers[q["key"]]) < q["min"]]
        if short:
            return ok(request, {"error": "Заполните: " + ", ".join(short)}, 400)

        try:
            return ok(request, await start(anthropic_client, answers, ip))
        except ValueError as e:
            return ok(request, {"error": str(e), "soft": True}, 429)
        except Exception:
            log.exception("Демо не запустилось")
            return ok(request, {"error": "Не получилось собрать клиента. Попробуйте ещё раз."}, 500)

    async def h_say(request):
        d = await body(request)
        try:
            return ok(request, await say(anthropic_client, d.get("sid"), d.get("text")))
        except ValueError as e:
            return ok(request, {"error": str(e)}, 400)
        except Exception:
            log.exception("Ход демо не прошёл")
            return ok(request, {"error": "Клиент задумался и не ответил. Напишите ещё раз."}, 500)

    async def h_finish(request):
        d = await body(request)
        try:
            return ok(request, await finish(
                anthropic_client, d.get("sid"),
                str(d.get("contact", "")).strip()[:120] or None,
                str(d.get("name", "")).strip()[:120] or None,
            ))
        except ValueError as e:
            return ok(request, {"error": str(e)}, 400)
        except Exception:
            log.exception("Разбор демо не собрался")
            return ok(request, {"error": "Разбор не собрался. Напишите нам — вышлем вручную."}, 500)

    r = app.router
    r.add_get("/api/try/config", h_config)
    r.add_post("/api/try/start", h_start)
    r.add_post("/api/try/say", h_say)
    r.add_post("/api/try/finish", h_finish)
    for path in ("/api/try/start", "/api/try/say", "/api/try/finish", "/api/try/config"):
        r.add_route("OPTIONS", path, preflight)
