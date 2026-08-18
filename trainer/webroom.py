# -*- coding: utf-8 -*-
"""
Тренировочная комната на сайте: менеджер входит по коду из бота и ведёт
переписку с клиентом прямо в браузере.

Зачем она нужна, если тренажёр уже работает в Telegram. Телефон хорош для
пяти минут в дороге, но разбор длиной в экран на нём читают по диагонали, а
руководитель просит тренироваться за рабочим столом. Комната — тот же
тренажёр для тех, кто сидит за ноутбуком.

Чем отличается от демо на сайте (webdemo.py):

  • Гостя там нет вовсе, здесь менеджер опознан и привязан к компании.
    Клиента строим по нише компании из базы, а не по опросу из трёх полей.
  • Там рубежи против накрутки, здесь — лимит тренировок по тарифу. Демо
    защищается от чужих, комната считает своих.
  • Демо забывает гостя. Комната пишет тренировку в общую историю: за неё
    платит руководитель и он же смотрит её в кабинете.

Состояние тренировки лежит там же, где у бота, — в active_sessions. Это не
экономия таблицы, а решение: менеджер может начать переписку в браузере,
закрыть ноутбук и дописать её в Telegram с телефона. Две отдельные памяти
означали бы две разные незаконченные тренировки у одного человека.

Отдельная кука. Панель ставит свою на домен панели, а комната открывается с
сайта — для браузера это разные сайты. Поэтому кука комнаты выписывается на
общий домен второго уровня и с пометкой, разрешающей отправку с соседнего
поддомена. Без этого вход работал бы, а первый же запрос за данными уходил
бы без куки.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import re
import time

from aiohttp import web

from . import (db, engine, guide, niche_loader, stats, store, tenancy, webadmin)

log = logging.getLogger(__name__)

# --- Настройки ---------------------------------------------------------------

COOKIE = "moss_room"

# На какой домен выписывать куку. Для aisaty.com это «.aisaty.com»: тогда
# страница на aisaty.com отправляет её на panel.aisaty.com. Пусто — кука
# остаётся на домене панели, то есть комната работает только с неё.
COOKIE_DOMAIN = os.environ.get("ROOM_COOKIE_DOMAIN", "")

SESSION_DAYS = 14

CODE_TTL = 300          # код из бота живёт пять минут
CODE_ATTEMPTS = 3

# Сколько ходов терпим в одной тренировке. Обычно сделка закрывается или
# проваливается раньше; потолок нужен на случай переписки без конца — она
# стоит денег и уже ничему не учит.
MAX_TURNS = int(os.environ.get("ROOM_MAX_TURNS", "40"))

# Фото от менеджера. Полтора мегабайта после кодирования — это примерно
# снимок с телефона без обработки; больше незачем, модель всё равно ужмёт.
# Потолок расхода на комнату, в долларах на компанию в сутки. Лимит тарифа
# считает тренировки, но одна тренировка на сорок ходов стоит как пять
# коротких: без денежного потолка отдел на большом тарифе способен за день
# съесть месячную маржу. Ноль — потолка нет.
ROOM_DAILY_USD = float(os.environ.get("ROOM_DAILY_USD", "8"))

MAX_IMAGE_BYTES = 1_500_000
MAX_IMAGES = 3
ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")

# Ожидающие подтверждения входы: токен -> код, срок, остаток попыток.
# В памяти процесса намеренно: перезапуск панели должен обнулять недоверенные
# половинки входа, а не воскрешать их.
_pending = {}


# --- Кто вошёл ---------------------------------------------------------------

def make_token(company_id, telegram_id):
    """Подписанный пропуск менеджера. Подпись — та же, что у панели."""
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = f"mgr:{company_id}:{telegram_id}:{exp}"
    return f"{payload}.{webadmin._sign(payload)}"


def read_token(token):
    """Разобрать пропуск. Возвращает (company_id, telegram_id) или None."""
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, webadmin._sign(payload)):
        return None
    parts = payload.split(":")
    if len(parts) != 4 or parts[0] != "mgr":
        return None
    try:
        company_id, telegram_id, exp = int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return None
    if exp < time.time():
        return None
    return company_id, telegram_id


def identity(request):
    """
    Менеджер за этим запросом или никого.

    Компанию берём из подписанной куки и только оттуда. Ни один обработчик
    комнаты не принимает номер компании из запроса — иначе чужую тренировку
    можно было бы открыть, подставив номер в адрес.
    """
    raw = request.cookies.get(COOKIE)
    if not raw:
        _auth = request.headers.get("Authorization", "")
        if _auth.startswith("Bearer "):
            raw = _auth[7:].strip()
    got = read_token(raw)
    if not got:
        return None
    company_id, telegram_id = got
    user = tenancy.get_user(telegram_id)
    # Компания в куке и компания в базе должны совпадать: менеджера могли
    # перевести или отключить уже после выдачи пропуска.
    if not user or user["company_id"] != company_id or not user["active"]:
        return None
    return user


# --- Тренировка --------------------------------------------------------------

def _empty_usage():
    return {"input_tokens": 0, "output_tokens": 0, "cache_write": 0, "cache_read": 0}


def _add_usage(session, usage):
    if not usage:
        return
    base = session.get("usage") or _empty_usage()
    session["usage"] = {k: base.get(k, 0) + usage.get(k, 0) for k in base}


def _view(session, extra=None):
    """
    Что видит браузер. Наружу отдаём только то, что нужно нарисовать:
    сценарий целиком содержит подсказки для модели, и показывать их
    менеджеру — значит отдать ему ответы к собственному экзамену.
    """
    scenario = session["scenario"]
    person = scenario["persona"]
    data = {
        "client": {
            "name": person["name"],
            "status": scenario["status_title"],
            "request": scenario.get("request"),
            "niche": (session.get("profile") or {}).get("title"),
        },
        "transcript": [{"role": r, "text": t} for r, t in session["transcript"]],
        "turns": session["turns"],
        "left": max(0, MAX_TURNS - session["turns"]),
        "state": session.get("state", "active"),
    }
    if extra:
        data.update(extra)
    return data


async def start(client, user):
    """
    Новая тренировка. Возвращает первое сообщение клиента либо бросает
    ValueError с текстом, который можно показать человеку как есть.
    """
    loop = asyncio.get_event_loop()

    try:
        await loop.run_in_executor(None, tenancy.check_can_train, user)
    except tenancy.Denied as d:
        raise ValueError(str(d))

    await loop.run_in_executor(None, check_budget, user["company_id"])

    profile = await loop.run_in_executor(
        None, niche_loader.active_profile, user["company_id"])
    if not profile:
        raise ValueError(
            "Профиль вашей компании ещё не настроен — руководителю нужно "
            "заполнить бриф в боте командой /setup."
        )

    scenario = engine.new_scenario(profile)
    session = {
        "scenario": scenario, "profile": profile,
        "company_id": user["company_id"],
        "transcript": [], "turns": 0,
        "silences": 0, "awaiting_followup": False, "last_silence_hours": 0,
        "usage": _empty_usage(),
        # Пометка «начато в комнате» — чтобы в кабинете было видно, где
        # тренируются, и чтобы понимать, нужна ли комната вообще.
        "web": True,
    }

    try:
        opening, usage = await loop.run_in_executor(
            None, engine.opening_message, client, scenario, profile)
    except Exception:
        log.exception("Первое сообщение клиента не собралось")
        opening, usage = [scenario["request"]], None

    _add_usage(session, usage)
    if usage:
        await loop.run_in_executor(
            None, stats.record_usage, user["company_id"], user["telegram_id"],
            "dialog", engine.DIALOG_MODEL, usage)

    for m in opening:
        session["transcript"].append(["buyer", m])

    await loop.run_in_executor(
        None, store.put, user["telegram_id"], user["company_id"], session)

    return _view(session, {"intro": engine.scenario_intro(scenario)})


def spent_today(company_id):
    """Сколько компания потратила на модель с начала суток, в долларах."""
    row = db.query(
        """SELECT COALESCE(SUM(cost_usd), 0) AS s FROM usage_log
            WHERE company_id = %s AND created_at > date_trunc('day', now())""",
        (company_id,), one=True,
    ) or {}
    return float(row.get("s") or 0)


def check_budget(company_id):
    """
    Не упёрлись ли в дневной потолок. Проверяем по базе, а не по счётчику в
    памяти: перезапуск панели иначе обнулял бы защиту.
    """
    if ROOM_DAILY_USD <= 0:
        return
    if spent_today(company_id) >= ROOM_DAILY_USD:
        raise ValueError(
            "На сегодня тренировки исчерпаны — отдел много занимался. "
            "Завтра счётчик обнулится, а пока можно потренироваться в боте."
        )


def parse_image(raw):
    """
    Разобрать картинку из браузера. Приходит строкой вида
    «data:image/jpeg;base64,…». Возвращает словарь для движка или None.

    Проверяем тип и размер здесь, а не только в браузере: до сервера может
    прийти что угодно, минуя нашу страницу.
    """
    if not raw or not isinstance(raw, str):
        return None
    m = re.match(r"^data:([\w/+.-]+);base64,(.+)$", raw, re.S)
    if not m:
        raise ValueError("Не разобрали файл — приложите обычное фото")
    media_type, data = m.group(1).lower(), m.group(2)
    if media_type not in ALLOWED_IMAGE_TYPES:
        raise ValueError("Такие файлы клиент не откроет. Подойдут JPG, PNG, WEBP или GIF")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Фото тяжёлое — ужмите его или пришлите другое")
    return {"media_type": media_type, "data": data}


def parse_images(raw):
    """
    Разобрать вложения из браузера: одну строку или список строк.
    Возвращает список словарей для движка.
    """
    items = [raw] if isinstance(raw, str) else list(raw or [])
    if len(items) > MAX_IMAGES:
        raise ValueError(f"Больше {MAX_IMAGES} фото за раз клиент не посмотрит")
    return [p for p in (parse_image(i) for i in items) if p]


async def say(client, user, text, images=None):
    """Ход менеджера. Возвращает ответ клиента и состояние сделки."""
    session = await asyncio.get_event_loop().run_in_executor(
        None, store.get, user["telegram_id"])
    if not session:
        raise ValueError("Тренировка не найдена — начните новую")

    pictures = parse_images(images)
    text = (text or "").strip()[:2000]
    if not text and not pictures:
        raise ValueError("Пустое сообщение")
    if pictures and not text:
        # Голое фото без слов движок принимает плохо: ему нужен ход
        # менеджера. Подставляем то, что и так подразумевается.
        text = "(прислал фото)"
    if session["turns"] >= MAX_TURNS:
        raise ValueError("Переписка затянулась — завершите её и посмотрите разбор")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, check_budget, user["company_id"])

    # Тренировка одна на бота и на комнату. Если человек успел походить с
    # телефона, пока здесь висела вкладка, наш ход лёг бы поверх чужого и
    # переписка разъехалась бы. Запоминаем номер хода и сверяем перед записью.
    started_at_turn = session["turns"]

    # В переписку пишем пометку, а не саму картинку: активная тренировка
    # лежит в базе, и складывать туда мегабайты изображений — верный способ
    # раздуть её до неподъёмного состояния. Показывает фото браузер, у себя.
    session["transcript"].append(["manager", f"📎 {text}" if pictures else text])

    out = await loop.run_in_executor(
        None, lambda: engine.step(client, session, text, pictures))
    _add_usage(session, out["usage"])
    await loop.run_in_executor(
        None, stats.record_usage, user["company_id"], user["telegram_id"],
        "dialog", engine.DIALOG_MODEL, out["usage"])

    latest = await loop.run_in_executor(None, store.get, user["telegram_id"])
    if latest and latest.get("turns", 0) != started_at_turn:
        # За время ответа модели тренировка ушла вперёд в другом месте.
        # Свой ход отбрасываем: две правды в одной переписке хуже потерянной
        # реплики, а человеку видно, что произошло.
        raise ValueError(
            "Эту тренировку продолжили в боте. Обновите страницу — "
            "переписка подтянется, и можно писать дальше."
        )

    session["turns"] += 1
    state = out["deal_state"]
    silence = 0

    if state == "silent":
        # Клиент пропал. В боте это заметно само собой — сообщение просто не
        # приходит. На странице тишину надо назвать словами, иначе она
        # читается как поломка, а не как поведение клиента.
        silence = out["silence_hours"]
        session["silences"] = session.get("silences", 0) + 1
        session["awaiting_followup"] = True
        session["last_silence_hours"] = silence
        session["transcript"].append(["system", engine.silence_marker(silence)])
    else:
        session["awaiting_followup"] = False
        for m in out["buyer_messages"]:
            session["transcript"].append(["buyer", m])

    session["state"] = state
    await loop.run_in_executor(
        None, store.put, user["telegram_id"], user["company_id"], session)

    return _view(session, {
        "messages": [] if state == "silent" else out["buyer_messages"],
        "silence_hours": silence,
        "over": state in ("won", "failed") or session["turns"] >= MAX_TURNS,
    })


async def finish(client, user):
    """
    Разбор и закрытие тренировки. Здесь же списывается тренировка из лимита:
    начатую и брошенную на первом ходу считать нечестно.
    """
    loop = asyncio.get_event_loop()
    session = await loop.run_in_executor(None, store.get, user["telegram_id"])
    if not session:
        raise ValueError("Тренировка не найдена — начните новую")

    state = session.get("state", "active")
    result = "won" if state == "won" else "lost"

    debrief, usage = await loop.run_in_executor(
        None, engine.final_debrief, client, session, result)
    if usage:
        await loop.run_in_executor(
            None, stats.record_usage, user["company_id"], user["telegram_id"],
            "debrief", engine.DEBRIEF_MODEL, usage)

    await loop.run_in_executor(
        None, lambda: stats.record_session(
            user, session["scenario"], result, session["turns"],
            session["transcript"], via="web"))
    used, limit = await loop.run_in_executor(
        None, tenancy.consume_session, user["company_id"])
    await loop.run_in_executor(None, store.drop, user["telegram_id"])

    return {
        "verdict": debrief,
        "result": result,
        "used": used,
        "limit": limit,
        "warning": tenancy.usage_warning(used, limit),
    }


# --- HTTP --------------------------------------------------------------------

def attach(app, bot, anthropic_client, origins):
    """
    Подключить комнату к панели.

    Комната живёт в том же процессе, что и бот с панелью: движок, база и ключ
    модели уже здесь. Отдельный сервис пришлось бы отдельно деплоить, отдельно
    охранять и держать с ним общую базу — ради одного экрана это дорого.
    """

    def cors(resp, request):
        origin = request.headers.get("Origin", "")
        if origin and (origin in origins or "*" in origins):
            resp.headers["Access-Control-Allow-Origin"] = origin
            # Комната ходит с куками, а с ними браузер требует точный адрес
            # источника и это разрешение. Со звёздочкой запрос не пройдёт.
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
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

    def set_cookie(resp, value, max_age):
        kw = {}
        if COOKIE_DOMAIN:
            kw["domain"] = COOKIE_DOMAIN
        resp.set_cookie(
            COOKIE, value, max_age=max_age, httponly=True,
            # Кука ездит между сайтом и панелью, поэтому «Lax» не годится:
            # с ним браузер не приложит её к запросу с соседнего домена.
            # «None» без «Secure» браузеры отвергают, поэтому только по https.
            samesite="None", secure=True, **kw)
        return resp

    def who(request):
        user = identity(request)
        if not user:
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Требуется вход"}),
                                       content_type="application/json")
        return user

    # ——— вход ———

    async def h_login(request):
        """
        Шаг первый: менеджер называет свой Telegram ID, бот присылает ему код.

        Пароля у менеджеров нет намеренно. Пароли пришлось бы раздать всему
        отделу, а потом восстанавливать их вместо работы. Код в боте
        доказывает то же самое: человек владеет тем аккаунтом, которым уже
        тренируется.
        """
        d = await body(request)
        raw = str(d.get("login") or "").strip()
        if not raw.isdigit():
            return ok(request, {"error": "Введите ваш Telegram ID — только цифры"}, 400)

        telegram_id = int(raw)
        loop = asyncio.get_event_loop()
        user = await loop.run_in_executor(None, tenancy.get_user, telegram_id)

        # Ответ одинаковый и для своих, и для чужих: иначе по нему можно
        # перебором узнать, какие аккаунты заведены в системе.
        token = hashlib.sha256(os.urandom(24)).hexdigest()[:32]
        if user and user["active"]:
            code = f"{random.randint(0, 999999):06d}"
            _pending[token] = {
                "code": code, "exp": time.time() + CODE_TTL,
                "left": CODE_ATTEMPTS, "telegram_id": telegram_id,
                "company_id": user["company_id"],
            }
            try:
                await bot.send_message(
                    telegram_id,
                    f"🔐 Код для входа в комнату: *{code}*\n\n"
                    "Действует пять минут. Если это не вы — просто не вводите его.",
                    parse_mode="Markdown")
            except Exception:
                log.exception("Код входа не доставлен менеджеру %s", telegram_id)
                return ok(request, {
                    "error": "Не получилось отправить код. Напишите боту любое "
                             "сообщение и попробуйте снова."}, 502)
        return ok(request, {"token": token})

    async def h_verify(request):
        d = await body(request)
        entry = _pending.get(str(d.get("token") or ""))
        if not entry or entry["exp"] < time.time():
            _pending.pop(str(d.get("token") or ""), None)
            return ok(request, {"error": "Код истёк — запросите новый"}, 400)

        if not hmac.compare_digest(str(d.get("code", "")), entry["code"]):
            entry["left"] -= 1
            if entry["left"] <= 0:
                _pending.pop(str(d.get("token")), None)
                return ok(request, {"error": "Слишком много попыток — начните заново"}, 400)
            return ok(request, {"error": f"Неверный код, осталось попыток: {entry['left']}"}, 400)

        _pending.pop(str(d.get("token")), None)
        _tok = make_token(entry["company_id"], entry["telegram_id"])
        resp = ok(request, {"ok": True, "token": _tok})
        return set_cookie(resp, _tok, SESSION_DAYS * 86400)

    async def h_logout(request):
        resp = ok(request, {"ok": True})
        return set_cookie(resp, "", 0)

    # ——— гайд ———

    async def h_guide(request):
        """
        Гайд по алгоритму продаж — той же страницей, что уходит файлом в боте.

        Отдаём его текстом, а не ссылкой на файл: страница показывается внутри
        комнаты рядом с перепиской, и открывать её отдельной вкладкой значило
        бы увести человека из тренировки.

        Файл один и тот же для всех, поэтому читаем его с диска каждый раз, но
        просим браузер подержать копию у себя: он не меняется между сборками.
        """
        who(request)
        if not guide.guide_exists():
            return ok(request, {"error": "Гайд не найден на сервере"}, 404)
        try:
            with open(guide.GUIDE_FILE, encoding="utf-8") as f:
                html = f.read()
        except Exception:
            log.exception("Гайд не прочитался")
            return ok(request, {"error": "Гайд не открылся"}, 500)
        resp = web.Response(text=html, content_type="text/html", charset="utf-8")
        resp.headers["Cache-Control"] = "private, max-age=3600"
        return cors(resp, request)

    # ——— комната ———

    async def h_me(request):
        """Кто вошёл, сколько тренировок осталось и есть ли незаконченная."""
        user = identity(request)
        if not user:
            return ok(request, {"authorized": False})

        loop = asyncio.get_event_loop()
        company = await loop.run_in_executor(None, tenancy.get_company, user["company_id"])
        session = await loop.run_in_executor(None, store.get, user["telegram_id"])
        return ok(request, {
            "authorized": True,
            "name": user.get("full_name") or user.get("username"),
            "company": company["title"] if company else None,
            "used": company["sessions_used"] if company else None,
            "limit": company["session_limit"] if company else None,
            # Незаконченная тренировка может быть начата и в боте — тогда
            # человек продолжит её здесь с того же места.
            "active": _view(session) if session else None,
        })

    async def h_start(request):
        user = who(request)
        try:
            return ok(request, await start(anthropic_client, user))
        except ValueError as e:
            return ok(request, {"error": str(e), "soft": True}, 409)
        except Exception:
            log.exception("Комната не запустилась")
            return ok(request, {"error": "Не получилось собрать клиента. Попробуйте ещё раз."}, 500)

    async def h_say(request):
        user = who(request)
        d = await body(request)
        try:
            return ok(request, await say(anthropic_client, user, d.get("text"),
                                        d.get("images") or d.get("image")))
        except ValueError as e:
            return ok(request, {"error": str(e)}, 400)
        except Exception:
            log.exception("Ход в комнате не прошёл")
            return ok(request, {"error": "Клиент задумался и не ответил. Напишите ещё раз."}, 500)

    async def h_finish(request):
        user = who(request)
        try:
            return ok(request, await finish(anthropic_client, user))
        except ValueError as e:
            return ok(request, {"error": str(e)}, 400)
        except Exception:
            log.exception("Разбор в комнате не собрался")
            return ok(request, {"error": "Разбор не собрался. Тренировка сохранена, "
                                         "загляните в бота."}, 500)

    r = app.router
    r.add_post("/api/room/login", h_login)
    r.add_post("/api/room/verify", h_verify)
    r.add_post("/api/room/logout", h_logout)
    r.add_get("/api/room/me", h_me)
    r.add_get("/api/room/guide", h_guide)
    r.add_post("/api/room/start", h_start)
    r.add_post("/api/room/say", h_say)
    r.add_post("/api/room/finish", h_finish)
    for path in ("/api/room/login", "/api/room/verify", "/api/room/logout",
                 "/api/room/me", "/api/room/guide", "/api/room/start",
                 "/api/room/say", "/api/room/finish"):
        r.add_route("OPTIONS", path, preflight)
