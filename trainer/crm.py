# -*- coding: utf-8 -*-
"""
Мини-CRM поверх панели: crm.aisaty.com.

Не отдельная система, а ещё одно окно в ту же базу. Лид рождается из демо
на сайте сам, компания создаётся тем же tenancy, счёт считается из тех же
тарифов, оплата пишется в те же payments. Менеджер ничего не вбивает
дважды — он двигает карточки и звонит.

Вход — как в комнате: Telegram ID и код в личку. Кто может войти, решает
таблица crm_users (владельцы из ADMIN_IDS могут всегда) — добавление
менеджера здесь же, в разделе «Команда».
"""

import asyncio
import csv
import datetime
import io
import json
import logging
import os
import random
import secrets
import time

from aiohttp import web

from . import db, notify, tenancy, webadmin

log = logging.getLogger(__name__)

COOKIE = "moss_crm"
SESSION_DAYS = 30
CODE_TTL = 300
CODE_ATTEMPTS = 5

# Бот кладётся сюда при attach: уведомления о лидах шлёт он же, из любого
# места модуля. Отдельный бот не нужен — у менеджеров и так есть диалог
# с рабочим ботом, а второй процесс пришлось бы отдельно деплоить.
BOT = None
_BOT_USERNAME = None

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "crm")

# Ссылка в уведомлениях. Хост можно переопределить переменной окружения.
CRM_URL = os.environ.get("CRM_URL", "https://crm.aisaty.com")

# Реквизиты для счёта. Пока оплата руками — текст счёта собирается из них.
INVOICE_DETAILS = os.environ.get("CRM_INVOICE_DETAILS", "")

# Колонки канбана. Порядок здесь = порядок на доске.
STATUSES = (
    ("new",   "Новые"),
    ("talk",  "В работе"),
    ("think", "Думают"),
    ("pilot", "Пилот"),
    ("paid",  "Оплатили"),
    ("lost",  "Отказ"),
)
STATUS_KEYS = {k for k, _ in STATUSES}

_pending = {}   # token -> {code, exp, left, tg}


# --- Схема -------------------------------------------------------------------

