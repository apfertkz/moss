# -*- coding: utf-8 -*-
"""
Данные для админ-панели: чтение сводок и списков.

Отдельно от веб-слоя намеренно. Здесь только запросы и подсчёты, без знания
о HTTP: то же самое понадобится команде бота, ночному отчёту и выгрузке.
Веб-слой остаётся тонким — маршрут, проверка входа, вызов отсюда.

Курс тенге держим одной константой: считать маржу в долларах при том, что
тарифы в тенге, бессмысленно.
"""

import datetime
import logging
import os

from . import db, tenancy, demo

log = logging.getLogger(__name__)

USD_KZT = float(os.environ.get("USD_KZT", "540"))


def _kzt(usd):
    return round(float(usd or 0) * USD_KZT)


# --- Обзор ------------------------------------------------------------------

def overview():
    """
    Первый экран панели. Всё, что нужно знать за десять секунд:
    сколько денег, сколько живых клиентов, что горит.
    """
    companies = db.query(
        """SELECT status, plan, COUNT(*) AS n FROM companies
            WHERE activation_code <> %s GROUP BY status, plan""",
        (demo.ACTIVATION_CODE,),
    ) or []

    by_status = {}
    for row in companies:
        by_status[row["status"]] = by_status.get(row["status"], 0) + row["n"]

    # Выручку берём из платежей, а не из прайса: иначе индивидуальные
    # договорённости и длинные сроки показывали бы неправду.
    revenue = mrr()

    sessions = db.query(
        """SELECT COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE result='won') AS won,
                  COUNT(*) FILTER (WHERE finished_at > now() - interval '7 days') AS week
             FROM sessions""", one=True,
    ) or {}

    spend = db.query(
        """SELECT COALESCE(SUM(cost_usd),0) AS usd
             FROM usage_log WHERE created_at > now() - interval '30 days'""", one=True,
    ) or {}

    total = int(sessions.get("total") or 0)
    won = int(sessions.get("won") or 0)

    return {
        "companies": {
            "active": by_status.get(tenancy.STATUS_ACTIVE, 0),
            "pending": by_status.get(tenancy.STATUS_PENDING_SETUP, 0),
            "suspended": by_status.get(tenancy.STATUS_SUSPENDED, 0),
            "total": sum(by_status.values()),
        },
        "revenue_kzt": revenue,
        "spend_kzt": _kzt(spend.get("usd")),
        "margin_kzt": revenue - _kzt(spend.get("usd")),
        "sessions": {
            "total": total,
            "week": int(sessions.get("week") or 0),
            "conversion": round(won / total * 100) if total else 0,
        },
        "attention": attention(),
    }


def attention():
    """
    Клиенты, требующие действия. Три сценария, из которых складывается
    почти весь отток: кончается подписка, застряли на брифе, отдел не
    тренируется. Каждый чинится одним звонком — если о нём знать.
    """
    expiring = db.query(
        """SELECT id, title, plan, expires_at FROM companies
            WHERE status <> %s AND expires_at IS NOT NULL
              AND expires_at BETWEEN now() AND now() + interval '7 days'
         ORDER BY expires_at LIMIT 20""",
        (tenancy.STATUS_SUSPENDED,),
    ) or []

    stuck = db.query(
        """SELECT id, title, created_at FROM companies
            WHERE status = %s AND created_at < now() - interval '1 day'
         ORDER BY created_at LIMIT 20""",
        (tenancy.STATUS_PENDING_SETUP,),
    ) or []

    idle = db.query(
        """SELECT c.id, c.title,
                  MAX(s.finished_at) AS last_session
             FROM companies c
             LEFT JOIN sessions s ON s.company_id = c.id
            WHERE c.status = %s AND c.activation_code <> %s
         GROUP BY c.id, c.title
           HAVING MAX(s.finished_at) IS NULL
               OR MAX(s.finished_at) < now() - interval '7 days'
         ORDER BY last_session NULLS FIRST LIMIT 20""",
        (tenancy.STATUS_ACTIVE, demo.ACTIVATION_CODE),
    ) or []

    return {
        "expiring": [dict(r, days_left=tenancy.days_left(r)) for r in expiring],
        "stuck": [dict(r) for r in stuck],
        "idle": [dict(r) for r in idle],
    }


# --- Клиенты ----------------------------------------------------------------

