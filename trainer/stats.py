# -*- coding: utf-8 -*-
"""
Статистика тренажёра на SQLite.

Пишем каждую завершённую тренировку (продажа/провал) и строим отчёт.

ВАЖНО про Railway: файловая система контейнера эфемерна — при каждом
редеплое обнуляется. Чтобы статистика жила между деплоями, примонтируй
Railway Volume и укажи путь к базе в переменной окружения TRAINER_DB_PATH
(например, /data/trainer.db). Без volume статистика всё равно работает,
но сбрасывается при редеплое (как и история диалогов сейчас).
"""

import os
import sqlite3
import datetime
import threading

DB_PATH = os.environ.get("TRAINER_DB_PATH", "trainer.db")
_lock = threading.Lock()


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trainer_sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                ts            TEXT    NOT NULL,
                niche_id      TEXT,
                psychotype_id TEXT,
                status_title  TEXT,
                request       TEXT,
                result        TEXT    NOT NULL,   -- 'won' | 'failed'
                turns         INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts_user ON trainer_sessions(user_id)")


def record_session(user_id, scenario, result, turns):
    """result: 'won' | 'failed'."""
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO trainer_sessions
               (user_id, ts, niche_id, psychotype_id, status_title, request, result, turns)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                user_id, ts,
                scenario.get("niche_id"),
                scenario.get("psychotype_id"),
                scenario.get("status_title"),
                scenario.get("request"),
                result, turns,
            ),
        )


# Русские названия психотипов для отчёта (без импорта, чтобы модуль был автономным)
_PSY_NAMES = {
    "survivor": "🟤 Выживающий",
    "keeper": "🟣 Хранитель",
    "dominant": "🔴 Властный",
    "systematic": "🔵 Системный",
    "achiever": "🟠 Исследователь",
}


def _fmt_report(rows_total, rows_by_psy, title):
    total = sum(r["cnt"] for r in rows_total)
    won = sum(r["cnt"] for r in rows_total if r["result"] == "won")
    failed = total - won
    if total == 0:
        return f"{title}\n\nПока нет ни одной завершённой тренировки. Нажми «🎯 Тренажёр» и начни."

    conv = round(won / total * 100)
    bar_won = "🟩" * round(conv / 10)
    bar_fail = "🟥" * (10 - round(conv / 10))

    lines = [
        title,
        "",
        f"Всего сделок: *{total}*",
        f"✅ Продано: *{won}*",
        f"❌ Провалено: *{failed}*",
        f"📈 Конверсия: *{conv}%*",
        f"{bar_won}{bar_fail}",
    ]

    if rows_by_psy:
        lines.append("")
        lines.append("*По психотипам (продано/всего):*")
        agg = {}
        for r in rows_by_psy:
            pid = r["psychotype_id"] or "?"
            agg.setdefault(pid, {"won": 0, "total": 0})
            agg[pid]["total"] += r["cnt"]
            if r["result"] == "won":
                agg[pid]["won"] += r["cnt"]
        for pid, d in agg.items():
            name = _PSY_NAMES.get(pid, pid)
            c = round(d["won"] / d["total"] * 100) if d["total"] else 0
            lines.append(f"• {name}: {d['won']}/{d['total']} ({c}%)")
    return "\n".join(lines)


def report_for_user(user_id):
    with _lock, _connect() as conn:
        rows_total = conn.execute(
            "SELECT result, COUNT(*) AS cnt FROM trainer_sessions WHERE user_id=? GROUP BY result",
            (user_id,),
        ).fetchall()
        rows_by_psy = conn.execute(
            "SELECT psychotype_id, result, COUNT(*) AS cnt FROM trainer_sessions "
            "WHERE user_id=? GROUP BY psychotype_id, result",
            (user_id,),
        ).fetchall()
    return _fmt_report(rows_total, rows_by_psy, "📊 *Твоя статистика тренажёра*")
