# -*- coding: utf-8 -*-
"""
Модуль «Тренажёр по продажам» для Telegram-бота.

Архитектура (модульно, чтобы переносить на любой бизнес):
  psychotypes.py  — 5 универсальных психотипов (спиральная динамика)
  algorithm.py    — эталонный смысловой алгоритм продажи по Гребенюку
  niches/*.json   — подключаемые ниша-паки (что продаём, кому, какие запросы)
  niche_loader.py — загрузка активной ниши (env TRAINER_NICHE, по умолчанию moss)
  engine.py       — Claude играет покупателя + скрыто оценивает по алгоритму
  stats.py        — статистика на SQLite + отчёт
  handlers.py     — кнопки, поток тренировки, интеграция в aiogram

Подключение в bot.py:
    from trainer import register_trainer
    register_trainer(dp, bot, client, is_allowed)
"""

from .handlers import register_trainer, main_reply_kb

__all__ = ["register_trainer", "main_reply_kb"]
