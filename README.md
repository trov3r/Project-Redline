# Project Redline

Игровой Telegram-бот про гонки, прокачку и кастомизацию автомобилей.

## Возможности

- соревнования и рейтинговая прогрессия;
- прокачка машины и модернизация узлов;
- кастомизация кузова, стекла, фар, полос, спойлера, неона и номеров;
- аукцион и торговля;
- ежедневные награды и билеты.

## Технологии

- Python
- pyTelegramBotAPI
- Pillow
- pytz

## Быстрый старт

1. Установи Python 3.11+.
2. Создай виртуальное окружение и установи зависимости:

```bash
pip install -r requirements.txt
```

3. Задай переменные окружения перед запуском. Минимально нужен `BOT_TOKEN`.

Linux / macOS:

```bash
export BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
python redline.py
```

Windows PowerShell:

```powershell
$env:BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
python redline.py
```

## Режим техработ

Для заглушки используй:

```bash
python maintenance.py
```

## Конфигурация

Основные настройки берутся из переменных окружения:

- `BOT_TOKEN` — токен Telegram-бота
- `BOT_USERNAME` — username для кнопки добавления в группу
- `CHANNEL_URL` — ссылка на канал
- `DOCS_URL` — ссылка на документацию
- `EXCLUSIVE_IDS` — список Telegram ID через запятую, можно оставить пустым
- `RESERVED_PLATES` — список зарезервированных номеров через запятую, можно оставить пустым
- `GAME_TIMEZONE` — таймзона для ежедневных сбросов

## Файлы данных

При первом запуске бот сам создаст локальные файлы:

- `database.json`
- `auction.json`

Они не должны попадать в репозиторий.

## Структура

```text
Project-Redline/
├── redline.py
├── maintenance.py
├── config.py
├── requirements.txt
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
└── assets/
    ├── base/
    ├── stripes/
    ├── liveries/
    ├── plates/
    ├── tuning/
    └── fonts/
```

## Лицензия

Проект распространяется под условиями Commons Clause License Condition v1.0 в связке с MIT для Project Redline.
См. файл `LICENSE`.
