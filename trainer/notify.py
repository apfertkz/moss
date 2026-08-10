# -*- coding: utf-8 -*-
"""
Уведомления: владельцу продукта об авариях, клиентам — о конце подписки.

Две разные задачи, но обе про одно: узнавать о проблеме раньше, чем о ней
скажет клиент. Логи Railway никто не читает по расписанию, а подписка,
закончившаяся молча, выглядит как поломка бота.

Напоминания отправляются один раз на период: отметка о доставке пишется
в базу вместе с датой окончания. Продлили подписку — дата изменилась,
и следующий круг напоминаний пройдёт заново.
"""

import asyncio
import datetime
import logging
import os

from . import db, tenancy

log = logging.getLogger(__name__)

ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x}

# За сколько дней предупреждаем. Для коротких подписок трёх точек хватает,
# для годовых нужен запас: решение продлить на год не принимается за сутки.
STEPS = (7, 3, 1)
LONG_STEPS = (30, 14, 7, 1)

# С какого срока подписка считается длинной.
LONG_TERM_DAYS = 150

# Как часто просыпается фоновая проверка.
INTERVAL_HOURS = 6


async def admin(bot, text, quiet=False):
    """Сообщение владельцу продукта. Ошибка отправки не должна ничего ронять."""
    for uid in ADMIN_IDS:
        try:
            await bot.send_message(uid, text, disable_notification=quiet)
        except Exception:
            log.exception("Не удалось уведомить администратора %s", uid)


async def alert(bot, where, exc):
    """Авария в работе бота — коротко и с местом, где рвануло."""
    await admin(bot, f"⚠️ Сбой: {where}\n\n{type(exc).__name__}: {str(exc)[:300]}")


# --- Напоминания о подписке -------------------------------------------------

def _already_sent(company_id, kind, period_end):
    return db.query(
        """SELECT 1 FROM reminders_sent
            WHERE company_id=%s AND kind=%s AND period_end=%s""",
        (company_id, kind, period_end), one=True,
    ) is not None


def _mark(company_id, kind, period_end):
    db.execute(
        """INSERT INTO reminders_sent (company_id, kind, period_end)
                VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
        (company_id, kind, period_end),
    )


def _steps_for(company):
    """
    Длинной подписке нужен более ранний первый сигнал: годовое продление
    согласовывают неделями, а не в день окончания.
    """
    from . import tenancy
    total = company.get("expires_at") and company.get("created_at")
    if total and (company["expires_at"] - company["created_at"]).days >= LONG_TERM_DAYS:
        return LONG_STEPS
    return STEPS


def _price_hint(company):
    """Сколько стоит продлить — чтобы человеку не пришлось спрашивать."""
    from . import tenancy
    terms = tenancy.PRICES.get(company.get("plan")) or {}
    if not terms:
        return ""
    parts = []
    for months in sorted(terms):
        if months == 1 or not terms[months]:
            continue
        parts.append(f"{months} мес — {terms[months]:,} ₸".replace(",", " "))
    return ("\n\nПродление: " + ", ".join(parts)) if parts else ""


def _text_for_owner(company, days):
    title = company["title"]
    hint = _price_hint(company)
    if days <= 1:
        return (f"⏳ Доступ для *{title}* заканчивается завтра.\n\n"
                f"После этого менеджеры не смогут тренироваться. "
                f"Напишите нам, чтобы продлить.{hint}")
    return (f"⏳ Доступ для *{title}* заканчивается через {days} дн.\n\n"
            f"Чтобы отдел не остался без тренировок, продлите заранее — "
            f"напишите нам.{hint}")


async def run_reminders(bot):
    """Один проход: предупредить владельцев и показать сводку администратору."""
    sent = 0
    try:
        soon = await asyncio.get_event_loop().run_in_executor(
            None, tenancy.expiring, max(LONG_STEPS))
    except Exception:
        log.exception("Не удалось получить список истекающих подписок")
        return 0

    for company in soon or []:
        days = tenancy.days_left(company)
        if days is None:
            continue
        step = next((s for s in _steps_for(company) if days <= s), None)
        if step is None:
            continue

        kind = f"expire_{step}"
        if _already_sent(company["id"], kind, company["expires_at"]):
            continue

        owner = tenancy.owner_of(company["id"])
        if owner:
            try:
                await bot.send_message(owner["telegram_id"],
                                       _text_for_owner(company, days),
                                       parse_mode="Markdown")
                sent += 1
            except Exception:
                log.warning("Владелец компании %s недоступен", company["id"])
        _mark(company["id"], kind, company["expires_at"])

        await admin(bot, f"📅 {company['title']}: осталось {days} дн. "
                         f"(тариф «{tenancy.PLANS.get(company['plan'], {}).get('title', company['plan'])}»)",
                    quiet=True)
    return sent


async def loop(bot):
    """
    Фоновая проверка. Работает вечно, каждую ошибку переживает:
    задача, упавшая молча, хуже её отсутствия.
    """
    from . import store

    await asyncio.sleep(60)  # дать боту подняться
    while True:
        try:
            n = await run_reminders(bot)
            if n:
                log.info("Отправлено напоминаний о подписке: %s", n)
        except Exception:
            log.exception("Сбой проверки подписок")

        try:
            dropped = await asyncio.get_event_loop().run_in_executor(None, store.cleanup, 24)
            if dropped:
                log.info("Убрано брошенных тренировок: %s", dropped)
        except Exception:
            log.exception("Сбой уборки брошенных тренировок")

        await asyncio.sleep(INTERVAL_HOURS * 3600)