def ensure_schema():
    """Создать таблицы CRM. Безопасно при каждом старте."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS crm_users (
            telegram_id BIGINT      PRIMARY KEY,
            name        TEXT        NOT NULL DEFAULT '',
            role        TEXT        NOT NULL DEFAULT 'manager',
            notify      BOOLEAN     NOT NULL DEFAULT TRUE,
            active      BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS crm_leads (
            id             BIGSERIAL   PRIMARY KEY,
            demo_id        BIGINT      UNIQUE REFERENCES web_demo(id) ON DELETE SET NULL,
            source         TEXT        NOT NULL DEFAULT 'site',
            name           TEXT        NOT NULL DEFAULT '',
            contact        TEXT        NOT NULL DEFAULT '',
            tg_id          BIGINT,
            niche          TEXT        NOT NULL DEFAULT '',
            note           TEXT        NOT NULL DEFAULT '',
            status         TEXT        NOT NULL DEFAULT 'new',
            assignee       BIGINT,
            plan_key       TEXT,
            months         INTEGER     NOT NULL DEFAULT 1,
            company_id     BIGINT      REFERENCES companies(id) ON DELETE SET NULL,
            pilot_notified BOOLEAN     NOT NULL DEFAULT FALSE,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_crm_leads_status ON crm_leads(status, updated_at DESC)")


# --- Доступ ------------------------------------------------------------------

def _user(tg):
    return db.query(
        "SELECT * FROM crm_users WHERE telegram_id=%s AND active", (tg,), one=True)


def allowed(tg):
    """Роль или None. Владельцы продукта входят всегда, без записи в таблице."""
    if tg in notify.ADMIN_IDS:
        return "owner"
    u = _user(tg)
    if u:
        return "owner" if u.get("role") == "owner" else "manager"
    return None


def _make_token(tg, role):
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = f"crm:{tg}:{role}:{exp}"
    return f"{payload}:{webadmin._sign(payload)}"


def _read_token(token):
    if not token or "." in token or token.count(":") != 4:
        return None
    payload, sig = token.rsplit(":", 1)
    import hmac as _hmac
    if not _hmac.compare_digest(sig, webadmin._sign(payload)):
        return None
    kind, tg, role, exp = payload.split(":")
    if kind != "crm" or int(exp) < time.time():
        return None
    return {"tg": int(tg), "role": role}


def identity(request):
    got = _read_token(request.cookies.get(COOKIE))
    if not got:
        return None
    # Роль могла измениться после выдачи куки — сверяем с базой.
    role = allowed(got["tg"])
    if not role:
        return None
    got["role"] = role
    return got


# --- Уведомления -------------------------------------------------------------

def _recipients():
    ids = set(notify.ADMIN_IDS)
    for r in db.query("SELECT telegram_id FROM crm_users WHERE active AND notify") or []:
        ids.add(int(r["telegram_id"]))
    return ids


async def _tell(text):
    if BOT is None:
        return
    for uid in _recipients():
        try:
            await BOT.send_message(uid, text, disable_web_page_preview=True)
        except Exception:
            log.exception("CRM: не достучались до %s", uid)


async def lead_from_demo(token):
    """
    Гость демо оставил контакт — завести лид и разбудить менеджеров.
    Вызывается из webdemo после finish; падать здесь нельзя ни при каких
    условиях — разбор человеку важнее нашей записной книжки.
    """
    try:
        row = db.query("SELECT * FROM web_demo WHERE token=%s", (token,), one=True)
        if not row or not (row.get("contact") or "").strip():
            return
        answers = row.get("answers") or {}
        if isinstance(answers, str):
            try:
                answers = json.loads(answers)
            except Exception:
                answers = {}
        lead = db.execute(
            """INSERT INTO crm_leads (demo_id, source, name, contact, niche)
               VALUES (%s, 'site', %s, %s, %s)
               ON CONFLICT (demo_id) DO UPDATE
                 SET name=EXCLUDED.name, contact=EXCLUDED.contact, updated_at=now()
               RETURNING *""",
            (row["id"], row.get("contact_name") or "", row["contact"],
             row.get("niche") or answers.get("product") or ""),
            returning=True)
        res = {"won": "закрыл сделку ✅", "lost": "слил сделку ❌"}.get(row.get("result"), "не довёл до конца")
        verdict = (row.get("verdict") or "").strip()
        if len(verdict) > 350:
            verdict = verdict[:350] + "…"
        await _tell(
            f"🔥 Новый лид с сайта\n\n"
            f"{lead['name'] or 'Без имени'} · {lead['contact']}\n"
            f"Ниша: {lead['niche'] or '—'}\n"
            f"Демо: {res}, ходов: {row.get('turns') or 0}\n\n"
            f"Разбор:\n{verdict or '—'}\n\n"
            f"Карточка: {CRM_URL}")
    except Exception:
        log.exception("CRM: лид из демо не завёлся")


async def _check_pilot_activity():
    """
    Пилот начал тренироваться — второй звонок самый уместный именно сейчас.
    Дёшево и надёжно: проверка при каждом открытии доски, уведомление один раз.
    """
    rows = db.query(
        """SELECT l.id, l.name, l.contact, c.title, c.sessions_used
             FROM crm_leads l JOIN companies c ON c.id = l.company_id
            WHERE NOT l.pilot_notified AND c.sessions_used > 0""") or []
    for r in rows:
        db.execute("UPDATE crm_leads SET pilot_notified=TRUE, updated_at=now() WHERE id=%s",
                   (r["id"],))
        await _tell(
            f"🏃 Пилот пошёл: «{r['title']}» провёл первую тренировку.\n"
            f"{r['name'] or ''} · {r['contact']}\n"
            f"Лучший момент для звонка. {CRM_URL}")


# --- Подключение -------------------------------------------------------------

def attach(app, bot):
    """Подключить CRM к панели. Вызывается из webadmin.build_app."""
    global BOT
    BOT = bot
    ensure_schema()

    def jsonify(data, status=200):
        return web.json_response(
            text=json.dumps(data, ensure_ascii=False, default=str), status=status)

    async def body(request):
        try:
            return await request.json()
        except Exception:
            return {}

    async def in_thread(fn, *args, **kwargs):
        return await asyncio.get_event_loop().run_in_executor(None, lambda: fn(*args, **kwargs))

    def who(request):
        got = identity(request)
        if not got:
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "Требуется вход"}), content_type="application/json")
        return got

    def owner_only(request):
        got = who(request)
        if got["role"] != "owner":
            raise web.HTTPForbidden(
                text=json.dumps({"error": "Только для владельца"}), content_type="application/json")
        return got

    async def bot_username():
        global _BOT_USERNAME
        if not _BOT_USERNAME:
            me = await bot.get_me()
            _BOT_USERNAME = me.username
        return _BOT_USERNAME

    # ---------- вход ----------

    async def h_login(request):
        d = await body(request)
        raw = str(d.get("tg") or "").strip()
        if not raw.isdigit():
            return jsonify({"error": "Telegram ID — только цифры"}, 400)
        tg = int(raw)
        token = secrets.token_urlsafe(16)
        role = await in_thread(allowed, tg)
        # Ответ одинаковый для своих и чужих: по нему нельзя перебором
        # выяснить, чьи ID заведены в CRM.
        if role:
            code = f"{random.randint(0, 999999):06d}"
            _pending[token] = {"code": code, "exp": time.time() + CODE_TTL,
                               "left": CODE_ATTEMPTS, "tg": tg, "role": role}
            try:
                await bot.send_message(
                    tg, f"🔐 Код входа в CRM: *{code}*\n\nДействует пять минут.",
                    parse_mode="Markdown")
            except Exception:
                log.exception("CRM: код не доставлен %s", tg)
                return jsonify({"error": "Не получилось отправить код. Напишите боту "
                                         "любое сообщение и попробуйте снова."}, 502)
        return jsonify({"token": token})

    async def h_verify(request):
        d = await body(request)
        entry = _pending.get(str(d.get("token") or ""))
        if not entry or entry["exp"] < time.time():
            _pending.pop(str(d.get("token") or ""), None)
            return jsonify({"error": "Код истёк — запросите новый"}, 400)
        import hmac as _hmac
        if not _hmac.compare_digest(str(d.get("code", "")), entry["code"]):
            entry["left"] -= 1
            if entry["left"] <= 0:
                _pending.pop(str(d.get("token")), None)
                return jsonify({"error": "Слишком много попыток — начните заново"}, 400)
            return jsonify({"error": f"Неверный код, осталось попыток: {entry['left']}"}, 400)
        _pending.pop(str(d.get("token")), None)
        tok = _make_token(entry["tg"], entry["role"])
        resp = jsonify({"ok": True, "role": entry["role"], "token": tok})
        resp.set_cookie(COOKIE, tok, max_age=SESSION_DAYS * 86400,
                        httponly=True, samesite="Lax")
        return resp

    async def h_logout(request):
        resp = jsonify({"ok": True})
        resp.del_cookie(COOKIE)
        return resp

    async def h_me(request):
        got = identity(request)
        if not got:
            return jsonify({"authorized": False})
        u = await in_thread(_user, got["tg"]) or {}
        return jsonify({"authorized": True, "tg": got["tg"], "role": got["role"],
                        "name": u.get("name") or ""})

    # ---------- доска ----------

    def _plans():
        rows = db.query(
            "SELECT key, title, price_kzt, seats, session_limit FROM plans ORDER BY sort") or []
        if not rows:
            rows = [{"key": k, **v} for k, v in tenancy.PLANS.items()]
        for p in rows:
            p["terms"] = {m: tenancy.price_for(p["key"], m) for m in (1, 3, 6, 12)}
        return rows

    def _leads():
        rows = db.query(
            """SELECT l.*,
                      d.result AS demo_result, d.turns AS demo_turns,
                      d.verdict AS demo_verdict, d.answers AS demo_answers,
                      c.title AS company_title, c.status AS company_status,
                      c.sessions_used, c.session_limit AS company_limit,
                      c.expires_at, c.activation_code
                 FROM crm_leads l
                 LEFT JOIN web_demo  d ON d.id = l.demo_id
                 LEFT JOIN companies c ON c.id = l.company_id
                ORDER BY l.updated_at DESC LIMIT 500""") or []
        return rows

    async def h_board(request):
        who(request)
        await _check_pilot_activity()
        leads = await in_thread(_leads)
        username = await bot_username()
        for l in leads:
            if l.get("activation_code"):
                l["activation_link"] = f"https://t.me/{username}?start={l.pop('activation_code')}"
            else:
                l.pop("activation_code", None)
        users = await in_thread(
            lambda: db.query("SELECT telegram_id, name, role, notify FROM crm_users "
                             "WHERE active ORDER BY created_at") or [])
        return jsonify({
            "columns": [{"key": k, "title": t} for k, t in STATUSES],
            "leads": leads,
            "plans": await in_thread(_plans),
            "users": users,
            "invoice_ready": bool(INVOICE_DETAILS),
        })

    # ---------- лиды ----------

    FIELDS = {"name", "contact", "niche", "note", "status", "assignee",
              "plan_key", "months", "tg_id"}

    async def h_lead_create(request):
        got = who(request)
        d = await body(request)
        if not str(d.get("contact") or "").strip() and not str(d.get("name") or "").strip():
            return jsonify({"error": "Нужны хотя бы имя или контакт"}, 400)
        lead = await in_thread(lambda: db.execute(
            """INSERT INTO crm_leads (source, name, contact, niche, note, assignee)
               VALUES ('manual', %s, %s, %s, %s, %s) RETURNING *""",
            (str(d.get("name") or "").strip()[:120],
             str(d.get("contact") or "").strip()[:120],
             str(d.get("niche") or "").strip()[:200],
             str(d.get("note") or "").strip()[:2000],
             got["tg"]),
            returning=True))
        return jsonify(lead)

    async def h_lead_patch(request):
        who(request)
        lid = int(request.match_info["id"])
        d = await body(request)
        sets, vals = [], []
        for k, v in d.items():
            if k not in FIELDS:
                continue
            if k == "status" and v not in STATUS_KEYS:
                return jsonify({"error": "Неизвестный статус"}, 400)
            if k in ("assignee", "tg_id", "months"):
                v = int(v) if v not in (None, "",) else None
                if k == "months":
                    v = v or 1
            else:
                v = str(v if v is not None else "")[:2000]
            sets.append(f"{k}=%s")
            vals.append(v)
        if not sets:
            return jsonify({"error": "Нечего менять"}, 400)
        vals.append(lid)
        lead = await in_thread(lambda: db.execute(
            f"UPDATE crm_leads SET {', '.join(sets)}, updated_at=now() "
            f"WHERE id=%s RETURNING *", tuple(vals), returning=True))
        if not lead:
            return jsonify({"error": "Лид не найден"}, 404)
        return jsonify(lead)

    async def h_lead_grant(request):
        """Выдать пилот: компания + ссылка активации. Как в админке, та же механика."""
        who(request)
        lid = int(request.match_info["id"])
        d = await body(request)
        lead = await in_thread(
            lambda: db.query("SELECT * FROM crm_leads WHERE id=%s", (lid,), one=True))
        if not lead:
            return jsonify({"error": "Лид не найден"}, 404)
        if lead.get("company_id"):
            return jsonify({"error": "Компания уже создана"}, 400)
        title = (d.get("title") or "").strip() or lead["name"] or f"Пилот · {lead['contact']}"
        plan = d.get("plan") or "trial"
        c = await in_thread(tenancy.create_company, title, plan)
        username = await bot_username()
        link = f"https://t.me/{username}?start={c['activation_code']}"
        await in_thread(lambda: db.execute(
            "UPDATE crm_leads SET company_id=%s, status='pilot', plan_key=%s, "
            "updated_at=now() WHERE id=%s", (c["id"], plan, lid)))
        sent = False
        if lead.get("tg_id"):
            try:
                await bot.send_message(
                    int(lead["tg_id"]),
                    f"Мы открыли для вас пилот Aisaty.\n\nНажмите, чтобы подключить "
                    f"компанию:\n{link}")
                sent = True
            except Exception:
                log.exception("CRM: ссылка активации не ушла %s", lead.get("tg_id"))
        return jsonify({"company_id": c["id"], "link": link, "sent": sent})

    async def h_lead_invoice(request):
        """Текст счёта из тарифа. Отправка — руками: оплата пока ручная и так честнее."""
        who(request)
        lid = int(request.match_info["id"])
        d = await body(request)
        plan = d.get("plan_key") or "base"
        months = int(d.get("months") or 1)
        price = await in_thread(tenancy.price_for, plan, months)
        plans = {p["key"]: p for p in await in_thread(_plans)}
        title = plans.get(plan, {}).get("title", plan)
        await in_thread(lambda: db.execute(
            "UPDATE crm_leads SET plan_key=%s, months=%s, updated_at=now() WHERE id=%s",
            (plan, months, lid)))
        text = (f"Счёт на оплату Aisaty\n\n"
                f"Тариф: {title}\n"
                f"Срок: {months} мес.\n"
                f"Сумма: {price:,} ₸".replace(",", " "))
        if INVOICE_DETAILS:
            text += f"\n\nРеквизиты:\n{INVOICE_DETAILS}"
        return jsonify({"text": text, "amount_kzt": price})

    async def h_lead_paid(request):
        """Оплата пришла: продлить компанию и записать поступление. Один раз, здесь."""
        who(request)
        lid = int(request.match_info["id"])
        d = await body(request)
        lead = await in_thread(
            lambda: db.query("SELECT * FROM crm_leads WHERE id=%s", (lid,), one=True))
        if not lead:
            return jsonify({"error": "Лид не найден"}, 404)
        if not lead.get("company_id"):
            return jsonify({"error": "Сначала выдайте доступ — оплату не к чему привязать"}, 400)
        months = int(d.get("months") or lead.get("months") or 1)
        plan = d.get("plan_key") or lead.get("plan_key") or "base"
        amount = d.get("amount_kzt")
        if amount is None:
            amount = await in_thread(tenancy.price_for, plan, months)
        cid = int(lead["company_id"])

        def _apply():
            if plan and plan != "trial":
                try:
                    tenancy.change_plan(cid, plan)
                except Exception:
                    log.exception("CRM: тариф не сменился")
            tenancy.extend_months(cid, months)
            tenancy.record_payment(cid, months, int(amount), plan, f"CRM, лид #{lid}")
            db.execute("UPDATE crm_leads SET status='paid', plan_key=%s, months=%s, "
                       "updated_at=now() WHERE id=%s", (plan, months, lid))
        await in_thread(_apply)
        await _tell(f"💰 Оплата: {lead['name'] or lead['contact']} — "
                    f"{int(amount):,} ₸ за {months} мес.".replace(",", " "))
        return jsonify({"ok": True, "amount_kzt": int(amount)})

    # ---------- команда ----------

    async def h_users(request):
        owner_only(request)
        rows = await in_thread(lambda: db.query(
            "SELECT telegram_id, name, role, notify, active, created_at "
            "FROM crm_users ORDER BY created_at") or [])
        return jsonify(rows)

    async def h_user_add(request):
        owner_only(request)
        d = await body(request)
        raw = str(d.get("tg") or "").strip()
        if not raw.isdigit():
            return jsonify({"error": "Telegram ID — только цифры"}, 400)
        role = "owner" if d.get("role") == "owner" else "manager"
        row = await in_thread(lambda: db.execute(
            """INSERT INTO crm_users (telegram_id, name, role)
               VALUES (%s, %s, %s)
               ON CONFLICT (telegram_id) DO UPDATE
                 SET name=EXCLUDED.name, role=EXCLUDED.role, active=TRUE
               RETURNING *""",
            (int(raw), str(d.get("name") or "").strip()[:80], role), returning=True))
        return jsonify(row)

    async def h_user_remove(request):
        owner_only(request)
        tg = int(request.match_info["tg"])
        await in_thread(lambda: db.execute(
            "UPDATE crm_users SET active=FALSE WHERE telegram_id=%s", (tg,)))
        return jsonify({"ok": True})

    # ---------- выгрузка ----------

    async def h_export(request):
        who(request)
        rows = await in_thread(_leads)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "создан", "имя", "контакт", "ниша", "статус", "источник",
                    "тариф", "мес", "компания", "тренировок", "заметка"])
        for l in rows:
            w.writerow([l["id"], l["created_at"], l["name"], l["contact"], l["niche"],
                        l["status"], l["source"], l.get("plan_key") or "",
                        l.get("months") or "", l.get("company_title") or "",
                        l.get("sessions_used") or "", (l.get("note") or "").replace("\n", " ")])
        return web.Response(
            body=("﻿" + buf.getvalue()).encode("utf-8"),
            content_type="text/csv", charset="utf-8",
            headers={"Content-Disposition": "attachment; filename=aisaty-leads.csv"})

    # ---------- статика ----------

    async def h_index(request):
        path = os.path.join(STATIC_DIR, "index.html")
        if not os.path.exists(path):
            return web.Response(text="CRM ещё не собрана.", content_type="text/plain")
        return web.FileResponse(path)

    r = app.router
    r.add_post("/api/crm/login", h_login)
    r.add_post("/api/crm/verify", h_verify)
    r.add_post("/api/crm/logout", h_logout)
    r.add_get("/api/crm/me", h_me)
    r.add_get("/api/crm/board", h_board)
    r.add_post("/api/crm/leads", h_lead_create)
    r.add_post("/api/crm/leads/{id}", h_lead_patch)
    r.add_post("/api/crm/leads/{id}/grant", h_lead_grant)
    r.add_post("/api/crm/leads/{id}/invoice", h_lead_invoice)
    r.add_post("/api/crm/leads/{id}/paid", h_lead_paid)
    r.add_get("/api/crm/users", h_users)
    r.add_post("/api/crm/users", h_user_add)
    r.add_post("/api/crm/users/{tg}/remove", h_user_remove)
    r.add_get("/api/crm/export", h_export)
    r.add_get("/crm", h_index)
    if os.path.isdir(STATIC_DIR):
        r.add_static("/crm-static/", STATIC_DIR)
    log.info("CRM подключена: /crm и /api/crm/*")
