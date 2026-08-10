# -*- coding: utf-8 -*-
"""
Веб-панель: HTTP-слой.

Живёт в том же процессе, что бот. Отдельный сервис не нужен: база рядом,
Telegram под рукой — код подтверждения уходит владельцу в личку тем же
ботом, который обслуживает клиентов.

Вход в две ступени: пароль из переменной окружения, затем одноразовый код
в Telegram. Пароль в переменной может утечь вместе с бэкапом настроек,
а вторая ступень требует доступа к телефону владельца.

Сессия — подписанная кука. Своей таблицы под сессии нет намеренно: она бы
пережила смену пароля и стала бы вторым, забытым способом входа.
"""

import asyncio
import base64
import datetime
import hashlib
import hmac
import json
import logging
import os
import random
import secrets
import time

from aiohttp import web

from . import db, tenancy, demo, admin_data, niche_loader

log = logging.getLogger(__name__)

PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SECRET = os.environ.get("ADMIN_SECRET") or hashlib.sha256(
    (PASSWORD + os.environ.get("BOT_TOKEN", "")).encode()).hexdigest()

COOKIE = "moss_admin"
SESSION_DAYS = 30
CODE_TTL = 300          # одноразовый код живёт пять минут
CODE_ATTEMPTS = 5

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "admin")

# Ожидающие подтверждения входы: токен -> код, срок, попытки.
_pending = {}

# Простая защита от перебора пароля: адрес -> [время неудач].
_failures = {}


# --- Подпись сессии ---------------------------------------------------------

def _sign(payload: str) -> str:
    mac = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def make_token(subject="admin"):
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{subject}:{exp}"
    return f"{payload}:{_sign(payload)}"


def valid_token(token):
    if not token or token.count(":") != 2:
        return False
    subject, exp, sig = token.split(":")
    if not hmac.compare_digest(sig, _sign(f"{subject}:{exp}")):
        return False
    try:
        return int(exp) > time.time()
    except ValueError:
        return False


def _authorized(request):
    return valid_token(request.cookies.get(COOKIE))


@web.middleware
async def auth_middleware(request, handler):
    """
    Всё под /api закрыто, кроме входа. Статика открыта: в ней нет данных,
    а закрывать её означало бы отдавать страницу входа тем же кодом.
    """
    path = request.path
    open_paths = ("/api/login", "/api/verify", "/api/whoami", "/api/health")
    if path.startswith("/api/") and path not in open_paths and not _authorized(request):
        return web.json_response({"error": "Требуется вход"}, status=401)
    return await handler(request)


# --- Вспомогательное --------------------------------------------------------

def jsonify(data, status=200):
    """
    Отдать JSON, приведя типы Postgres к пригодным для передачи.
    Даты — в ISO, деньги — в числа: на стороне браузера ничего не парсим руками.
    """
    def convert(o):
        if isinstance(o, (datetime.datetime, datetime.date)):
            return o.isoformat()
        if isinstance(o, datetime.timedelta):
            return o.total_seconds()
        try:
            return float(o)
        except (TypeError, ValueError):
            return str(o)

    return web.json_response(
        text=json.dumps(data, default=convert, ensure_ascii=False),
        status=status, content_type="application/json")


async def in_thread(fn, *args, **kwargs):
    """Запросы к базе синхронные — уводим их с петли событий."""
    loop = asyncio.get_event_loop()
    if kwargs:
        from functools import partial
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))
    return await loop.run_in_executor(None, fn, *args)


async def body(request):
    try:
        return await request.json()
    except Exception:
        return {}


def actor(request):
    return "admin"


# --- Вход -------------------------------------------------------------------

def _throttled(ip):
    """Пять неудач за пять минут — и адрес отдыхает."""
    now = time.time()
    tries = [t for t in _failures.get(ip, []) if now - t < 300]
    _failures[ip] = tries
    return len(tries) >= 5