def companies(status=None, q=None, limit=200):
    """Список компаний с показателями. Демо-компания сюда не попадает."""
    where = ["c.activation_code <> %s"]
    params = [demo.ACTIVATION_CODE]

    if status and status != "all":
        where.append("c.status = %s")
        params.append(status)
    if q:
        where.append("(c.title ILIKE %s OR c.contact_email ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]

    params.append(limit)
    rows = db.query(
        f"""SELECT c.*,
                   (SELECT COUNT(*) FROM users u WHERE u.company_id=c.id AND u.active) AS seats_taken,
                   (SELECT COUNT(*) FROM sessions s WHERE s.company_id=c.id) AS sessions_total,
                   (SELECT COUNT(*) FROM sessions s WHERE s.company_id=c.id AND s.result='won') AS sessions_won,
                   (SELECT COALESCE(SUM(cost_usd),0) FROM usage_log g WHERE g.company_id=c.id) AS spend_usd
              FROM companies c
             WHERE {' AND '.join(where)}
          ORDER BY c.created_at DESC LIMIT %s""",
        tuple(params),
    ) or []
    return [_company_row(r) for r in rows]


def _company_row(r):
    total = int(r.get("sessions_total") or 0)
    won = int(r.get("sessions_won") or 0)
    plan = tenancy.PLANS.get(r["plan"], {})
    return {
        "id": r["id"],
        "title": r["title"],
        "plan": r["plan"],
        "plan_title": plan.get("title", r["plan"]),
        "price_kzt": plan.get("price_kzt", 0),
        "status": r["status"],
        "seats": r["seats"],
        "seats_taken": int(r.get("seats_taken") or 0),
        "session_limit": r["session_limit"],
        "sessions_used": r["sessions_used"],
        "sessions_total": total,
        "conversion": round(won / total * 100) if total else None,
        "spend_kzt": _kzt(r.get("spend_usd")),
        "expires_at": r["expires_at"],
        "days_left": tenancy.days_left(r),
        "created_at": r["created_at"],
        "activation_code": r["activation_code"],
        "invite_code": r["invite_code"],
        "contact_email": r.get("contact_email"),
        "health": health(r, total),
    }


def health(company, sessions_total):
    """
    Простой индекс риска: используется ли то, за что заплачено.

    Не наука, а сигнал. Компания, где за месяц не провели ни одной
    тренировки, не продлится — и об этом надо знать за неделю, а не
    в день окончания.
    """
    if company["status"] == tenancy.STATUS_SUSPENDED:
        return "suspended"
    if company["status"] == tenancy.STATUS_PENDING_SETUP:
        return "setup"
    if not sessions_total:
        return "cold"

    used = int(company.get("sessions_used") or 0)
    limit = max(1, int(company.get("session_limit") or 1))
    share = used / limit
    if share < 0.1:
        return "cold"
    if share < 0.4:
        return "warm"
    return "hot"


def company(company_id):
    """Карточка компании: показатели, сотрудники, расход, история."""
    rows = db.query(
        """SELECT c.*,
                  (SELECT COUNT(*) FROM users u WHERE u.company_id=c.id AND u.active) AS seats_taken,
                  (SELECT COUNT(*) FROM sessions s WHERE s.company_id=c.id) AS sessions_total,
                  (SELECT COUNT(*) FROM sessions s WHERE s.company_id=c.id AND s.result='won') AS sessions_won,
                  (SELECT COALESCE(SUM(cost_usd),0) FROM usage_log g WHERE g.company_id=c.id) AS spend_usd
             FROM companies c WHERE c.id=%s""",
        (company_id,), one=True,
    )
    if not rows:
        return None

    card = _company_row(rows)
    card["team"] = [dict(t) for t in (tenancy.team(company_id) or [])]
    card["history"] = [dict(h) for h in (tenancy.history(company_id, 30) or [])]
    card["profile"] = _profile_brief(company_id)
    card["payments"] = [dict(p) for p in tenancy.payments_of(company_id)]
    card["prices"] = tenancy.PRICES.get(card["plan"]) or {}
    return card


def _profile_brief(company_id):
    row = db.query(
        """SELECT profile, created_at FROM niche_profiles
            WHERE company_id=%s AND is_active LIMIT 1""",
        (company_id,), one=True,
    )
    if not row:
        return None
    p = row["profile"]
    if isinstance(p, str):
        import json
        p = json.loads(p)
    return {
        "title": p.get("title"),
        "statuses": len(p.get("statuses") or []),
        "requests": len(p.get("requests") or []),
        "created_at": row["created_at"],
    }


# --- Пользователи -----------------------------------------------------------

def users(q=None, company_id=None, limit=200):
    where, params = [], []
    if q:
        where.append("(u.full_name ILIKE %s OR u.username ILIKE %s OR CAST(u.telegram_id AS TEXT) LIKE %s)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if company_id:
        where.append("u.company_id = %s")
        params.append(company_id)
    params.append(limit)

    rows = db.query(
        f"""SELECT u.*, c.title AS company_title, c.status AS company_status,
                   COUNT(s.id) AS sessions,
                   COUNT(*) FILTER (WHERE s.result='won') AS won
              FROM users u
              JOIN companies c ON c.id = u.company_id
              LEFT JOIN sessions s ON s.user_id = u.id
             {('WHERE ' + ' AND '.join(where)) if where else ''}
          GROUP BY u.id, c.title, c.status
          ORDER BY u.joined_at DESC LIMIT %s""",
        tuple(params),
    ) or []

    out = []
    for r in rows:
        total = int(r.get("sessions") or 0)
        won = int(r.get("won") or 0)
        out.append({
            "telegram_id": r["telegram_id"],
            "full_name": r.get("full_name"),
            "username": r.get("username"),
            "role": r["role"],
            "active": r["active"],
            "company_id": r["company_id"],
            "company_title": r["company_title"],
            "sessions": total,
            "conversion": round(won / total * 100) if total else None,
            "last_seen_at": r.get("last_seen_at"),
            "joined_at": r.get("joined_at"),
        })
    return out


def last_session(telegram_id):
    """Последняя тренировка целиком — для разбора претензий клиента."""
    s = db.query(
        """SELECT * FROM sessions WHERE telegram_id=%s
        ORDER BY finished_at DESC LIMIT 1""",
        (telegram_id,), one=True,
    )
    if not s:
        return None
    msgs = db.query(
        "SELECT role, text, created_at FROM messages WHERE session_id=%s ORDER BY id",
        (s["id"],),
    ) or []
    return {"session": dict(s), "messages": [dict(m) for m in msgs]}


# --- Демо -------------------------------------------------------------------

def demo_queue(limit=100):
    """Гости, попробовавшие демо. Единственный источник тёплых лидов."""
    try:
        cid = demo.company_id()
    except Exception:
        return []
    rows = db.query(
        """SELECT u.telegram_id, u.full_name, u.username, u.joined_at,
                  COUNT(s.id) AS sessions,
                  COUNT(*) FILTER (WHERE s.result='won') AS won,
                  MAX(s.finished_at) AS last_at
             FROM users u
             LEFT JOIN sessions s ON s.user_id = u.id
            WHERE u.company_id = %s
         GROUP BY u.telegram_id, u.full_name, u.username, u.joined_at
         ORDER BY u.joined_at DESC LIMIT %s""",
        (cid, limit),
    ) or []
    return [{
        "telegram_id": r["telegram_id"],
        "full_name": r.get("full_name"),
        "username": r.get("username"),
        "sessions": int(r.get("sessions") or 0),
        "won": int(r.get("won") or 0),
        "joined_at": r.get("joined_at"),
        "last_at": r.get("last_at"),
    } for r in rows]


# --- Деньги -----------------------------------------------------------------

def mrr():
    """
    Выручка в пересчёте на месяц.

    Годовой платёж делится на двенадцать: без этого месяц с удачной
    предоплатой выглядит как рост, а следующий — как обвал.
    Считаем только по действующим подпискам.
    """
    row = db.query(
        """SELECT COALESCE(SUM(p.amount_kzt::numeric / GREATEST(p.months,1)), 0) AS v
             FROM payments p
             JOIN companies c ON c.id = p.company_id
            WHERE c.status = %s
              AND p.created_at = (SELECT MAX(created_at) FROM payments x
                                   WHERE x.company_id = p.company_id)""",
        (tenancy.STATUS_ACTIVE,), one=True,
    ) or {}
    return round(float(row.get("v") or 0))


def income(days=30):
    """Сколько реально пришло за период — с пиками в месяцы продлений."""
    row = db.query(
        """SELECT COALESCE(SUM(amount_kzt),0) AS v, COUNT(*) AS n
             FROM payments WHERE created_at > now() - (%s || ' days')::interval""",
        (str(days),), one=True,
    ) or {}
    return {"amount_kzt": int(row.get("v") or 0), "count": int(row.get("n") or 0)}


def web_leads(limit=100):
    """
    Заявки с демо на сайте. Даже без контакта строка ценна: видно, какие
    ниши приходят и на каком шаге люди отваливаются.
    """
    rows = db.query(
        """SELECT id, niche, turns, result, contact, contact_name, verdict,
                  cost_usd, created_at, finished_at
             FROM web_demo ORDER BY created_at DESC LIMIT %s""",
        (limit,),
    ) or []
    return [{
        "id": r["id"],
        "niche": r.get("niche"),
        "turns": int(r.get("turns") or 0),
        "result": r.get("result"),
        "contact": r.get("contact"),
        "name": r.get("contact_name"),
        "verdict": r.get("verdict"),
        "cost_kzt": _kzt(r.get("cost_usd")),
        "created_at": r.get("created_at"),
        "finished": bool(r.get("finished_at")),
    } for r in rows]


def web_demo_stats(days=30):
    """Сводка по демо на сайте: сколько запусков, доходов до конца, заявок."""
    row = db.query(
        """SELECT COUNT(*) AS started,
                  COUNT(*) FILTER (WHERE finished_at IS NOT NULL) AS finished,
                  COUNT(*) FILTER (WHERE contact IS NOT NULL AND contact <> '') AS leads,
                  COALESCE(SUM(cost_usd), 0) AS spend
             FROM web_demo
            WHERE created_at > now() - (%s || ' days')::interval""",
        (str(days),), one=True,
    ) or {}
    today = db.query(
        """SELECT COALESCE(SUM(cost_usd), 0) AS s FROM web_demo
            WHERE created_at > date_trunc('day', now())""", one=True) or {}
    started = int(row.get("started") or 0)
    return {
        "started": started,
        "finished": int(row.get("finished") or 0),
        "leads": int(row.get("leads") or 0),
        "spend_kzt": _kzt(row.get("spend")),
        "today_kzt": _kzt(today.get("s")),
        "to_end": round(int(row.get("finished") or 0) / started * 100) if started else 0,
    }


def money(days=30):
    """
    Расход и маржа по каждому клиенту. Здесь становится видно,
    не убыточен ли конкретный тариф.
    """
    rows = db.query(
        """SELECT c.id, c.title, c.plan, c.status,
                  COALESCE(SUM(g.cost_usd),0) AS usd,
                  COUNT(DISTINCT s.id) AS sessions
             FROM companies c
             LEFT JOIN usage_log g ON g.company_id=c.id
                   AND g.created_at > now() - (%s || ' days')::interval
             LEFT JOIN sessions s ON s.company_id=c.id
                   AND s.finished_at > now() - (%s || ' days')::interval
         GROUP BY c.id, c.title, c.plan, c.status
         ORDER BY usd DESC""",
        (str(days), str(days)),
    ) or []

    # Последний платёж по каждой компании: он показывает, за что клиент
    # платит на самом деле, включая индивидуальные договорённости.
    paid = {}
    for r in (db.query(
        """SELECT DISTINCT ON (company_id) company_id, amount_kzt, months
             FROM payments ORDER BY company_id, created_at DESC""") or []):
        paid[r["company_id"]] = (int(r["amount_kzt"]), max(1, int(r["months"])))

    out = []
    for r in rows:
        spend = _kzt(r["usd"])
        if r["id"] in paid:
            amount, months = paid[r["id"]]
            price = round(amount / months)
        else:
            price = tenancy.PLANS.get(r["plan"], {}).get("price_kzt", 0)
        revenue = price if r["status"] == tenancy.STATUS_ACTIVE else 0
        sessions = int(r.get("sessions") or 0)
        out.append({
            "id": r["id"],
            "title": r["title"],
            "plan": r["plan"],
            "revenue_kzt": revenue,
            "spend_kzt": spend,
            "margin_kzt": revenue - spend,
            "sessions": sessions,
            "per_session_kzt": round(spend / sessions) if sessions else None,
        })

    by_model = db.query(
        """SELECT model, COALESCE(SUM(cost_usd),0) AS usd, COUNT(*) AS calls
             FROM usage_log WHERE created_at > now() - (%s || ' days')::interval
         GROUP BY model ORDER BY usd DESC""",
        (str(days),),
    ) or []

    return {
        "companies": out,
        "by_model": [{"model": r["model"], "spend_kzt": _kzt(r["usd"]),
                      "calls": int(r["calls"])} for r in by_model],
        "total_spend_kzt": sum(x["spend_kzt"] for x in out),
        "total_revenue_kzt": sum(x["revenue_kzt"] for x in out),
        "mrr_kzt": mrr(),
        "income": income(days),
    }


# --- Отчёты -----------------------------------------------------------------

def summary(days=30):
    """Сводка за период — то, что уходит в ночной отчёт и в выгрузку."""
    new = db.query(
        """SELECT COUNT(*) AS n FROM companies
            WHERE created_at > now() - (%s || ' days')::interval
              AND activation_code <> %s""",
        (str(days), demo.ACTIVATION_CODE), one=True,
    ) or {}
    sess = db.query(
        """SELECT COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE result='won') AS won,
                  COUNT(DISTINCT telegram_id) AS people
             FROM sessions WHERE finished_at > now() - (%s || ' days')::interval""",
        (str(days),), one=True,
    ) or {}
    total = int(sess.get("total") or 0)
    won = int(sess.get("won") or 0)

    daily = db.query(
        """SELECT date_trunc('day', finished_at) AS day,
                  COUNT(*) AS n,
                  COUNT(*) FILTER (WHERE result='won') AS won
             FROM sessions WHERE finished_at > now() - (%s || ' days')::interval
         GROUP BY 1 ORDER BY 1""",
        (str(days),),
    ) or []

    return {
        "days": days,
        "new_companies": int(new.get("n") or 0),
        "sessions": total,
        "people": int(sess.get("people") or 0),
        "conversion": round(won / total * 100) if total else 0,
        "daily": [{"day": r["day"], "n": int(r["n"]), "won": int(r["won"])} for r in daily],
    }


def company_summary(company_id, days=30):
    """
    То же, что summary, но в границах одной компании.

    Отдельная функция, а не параметр к общей: руководителю нельзя показывать
    ни новых клиентов продукта, ни чужие тренировки, и лучше, чтобы это
    решалось запросом, а не фильтром на стороне браузера.
    """
    sess = db.query(
        """SELECT COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE result='won') AS won,
                  COUNT(DISTINCT telegram_id) AS people
             FROM sessions
            WHERE company_id=%s
              AND finished_at > now() - (%s || ' days')::interval""",
        (company_id, str(days)), one=True,
    ) or {}
    total = int(sess.get("total") or 0)
    won = int(sess.get("won") or 0)

    daily = db.query(
        """SELECT date_trunc('day', finished_at) AS day,
                  COUNT(*) AS n,
                  COUNT(*) FILTER (WHERE result='won') AS won
             FROM sessions
            WHERE company_id=%s
              AND finished_at > now() - (%s || ' days')::interval
         GROUP BY 1 ORDER BY 1""",
        (company_id, str(days)),
    ) or []

    best = db.query(
        """SELECT u.full_name, u.username, u.telegram_id,
                  COUNT(s.id) AS n,
                  COUNT(*) FILTER (WHERE s.result='won') AS won
             FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.company_id=%s
              AND s.finished_at > now() - (%s || ' days')::interval
         GROUP BY u.id ORDER BY n DESC LIMIT 20""",
        (company_id, str(days)),
    ) or []

    return {
        "days": days,
        "sessions": total,
        "people": int(sess.get("people") or 0),
        "conversion": round(won / total * 100) if total else 0,
        "daily": [{"day": r["day"], "n": int(r["n"]), "won": int(r["won"])} for r in daily],
        "team": [{
            "telegram_id": r["telegram_id"],
            "full_name": r.get("full_name"),
            "username": r.get("username"),
            "sessions": int(r["n"]),
            "conversion": round(int(r["won"]) / int(r["n"]) * 100) if r["n"] else 0,
        } for r in best],
    }


def psychotypes(company_id=None, days=90):
    """Разбивка по типам клиентов: где отдел ломается."""
    where = ["finished_at > now() - (%s || ' days')::interval"]
    params = [str(days)]
    if company_id:
        where.append("company_id = %s")
        params.append(company_id)
    rows = db.query(
        f"""SELECT psychotype_id, status_title,
                   COUNT(*) AS n, COUNT(*) FILTER (WHERE result='won') AS won
              FROM sessions WHERE {' AND '.join(where)}
          GROUP BY psychotype_id, status_title ORDER BY n DESC""",
        tuple(params),
    ) or []
    return [{
        "psychotype": r.get("psychotype_id"),
        "status": r.get("status_title"),
        "n": int(r["n"]),
        "conversion": round(int(r["won"]) / int(r["n"]) * 100) if r["n"] else 0,
    } for r in rows]


# --- Рассылка ---------------------------------------------------------------

SEGMENTS = {
    "owners": "Владельцы компаний",
    "managers": "Менеджеры",
    "all": "Все пользователи",
    "expiring": "У кого истекает подписка",
    "idle": "Не заходили 7 дней",
    "demo": "Гости после демо",
}


def segment(name, company_id=None):
    """Кому уйдёт рассылка. Возвращает список telegram_id."""
    if company_id:
        # Внутри компании сегменты те же, но список никогда не выходит
        # за её границы: это единственный способ рассылки для руководителя.
        if name == "idle":
            rows = db.query(
                """SELECT telegram_id FROM users
                    WHERE company_id=%s AND active
                      AND (last_seen_at IS NULL
                           OR last_seen_at < now() - interval '7 days')""",
                (company_id,))
        elif name in (tenancy.ROLE_OWNER, tenancy.ROLE_MANAGER, "owners", "managers"):
            role = tenancy.ROLE_OWNER if name in (tenancy.ROLE_OWNER, "owners") \
                else tenancy.ROLE_MANAGER
            rows = db.query(
                "SELECT telegram_id FROM users WHERE company_id=%s AND active AND role=%s",
                (company_id, role))
        else:
            rows = db.query(
                "SELECT telegram_id FROM users WHERE company_id=%s AND active", (company_id,))
    elif name == "owners":
        rows = db.query(
            """SELECT u.telegram_id FROM users u JOIN companies c ON c.id=u.company_id
                WHERE u.role=%s AND u.active AND c.activation_code <> %s""",
            (tenancy.ROLE_OWNER, demo.ACTIVATION_CODE))
    elif name == "managers":
        rows = db.query(
            """SELECT u.telegram_id FROM users u JOIN companies c ON c.id=u.company_id
                WHERE u.role=%s AND u.active AND c.activation_code <> %s""",
            (tenancy.ROLE_MANAGER, demo.ACTIVATION_CODE))
    elif name == "expiring":
        rows = db.query(
            """SELECT u.telegram_id FROM users u JOIN companies c ON c.id=u.company_id
                WHERE u.role=%s AND u.active AND c.expires_at IS NOT NULL
                  AND c.expires_at BETWEEN now() AND now() + interval '14 days'""",
            (tenancy.ROLE_OWNER,))
    elif name == "idle":
        rows = db.query(
            """SELECT u.telegram_id FROM users u JOIN companies c ON c.id=u.company_id
                WHERE u.active AND c.activation_code <> %s
                  AND (u.last_seen_at IS NULL OR u.last_seen_at < now() - interval '7 days')""",
            (demo.ACTIVATION_CODE,))
    elif name == "demo":
        try:
            rows = db.query("SELECT telegram_id FROM users WHERE company_id=%s",
                            (demo.company_id(),))
        except Exception:
            rows = []
    else:  # all
        rows = db.query(
            """SELECT u.telegram_id FROM users u JOIN companies c ON c.id=u.company_id
                WHERE u.active AND c.activation_code <> %s""",
            (demo.ACTIVATION_CODE,))
    return [r["telegram_id"] for r in (rows or [])]


# --- Выгрузки ---------------------------------------------------------------
#
# CSV, а не Excel: открывается всем, включая Google Таблицы, и не требует
# лишней зависимости. Разделитель — точка с запятой: русский Excel иначе
# сваливает всю строку в одну ячейку.

def _csv(headers, rows):
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    # BOM — чтобы Excel понял, что это UTF-8, а не крякозябры.
    return "﻿" + buf.getvalue()


def export_companies():
    rows = companies(limit=10000)
    return _csv(
        ["Клиент", "Тариф", "Статус", "Мест занято", "Мест всего",
         "Тренировок", "Лимит", "Конверсия, %", "Расход, ₸", "Оплачено до", "Подключён"],
        [[c["title"], c["plan_title"], c["status"], c["seats_taken"], c["seats"],
          c["sessions_used"], c["session_limit"], c["conversion"] if c["conversion"] is not None else "",
          c["spend_kzt"], (c["expires_at"] or "")[:10] if isinstance(c["expires_at"], str) else c["expires_at"],
          str(c["created_at"])[:10]] for c in rows],
    )


def export_money(days=30):
    d = money(days)
    return _csv(
        ["Клиент", "Тариф", "Выручка, ₸", "Расход, ₸", "Маржа, ₸",
         "Тренировок", "Себестоимость тренировки, ₸"],
        [[c["title"], c["plan"], c["revenue_kzt"], c["spend_kzt"], c["margin_kzt"],
          c["sessions"], c["per_session_kzt"] if c["per_session_kzt"] is not None else ""]
         for c in d["companies"]],
    )


def export_sessions(company_id=None, days=90):
    where = ["s.finished_at > now() - (%s || ' days')::interval"]
    params = [str(days)]
    if company_id:
        where.append("s.company_id = %s")
        params.append(company_id)

    rows = db.query(
        f"""SELECT s.finished_at, c.title AS company, u.full_name, u.username,
                   s.status_title, s.psychotype_id, s.result, s.turns
              FROM sessions s
              JOIN companies c ON c.id = s.company_id
              LEFT JOIN users u ON u.id = s.user_id
             WHERE {' AND '.join(where)}
          ORDER BY s.finished_at DESC LIMIT 20000""",
        tuple(params),
    ) or []

    return _csv(
        ["Дата", "Клиент", "Менеджер", "Тип покупателя", "Психотип", "Итог", "Ходов"],
        [[str(r["finished_at"])[:16], r["company"],
          r.get("full_name") or r.get("username") or "",
          r.get("status_title") or "", r.get("psychotype_id") or "",
          "закрыта" if r["result"] == "won" else "провалена", r["turns"]] for r in rows],
    )


def client_report(company_id):
    """
    Отчёт для самого клиента: кто как продаёт и на ком ломается.
    Это главный аргумент при продлении, поэтому текст написан так,
    чтобы его можно было переслать директору без правок.
    """
    c = company(company_id)
    if not c:
        return None

    lines = [
        f"# Отдел продаж: {c['title']}",
        "",
        f"Период: последние 90 дней. Тариф «{c['plan_title']}».",
        "",
        "## Итог",
        "",
        f"- Тренировок проведено: {c['sessions_total']}",
        f"- Сделок закрыто: {c['conversion'] if c['conversion'] is not None else 0}% от всех тренировок",
        f"- Менеджеров подключено: {c['seats_taken']} из {c['seats']}",
        "",
        "## По менеджерам",
        "",
    ]

    team = sorted(c.get("team") or [], key=lambda m: -(m.get("total") or 0))
    if not team:
        lines.append("Менеджеры ещё не подключены.")
    for m in team:
        total = m.get("total") or 0
        conv = round((m.get("won") or 0) / total * 100) if total else 0
        name = m.get("full_name") or m.get("username") or m["telegram_id"]
        mark = "—" if not total else ("сильно" if conv >= 50 else "средне" if conv >= 30 else "слабо")
        lines.append(f"- **{name}**: {total} тренировок, конверсия {conv}% ({mark})")

    types = psychotypes(company_id)
    if types:
        lines += ["", "## С кем справляются хуже всего", ""]
        for t in sorted(types, key=lambda x: x["conversion"])[:5]:
            lines.append(f"- {t['status'] or t['psychotype']}: конверсия {t['conversion']}% "
                         f"из {t['n']} тренировок")

    lines += [
        "",
        "## Что с этим делать",
        "",
        "Смотрите на тип покупателя с самой низкой конверсией: именно там отдел",
        "теряет деньги на живых заявках. Разберите с менеджерами один такой",
        "диалог целиком — что спросили, где назвали цену, чем закрыли.",
    ]
    return "\n".join(lines)
