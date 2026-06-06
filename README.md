# Sales Agent Bot (Гребенюк)

Телеграм-бот для анализа переписок с клиентами по методологии Гребенюка.

## Деплой на Railway

1. Залей эту папку на GitHub (новый репозиторий)
2. Зайди на railway.app → New Project → Deploy from GitHub → выбери репо
3. В настройках проекта → Variables → добавь:
   - `BOT_TOKEN` — токен от @BotFather
   - `ANTHROPIC_API_KEY` — ключ от Anthropic
   - `ALLOWED_USER_ID` — твой Telegram ID (узнай у @userinfobot)
4. Railway сам задеплоит и запустит

## Использование

Просто скидывай скриншот переписки в бот.
Можно добавить подпись к скрину с контекстом.