def build_app(bot):
    app = web.Application(middlewares=[auth_middleware])

    # ---------- вход ----------

    async def login(request):
        ip = request.remote or "?"
        if _throttled(ip):
            return jsonify({"error": "Слишком много попыток. Подождите пять минут."}, 429)

        data = await body(request)
        if not PASSWORD:
            return jsonify({"error": "Пароль панели не задан на сервере"}, 500)
        if data.get("password") != PASSWORD:
            _failures.setdefault(ip, []).append(time.time())
            return jsonify({"error": "Неверный пароль"}, 403)

        code = f"{random.randint(0, 999999):06d}"
        token = secrets.token_urlsafe(16)
        _pending[token] = {"code": code, "exp": time.time() + CODE_TTL, "left": CODE_ATTEMPTS}

        sent = 0
        for uid in sorted(tenancy_admin_ids()):
            try:
                await bot.send_message(
                    uid, f"🔐 Код входа в панель: *{code}*\n\nДействует пять минут. "
                         f"Если это не вы — просто не вводите его.",
                    parse_mode="Markdown")
                sent += 1
            except Exception:
                log.exception("Не удалось отправить код администратору %s", uid)

        if not sent:
            return jsonify({"error": "Некому отправить код: проверьте ADMIN_IDS"}, 500)
        return jsonify({"token": token})

    async def verify(request):
        data = await body(request)
        token = data.get("token")
        entry = _pending.get(token)
        if not entry or entry["exp"] < time.time():
            _pending.pop(token, None)
            return jsonify({"error": "Код устарел, начните заново"}, 403)

        entry["left"] -= 1
        if entry["left"] <= 0:
            _pending.pop(token, None)
        if not hmac.compare_digest(str(data.get("code", "")), entry["code"]):
            return jsonify({"error": "Неверный код"}, 403)

        _pending.pop(token, None)
        resp = jsonify({"ok": True})
        resp.set_cookie(COOKIE, make_token(), max_age=SESSION_DAYS * 86400,
                        httponly=True, samesite="Lax")
        return resp

    async def whoami(request):
        return jsonify({"authorized": _authorized(request)})

    async def logout(request):
        resp = jsonify({"ok": True})
        resp.del_cookie(COOKIE)
        return resp

    # ---------- обзор ----------

    async def overview(request):
        return jsonify(await in_thread(admin_data.overview))

    # ---------- клиенты ----------

    async def companies(request):
        return jsonify(await in_thread(
            admin_data.companies,
            request.query.get("status"), request.query.get("q")))

    async def company(request):
        cid = int(request.match_info["id"])
        card = await in_thread(admin_data.company, cid)
        if not card:
            return jsonify({"error": "Компания не найдена"}, 404)
        return jsonify(card)

    async def company_create(request):
        d = await body(request)
        title = (d.get("title") or "").strip()
        if not title:
            return jsonify({"error": "Нужно название"}, 400)
        plan = d.get("plan") or tenancy.DEFAULT_PLAN
        if plan not in tenancy.PLANS:
            return jsonify({"error": "Неизвестный тариф"}, 400)

        c = await in_thread(tenancy.create_company, title, plan, d.get("email"))
        await in_thread(tenancy.log_action, actor(request), "create", c["id"], None,
                        {"plan": plan})
        me = await bot.get_me()
        return jsonify({
            "id": c["id"],
            "link": f"https://t.me/{me.username}?start={c['activation_code']}",
            "activation_code": c["activation_code"],
        })

    async def company_action(request):
        cid = int(request.match_info["id"])
        what = request.match_info["action"]
        d = await body(request)

        def extend_term():
            """Продлить на срок и записать поступление."""
            months = int(d.get("months", 1))
            company = tenancy.get_company(cid)
            plan = company["plan"] if company else None
            amount = d.get("amount_kzt")
            if amount is None:
                amount = tenancy.price_for(plan, months)
            tenancy.extend_months(cid, months)
            if int(amount) > 0 or d.get("note"):
                tenancy.record_payment(cid, months, int(amount), plan, d.get("note"))
            return True

        actions = {
            "term":     extend_term,
            "extend":   lambda: tenancy.extend(cid, int(d.get("days", 30))),
            "plan":     lambda: tenancy.change_plan(cid, d.get("plan")),
            "seats":    lambda: tenancy.add_seats(cid, int(d.get("n", 1))),
            "sessions": lambda: tenancy.add_sessions(cid, int(d.get("n", 50))),
            "suspend":  lambda: tenancy.suspend(cid),
            "resume":   lambda: tenancy.resume(cid),
            "rotate":   lambda: tenancy.rotate_invite_code(cid),
            "reset":    lambda: tenancy.reset_period(cid),
        }
        if what not in actions:
            return jsonify({"error": "Неизвестное действие"}, 400)

        try:
            await in_thread(actions[what])
        except ValueError as e:
            return jsonify({"error": str(e)}, 400)

        await in_thread(tenancy.log_action, actor(request), what, cid, None, d or None)
        return jsonify(await in_thread(admin_data.company, cid))

    # ---------- пользователи ----------

    async def users(request):
        cid = request.query.get("company_id")
        return jsonify(await in_thread(
            admin_data.users, request.query.get("q"), int(cid) if cid else None))

    async def user_action(request):
        tg = int(request.match_info["tg"])
        what = request.match_info["action"]
        d = await body(request)
        u = await in_thread(tenancy.get_user, tg)
        if not u:
            return jsonify({"error": "Пользователь не найден"}, 404)

        if what in ("enable", "disable"):
            await in_thread(tenancy.set_user_active, u["company_id"], tg, what == "enable")
        elif what == "role":
            role = d.get("role")
            if role not in (tenancy.ROLE_OWNER, tenancy.ROLE_MANAGER):
                return jsonify({"error": "Неизвестная роль"}, 400)
            await in_thread(db.execute, "UPDATE users SET role=%s WHERE telegram_id=%s",
                            (role, tg))
        elif what == "move":
            target = int(d.get("company_id"))
            await in_thread(db.execute, "UPDATE users SET company_id=%s WHERE telegram_id=%s",
                            (target, tg))
        elif what == "remove":
            await in_thread(db.execute, "DELETE FROM users WHERE telegram_id=%s", (tg,))
        else:
            return jsonify({"error": "Неизвестное действие"}, 400)

        await in_thread(tenancy.log_action, actor(request), f"user.{what}",
                        u["company_id"], tg, d or None)
        return jsonify({"ok": True})

    async def user_session(request):
        tg = int(request.match_info["tg"])
        data = await in_thread(admin_data.last_session, tg)
        return jsonify(data or {"session": None, "messages": []})

    # ---------- демо ----------

    async def demo_queue(request):
        return jsonify(await in_thread(admin_data.demo_queue))

    async def demo_grant(request):
        """Выдать гостю пилот: создаём компанию и шлём ему ссылку в личку."""
        tg = int(request.match_info["tg"])
        d = await body(request)
        title = (d.get("title") or "").strip() or f"Пилот {tg}"
        plan = d.get("plan") or "trial"

        c = await in_thread(tenancy.create_company, title, plan, d.get("email"))
        await in_thread(demo.release, tg)
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start={c['activation_code']}"
        try:
            await bot.send_message(
                tg, f"Мы открыли для вас пилот на отдел продаж.\n\n"
                    f"Нажмите, чтобы подключить компанию:\n{link}")
        except Exception:
            log.exception("Гость %s недоступен", tg)

        await in_thread(tenancy.log_action, actor(request), "demo.grant", c["id"], tg,
                        {"plan": plan})
        return jsonify({"id": c["id"], "link": link})

    # ---------- деньги и отчёты ----------

    async def money(request):
        return jsonify(await in_thread(admin_data.money, int(request.query.get("days", 30))))

    async def summary(request):
        return jsonify(await in_thread(admin_data.summary, int(request.query.get("days", 30))))

    async def psychotypes(request):
        cid = request.query.get("company_id")
        return jsonify(await in_thread(admin_data.psychotypes,
                                       int(cid) if cid else None))

    async def log_tail(request):
        rows = await in_thread(
            db.query,
            """SELECT l.*, c.title AS company_title FROM admin_log l
                 LEFT JOIN companies c ON c.id = l.company_id
             ORDER BY l.created_at DESC LIMIT 100""")
        return jsonify([dict(r) for r in (rows or [])])

    # ---------- рассылка ----------

    async def broadcast_preview(request):
        d = await body(request)
        cid = d.get("company_id")
        ids = await in_thread(admin_data.segment, d.get("segment", "owners"),
                              int(cid) if cid else None)
        return jsonify({"count": len(ids), "segments": admin_data.SEGMENTS})

    async def broadcast_send(request):
        """
        Отправка пачками с паузой: Telegram режет массовые рассылки,
        без пауз половина сообщений не доходит и бота могут придержать.
        """
        d = await body(request)
        text = (d.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Пустое сообщение"}, 400)

        if d.get("test"):
            ids = sorted(tenancy_admin_ids())
        else:
            cid = d.get("company_id")
            ids = await in_thread(admin_data.segment, d.get("segment", "owners"),
                                  int(cid) if cid else None)

        sent = blocked = failed = 0
        for i, uid in enumerate(ids):
            try:
                await bot.send_message(uid, text, parse_mode="Markdown")
                sent += 1
            except Exception as e:
                if "blocked" in str(e).lower() or "chat not found" in str(e).lower():
                    blocked += 1
                else:
                    failed += 1
            if (i + 1) % 20 == 0:
                await asyncio.sleep(1.2)
            else:
                await asyncio.sleep(0.05)

        await in_thread(tenancy.log_action, actor(request), "broadcast", None, None,
                        {"segment": d.get("segment"), "sent": sent})
        return jsonify({"sent": sent, "blocked": blocked, "failed": failed,
                        "total": len(ids)})

    # ---------- настройки ----------

    async def plan_save(request):
        """Правка тарифа из панели — без деплоя."""
        d = await body(request)
        key = (d.get("key") or "").strip()
        if not key:
            return jsonify({"error": "Не указан тариф"}, 400)
        await in_thread(tenancy.save_plan, key, d.get("title"), d.get("price_kzt"),
                        d.get("seats"), d.get("session_limit"))
        await in_thread(tenancy.log_action, actor(request), "plan.save", None, None, d)
        return jsonify({"plans": tenancy.PLANS})

    async def price_save(request):
        """Цена конкретного срока конкретного тарифа."""
        d = await body(request)
        key = (d.get("key") or "").strip()
        months = int(d.get("months", 0))
        if not key or months <= 0:
            return jsonify({"error": "Нужны тариф и срок"}, 400)
        await in_thread(tenancy.save_price, key, months, int(d.get("price_kzt", 0)))
        await in_thread(tenancy.log_action, actor(request), "price.save", None, None, d)
        return jsonify({"prices": tenancy.PRICES})

    async def export(request):
        """Выгрузки в CSV. Имя файла с датой — иначе в папке каша."""
        what = request.match_info["what"]
        days = int(request.query.get("days", 30))
        cid = request.query.get("company_id")

        makers = {
            "companies": lambda: admin_data.export_companies(),
            "money": lambda: admin_data.export_money(days),
            "sessions": lambda: admin_data.export_sessions(int(cid) if cid else None, 90),
        }
        if what not in makers:
            return web.Response(status=404, text="Нет такой выгрузки")

        text = await in_thread(makers[what])
        stamp = datetime.date.today().isoformat()
        return web.Response(
            body=text.encode("utf-8"),
            content_type="text/csv", charset="utf-8",
            headers={"Content-Disposition": f'attachment; filename="moss-{what}-{stamp}.csv"'},
        )

    async def client_report(request):
        """Отчёт для клиента: показать в панели и по кнопке отправить владельцу."""
        cid = int(request.match_info["id"])
        text = await in_thread(admin_data.client_report, cid)
        if not text:
            return jsonify({"error": "Компания не найдена"}, 404)

        if (await body(request)).get("send"):
            owner = await in_thread(tenancy.owner_of, cid)
            if not owner:
                return jsonify({"error": "У компании нет руководителя"}, 400)
            try:
                await bot.send_message(owner["telegram_id"], text, parse_mode="Markdown")
            except Exception as e:
                return jsonify({"error": f"Не доставлено: {e}"}, 502)
            await in_thread(tenancy.log_action, actor(request), "report.send", cid,
                            owner["telegram_id"])
            return jsonify({"sent": True, "text": text})

        return jsonify({"text": text})

    async def settings(request):
        return jsonify({
            "plans": tenancy.PLANS,
            "prices": tenancy.PRICES,
            "terms": list(tenancy.TERMS),
            "usd_kzt": admin_data.USD_KZT,
            "demo_limit": demo.LIMIT,
            "segments": admin_data.SEGMENTS,
        })

    async def health(request):
        ok = await in_thread(db.healthcheck)
        return jsonify({"db": ok})

    # ---------- статика ----------

    async def index(request):
        path = os.path.join(STATIC_DIR, "index.html")
        if not os.path.exists(path):
            return web.Response(
                text="Панель ещё не собрана. Выполните сборку фронтенда.",
                content_type="text/plain")
        return web.FileResponse(path)

    r = app.router
    r.add_post("/api/login", login)
    r.add_post("/api/verify", verify)
    r.add_post("/api/logout", logout)
    r.add_get("/api/whoami", whoami)
    r.add_get("/api/health", health)

    r.add_get("/api/overview", overview)
    r.add_get("/api/companies", companies)
    r.add_post("/api/companies", company_create)
    r.add_get("/api/companies/{id}", company)
    # Обязательно до маршрута с произвольным действием: aiohttp выбирает
    # первый подходящий по порядку регистрации.
    r.add_post("/api/companies/{id}/report", client_report)
    r.add_post("/api/companies/{id}/{action}", company_action)

    r.add_get("/api/users", users)
    r.add_get("/api/users/{tg}/session", user_session)
    r.add_post("/api/users/{tg}/{action}", user_action)

    r.add_get("/api/demo", demo_queue)
    r.add_post("/api/demo/{tg}/grant", demo_grant)

    r.add_get("/api/money", money)
    r.add_get("/api/summary", summary)
    r.add_get("/api/psychotypes", psychotypes)
    r.add_get("/api/log", log_tail)
    r.add_get("/api/settings", settings)
    r.add_post("/api/plans", plan_save)
    r.add_post("/api/prices", price_save)
    r.add_get("/api/export/{what}", export)

    r.add_post("/api/broadcast/preview", broadcast_preview)
    r.add_post("/api/broadcast/send", broadcast_send)

    if os.path.isdir(os.path.join(STATIC_DIR, "assets")):
        r.add_static("/assets/", os.path.join(STATIC_DIR, "assets"))
    r.add_get("/", index)
    r.add_get("/{tail:.*}", index)   # одностраничное приложение: всё ведёт в index

    return app


def tenancy_admin_ids():
    from . import notify
    return notify.ADMIN_IDS


async def start(bot):
    """
    Поднять панель. Railway задаёт PORT, когда у сервиса создан домен;
    без него панель просто не запускается и бот работает как раньше.
    """
    port = os.environ.get("PORT")
    if not port:
        log.info("PORT не задан — панель не поднимается")
        return None
    if not PASSWORD:
        log.warning("ADMIN_PASSWORD не задан — панель поднимается, но вход невозможен")

    runner = web.AppRunner(build_app(bot))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(port)).start()
    log.info("Панель управления слушает порт %s", port)
    return runner
