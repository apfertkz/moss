# -*- coding: utf-8 -*-
"""
Хранение активных тренировок.

Раньше диалоги жили в словаре в памяти процесса. Любой деплой на Railway —
а он случается при каждом коммите — обрывал их молча: менеджер продолжал
писать клиенту, а бот уже не помнил ни сценария, ни стенограммы и отвечал
как на первое сообщение. Со стороны это выглядело как поломка.

Теперь сессия живёт в базе, а память работает как кэш: читаем оттуда, если
есть, иначе поднимаем из базы. Пишем после каждого хода — тренировка это
десяток ходов, накладные расходы на запись несопоставимы со стоимостью
одного обращения к модели.
"""

import json
import logging

from . import db

log = logging.getLogger(__name__)

# telegram_id -> сессия. Кэш поверх базы, а не источник правды.
_cache = {}


def get(telegram_id):
    """Активная тренировка или None."""
    if telegram_id in _cache:
        return _cache[telegram_id]

    row = db.query("SELECT data FROM active_sessions WHERE telegram_id=%s",
                   (telegram_id,), one=True)
    if not row:
        return None

    data = row["data"]
    if isinstance(data, str):
        data = json.loads(data)
    _cache[telegram_id] = data
    log.info("Тренировка %s поднята из базы после перезапуска", telegram_id)
    return data


def put(telegram_id, company_id, session):
    """Сохранить состояние. Вызывается после каждого хода."""
    _cache[telegram_id] = session
    try:
        db.execute(
            """INSERT INTO active_sessions (telegram_id, company_id, data, updated_at)
                    VALUES (%s,%s,%s, now())
               ON CONFLICT (telegram_id)
                 DO UPDATE SET data = EXCLUDED.data,
                               company_id = EXCLUDED.company_id,
                               updated_at = now()""",
            (telegram_id, company_id, json.dumps(session, ensure_ascii=False)),
        )
    except Exception:
        # Не роняем тренировку из-за сбоя записи: в памяти она осталась,
        # потеряется только при перезапуске.
        log.exception("Не удалось сохранить тренировку %s", telegram_id)


def drop(telegram_id):
    """Убрать тренировку — завершена или брошена."""
    was = _cache.pop(telegram_id, None)
    try:
        db.execute("DELETE FROM active_sessions WHERE telegram_id=%s", (telegram_id,))
    except Exception:
        log.exception("Не удалось удалить тренировку %s", telegram_id)
    return was


def active(telegram_id):
    return get(telegram_id) is not None


def cleanup(hours=24):
    """
    Убрать брошенные тренировки. Человек начал, отвлёкся и не вернулся —
    через сутки такая сессия только мешает: он ждёт нового клиента,
    а попадает в старый разговор.
    """
    rows = db.query(
        """DELETE FROM active_sessions
            WHERE updated_at < now() - (%s || ' hours')::interval
        RETURNING telegram_id""",
        (str(hours),),
    )
    for r in rows or []:
        _cache.pop(r["telegram_id"], None)
    return len(rows or [])


class SessionMap:
    """
    Словарь активных тренировок поверх базы.

    Ведёт себя как обычный dict, чтобы не переписывать весь обработчик:
    те же `uid in SESSIONS`, `SESSIONS[uid]`, `.get`, `.pop`. Разница в том,
    что запись уходит в базу и переживает перезапуск.

    company_id берётся из самой сессии — он нужен, чтобы запись удалилась
    вместе с компанией и чтобы по ней можно было искать в панели.
    """

    def __contains__(self, telegram_id):
        return get(telegram_id) is not None

    def __getitem__(self, telegram_id):
        s = get(telegram_id)
        if s is None:
            raise KeyError(telegram_id)
        return s

    def __setitem__(self, telegram_id, session):
        put(telegram_id, session.get("company_id"), session)

    def get(self, telegram_id, default=None):
        s = get(telegram_id)
        return default if s is None else s

    def pop(self, telegram_id, default=None):
        s = drop(telegram_id)
        return default if s is None else s

    def save(self, telegram_id, session):
        """Явное сохранение после хода — читается понятнее, чем присваивание."""
        self[telegram_id] = session
