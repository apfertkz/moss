# -*- coding: utf-8 -*-
"""
Статистика тренажёра и учёт расхода.

Было: SQLite-файл, ключ — только user_id. Файл терялся при каждом деплое.
Стало: Postgres, всё в разрезе компании. Любой запрос фильтруется по company_id —
это защита от того, чтобы одна компания увидела цифры другой.
"""

import json
import logging

from . import db, costs

log = logging.getLogger(__name__)

_PSY_NAMES = {
    "survivor": "🟤 Выживающий",
    "keeper": "🟣 Хранитель",
    "dominant": "🔴 Властный",
    "systematic": "🔵 Системный",
    "achiever": "🟠 Исследователь",
}


# --- Запись -----------------------------------------------------------------

def record_session(user, scenario, result, turns, transcript=None, via="bot"):
    """
    Сохранить завершённую тренировку. user — строка из tenancy.get_user().

    via — где тренировались: «bot» или «web». По умолчанию бот, чтобы старые
    вызовы работали как прежде.
    """
    row = db.execute(
        """INSERT INTO sessions
           (company_id, user_id, telegram_id, psychotype_id, status_title, request, result, turns, via)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (user["company_id"], user["id"], user["telegram_id"],
         scenario.get("psychotype_id"), scenario.get("status_title"),
         scenario.get("request"), result, turns, via),
        returning=True,
    )
    session_id = row["id"]

    if transcript:
        with db.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO messages (session_id, company_id, role, text)
                       VALUES (%s,%s,%s,%s)""",
                    [(session_id, user["company_id"], role, text) for role, text in transcript],
                )
    return session_id


def record_usage(company_id, telegram_id, kind, model, usage):
    """Записать фактический расход одного вызова модели."""
    if not usage:
        return
    db.execute(
        """INSERT INTO usage_log
           (company_id, telegram_id, kind, model, input_tokens, output_tokens,
            cache_write, cache_read, cost_usd)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (company_id, telegram_id, kind, model,
         usage["input_tokens"], usage["output_tokens"],
         usage["cache_write"], usage["cache_read"], costs.cost_usd(model, usage)),
    )


# --- Отчёты -----------------------------------------------------------------

def _bar(conv):
    filled = round(conv / 10)
    return "🟩" * filled + "🟥" * (10 - filled)


def report_for_user(user):
    """Личная статистика менеджера."""
    cid, uid = user["company_id"], user["id"]
    row = db.query(
        """SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE result='won') AS won
           FROM sessions WHERE company_id=%s AND user_id=%s""",
        (cid, uid), one=True,
    )
    total, won = row["total"], row["won"]
    if not total:
        return "📊 *Твоя статистика*\n\nПока нет ни одной завершённой тренировки. Жми «🎯 Новый клиент»."

    conv = round(won / total * 100)
    lines = [
        "📊 *Твоя статистика*", "",
        f"Всего сделок: *{total}*",
        f"✅ Продано: *{won}*",
        f"❌ Провалено: *{total - won}*",
        f"📈 Конверсия: *{conv}%*",
        _bar(conv),
    ]

    by_psy = db.query(
        """SELECT psychotype_id, COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE result='won') AS won
           FROM sessions WHERE company_id=%s AND user_id=%s
           GROUP BY psychotype_id ORDER BY total DESC""",
        (cid, uid),
    )
    if by_psy:
        lines += ["", "*По психотипам (продано/всего):*"]
        for r in by_psy:
            name = _PSY_NAMES.get(r["psychotype_id"], r["psychotype_id"] or "?")
            c = round(r["won"] / r["total"] * 100) if r["total"] else 0
            lines.append(f"• {name}: {r['won']}/{r['total']} ({c}%)")

    weak = min(by_psy, key=lambda r: r["won"] / r["total"]) if by_psy else None
    if weak and weak["total"] >= 3 and weak["won"] / weak["total"] < 0.5:
        lines += ["", f"_Слабое место: {_PSY_NAMES.get(weak['psychotype_id'], '')}. "
                      f"Потренируйся на нём._"]
    return "\n".join(lines)


def report_for_company(company_id):
    """Сводка по отделу — то, ради чего владелец платит."""
    row = db.query(
        """SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE result='won') AS won,
                  COUNT(DISTINCT user_id) AS active_users
           FROM sessions WHERE company_id=%s""",
        (company_id,), one=True,
    )
    total, won = row["total"], row["won"]
    if not total:
        return ("📊 *Сводка по отделу*\n\nПока никто не тренировался.\n"
                "Отправьте менеджерам ссылку-приглашение — команда /invite.")

    conv = round(won / total * 100)
    lines = [
        "📊 *Сводка по отделу*", "",
        f"Тренировок: *{total}*",
        f"Средняя конверсия: *{conv}%*",
        _bar(conv), "",
        "*По менеджерам:*",
    ]

    from . import tenancy
    for m in tenancy.team(company_id):
        name = m["full_name"] or (f"@{m['username']}" if m["username"] else str(m["telegram_id"]))
        if not m["total"]:
            lines.append(f"• {name} — _ни одной тренировки_")
            continue
        c = round(m["won"] / m["total"] * 100)
        mark = "🥇" if c >= 70 else ("⚠️" if c < 40 else "•")
        lines.append(f"{mark} {name}: {m['won']}/{m['total']} ({c}%)")

    idle = [m for m in tenancy.team(company_id) if not m["total"]]
    if idle:
        lines += ["", f"_Не заходили: {len(idle)}. Это первый признак будущего оттока — "
                      f"напомните им лично._"]
    return "\n".join(lines)


def company_spend(company_id, days=30):
    """Сколько компания стоила нам в деньгах за период. Для внутреннего контроля маржи."""
    row = db.query(
        """SELECT COALESCE(SUM(cost_usd),0) AS usd, COUNT(*) AS calls,
                  COALESCE(SUM(input_tokens+cache_read+cache_write),0) AS tokens_in,
                  COALESCE(SUM(output_tokens),0) AS tokens_out
           FROM usage_log
           WHERE company_id=%s AND created_at > now() - (%s || ' days')::interval""",
        (company_id, str(days)), one=True,
    )
    return row


def export_rows(company_id):
    """Плоская выгрузка тренировок компании — для /export."""
    return db.query(
        """SELECT s.finished_at, u.full_name, u.username, s.telegram_id,
                  s.status_title, s.psychotype_id, s.result, s.turns
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.company_id=%s ORDER BY s.finished_at DESC LIMIT 1000""",
        (company_id,),
    )
