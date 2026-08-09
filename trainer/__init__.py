# -*- coding: utf-8 -*-
"""
Тренажёр отдела продаж — мультиарендная версия.

Один экземпляр бота обслуживает много компаний. Компания определяется по
telegram_id пользователя, профиль ниши и статистика — свои у каждой.

Модули:
  db          — PostgreSQL: пул соединений и схема
  tenancy     — компании, сотрудники, роли, тарифы, лимиты
  onboarding  — вход по ссылке: активация владельца и приглашение менеджеров
  niche_loader— профили ниш в базе, валидация схемы
  brief       — мастер брифа: восемь вопросов → профиль ниши
  engine      — движок диалога: покупатель + скрытая оценка + разбор
  persona     — живой человек поверх психотипа: имя, настроение, манера письма
  psychotypes — пять психотипов по спиральной динамике
  algorithm   — эталонный алгоритм продажи по Гребенюку
  stats       — статистика по менеджеру и по отделу, учёт расхода
  costs       — цены моделей и расчёт себестоимости вызова
  guide       — раздача гайда: ссылка, тексты и отдача веб-страницы
  handlers    — хендлеры aiogram
"""

from .handlers import register_trainer, main_reply_kb, trainer_reply_kb
from . import db, tenancy, onboarding, niche_loader, stats, engine, costs, brief, persona, guide, demo

__all__ = [
    "demo",
    "register_trainer", "main_reply_kb", "trainer_reply_kb",
    "db", "tenancy", "onboarding", "niche_loader", "stats", "engine", "costs", "brief",
    "persona", "guide",
]
