import telebot 
from telebot import types 
from PIL import Image, ImageDraw, ImageFont
import io
import os
import json
import re
import time
import threading
import random
import datetime
import pytz
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pathlib import Path

from config import (
    AUC_DB_FILE,
    ASSETS_DIR,
    BOT_TOKEN,
    CHANNEL_URL,
    DB_FILE,
    DOCS_URL,
    EXCLUSIVE_IDS,
    FONT_PATH,
    GAME_TIMEZONE,
    RESERVED_PLATES,
    STARTGROUP_URL,
)

ASSET_ROOT = Path(ASSETS_DIR) if ASSETS_DIR else Path("assets")
BASE_ASSETS_DIR = ASSET_ROOT / "base"
STRIPES_ASSETS_DIR = ASSET_ROOT / "stripes"
LIVERIES_ASSETS_DIR = ASSET_ROOT / "liveries"
PLATES_ASSETS_DIR = ASSET_ROOT / "plates"
TUNING_ASSETS_DIR = ASSET_ROOT / "tuning"
FONTS_ASSETS_DIR = ASSET_ROOT / "fonts"

def _normalize_asset_key(path: Path) -> str:
    return path.relative_to(ASSET_ROOT).as_posix()

def _first_font_path():
    if FONT_PATH:
        candidate = Path(FONT_PATH)
        if candidate.exists():
            return candidate
    if FONTS_ASSETS_DIR.exists():
        for pattern in ("*.ttf", "*.otf", "*.ttc"):
            found = sorted(FONTS_ASSETS_DIR.glob(pattern))
            if found:
                return found[0]
    return None

RESOLVED_FONT_PATH = _first_font_path()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")
bot = telebot.TeleBot(BOT_TOKEN)

CANVAS_SIZE = (1000, 1000)
ITEMS_PER_PAGE = 3

auction_lock = threading.Lock()
loaded_layers = {}
user_paint_state = {}
user_plate_state = {}
user_preview_state = {}

PAINTABLE = ["body", "glass", "rear_lights", "front_lights", "racing_stripe"]

PAINT_PRICES = {
    "body": 1000,
    "glass": 500,
    "rear_lights": 150,
    "front_lights": 150,
    "racing_stripe": 100,
    "spoiler": 250,
    "neon": 500
}

TUNING_PRICES = {
    "spoiler": 2500,
    "neon": 5000,
    "plate": 1000
}

STRIPE_PRICE = 250
BASE_LIVERY_PRICE = 25000
PLATE_STANDARD_PRICE = 2500
PLATE_ELITE_PRICE = 25000
TICKET_PRICE = 100

CUSTOM_NAMES = {
    "body": "Кузов",
    "glass": "Стекло",
    "rear_lights": "Задние фары",
    "front_lights": "Передние фары",
    "racing_stripe": "Гоночная полоса"
}

MODERN_PARTS = {
    "transmission": "Трансмиссия",
    "brakes": "Тормоза",
    "engine": "Двигатель",
    "turbo": "Турбонаддув",
    "compressor": "Компрессор"
}

MODERN_DEFAULT_LEVEL = 1

TUNING_PARTS = {
    "spoiler": "Спойлер",
    "neon": "Неон",
    "plate": "Номер"
}

DAILY_REWARDS = [
    {"type": "coins", "min": 250, "max": 500},
    {"type": "tickets", "min": 3, "max": 6},
    {"type": "cups", "min": 2, "max": 4},
    {"type": "tokens", "min": 1, "max": 1},
    {"type": "jackpot"}
]

ALLOWED_STRIPES = [f"racing_stripe_{i}" for i in range(1, 7)]
TUNING_TYPES = ["spoiler", "neon", "plate"]

TICKET_COOLDOWN = 600
MAX_STORED_TICKETS = 10
BUY_COOLDOWN = 43200
DAILY_COOLDOWN = 86400

RATE = 5000
MIN_COINS = 10000
MIN_TOKENS = 2
COMMISSION = 0.10

def load_db():
    if not os.path.exists(DB_FILE):
        return {"users": {}}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=4, ensure_ascii=False)

db = load_db()

def load_auc_db():
    if not os.path.exists(AUC_DB_FILE):
        return {"lots": []}
    with open(AUC_DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_auc_db(db):
    with open(AUC_DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=4, ensure_ascii=False)

auc_db = load_auc_db()

def get_user(db, user_data):
    if hasattr(user_data, 'id'):
        uid = str(user_data.id)
        first_name = user_data.first_name
    else:
        uid = str(user_data)
        first_name = None

    if uid not in db["users"]:
        db["users"][uid] = {
            "first_name": first_name if first_name else "Водитель",
            "level": 1,
            "coins": 250,
            "tokens": 0,
            "tickets": 3,
            "cups": 0,
            "colors": {
                "body": "#4049D2",
                "glass": "#7F87FF",
                "rear_lights": "#B20107",
                "front_lights": "#B2B1D0",
                "racing_stripe": "#FFFFFF"
            },
            "stripe": "racing_stripe_1",
            "livery": None,
            "owned_stripes": ["racing_stripe_1"],
            "owned_liveries": [],
            "modern": {key: MODERN_DEFAULT_LEVEL for key in MODERN_PARTS},
            "tuning": {
                "owned": [],
                "equipped": {},
                "plate_text": None,
                "plate_type": None
            },
            "ticket": {"stored": 0, "last_ticket": 0, "last_buy": 0},
            "daily": {"daily_streak": 0, "last_daily": 0, "daily_session": 0}
        }
    else:
        if first_name:
            db["users"][uid]["first_name"] = first_name
        
    return db["users"][uid]

def format_name(name):
    if len(name) > 30:
        return f"{name[:30]}..."
    return name

def format_value(value):
    if not isinstance(value, (int, float)) or value < 1000:
        return f"{value:,}"

    suffixes = [
        (10**12, 'T'),
        (10**9, 'B'),
        (10**6, 'M'),
        (10**3, 'K')
    ]

    for limit, suffix in suffixes:
        if value >= limit:
            reduced_value = value / limit
            formatted = f"{reduced_value:,.1f}".replace('.0', '')
            return f"{formatted}{suffix}"
    
    return str(value)

def get_market_livery_price(livery_name):
    prices_in_tokens = [
        lot["price"]
        for lot in auc_db["lots"]
        if lot["type"] == "livery" and lot["item"] == livery_name
    ]

    if not prices_in_tokens:
        return BASE_LIVERY_PRICE

    avg_tokens = sum(prices_in_tokens) / len(prices_in_tokens)

    return round(avg_tokens * RATE)

def validate_cheap_plate(text):
    return bool(re.fullmatch(r"[0-9][A-Z]{3}[0-9]{3}", text))

def validate_premium_plate(text):
    if len(text) > 7:
        return False
    if not re.fullmatch(r"[A-Z0-9]+", text):
        return False
    if not re.search(r"\d", text):
        return False
    return True

def is_plate_taken(text, current_user_id):
    for uid, user in db["users"].items():
        if uid == str(current_user_id):
            continue
        if user["tuning"].get("plate_text") == text:
            return True
    return False

def is_plate_on_auction(plate_text):
    auc_db = load_auc_db()
    return any(
        lot["type"] == "plate" and lot["item"].upper() == plate_text.upper() 
        for lot in auc_db["lots"]
    )

def is_plate_taken_anywhere(plate_text, user_id):
    if plate_text.upper() in RESERVED_PLATES:
        return True

    if is_plate_taken(plate_text, user_id):
        return True

    if is_plate_on_auction(plate_text):
        return True
        
    return False

def get_item_number(item_name):
    if not item_name:
        return "отсутствует"

    match = re.search(r'_(\d+)$', item_name)
    if match:
        return f"№{match.group(1)}"

    return item_name


def get_all_stripes(user_id):
    normal_stripes = []
    exclusive_stripes = []

    if not STRIPES_ASSETS_DIR.exists():
        return []

    for file in STRIPES_ASSETS_DIR.glob("*.png"):
        name = file.stem

        if name.startswith("racing_stripe_"):
            normal_stripes.append(name)
        elif name.startswith("exclusive_stripe_"):
            if user_id in EXCLUSIVE_IDS:
                exclusive_stripes.append(name)

    def extract_number(s):
        match = re.search(r'_(\d+)$', s)
        return int(match.group(1)) if match else 0

    normal_stripes.sort(key=extract_number)
    exclusive_stripes.sort(key=extract_number)

    return normal_stripes + exclusive_stripes

def get_all_liveries(user_id):
    normal_liveries = []
    exclusive_liveries = []

    if not LIVERIES_ASSETS_DIR.exists():
        return []

    for file in LIVERIES_ASSETS_DIR.glob("*.png"):
        name = file.stem

        if name.startswith("racing_livery_"):
            normal_liveries.append(name)
        elif name.startswith("exclusive_livery_"):
            if user_id in EXCLUSIVE_IDS:
                exclusive_liveries.append(name)

    def extract_number(s):
        match = re.search(r'_(\d+)$', s)
        return int(match.group(1)) if match else 0

    normal_liveries.sort(key=extract_number)
    exclusive_liveries.sort(key=extract_number)

    return normal_liveries + exclusive_liveries

def load_layers():
    if not ASSET_ROOT.exists():
        print(f"Ошибка: директория {ASSET_ROOT} не найдена.")
        return

    count = 0
    for path in ASSET_ROOT.rglob("*.png"):
        try:
            with Image.open(path) as img:
                loaded_layers[_normalize_asset_key(path)] = img.convert("RGBA").copy()
            count += 1
        except Exception as e:
            print(f"Ошибка загрузки {path.name}: {e}")

    print(f"Загружено слоев в память: {count}")

def apply_color(image, hex_color):
    r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (1, 3, 5))
    overlay = Image.new("RGBA", image.size, (r, g, b, 255))
    return Image.composite(overlay, image, image)


def _candidate_asset_keys(filename):
    filename = filename.replace('\\', '/')
    if '/' in filename:
        return [filename]
    return [
        f"base/{filename}",
        f"stripes/{filename}",
        f"liveries/{filename}",
        f"plates/{filename}",
        f"tuning/{filename}",
        filename,
    ]

def add_layer(base, filename, color=None):
    layer = None
    for key in _candidate_asset_keys(filename):
        if key in loaded_layers:
            layer = loaded_layers[key].copy()
            break

    if layer is None:
        return base

    if color:
        layer = apply_color(layer, color)

    return Image.alpha_composite(base, layer)

def build_image(user_id, colors_override=None, stripe_override="NO_OVERRIDE", livery_override="NO_OVERRIDE", plate_override="NO_OVERRIDE"):
    user = get_user(db, user_id)

    colors = {**user.get("colors", {}), **(colors_override or {})}
    stripe = stripe_override if stripe_override != "NO_OVERRIDE" else user.get("stripe")
    livery = livery_override if livery_override != "NO_OVERRIDE" else user.get("livery")
    plate_text = plate_override if plate_override != "NO_OVERRIDE" else user.get("tuning", {}).get("plate_text")

    tuning = user.get("tuning", {}).get("equipped", {})

    base = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))

    base = add_layer(base, "base/background.png")

    if neon := tuning.get("neon"):
        base = add_layer(base, f"tuning/{neon}.png", colors.get("neon"))

    base = add_layer(base, "base/body.png", colors.get("body"))

    if stripe:
        base = add_layer(base, f"stripes/{stripe}.png", colors.get("racing_stripe"))

    if livery:
        base = add_layer(base, f"liveries/{livery}.png")

    for part in ["glass", "rear_lights", "front_lights", "outline"]:
        base = add_layer(base, f"base/{part}.png", colors.get(part))

    if spoiler := tuning.get("spoiler"):
        base = add_layer(base, f"tuning/{spoiler}.png", colors.get("spoiler"))
        base = add_layer(base, "base/spoiler_outline.png")

    if plate := tuning.get("plate"):
        base = add_layer(base, f"plates/{plate}.png")

    if plate_text and plate:
        draw = ImageDraw.Draw(base)
        try:
            font_path = RESOLVED_FONT_PATH if RESOLVED_FONT_PATH else None
            if font_path and Path(font_path).exists():
                font = ImageFont.truetype(str(font_path), 43)
            else:
                font = ImageFont.load_default()
        except:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), plate_text, font=font)
        x = 500 - (bbox[2] - bbox[0]) // 2
        y = 888

        draw.text((x, y + 2), plate_text, fill=(0, 0, 0, 120), font=font)
        draw.text((x, y), plate_text, fill=(20, 20, 20), font=font)

    return base

def time_until_msk_midnight():
    msk = pytz.timezone(GAME_TIMEZONE)
    now = datetime.datetime.now(msk)

    tomorrow = now + datetime.timedelta(days=1)
    midnight = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)

    remaining = midnight - now
    minutes = remaining.seconds // 60
    hours = minutes // 60
    minutes = minutes % 60

    return hours, minutes, midnight.timestamp()

@bot.message_handler(commands=['start'])
def start(message):
    welcome_text = (
        "👋 <b>Добро пожаловать в Redline Project! Telegram-бот, где каждый найдет для себя что-то интересное: от спокойного продвижения до практически моментальных достижений. Вот что вас ждёт:</b>\n\n"
        "<blockquote>• 🫟 <i>Кастомизация</i> <b>(/custom)</b> - Разнообразие полос, ливрей и безграничная свобода в покраске деталей. Подбирайте уникальные сочетания, создавая собственный дизайн и выделяйтесь среди других игроков.</blockquote>\n\n"
        "<blockquote>• ⚙️ <i>Модернизация</i> <b>(/modern)</b> - Улучшайте свои характеристики и прокачивайте возможности. Система позволяет усиливать ключевые параметры, делая вас быстрее, эффективнее и конкурентоспособнее. Помимо этого доступны и визуальные улучшения по кузову — спойлеры, номерные знаки и не только.</blockquote>\n\n"
        "<blockquote>• 🏛 <i>Экономика</i> <b>(/auc, /sell, /trade)</b> - Полноценная внутриигровая торговая система: участвуйте в аукционах, выкупайте редкие и ценные предметы или выставляйте свои собственные по интересующей вас расценке.</blockquote>\n\n"
        "<blockquote>• 🏁 <i>Соревнования</i> <b>(/race, /leads)</b> - Здесь побеждает не сила, а грамотность. Всегда стремитесь занять лучшие позиции, ведь до финиша доходят только те, кто не сдаются.</blockquote>\n\n"
        "🎖️ <b>Исследуйте, экспериментируйте и открывайте новые возможности. Начните прямо сейчас и постройте свою уникальную стратегию в Redline Project! Введите /car для просмотра своей первой машины...</b>"
    )

    keyboard = types.InlineKeyboardMarkup()
    add_group = types.InlineKeyboardButton(
        text="➕ Добавить в группу",
        url=STARTGROUP_URL  
    )
    channel = types.InlineKeyboardButton(
        text="📢 Наш канал",
        url=CHANNEL_URL  
    )
    post = types.InlineKeyboardButton(
        text="📘 Документация",
        url=DOCS_URL  
    )
    keyboard.add(add_group)
    keyboard.add(channel)
    keyboard.add(post)

    bot.send_message(message.chat.id, welcome_text, reply_markup=keyboard, parse_mode="HTML", reply_to_message_id=message.message_id)

@bot.message_handler(commands=['help'])
def help(message):
    help_text = (
        "🆘 <b>Возникли трудности во время игры? Документация кратко познакомит вас со всем функционалом — от основного до дополнительного, но не менее важного. Вы также обладаете правом обратиться к администрации проекта в случае, если у вас возникли какие-либо проблемы.</b>\n\n"
        "<blockquote><i>ℹ️ Все заявки принимаются исключительно через личные сообщения канала. Для предложений и общих вопросов используйте публичное обсуждение.</i></blockquote>"
    )

    keyboard = types.InlineKeyboardMarkup()
    post = types.InlineKeyboardButton(
        text="📘 Документация",
        url=DOCS_URL  
    )
    admin = types.InlineKeyboardButton(
        text="🛡️ Администрация",
        url=DOCS_URL  
    )
    keyboard.add(post)
    keyboard.add(admin)

    bot.send_message(message.chat.id, help_text, reply_markup=keyboard, parse_mode="HTML", reply_to_message_id=message.message_id)

@bot.message_handler(commands=["car"])
def car(message):
    user = get_user(db, message.from_user.id)

    img = build_image(message.from_user.id)
    bio = io.BytesIO()
    img.save(bio, "PNG")
    bio.seek(0)

    caption = "🚗 <b>Ваш автомобиль </b>\n\n"

    caption += "🫟 <b>Стилистические особенности:</b>\n"

    custom = ""

    plate_text = user["tuning"].get("plate_text")
    plate_type = user["tuning"].get("plate_type")

    premium_plate = False

    if plate_text:
        custom += f"• Номер: <b>{plate_text}</b>\n"
        if plate_type == "premium":
            premium_plate = True
            custom += "• Тип номера: <b>Элитный</b>\n"
        else:
            custom += "• Тип номера: <b>Стандартный</b>\n"
    else:
        custom += "• Номер: <b>отсутствует</b>\n"

    livery = user.get("livery")
    current_livery = get_item_number(livery)

    if livery:
        custom += f"• Ливрея: <b>{current_livery}</b>"
        has_livery = True
    else:
        custom += "• Ливрея: <b>отсутствует</b>"
        has_livery = False

    caption += f"<blockquote>{custom}</blockquote>\n"

    caption += "\n⚙️ <b>Технические аспекты:</b>\n"

    modern = ""

    total_level = 0
    for i, (key, level) in enumerate(user["modern"].items(), 1):
        name = MODERN_PARTS.get(key, key)
        ending = "" if i == 5 else "\n"
        modern += f"• {name}: <b>Ур. {level}</b>{ending}"
        total_level += level

    caption += f"<blockquote>{modern}</blockquote>\n"

    caption += "\n📊 <b>Общая мощность: {} 🔥</b>".format(total_level)

    if has_livery and premium_plate:
        style = "Профессиональный 🆒"
    elif has_livery or premium_plate:
        style = "Уличный 🆗"
    else:
        style = "Классический 🆓"

    caption += f"\n✨ <b>Общий стиль: {style}</b>"

    bot.send_photo(
        message.chat.id,
        bio,
        caption=caption,
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )

@bot.message_handler(commands=['info'])
def send_user_info(message):
    user_data = get_user(db, message.from_user.id)
    now = int(time.time())
    
    streak = user_data['daily']['daily_streak']

    ticket_interval = TICKET_COOLDOWN
    time_since_ticket = now - user_data['ticket']['last_ticket']
    
    if user_data['ticket']['stored'] >= 10:
        ticket_status = "сейф заполнен!"
    else:
        seconds_until_ticket = ticket_interval - (time_since_ticket % ticket_interval)
        ticket_status = f"через {format_time_left(seconds_until_ticket)}"

    daily_interval = DAILY_COOLDOWN
    time_since_daily = now - user_data['daily']['last_daily']
    
    if time_since_daily >= daily_interval:
        daily_status = "доступна!"
    else:
        seconds_until_daily = daily_interval - (time_since_daily % daily_interval)
        daily_status = f"через {format_time_left(seconds_until_daily)}"

    last_buy = user_data["ticket"].get("last_buy", 0)
    time_passed_buy = now - last_buy

    if time_passed_buy < BUY_COOLDOWN:
        time_left_buy = BUY_COOLDOWN - time_passed_buy
        buy_status = f"через {format_time_left(time_left_buy)}"
    else:
        buy_status = "доступна!"

    coins = user_data['coins']
    tokens = user_data['tokens']
    tickets = user_data['tickets']
    cups = user_data['cups']

    text = (
        "<b>📊 Ваша информация</b>\n\n"
        "📖 <b>Общая статистика:</b>\n"
        f"<blockquote>• Уровень: <b>{user_data['level']}</b>\n"
        f"• Монеты: <b>{format_value(coins)}</b>\n"
        f"• Токены: <b>{format_value(tokens)}</b>\n"
        f"• Билеты: <b>{format_value(tickets)}</b>\n"
        f"• Кубки: <b>{format_value(cups)}</b></blockquote>\n\n"
        "⏰ <b>Временная статистика:</b>\n"
        f"<blockquote>• Билетов в сейфе: <b>{user_data['ticket']['stored']}/10</b>\n"
        f"• Текущая серия: <b>{streak} {plural(streak, ['день', 'дня', 'дней'])}</b>\n"
        f"• Новый билет: <b>{ticket_status}</b>\n"
        f"• Новая награда: <b>{daily_status}</b>\n"
        f"• Покупка билета: <b>{buy_status}</b></blockquote>"
    )

    bot.send_message(
        message.chat.id, 
        text, 
        parse_mode='HTML', 
        reply_to_message_id=message.message_id
    )

@bot.message_handler(commands=['trade'])
def trade_command(message):
    user_id = message.from_user.id
    user = get_user(db, user_id)

    parts = message.text.split()

    coins = user["coins"]
    tokens = user["tokens"]

    if len(parts) < 3:
        help_text = (
            "<b>🔄 Система обмена валют</b>\n\n"
            f"💰 <b>Баланс: {format_value(coins)}$</b>\n"
            f"📀 <b>Токены: {format_value(tokens)}✦︎</b>\n\n"
            "💰 <code>TKS [кол-во]</code> — Обмен монет на токены\n"
            "<i>(Минимум 10000 монет, курс 5000:1)</i>\n\n"
            "📀 <code>CNS [кол-во]</code> — Обмен токенов на монеты\n"
            "<i>(Минимум 2 токена, курс 5000:1, комиссия 10%)</i>\n\n"
            "Пример: <code>/trade TKS 10000</code>"
        )
        bot.reply_to(message, help_text, parse_mode="HTML")
        return

    trade_type = parts[1].upper()
    try:
        used_tokens = int(parts[2])
    except ValueError:
        bot.reply_to(message, "❌ <b>Сумма должна быть числом.</b>", parse_mode="HTML")
        return

    if used_tokens <= 0:
        bot.reply_to(message, "❌ <b>Сумма должна быть больше нуля.</b>", parse_mode="HTML")
        return

    if trade_type == "TKS":
        if user["coins"] < MIN_COINS:
            bot.reply_to(message, f"❌ <b>Минимальный порог для обмена — {MIN_COINS} монет.</b>", parse_mode="HTML")
            return
        
        if user["coins"] < used_tokens:
            bot.reply_to(message, "❌ <b>У вас недостаточно монет на балансе.</b>", parse_mode="HTML")
            return

        new_tokens = used_tokens // RATE
        if new_tokens < 1:
            bot.reply_to(message, f"❌ <b>Минимальный порог для обмена — {RATE} монет (1 токен).</b>", parse_mode="HTML")
            return

        used_coins = new_tokens * RATE
        user["coins"] -= used_coins
        user["tokens"] += new_tokens

        save_db(db)
        
        success_msg = (
            "✅ <b>Обмен завершен!</b>\n"
            f"Списано: <b>{format_value(used_coins)}$</b>\n"
            f"Получено: <b>{format_value(new_tokens)}✦︎</b>\n"
        )
        bot.reply_to(message, success_msg, parse_mode="HTML")

    elif trade_type == "CNS":
        if user["tokens"] < MIN_TOKENS:
            bot.reply_to(message, f"❌ <b>Минимальный порог для обмена — {MIN_TOKENS} токена.</b>", parse_mode="HTML")
            return
        
        if user["tokens"] < used_tokens:
            bot.reply_to(message, "❌ <b>У вас недостаточно токенов на балансе.</b>", parse_mode="HTML")
            return

        total_receive = used_tokens * RATE
        fee = int(total_receive * COMMISSION)
        new_coins = total_receive - fee

        user["tokens"] -= used_tokens
        user["coins"] += new_coins
        
        save_db(db)
        
        success_msg = (
            "✅ <b>Обмен завершен!</b>\n"
            f"Списано: <b>{format_value(used_tokens)}✦︎</b>\n"
            f"Получено: <b>{format_value(new_coins)}$</b>\n"
        )
        bot.reply_to(message, success_msg, parse_mode="HTML")

    else:
        bot.reply_to(message, "⚠️ <b>Используйте тип TKS или CNS.</b>", parse_mode="HTML")

@bot.message_handler(commands=['sell'])
def sell_item(message):
    args = message.text.split()
    if len(args) < 3:
        return bot.reply_to(message, "❌ <b>Неверный формат!</b> Пример: <code>/sell [ID ливреи/Текст номера] [5-10 токенов]</code>.", parse_mode="HTML")

    user_id = str(message.from_user.id)
    global db
    user = get_user(db, user_id)
    
    try:
        price_tokens = int(args[2])
        if not (5 <= price_tokens <= 10):
            return bot.reply_to(message, "❌ <b>Превышен лимит!</b> Пример: <code>/sell [ID ливреи/Текст номера] [5-10 токенов]</code>.", parse_mode="HTML")
    except ValueError:
        return bot.reply_to(message, "❌ <b>Некорректное число токенов! Укажите целое число.</b>", parse_mode="HTML")

    item_input = args[1]

    if item_input.isdigit():
        item_val = f"racing_livery_{item_input}"
        if item_val not in user.get("owned_liveries", []):
            return bot.reply_to(message, "❌ <b>У вас отсутствует ливрея с данным ID!</b>", parse_mode="HTML")
        
        user["owned_liveries"].remove(item_val)
        if user.get("livery") == item_val:
            user["livery"] = None
        item_type = "livery"

    else:
        item_val = item_input.upper()
        current_plate = user["tuning"].get("plate_text")
        current_type = user["tuning"].get("plate_type")

        if current_plate != item_val:
            return bot.reply_to(message, "❌ <b>У вас отсутствует номер с данным текстом!</b>", parse_mode="HTML")

        if current_type == "cheap":
            return bot.reply_to(message, "❌ <b>Стандартные номера нельзя выставлять на аукцион. В продажу входят исключительно элитные.</b>", parse_mode="HTML")

        user["tuning"]["plate_text"] = None
        user["tuning"]["plate_type"] = None
        item_type = "plate"

    lot_random_id = random.randint(100000, 999999)
    lot_id = f"{lot_random_id}_{user_id}"

    while any(l["lot_id"] == lot_id for l in auc_db["lots"]):
        lot_random_id = random.randint(100000, 999999)
        lot_id = f"{lot_random_id}_{user_id}"

    new_lot = {
        "lot_id": lot_id,
        "seller_id": user_id,
        "seller_name": message.from_user.first_name,
        "type": item_type,
        "item": item_val,
        "price": price_tokens
    }
    
    auc_db["lots"].append(new_lot)
    
    save_db(db)
    save_auc_db(auc_db)

    display_name = item_val.replace("racing_livery_", "Ливрея №") if item_type == "livery" else f"Номер [{item_val}]"

    msg = (f"<b>✅ Лот выставлен!</b>\n\n"
        f"🆔 ID лота: <code>{lot_id}</code>\n"
        f"📦 Товар: <b>{display_name}</b>\n"
        f"📀 Цена: <b>{format_value(price_tokens)}✦︎</b>\n\n"
        f"<blockquote><i>ℹ️ В случае возникновения возможных проблем сообщите ID лота администрации Redline Project. Достоверный контакт по команде /help.</i></blockquote>")
    bot.reply_to(message, msg, parse_mode="HTML")

@bot.message_handler(commands=['auc'])
def auc_command(message):
    show_auction(message, page=0, is_callback=False)

@bot.callback_query_handler(func=lambda call: call.data.startswith('auc_page|'))
def auction_pagination(call):
    data = call.data.split("|")
    owner_id = int(data[1])
    page = int(data[2])

    if call.from_user.id != owner_id:
        return bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")

    show_auction(call, page=page, is_callback=True)
    bot.answer_callback_query(call.id)

def show_auction(obj, page=0, is_callback=False):
    global auc_db
    lots = auc_db.get("lots", [])
    owner_id = obj.from_user.id 
    user = get_user(db, owner_id)

    if user:
        tokens = user.get("tokens", 0)
    else:
        tokens = 0

    if is_callback:
        chat_id = obj.message.chat.id
        message_id = obj.message.message_id
        user_id = obj.from_user.id
        reply_to = None
    else:
        chat_id = obj.chat.id
        user_id = obj.from_user.id
        reply_to = obj.message_id

    if not lots:
        empty_text = "<i>🏛 Аукцион пуст.</i>"
        if is_callback:
            return bot.edit_message_text(empty_text, chat_id, message_id, parse_mode="HTML")
        return bot.send_message(chat_id, empty_text, parse_mode="HTML", reply_to_message_id=reply_to)

    items_per_page = ITEMS_PER_PAGE
    total_pages = (len(lots) - 1) // items_per_page + 1
    start_idx = page * items_per_page
    current_lots = lots[start_idx : start_idx + items_per_page]

    text = f"<b>🏛 Аукцион <i>(Страница {page + 1}/{total_pages})</i></b>\n"
    text += f"📀 <b>Токены: {format_value(tokens)}✦︎</b>\n\n"
    markup = types.InlineKeyboardMarkup()

    for lot in current_lots:
        is_official = int(lot["seller_id"]) in EXCLUSIVE_IDS
        v_mark = "✅ <u>[верифицирован]</u>\n" if is_official else ""
        price = lot['price']
        
        seller_name = format_name(lot.get("seller_name", "Водитель"))
        display_name = lot["item"].replace("racing_livery_", "Ливрея №") if lot["type"] == "livery" else f"Номер [{lot['item']}]"

        text += (f"👤 <b>{seller_name}</b>\n"
                 f"{v_mark}"
                 f"📦 <b>{display_name}</b> | 📀 <b>{format_value(price)}✦︎</b>\n\n")

        markup.add(types.InlineKeyboardButton(
            text=f"🛒 Купить «{display_name}» ({format_value(price)}✦︎)", 
            callback_data=f"auc_pre|{user_id}|{lot['lot_id']}"
        ))

    text += f"<blockquote><i>ℹ️ Метка «верифицирован» - официально зарегистрированный аккаунт, сделки с которым наиболее выгодны и безопасны.</i></blockquote>"

    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️", callback_data=f"auc_page|{user_id}|{page-1}"))
    if start_idx + items_per_page < len(lots):
        nav_buttons.append(types.InlineKeyboardButton("➡️", callback_data=f"auc_page|{user_id}|{page+1}"))
    
    if nav_buttons:
        markup.row(*nav_buttons)

    if is_callback:
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="HTML")
        except Exception as e:
            pass
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML", reply_to_message_id=reply_to)

@bot.callback_query_handler(func=lambda call: call.data.startswith('auc_pre|'))
def auction_preview(call):
    data = call.data.split("|")
    owner_id = data[1]
    lot_id = data[2]

    if str(call.from_user.id) != owner_id:
        return bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")

    lot = next((l for l in auc_db.get("lots", []) if l["lot_id"] == lot_id), None)
    if not lot:
        return bot.answer_callback_query(call.id, "❌ Лот уже продан или снят!")

    l_ov = lot["item"] if lot["type"] == "livery" else "NO_OVERRIDE"
    p_ov = lot["item"] if lot["type"] != "livery" else "NO_OVERRIDE"
    
    display_name = lot["item"].replace("racing_livery_", "Ливрея №") if lot["type"] == "livery" else f"Номер [{lot['item']}]"

    preview_img = build_image(call.from_user.id, livery_override=l_ov, plate_override=p_ov)
    
    bio = io.BytesIO()
    preview_img.save(bio, "PNG")
    bio.seek(0)

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "✅ Купить",
            callback_data=f"confirm_auc|{owner_id}|{lot_id}",
            style="success"
        ),
        types.InlineKeyboardButton(
            "❌ Отменить",
            callback_data=f"cancel_auc|{owner_id}",
            style="danger"
        )
    )

    seller_name = format_name(lot.get("seller_name", "Водитель"))
    price = lot['price']

    caption = (
        f"🛒 <b>Предпросмотр товара</b>\n\n"
        f"Продавец: <b>{seller_name}</b>\n"
        f"Товар: <b>{display_name}</b>\n\n"
        f"📀 <b>Стоимость: {format_value(price)}✦︎</b>"
    )

    bot.send_photo(
        call.message.chat.id,
        bio,
        caption=caption,
        parse_mode="HTML",
        reply_markup=markup,
        reply_to_message_id=call.message.message_id
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_auc|"))
def process_auction_buy(call):
    data = call.data.split("|")
    owner_id = data[1]
    lot_id = data[2]

    if str(call.from_user.id) != owner_id:
        return bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")

    global db, auc_db
    lot = next((l for l in auc_db.get("lots", []) if l["lot_id"] == lot_id), None)

    if not lot:
        bot.answer_callback_query(call.id, "❌ Лот уже продан или снят!")
        return bot.delete_message(call.message.chat.id, call.message.message_id)

    buyer_id = str(call.from_user.id)
    if lot["seller_id"] == buyer_id:
        return bot.answer_callback_query(call.id, "❌ Нельзя купить собственный лот!")

    buyer_user = get_user(db, buyer_id)

    if lot["type"] == "livery":
        owned = buyer_user.get("owned_liveries", [])
        if lot["item"] in owned:
            return bot.answer_callback_query(call.id, "❌ У вас уже есть эта ливрея!")

    price = lot["price"]
    shortage = price - buyer_user["tokens"]
    if buyer_user.get("tokens", 0) < price:
        return bot.answer_callback_query(call.id, f"❌ Недостаточно токенов! ({format_value(shortage)}✦︎)")

    seller_id = lot["seller_id"]
    seller_user = get_user(db, seller_id)

    buyer_user["tokens"] -= price
    seller_user["tokens"] += price

    if lot["type"] == "livery":
        if "owned_liveries" not in buyer_user: 
            buyer_user["owned_liveries"] = []
        buyer_user["owned_liveries"].append(lot["item"])
        buyer_user["livery"] = lot["item"]
        res_name = lot["item"].replace("racing_livery_", "Ливрея №")
    else:
        buyer_user["tuning"]["plate_text"] = lot["item"]
        buyer_user["tuning"]["plate_type"] = "premium"
        res_name = f"Номер [{lot['item']}]"

    auc_db["lots"].remove(lot)
    save_db(db)
    save_auc_db(auc_db)

    bot.answer_callback_query(call.id, "✅ Успешно!")
    bot.edit_message_caption(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        caption=f"✅ <b>Товар «{res_name}» успешно куплен!</b>\n📀 <b>Списано: {format_value(price)}✦︎</b>",
        parse_mode="HTML",
        reply_markup=None
    )

    try:
        buyer_name = call.from_user.first_name
        bot.send_message(
            seller_id,
            f"✅ <b>Ваш товар успешно продан!</b>\n\n"
            f"👤 Покупатель: <b>{buyer_name}</b>\n"
            f"📦 Товар: <b>{res_name}</b>\n"
            f"📀 Зачислено: <b>{format_value(price)}✦︎</b>\n\n"
            f"<blockquote><i>ℹ️ Полученную с продажи прибыль возможно конвертировать в монеты и обратно - воспользуйтесь командой /trade в любое время, если понадобится какая-либо валюта.</i></blockquote>",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Не удалось отправить уведомление продавцу {seller_id}: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("cancel_auc|"))
def cancel_auction_buy(call):
    owner_id = call.data.split("|")[1]
    if str(call.from_user.id) != owner_id:
        return bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")

    bot.answer_callback_query(call.id, "❌ Отменено.")
    bot.edit_message_caption(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        caption="❌ <b>Выбор товара отменён.</b>",
        parse_mode="HTML",
        reply_markup=None
    )

@bot.message_handler(commands=["custom"])
def custom_menu(message):
    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "🎨 Покраска",
            callback_data=f"custompaint|{message.from_user.id}|0"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🏁 Полосы",
            callback_data=f"customstripe|{message.from_user.id}|0"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🏎️ Ливреи",
            callback_data=f"customlivery|{message.from_user.id}|0"
        )
    )

    bot.send_message(
        message.chat.id,
        "🫟 <b>Кастомизация автомобиля</b>\n\n<i>Выберите раздел:</i>",
        parse_mode="HTML",
        reply_markup=markup,
        reply_to_message_id=message.message_id
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("custompaint|"))
def open_paint_menu(call):
    _, owner_id, page = call.data.split("|")
    page = int(page)

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    user = get_user(db, call.from_user)

    total_paintable = list(PAINT_PRICES.keys())
    available_parts = []

    for part in PAINTABLE:
        if part == "racing_stripe" and not user["stripe"]:
            continue
        available_parts.append(part)

    if user["tuning"]["equipped"].get("spoiler"):
        available_parts.append("spoiler")

    if user["tuning"]["equipped"].get("neon"):
        available_parts.append("neon")

    per_page = ITEMS_PER_PAGE
    start = page * per_page
    end = start + per_page
    page_items = available_parts[start:end]

    coins = user["coins"]

    markup = types.InlineKeyboardMarkup()

    text = f"💰 <b>Баланс: {format_value(coins)}$</b>\n\n"
    text += f"Доступно к покраске: <b>{len(available_parts)}/{len(total_paintable)}</b>\n\n"
    text += "🔻 <b>Выберите деталь:</b>"

    for part in page_items:
        if part in CUSTOM_NAMES:
            name = CUSTOM_NAMES[part]
        elif part in TUNING_PARTS:
            name = TUNING_PARTS[part]
        else:
            name = part
        
        price = PAINT_PRICES.get(part, 0)

        markup.add(types.InlineKeyboardButton(
            f"🎨 {name} ({format_value(price)}$)",
            callback_data=f"paint|{call.from_user.id}|{part}"
        ))

    nav = []

    if page > 0:
        nav.append(types.InlineKeyboardButton(
            "⬅",
            callback_data=f"custompaint|{owner_id}|{page-1}"
        ))

    if end < len(available_parts):
        nav.append(types.InlineKeyboardButton(
            "➡",
            callback_data=f"custompaint|{owner_id}|{page+1}"
        ))

    if nav:
        markup.row(*nav)

    markup.add(types.InlineKeyboardButton(
        "🔙 Назад",
        callback_data=f"backcustom|{owner_id}"
    ))

    bot.edit_message_text(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=markup
    )

    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("paint|"))
def paint_select(call):
    _, owner_id, part = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return
    
    user = get_user(db, call.from_user)

    if part not in PAINT_PRICES:
        bot.answer_callback_query(call.id, "❌ Ошибка присутствия детали!")
        return
    
    if part in ["spoiler", "neon"]:
        if not user["tuning"]["equipped"].get(part):
            bot.answer_callback_query(call.id, "❌ Ошибка присутствия детали!")
            return

    price = PAINT_PRICES.get(part, 0)

    if part in CUSTOM_NAMES:
        name = CUSTOM_NAMES[part]
    elif part in TUNING_PARTS:
        name = TUNING_PARTS[part]
    else:
        name = part

    user_paint_state[call.from_user.id] = {
        "part": part,
        "message_id": call.message.message_id,
        "chat_id": call.message.chat.id
    }

    bot.edit_message_text(
        f"✏️ <b>Введите HEX ответом на сообщение бота (цифры от 0 до 9 и буквы от A до F):</b>\n\n"
        f"🎨 <b>Покраска «{name}»</b>\n"
        f"Шестнадцатеричная система счисления\n"
        f"Пример: <code>#A1B2C3</code>\n\n"
        f"💰 <b>Стоимость: {format_value(price)}$</b>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML"
    )

    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: m.from_user.id in user_paint_state)
def process_hex(message):
    state = user_paint_state.get(message.from_user.id)
    if not state:
        return

    if not message.reply_to_message or message.reply_to_message.message_id != state["message_id"]:
        return 

    raw_input = message.text.strip().upper()

    if not raw_input.startswith('#'):
        hex_color = f"#{raw_input}"
    else:
        hex_color = raw_input

    if not re.fullmatch(r"#([0-9A-F]{6})", hex_color):
        bot.send_message(
            message.chat.id,
            "❌ <b>Неверный формат!</b> Пример: <code>#A1B2C3</code>.",
            parse_mode="HTML",
            reply_to_message_id=message.message_id
        )
        user_paint_state.pop(message.from_user.id, None)
        return

    part = state["part"]
    user = get_user(db, message.from_user.id)
    old_color = user["colors"].get(part, "#FFFFFF").upper()

    if hex_color == old_color:
        bot.send_message(
            message.chat.id,
            f"⚠️ <b>Цвет</b> <code>{hex_color}</code> <b>уже применен!</b>",
            parse_mode="HTML",
            reply_to_message_id=message.message_id
        )
        user_paint_state.pop(message.from_user.id, None)
        return

    price = PAINT_PRICES.get(part, 0)

    preview_img = build_image(
        message.from_user.id,
        colors_override={part: hex_color}
    )

    bio = io.BytesIO()
    preview_img.save(bio, "PNG")
    bio.seek(0)

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "✅ Купить",
            callback_data=f"buypaint|{message.from_user.id}|{part}|{hex_color}",
            style="success"
        ),
        types.InlineKeyboardButton(
            "❌ Отменить",
            callback_data=f"cancelpaint|{message.from_user.id}",
            style="danger"
        )
    )

    sent = bot.send_photo(
        message.chat.id,
        bio,
        caption=(
            "🎨 <b>Предпросмотр покраски</b>\n\n"
            f"Старый цвет: <code>{old_color}</code>\n"
            f"Новый цвет: <code>{hex_color}</code>\n\n"
            f"💰 <b>Стоимость: {format_value(price)}$</b>"
        ),
        parse_mode="HTML",
        reply_markup=markup,
        reply_to_message_id=message.message_id
    )

    user_preview_state[message.from_user.id] = {
        "type": "paint",
        "part": part,
        "color": hex_color,
        "old_color": old_color,
        "message_id": sent.message_id,
        "chat_id": message.chat.id,
    }

    user_paint_state.pop(message.from_user.id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("buypaint|"))
def confirm_purchase(call):
    _, owner_id, part, hex_color = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    preview = user_preview_state.get(call.from_user.id)

    if not preview or preview.get("type") != "paint":
        return

    if preview.get("message_id") != call.message.message_id:
        bot.answer_callback_query(call.id, "⛔ Сессия истекла.")
        return

    if not preview or preview.get("type") != "paint":
        return

    user = get_user(db, call.from_user)
    price = PAINT_PRICES.get(part, 0)
    shortage = price - user["coins"]

    if user["coins"] < price:
        bot.answer_callback_query(call.id, f"❌ Недостаточно монет! ({format_value(shortage)}$)")
        return

    old_color = preview["old_color"]

    user["coins"] -= price
    user["colors"][part] = hex_color
    save_db(db)

    if part in CUSTOM_NAMES:
        name = CUSTOM_NAMES[part]
    elif part in TUNING_PARTS:
        name = TUNING_PARTS[part]
    else:
        name = part

    bot.answer_callback_query(call.id, "✅ Успешно!")

    caption = (
    f"✅ <b>Покраска «{name}» [{hex_color}] успешно куплена!</b>"
    )
    caption += f"\n💰 <b>Списано: {format_value(price)}$</b>"

    bot.edit_message_caption(
        caption=caption,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=None
    )

    user_preview_state.pop(call.from_user.id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("cancelpaint|"))
def cancel_purchase(call):
    _, owner_id = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    bot.answer_callback_query(call.id, "❌ Отменено.")

    bot.edit_message_caption(
        caption="❌ <b>Выбор покраски отменён.</b>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=None
    )

    user_preview_state.pop(call.from_user.id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("customstripe|"))
def open_stripe_menu(call):
    _, owner_id, page = call.data.split("|")
    page = int(page)

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    user = get_user(db, call.from_user)
    stripes = get_all_stripes(call.from_user.id)

    per_page = ITEMS_PER_PAGE
    start = page * per_page
    end = start + per_page
    page_items = stripes[start:end]
    stripe = user["stripe"]
    current_stripe = get_item_number(stripe) if get_item_number(stripe) else "отсутствует"
    coins = user["coins"]

    markup = types.InlineKeyboardMarkup()

    text = f"💰 <b>Баланс: {format_value(coins)}$</b>\n\n"
    text += f"Текущая полоса: <b>{current_stripe}</b>\n\n"
    text += "🔻 <b>Выберите полосу:</b>"

    price = STRIPE_PRICE
    value = ""

    markup.add(types.InlineKeyboardButton(
        "🚫 Снять полосу",
        callback_data=f"stripepreview|{call.from_user.id}|none"
    ))

    for stripe in page_items:
        if stripe == user["stripe"]:
            emoji = "✅"
        elif stripe.startswith("exclusive_stripe_"):
            emoji = "💎"
            value = "(V.I.P)"
        elif stripe in user["owned_stripes"]:
            emoji = "🟢"
        else:
            emoji = "🔒"
            value = f"({format_value(price)}$)"

        number = get_item_number(stripe)    
        markup.add(types.InlineKeyboardButton(
            f"{emoji} Полоса {number} {value}",
            callback_data=f"stripepreview|{call.from_user.id}|{stripe}"
        ))

    nav = []

    if page > 0:
        nav.append(types.InlineKeyboardButton("⬅", callback_data=f"customstripe|{owner_id}|{page-1}"))

    if end < len(stripes):
        nav.append(types.InlineKeyboardButton("➡", callback_data=f"customstripe|{owner_id}|{page+1}"))

    if nav:
        markup.row(*nav)

    markup.add(types.InlineKeyboardButton(
        "🔙 Назад",
        callback_data=f"backcustom|{owner_id}"
    ))

    bot.edit_message_text(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("stripepreview|"))
def stripe_preview(call):
    _, owner_id, stripe = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    user = get_user(db, call.from_user)

    old_stripe = user["stripe"]
    old_name = get_item_number(old_stripe)

    if stripe == "none":
        new_name = "отсутствует"
        price = 0
    elif stripe.startswith("exclusive_stripe_"):
        new_name = get_item_number(stripe)
        price = 0
    elif stripe in user["owned_stripes"]:
        new_name = get_item_number(stripe)
        price = 0
    else:
        new_name = get_item_number(stripe)
        price = STRIPE_PRICE
    preview_img = build_image(call.from_user.id, stripe_override=stripe)

    bio = io.BytesIO()
    preview_img.save(bio, "PNG")
    bio.seek(0)

    btn_text = ""

    if price > 0:
        btn_text += f"✅ Купить"
    else:
        btn_text += f"✅ Применить"

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            f"{btn_text}",
            callback_data=f"buystripe|{owner_id}|{stripe}",
            style="success"
        ),
        types.InlineKeyboardButton(
            "❌ Отменить",
            callback_data=f"cancelstripe|{owner_id}",
            style="danger"
        )
    )

    caption = (
    "🏁 <b>Предпросмотр полосы</b>\n\n"
    f"Старая полоса: <b>{old_name}</b>\n"
    f"Новая полоса: <b>{new_name}</b>"
    )

    if price > 0:
        caption += f"\n\n💰 <b>Стоимость: {format_value(price)}$</b>"

    sent = bot.send_photo(
        call.message.chat.id,
        bio,
        caption=caption,
        parse_mode="HTML",
        reply_markup=markup,
        reply_to_message_id=call.message.message_id
    )

    user_preview_state[call.from_user.id] = {
        "type": "stripe",
        "stripe": stripe,
        "old_stripe": old_stripe,
        "price": price,
        "message_id": sent.message_id,
        "chat_id": call.message.chat.id,
    }

@bot.callback_query_handler(func=lambda call: call.data.startswith("buystripe|"))
def stripe_buy(call):
    _, owner_id, stripe = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    preview = user_preview_state.get(call.from_user.id)

    if not preview or preview.get("type") != "stripe":
        return

    if preview.get("message_id") != call.message.message_id:
        bot.answer_callback_query(call.id, "⛔ Сессия истекла.")
        return

    if not preview or preview.get("type") != "stripe":
        return

    user = get_user(db, call.from_user)

    price = preview["price"]
    old_stripe = preview["old_stripe"]
    old_name = get_item_number(old_stripe)
    shortage = price - user["coins"]

    if user["coins"] < price:
        bot.answer_callback_query(call.id, f"❌ Недостаточно монет! ({format_value(shortage)}$)")
        return

    if price > 0:
        user["coins"] -= price

    if stripe != "none":
        if not stripe.startswith("exclusive_") and stripe not in user["owned_stripes"]:
            user["owned_stripes"].append(stripe)
        
        user["stripe"] = stripe
        user["livery"] = None
        new_name = get_item_number(stripe)
    else:
        user["stripe"] = None
        new_name = "отсутствует"

    save_db(db)

    bot.answer_callback_query(call.id, "✅ Успешно!")

    if price > 0:
        caption = (
        f"✅ <b>Полоса {new_name} успешно куплена!</b>"
        )
        caption += f"\n💰 <b>Списано: {format_value(price)}$</b>"
    else:
        caption = (
        f"✅ <b>Полоса {new_name} успешно применена!</b>"
        )

    bot.edit_message_caption(
        caption=caption,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=None
    )

    user_preview_state.pop(call.from_user.id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("cancelstripe|"))
def cancel_stripe(call):
    _, owner_id = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    bot.answer_callback_query(call.id, "❌ Отменено.")

    bot.edit_message_caption(
        "❌ <b>Выбор полосы отменён.</b>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=None
    )

    user_preview_state.pop(call.from_user.id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("customlivery|"))
def open_livery_menu(call):
    _, owner_id, page = call.data.split("|")
    page = int(page)

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return
    
    user = get_user(db, call.from_user)
    liveries = get_all_liveries(call.from_user.id)

    per_page = ITEMS_PER_PAGE
    start = page * per_page
    end = start + per_page
    page_items = liveries[start:end]
    
    livery = user["livery"]
    current_livery = get_item_number(livery) if get_item_number(livery) else "отсутствует"
    coins = user["coins"]

    markup = types.InlineKeyboardMarkup()
    text = f"💰 <b>Баланс: {format_value(coins)}$</b>\n\n"
    text += f"Текущая ливрея: <b>{current_livery}</b>\n\n"
    text += "🔻 <b>Выберите ливрею:</b>"

    value = ""

    markup.add(types.InlineKeyboardButton("🚫 Снять ливрею", callback_data=f"liverypreview|{owner_id}|none"))

    for liv in page_items:
        if liv == user["livery"]:
            emoji = "✅"
        elif liv.startswith("exclusive_livery_"):
            emoji = "💎"
            value = "(V.I.P)"
        elif liv in user["owned_liveries"]:
            emoji = "🟢"
        else:
            emoji = "🔒"
            price = get_market_livery_price(liv)
            value = f"({format_value(price)}$)"

        number = get_item_number(liv)
        markup.add(types.InlineKeyboardButton(
            f"{emoji} Ливрея {number} {value}",
            callback_data=f"liverypreview|{owner_id}|{liv}"
        ))

    nav = []

    if page > 0:
        nav.append(types.InlineKeyboardButton("⬅", callback_data=f"customlivery|{owner_id}|{page-1}"))

    if end < len(liveries):
        nav.append(types.InlineKeyboardButton("➡", callback_data=f"customlivery|{owner_id}|{page+1}"))

    if nav:
        markup.row(*nav)

    markup.add(types.InlineKeyboardButton(
        "🔙 Назад",
        callback_data=f"backcustom|{owner_id}"
    ))

    bot.edit_message_text(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("liverypreview|"))
def livery_preview(call):
    _, owner_id, livery = call.data.split("|")
    
    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    user = get_user(db, call.from_user)
    old_livery = user["livery"]
    old_name = get_item_number(old_livery)

    if livery == "none":
        new_name = "отсутствует"
        price = 0
    elif livery.startswith("exclusive_livery_"):
        new_name = get_item_number(livery)
        price = 0
    elif livery in user["owned_liveries"]:
        new_name = get_item_number(livery)
        price = 0
    else:
        new_name = get_item_number(livery)
        price = get_market_livery_price(livery)

    preview_img = build_image(call.from_user.id, livery_override=livery)

    bio = io.BytesIO()
    preview_img.save(bio, "PNG")
    bio.seek(0)

    btn_text = ""

    if price > 0:
        btn_text += f"✅ Купить"
    else:
        btn_text += f"✅ Применить"

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            f"{btn_text}",
            callback_data=f"buylivery|{owner_id}|{livery}",
            style="success"
        ),
        types.InlineKeyboardButton(
            "❌ Отменить",
            callback_data=f"cancellivery|{owner_id}",
            style="danger"
        )
    )

    caption = (
    "🏎️ <b>Предпросмотр ливреи</b>\n\n"
    f"Старая ливрея: <b>{old_name}</b>\n"
    f"Новая ливрея: <b>{new_name}</b>"
    )

    if price > 0:
        caption += f"\n\n💰 <b>Стоимость: {format_value(price)}$</b>"

    sent = bot.send_photo(
        call.message.chat.id,
        bio,
        caption=caption,
        parse_mode="HTML",
        reply_markup=markup,
        reply_to_message_id=call.message.message_id
    )

    user_preview_state[call.from_user.id] = {
        "type": "livery",
        "livery": livery,
        "old_livery": old_livery,
        "price": price,
        "message_id": sent.message_id,
        "chat_id": call.message.chat.id,
    }

@bot.callback_query_handler(func=lambda call: call.data.startswith("buylivery|"))
def livery_buy(call):
    _, owner_id, livery = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    preview = user_preview_state.get(call.from_user.id)

    if not preview or preview.get("type") != "livery":
        return

    if preview.get("message_id") != call.message.message_id:
        bot.answer_callback_query(call.id, "⛔ Сессия истекла.")
        return

    if not preview or preview.get("type") != "livery":
        return

    user = get_user(db, call.from_user)
    price = preview["price"]
    shortage = price - user["coins"]
    old_livery = preview["old_livery"]

    if user["coins"] < price:
        bot.answer_callback_query(call.id, f"❌ Недостаточно монет! ({format_value(shortage)}$)")
        return

    if livery != "none":
        if not livery.startswith("exclusive_") and livery not in user["owned_liveries"]:
            user["owned_liveries"].append(livery)

        user["livery"] = livery
        user["stripe"] = None
        new_name = get_item_number(livery)
    else:
        user["livery"] = None
        new_name = "отсутствует"

    save_db(db)

    bot.answer_callback_query(call.id, "✅ Успешно!")

    if price > 0:
        caption = (
        f"✅ <b>Ливрея {new_name} успешно куплена!</b>"
        )
        caption += f"\n💰 <b>Списано: {format_value(price)}$</b>"
    else:
        caption = (
        f"✅ <b>Ливрея {new_name} успешно применена!</b>"
        )

    bot.edit_message_caption(
        caption=caption,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=None
    )

    user_preview_state.pop(call.from_user.id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("cancellivery|"))
def cancel_livery(call):
    _, owner_id = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    bot.answer_callback_query(call.id, "❌ Отменено.")

    bot.edit_message_caption(
        "❌ <b>Выбор ливреи отменён.</b>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=None
    )

    user_preview_state.pop(call.from_user.id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("backcustom|"))
def back_to_custom(call):
    _, owner_id = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "🎨 Покраска",
            callback_data=f"custompaint|{owner_id}|0"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🏁 Полосы",
            callback_data=f"customstripe|{owner_id}|0"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🏎️ Ливреи",
            callback_data=f"customlivery|{owner_id}|0"
        )
    )

    bot.edit_message_text(
        "🫟 <b>Кастомизация автомобиля</b>\n\n<i>Выберите раздел:</i>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=markup
    )

@bot.message_handler(commands=["modern"])
def modern_menu(message):
    markup = types.InlineKeyboardMarkup()

    markup.add(types.InlineKeyboardButton(
        "📈 Характеристики",
        callback_data=f"modernstats|{message.from_user.id}"
    ))

    markup.add(types.InlineKeyboardButton(
        "🛠️ Тюнинг",
        callback_data=f"moderntuning|{message.from_user.id}"
    ))

    user = get_user(db, message.from_user.id)

    if "plate" in user["tuning"]["owned"]:
        markup.add(types.InlineKeyboardButton(
            "🔢 Номер",
            callback_data=f"modernplate|{message.from_user.id}"
        ))

    bot.send_message(
        message.chat.id,
        "⚙️ <b>Модернизация автомобиля</b>\n\n<i>Выберите раздел:</i>",
        parse_mode="HTML",
        reply_markup=markup,
        reply_to_message_id=message.message_id
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("modernstats|"))
def open_modern_stats(call):
    _, owner_id = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    user = get_user(db, call.from_user)
    coins = user['coins']

    markup = types.InlineKeyboardMarkup()
    text = f"💰 <b>Баланс: {format_value(coins)}$</b>\n\n"
    mod_list = ""

    for key, name in MODERN_PARTS.items():
        level = user["modern"].get(key, 1)

        if level >= 10:
            price_text = "MAX"
        else:
            price = 100 * (2 ** (level - 1))
            price_text = f"{format_value(price)}$"

        mod_list += f"{name}: <b>Ур. {level}</b>\n"

        markup.add(types.InlineKeyboardButton(
            f"⬆ {name} ({price_text})",
            callback_data=f"modernupgrade|{owner_id}|{key}"
        ))

    text += f"{mod_list}"
    text += f"\n🔻 <b>Выберите пункт:</b>"

    markup.add(types.InlineKeyboardButton(
        "🔙 Назад",
        callback_data=f"backmodern|{owner_id}"
    ))

    bot.edit_message_text(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=markup
    )

    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("modernupgrade|"))
def modern_upgrade(call):
    _, owner_id, part = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    user = get_user(db, call.from_user)
    coins = user['coins']
    level = user["modern"].get(part, 1)

    if level >= 10:
        bot.answer_callback_query(call.id, "🚫 Максимальный уровень!")
        return

    price = 100 * (2 ** (level - 1))
    shortage = price - user["coins"]

    if user["coins"] < price:
        bot.answer_callback_query(call.id, f"❌ Недостаточно монет! ({format_value(shortage)}$)")
        return

    user["coins"] -= price
    user["modern"][part] = level + 1
    save_db(db)

    bot.answer_callback_query(call.id, "✅ Успешно!")

    markup = types.InlineKeyboardMarkup()
    text = f"💰 <b>Баланс: {format_value(coins)}$</b>\n\n"

    for key, name in MODERN_PARTS.items():
        lvl = user["modern"].get(key, 1)

        if lvl >= 10:
            price_text = "MAX"
        else:
            next_price = 100 * (2 ** (lvl - 1))
            price_text = f"{format_value(next_price)}$"

        text += f"{name}: <b>Ур. {lvl}</b>\n"

        markup.add(types.InlineKeyboardButton(
            f"⬆ {name} ({price_text})",
            callback_data=f"modernupgrade|{owner_id}|{key}"
        ))

    text += f"\n🔻 <b>Выберите пункт:</b>"

    markup.add(types.InlineKeyboardButton(
        "🔙 Назад",
        callback_data=f"backmodern|{owner_id}"
    ))

    bot.edit_message_text(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("moderntuning|"))
def open_tuning(call):
    _, owner_id = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    user = get_user(db, call.from_user)
    coins = user["coins"]
    owned_count = len(user["tuning"]["owned"])
    total_count = len(TUNING_PARTS)

    markup = types.InlineKeyboardMarkup()

    text = f"💰 <b>Баланс: {format_value(coins)}$</b>\n\n"
    text += f"Доступно к применению: <b>{owned_count}/{total_count}</b>\n\n"
    text += "🔻 <b>Выберите деталь:</b>"

    value = ""

    for part, name in TUNING_PARTS.items():
        equipped = user["tuning"]["equipped"].get(part)
        owned = part in user["tuning"]["owned"]

        if equipped:
            emoji = "✅"
        elif owned:
            emoji = "🟢"
        else:
            emoji = "🔒"
            price = TUNING_PRICES[part]
            value = f"({format_value(price)}$)"

        markup.add(types.InlineKeyboardButton(
            f"{emoji} {name} {value}",
            callback_data=f"tuningpreview|{owner_id}|{part}"
        ))

    markup.add(types.InlineKeyboardButton(
        "🔙 Назад",
        callback_data=f"backmodern|{owner_id}"
    ))

    bot.edit_message_text(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("tuningpreview|"))
def tuning_preview(call):
    _, owner_id, part = call.data.split("|")
    
    if str(call.from_user.id) != owner_id:
        return bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")

    user = get_user(db, call.from_user)
    tuning_data = user.setdefault("tuning", {"owned": [], "equipped": {}})
    price = TUNING_PRICES[part]
    name = TUNING_PARTS[part]

    is_owned = part in tuning_data.get("owned", [])
    is_equipped = tuning_data.get("equipped", {}).get(part) is not None

    if is_equipped:
        action, status, btn_text = "remove", "надето", "✅ Снять"
    elif is_owned:
        action, status, btn_text = "equip", "снято", "✅ Надеть"
    else:
        action, status, btn_text = "buy", "отсутствует", "✅ Купить"

    original_equipped = tuning_data["equipped"].copy()
    
    if action in ["buy", "equip"]:
        tuning_data["equipped"][part] = f"{part}_1"
    else:
        tuning_data["equipped"].pop(part, None)

    preview_img = build_image(call.from_user.id)

    tuning_data["equipped"] = original_equipped

    bio = io.BytesIO()
    preview_img.save(bio, "PNG")
    bio.seek(0)

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            f"{btn_text}",
            callback_data=f"applytuning|{owner_id}|{part}|{action}",
            style="success"
        ),
        types.InlineKeyboardButton(
            "❌ Отменить",
            callback_data=f"canceltuning|{owner_id}",
            style="danger"
        )
    )

    caption = f"🛠️ <b>Предпросмотр тюнинга</b>\n\n"
    caption += f"Деталь: <b>«{name}»</b>\n"
    caption += f"Статус: <b>{status}</b>"

    if action == "buy":
        caption += f"\n\n💰 <b>Стоимость: {format_value(price)}$</b>"
    elif action == "equip":
        caption += "\n\n✅ <b>Надеть деталь?</b>"
    else:
        caption += "\n\n❌ <b>Снять деталь?</b>"

    sent = bot.send_photo(
        call.message.chat.id,
        bio,
        caption=caption,
        parse_mode="HTML",
        reply_markup=markup,
        reply_to_message_id=call.message.message_id
    )

    user_preview_state[call.from_user.id] = {
        "type": "tuning",
        "part": part,
        "action": action,
        "price": price,
        "message_id": sent.message_id,
        "chat_id": call.message.chat.id,
    }

    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("applytuning|"))
def tuning_apply(call):
    _, owner_id, part, action = call.data.split("|")
    
    if str(call.from_user.id) != owner_id:
        return bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")

    preview = user_preview_state.get(call.from_user.id)
    if not preview or preview.get("message_id") != call.message.message_id:
        return bot.answer_callback_query(call.id, "⛔ Сессия истекла.")

    user = get_user(db, call.from_user)
    tuning = user["tuning"]
    price = preview["price"]
    name = TUNING_PARTS[part]
    shortage = price - user["coins"]

    if action == "buy":
        if user["coins"] < price:
            bot.answer_callback_query(call.id, f"❌ Недостаточно монет! ({format_value(shortage)}$)")
            return
        
        user["coins"] -= preview["price"]
        if part not in tuning["owned"]:
            tuning["owned"].append(part)
        tuning["equipped"][part] = f"{part}_1"

    elif action == "equip":
        tuning["equipped"][part] = f"{part}_1"

    elif action == "remove":
        tuning["equipped"].pop(part, None)

    save_db(db)

    bot.answer_callback_query(call.id, "✅ Успешно!")

    if action == "buy":
        caption = (
        f"✅ <b>Тюнинг «{name}» успешно куплен!</b>"
        )
        caption += f"\n💰 <b>Списано: {format_value(price)}$</b>"
    elif action == "equip":
        caption = (
        f"✅ <b>Тюнинг «{name}» успешно применен!</b>"
        )
    else:
        caption = (
        f"✅ <b>Тюнинг «{name}» успешно снят!</b>"
        )

    bot.edit_message_caption(
        caption=caption,
        chat_id=preview["chat_id"],
        message_id=preview["message_id"],
        parse_mode="HTML",
        reply_markup=None
    )

    user_preview_state.pop(call.from_user.id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("canceltuning|"))
def cancel_tuning(call):
    _, owner_id = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    bot.answer_callback_query(call.id, "❌ Отменено.")

    bot.edit_message_caption(
        "❌ <b>Выбор детали отменён.</b>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=None
    )

    user_preview_state.pop(call.from_user.id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("modernplate|"))
def plate_menu(call):
    _, owner_id = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    user = get_user(db, call.from_user)
    current = user["tuning"].get("plate_text") or "отсутствует"
    coins = user["coins"]
    standard = PLATE_STANDARD_PRICE
    elite = PLATE_ELITE_PRICE

    markup = types.InlineKeyboardMarkup()
    
    text = f"💰 <b>Баланс: {format_value(coins)}$</b>\n\n"
    text += f"Текущий номер: <b>{current}</b>\n\n"
    text += f"🔻 <b>Выберите тип:</b>"
    
    markup.add(types.InlineKeyboardButton(
        f"💳 Стандартный ({format_value(standard)}$)",
        callback_data=f"platecreate|{owner_id}|cheap"
    ))

    markup.add(types.InlineKeyboardButton(
        f"💎 Элитный ({format_value(elite)}$)",
        callback_data=f"platecreate|{owner_id}|premium"
    ))

    markup.add(types.InlineKeyboardButton(
        "🔙 Назад",
        callback_data=f"backmodern|{owner_id}"
    ))

    bot.edit_message_text(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("platecreate|"))
def plate_create(call):
    _, owner_id, plate_type = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    user_plate_state[call.from_user.id] = plate_type
    standard = PLATE_STANDARD_PRICE
    elite = PLATE_ELITE_PRICE

    if plate_type == "cheap":
        description = (
            "💳 <b>Стандартный номер</b>\n"
            "Упрощённый семизначный формат Калифорнии\n"
            "Пример: <code>1ABC123</code>\n\n"
            f"💰 <b>Стоимость: {format_value(standard)}$</b>"
        )
    else:
        description = (
            "💎 <b>Элитный номер</b>\n"
            "Свободный формат (минимум одна цифра)\n"
            "Пример: <code>REDL1NE</code>\n\n"
            f"💰 <b>Стоимость: {format_value(elite)}$</b>"
        )

    bot.edit_message_text(
        f"✏️ <b>Введите номер ответом на сообщение бота (до 7 символов, латиница и цифры):</b>\n\n"
        f"{description}",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML"
    )

    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: m.from_user.id in user_plate_state)
def handle_plate_text(message):
    user_id = message.from_user.id
    state = user_plate_state.get(user_id)

    if isinstance(state, str):
        plate_type = state
        required_msg_id = None 
    else:
        plate_type = state.get("type")
        required_msg_id = state.get("message_id")

    if not message.reply_to_message:
        return

    if required_msg_id and message.reply_to_message.message_id != required_msg_id:
        return
    
    user = get_user(db, user_id)
    text = message.text.upper().strip()

    visual_type = "элитный" if plate_type == "premium" else "стандартный"

    def reset_with_error(text_error):
        bot.reply_to(message, text_error, parse_mode="HTML")
        user_plate_state.pop(user_id, None)

    if not (2 <= len(text) <= 7):
        return reset_with_error("❌ <b>Номер должен быть от 2 до 7 символов.</b>")

    current_plate = (user.get("tuning", {}).get("plate_text") or "").upper()

    if text == current_plate:
        return reset_with_error(f"⚠️ <b>Идентификатор [{text}] уже установлен!</b>")

    if plate_type == "cheap":
        if not validate_cheap_plate(text):
            return reset_with_error("❌ <b>Неверный формат!</b> Пример: <code>1ABC123</code>.")
        price = PLATE_STANDARD_PRICE
    else:
        if not re.match(r"^(?=.*[A-Z])(?=.*\d)[A-Z0-9]+$", text):
            return reset_with_error("❌ <b>Неверный формат! Элитный номер должен содержать минимум одну букву и одну цифру.</b>")
        price = PLATE_ELITE_PRICE

    if is_plate_taken_anywhere(text, user_id):
        return reset_with_error("❌ <b>Данный номер уже занят или выставлен на аукцион!</b>")

    tuning = user.setdefault("tuning", {})
    equipped = tuning.setdefault("equipped", {})

    if "plate" not in equipped:
        equipped["plate"] = "plate_1"

    user = get_user(db, user_id)

    preview_img = build_image(user_id, plate_override=text)

    bio = io.BytesIO()
    preview_img.save(bio, "PNG")
    bio.seek(0)

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "✅ Купить",
            callback_data=f"applyplate|{user_id}"
        ),
        types.InlineKeyboardButton(
            "❌ Отменить",
            callback_data=f"cancelplate|{user_id}"
        )
    )

    caption = (
        "🔢 <b>Предпросмотр номера</b>\n\n"
        f"Идентификатор: <b>{text}</b>\n"
        f"Тип: <b>{visual_type}</b>\n\n"
        f"💰 <b>Стоимость: {format_value(price)}$</b>"
    )

    sent = bot.send_photo(
        message.chat.id,
        bio,
        caption=caption,
        parse_mode="HTML",
        reply_markup=markup,
        reply_to_message_id=message.message_id
    )

    user_preview_state[user_id] = {
        "type": "plate",
        "plate_text": text,
        "plate_type": plate_type,
        "price": price,
        "message_id": sent.message_id
    }

    user_plate_state.pop(user_id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("applyplate|"))
def plate_apply(call):
    owner_id = int(call.data.split("|")[1])

    if call.from_user.id != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    preview = user_preview_state.get(owner_id)
    if not preview or preview.get("type") != "plate":
        bot.answer_callback_query(call.id, "⛔ Сессия истекла.")
        return

    user = get_user(db, owner_id)
    price = preview["price"]
    plate_text = preview["plate_text"]
    plate_type = preview["plate_type"]

    visual_type = ""

    if plate_type == "premium":
            visual_type += "Элитный"
    else:
            visual_type += "Стандартный"
    shortage = price - user["coins"]

    if user["coins"] < price:
        bot.answer_callback_query(call.id, f"❌ Недостаточно монет! ({format_value(shortage)}$)")
        return

    user["coins"] -= price
    user["tuning"]["plate_text"] = plate_text
    user["tuning"]["plate_type"] = plate_type
    
    tuning = user.get("tuning", {})
    equipped = tuning.setdefault("equipped", {})

    if "plate" not in equipped:
        equipped["plate"] = "plate_1"

    save_db(db)

    bot.answer_callback_query(call.id, "✅ Успешно!")

    caption = (
    f"✅ <b>{visual_type} номер [{plate_text}] успешно куплен!</b>"
    )
    caption += f"\n💰 <b>Списано: {format_value(price)}$</b>"

    bot.edit_message_caption(
        caption=caption,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=None
    )

    user_preview_state.pop(owner_id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("cancelplate|"))
def plate_cancel(call):
    owner_id = int(call.data.split("|")[1])

    if call.from_user.id != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    bot.answer_callback_query(call.id, "❌ Отменено.")
    
    bot.edit_message_caption(
        caption="❌ <b>Выбор идентификатора отменён.</b>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=None
    )

    user_preview_state.pop(owner_id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith("backmodern|"))
def back_modern(call):
    _, owner_id = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    markup = types.InlineKeyboardMarkup()

    markup.add(types.InlineKeyboardButton(
        "📈 Характеристики",
        callback_data=f"modernstats|{owner_id}"
    ))

    markup.add(types.InlineKeyboardButton(
        "🛠️ Тюнинг",
        callback_data=f"moderntuning|{owner_id}"
    ))

    user = get_user(db, call.from_user)

    if "plate" in user["tuning"]["owned"]:
        markup.add(types.InlineKeyboardButton(
            "🔢 Номер",
            callback_data=f"modernplate|{owner_id}"
        ))

    bot.edit_message_text(
        "⚙️ <b>Модернизация автомобиля</b>\n\n<i>Выберите раздел:</i>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=markup
    )

    bot.answer_callback_query(call.id)

def update_farm(user):
    now = int(time.time())

    if user['ticket']['last_ticket'] == 0:
        user['ticket']['last_ticket'] = now
        save_db(db)
        return

    elapsed = now - user['ticket']['last_ticket']
    new_tickets = elapsed // TICKET_COOLDOWN

    if new_tickets > 0:
        current_stored = user["ticket"]["stored"]
        can_add = MAX_STORED_TICKETS - current_stored
        
        added = min(can_add, new_tickets)
        user["ticket"]["stored"] += added

        user['ticket']['last_ticket'] += new_tickets * TICKET_COOLDOWN
        save_db(db)

def get_farm_text(user):
    now = int(time.time())
    stored = user["ticket"]["stored"]
    tickets = user["tickets"]
    coins = user["coins"]

    if stored < MAX_STORED_TICKETS:
        time_passed_farm = now - user['ticket']['last_ticket']
        time_to_next_farm = TICKET_COOLDOWN - (time_passed_farm % TICKET_COOLDOWN)
        farm_info = f"⏳ Следующий билет: <b>через {format_time_left(time_to_next_farm)}</b>"
    else:
        farm_info = "✅ <b>Сейф заполнен!</b>"

    last_buy = user["ticket"].get("last_buy", 0)
    time_passed_buy = now - last_buy
    
    if time_passed_buy < BUY_COOLDOWN:
        time_left_buy = BUY_COOLDOWN - time_passed_buy
        buy_info = f"🛒 Следующая покупка: <b>через {format_time_left(time_left_buy)}</b>"
    else:
        buy_info = "✅ <b>Покупка доступна!</b>"

    return (
        f"🏦 <b>В сейфе: {stored}/{MAX_STORED_TICKETS}</b>\n\n"
        f"💰 <b>Баланс: {format_value(coins)}$</b>\n"
        f"🎟️ <b>Билеты: {format_value(tickets)}</b>\n"
        f"{farm_info}\n"
        f"{buy_info}\n\n"
        f"<blockquote><i>ℹ️ Билеты копятся автоматически - 1 билет в 10 минут. Надоело ждать? Попробуйте купить «золотой билет», если появилось желание продолжить игру прямо сейчас.</i></blockquote>"
    )

def get_farm_keyboard(user, owner_id):
    markup = types.InlineKeyboardMarkup()
    stored = user["ticket"]["stored"]

    if stored > 0:
        collect_text = f"✅ Забрать ({stored})"
        btn_style = "success"
    else:
        collect_text = "❌ Забрать"
        btn_style = "danger"

    markup.add(
        types.InlineKeyboardButton(
            text=collect_text, 
            callback_data=f"farm_collect|{owner_id}",
            style=btn_style
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            text=f"🎫 Купить билет ({TICKET_PRICE}$)", 
            callback_data=f"farm_buy|{owner_id}"
        )
    )

    return markup

@bot.message_handler(commands=['get'])
def ticket_command(message):
    owner_id = message.from_user.id
    user = get_user(db, owner_id)
    update_farm(user)
    
    bot.send_message(
        message.chat.id, 
        get_farm_text(user), 
        reply_markup=get_farm_keyboard(user, owner_id), 
        parse_mode="HTML",
        reply_to_message_id=message.message_id
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('farm_'))
def farm_callback(call):
    action, owner_id = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    user = get_user(db, call.from_user)
    now = int(time.time())
    
    if action == "farm_collect":
        update_farm(user)
        stored = user["ticket"]["stored"]
        
        if stored <= 0:
            bot.answer_callback_query(call.id, "❌ Отсутствуют накопленные билеты!")
        else:
            user["tickets"] += stored
            user["ticket"]["stored"] = 0
            save_db(db)
            
            bot.answer_callback_query(call.id, f"✅ Вы забрали {stored} билетов!")
            
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=get_farm_text(user),
                reply_markup=get_farm_keyboard(user, owner_id),
                parse_mode="HTML"
            )

    elif action == "farm_buy":
        last_buy = user["ticket"].get("last_buy", 0)
        time_passed = now - last_buy
        price = TICKET_PRICE
        
        if time_passed < BUY_COOLDOWN:
            left = BUY_COOLDOWN - time_passed
            bot.answer_callback_query(call.id, f"⏳ Купить можно через: {format_time_left(left)}")
        elif user["coins"] < TICKET_PRICE:
                shortage = price - user["coins"]
                bot.answer_callback_query(call.id, f"❌ Недостаточно монет! ({format_value(shortage)}$)")
        else:
            user["coins"] -= TICKET_PRICE
            user["tickets"] += 1
            user["ticket"]["last_buy"] = now
            save_db(db)
            
            bot.answer_callback_query(call.id, "✅ Успешно!")
            
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=get_farm_text(user),
                reply_markup=get_farm_keyboard(user, owner_id),
                parse_mode="HTML"
            )

    elif action == "farm_refresh":
        update_farm(user)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=get_farm_text(user),
            reply_markup=get_farm_keyboard(user, owner_id),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id, "🔄 Данные обновлены...")

def get_balanced_chances(streak):
    effective_streak = min(streak, 90)
    bonus = (effective_streak // 10) * 0.02
    
    chances = [0.50, 0.25, 0.15, 0.08, 0.02 + bonus]
    total = sum(chances)
    return [c / total for c in chances]

@bot.message_handler(commands=['daily'])
def daily_command(message):
    user = get_user(db, message.from_user.id)
    now = time.time()
    one_day = DAILY_COOLDOWN
    
    last_time = user['daily']['last_daily']
    streak = user['daily']['daily_streak']
    chances = get_balanced_chances(streak)

    p_coins   = chances[0] * 100
    p_tickets = chances[1] * 100
    p_cups    = chances[2] * 100
    p_tokens  = chances[3] * 100
    p_jackpot = chances[4] * 100

    if now - last_time < one_day:
        remaining_seconds = int((last_time + one_day) - now)
        time_text = format_time_left(remaining_seconds)

        text = (
            f"🕒 <b>Вы уже забирали награду сегодня!</b>\n\n"
            f"Новая награда: <b>через {time_text}</b>\n\n"
            f"✨ <b>Текущая серия: {streak} {plural(streak, ['день', 'дня', 'дней'])}. Помните - чем больше серия, тем больше шанс на джекпот!</b>"
        )
        bot.reply_to(message, text, parse_mode='HTML')
        return

    session_id = int(now)
    user['daily']['daily_session'] = session_id
    save_db(db)

    markup = types.InlineKeyboardMarkup()
    callback_data = f"claim_daily|{message.from_user.id}|{session_id}"
    
    btns = [types.InlineKeyboardButton(text="🎁", callback_data=callback_data, style="primary") for _ in range(3)]
    markup.add(*btns)

    text = (
        f"✨ <b>Текущая серия: {streak} {plural(streak, ['день', 'дня', 'дней'])}!</b>\n\n"
        "<b>📊 Актуальные шансы:</b>\n"
        f"<blockquote>• Монеты: <b>{p_coins:.1f}%</b>\n"
        f"• Билеты: <b>{p_tickets:.1f}%</b>\n"
        f"• Кубки: <b>{p_cups:.1f}%</b>\n"
        f"• Токены: <b>{p_tokens:.1f}%</b>\n"
        f"• Джекпот: <b>{p_jackpot:.1f}%</b></blockquote>\n\n"
        f"<i>Выбери одну из коробок ниже:</i>"
    )

    bot.reply_to(
        message,
        text,
        parse_mode='HTML', 
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("claim_daily"))
def claim_callback(call):
    _, owner_id, session_id = call.data.split("|")

    if str(call.from_user.id) != owner_id:
        bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")
        return

    user = get_user(db, call.from_user)

    if user['daily']['daily_session'] != int(session_id):
        bot.answer_callback_query(call.id, "❌ Эти кнопки устарели! Введи /daily заново.")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        return

    now = time.time()
    last_time = user['daily']['last_daily']

    if now - last_time < DAILY_COOLDOWN:
        bot.answer_callback_query(call.id, "❌ Награда уже получена!")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        return
    
    skipd = DAILY_COOLDOWN * 2

    if now - last_time > skipd:
        streak = 1
    else:
        streak = user['daily']['daily_streak'] + 1

    chances = get_balanced_chances(streak)
    reward_type = random.choices(DAILY_REWARDS, weights=chances, k=1)[0]
    
    result_text = ""

    if reward_type["type"] == "jackpot":
        option = random.choice(["stripe", "tuning"])

        if option == "stripe":
            owned = user.get("owned_stripes", [])
            available = [s for s in ALLOWED_STRIPES if s not in owned]

            if available:
                new_item = random.choice(available)
                user.setdefault("owned_stripes", []).append(new_item)
                display_name = get_item_number(new_item)
                result_text = f"<b>ДЖЕКПОТ!</b>\nНовая полоса: <b>{display_name}</b>"
            else:
                option = "tuning"

        if option == "tuning":
            category = random.choice(TUNING_TYPES)
            new_item = f"{category}_1" 
            user.setdefault("tuning", {}).setdefault("owned", [])

            if new_item not in user["tuning"]["owned"]:
                user["tuning"]["owned"].append(new_item)
                display_name = TUNING_PARTS.get(category, category)
                result_text = f"<b>ДЖЕКПОТ!</b>\nНовая деталь тюнинга: <b>«{display_name}»</b>"
            else:
                amt = 5000
                user["coins"] = user.get("coins", 0) + amt
                result_text = f"<b>ДЖЕКПОТ!</b>\nМонеты: <b>{amt}$</b>"
    else:
        amt = random.randint(reward_type["min"], reward_type["max"])
        r_key = reward_type["type"]
        user[r_key] = user.get(r_key, 0) + amt

        if r_key == "cups":
            update_level(user)
        
        icons = {"coins": "💰", "tokens": "📀", "tickets": "🎟️", "cups": "🏆"}
        result_text = f"<b>+{amt}</b>{icons.get(r_key, '')}"

    user['daily']['daily_streak'] = streak
    user['daily']['last_daily'] = now
    user['daily']['daily_session'] = 0
    time_now = time.time()
    save_db(db)

    daily_interval = DAILY_COOLDOWN
    time_since_daily = time_now - now
    
    seconds_until_daily = daily_interval - (time_since_daily % daily_interval)
    daily_time = f"через {format_time_left(seconds_until_daily)}"
    
    final_text = (
        f"✅ <b>Награда получена!</b>\n\n"
        f"Награда: {result_text}\n\n"
        f"✨ <b>Текущая серия: {streak} {plural(streak, ['день', 'дня', 'дней'])}. Следующая награда будет готова {daily_time}...</b>"
    )

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=final_text,
        parse_mode='HTML'
    )

def plural(n, forms):
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    elif 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20):
        return forms[1]
    else:
        return forms[2]

def format_time_left(seconds):
    seconds = int(seconds) 
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours} {plural(hours, ['час', 'часа', 'часов'])} {minutes} {plural(minutes, ['минута', 'минуты', 'минут'])}"
    elif minutes > 0:
        return f"{minutes} {plural(minutes, ['минута', 'минуты', 'минут'])}"
    else:
        return f"{secs} {plural(secs, ['секунда', 'секунды', 'секунд'])}"

@bot.message_handler(commands=['leads'])
def rank_command(message):
    user = get_user(db, message.from_user.id)
    
    users_dict = db["users"]
    user_id_str = str(message.from_user.id)
    
    total_levels = sum(data.get('level', 1) for data in users_dict.values())
    total_cups = sum(data.get('cups', 0) for data in users_dict.values())
    total_players = len(users_dict)

    sorted_users = sorted(
        users_dict.items(), 
        key=lambda x: x[1].get('cups', 1), 
        reverse=True
    )

    text = "<b>🏆 Топ-5 игроков по уровню 🏆</b>\n\n"

    for i, (uid, data) in enumerate(sorted_users[:5], 1):
        raw_name = data.get("first_name", "Водитель")
        name = format_name(raw_name)
        user_level = data.get("level", 1)
        user_cups = data.get("cups", 0)

        if i == 1: emoji_rank = "🥇"
        elif i == 2: emoji_rank = "🥈"
        elif i == 3: emoji_rank = "🥉"
        else: emoji_rank = get_emoji_number(i)

        if uid == user_id_str:
            text += f"{emoji_rank} <u><b>{name}</b></u>\n└ 📶 <b>{user_level}</b> | 🏆 <b>{format_value(user_cups)}</b> | 🌟 <b>Вы</b>\n\n"
        else:
            text += f"{emoji_rank} <b>{name}</b>\n└ 📶 <b>{user_level}</b> | 🏆 <b>{format_value(user_cups)}</b>\n\n"

    user_rank = None
    user_data = None

    for i, (uid, data) in enumerate(sorted_users, 1):
        if uid == user_id_str:
            user_rank = i
            user_data = data
            break

    if user_rank:
        user_name = format_name(user_data.get("first_name", "Водитель"))
        u_level = user_data.get("level", 1)

        if user_rank == 1: current_emoji = "🥇"
        elif user_rank == 2: current_emoji = "🥈"
        elif user_rank == 3: current_emoji = "🥉"
        else: current_emoji = get_emoji_number(user_rank)

        u_cups = data.get("cups", 0)
        update_level(user)

        text += f"<b>Ваше место:</b>\n"
        text += f"{current_emoji} <b>{user_name}</b>\n└ 📶 <b>{u_level}</b> | 🏆 <b>{format_value(u_cups)}</b>\n\n"

        text += f"📊 <b>Глобальная статистика:</b>\n"
        text += f"<blockquote>• Всего игроков: <b>{format_value(total_players)}</b>\n"
        text += f"• Общий уровень: <b>{format_value(total_levels)}</b>\n"
        text += f"• Общее кол-во кубков: <b>{format_value(total_cups)}</b></blockquote>"
                
    bot.send_message(message.chat.id, text, parse_mode='HTML', reply_to_message_id=message.message_id)

def get_emoji_number(n):
    emoji_map = {
        '0': '0️⃣', '1': '1️⃣', '2': '2️⃣', '3': '3️⃣', '4': '4️⃣',
        '5': '5️⃣', '6': '6️⃣', '7': '7️⃣', '8': '8️⃣', '9': '9️⃣'
    }
    return ''.join(emoji_map[digit] for digit in str(n))

@bot.message_handler(commands=['race'])
def start_race_request(message):

    if not message.reply_to_message:
        return bot.reply_to(message, "🚫 <b>Команда должна быть ответом на сообщение игрока, которого вы хотите вызвать.</b>", parse_mode='HTML')

    player1_data = message.from_user
    player2_data = message.reply_to_message.from_user

    if player1_data.id == player2_data.id:
        return bot.reply_to(message, "🚫 <b>Вы не можете участвовать в гонке с самим собой.</b>", parse_mode='HTML')
    
    if player2_data.is_bot:
        return bot.reply_to(message, "🚫 <b>Вы не можете участвовать в гонке с ботом.</b>", parse_mode='HTML')

    p1 = get_user(db, player1_data)
    p2 = get_user(db, player2_data)
    save_db(db)

    if p1.get("tickets", 0) < 1:
        return bot.reply_to(message, "❌ <b>У вас недостаточно билетов для гонки.</b>", parse_mode='HTML')
    
    if p2.get("tickets", 0) < 1:
        return bot.reply_to(message, f"❌ <b>У игрока {player2_data.first_name} недостаточно билетов для гонки.</b>", parse_mode='HTML')

    markup = types.InlineKeyboardMarkup()
    
    markup.add(
        types.InlineKeyboardButton(
            "✅ Принять",
            callback_data=f"race_accept_{player1_data.id}_{player2_data.id}",
            style="success"
        ),
        types.InlineKeyboardButton(
            "❌ Отклонить",
            callback_data=f"race_decline_{player1_data.id}_{player2_data.id}",
            style="danger"
        )
    )

    bot.send_message(
        message.chat.id,
        f"🏁 <b>{player1_data.first_name}</b> вызывает на гонку <b>{player2_data.first_name}</b>!\n\n"
        f"<blockquote><i>ℹ️ Ставка равняется 1 билету и 1 кубку, двое игроков обязаны подходить под эти требования.</i></blockquote>",
        reply_markup=markup,
        parse_mode='HTML',
        reply_to_message_id=message.message_id
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("race_"))
def handle_race_buttons(call):
    data = call.data.split("_")
    action = data[1]
    p1_id = int(data[2])
    p2_id = int(data[3])

    if call.from_user.id != p2_id:
        return bot.answer_callback_query(call.id, "❌ Сторонний интерфейс!")

    p1 = get_user(db, p1_id)
    p2 = get_user(db, p2_id)

    p1_name = p1["first_name"]
    p2_name = call.from_user.first_name

    if action == "decline":
        bot.edit_message_text(
            f"🚫 <b>Игрок {p2_name} отклонил вызов игрока {p1_name}.</b>",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='HTML'
        )
        return

    if action == "accept":
        if p1["tickets"] < 1 or p2["tickets"] < 1:
            return bot.edit_message_text(
                "‼️ <b>Гонка отменена: у одного из игроков закончились билеты.</b>", 
                call.message.chat.id, 
                call.message.message_id,
                parse_mode='HTML'
            )

        p1["tickets"] -= 1
        p2["tickets"] -= 1
        save_db(db)

        stages = ["🔴 На старт...", "🟡 Внимание...", "🟢 Марш!"]
        for stage in stages:
            try:
                bot.edit_message_text(f"<b>{stage}</b>", call.message.chat.id, call.message.message_id, parse_mode='HTML')
                time.sleep(1)
            except:
                pass

        p1_mod_sum = sum(p1["modern"].values())
        p2_mod_sum = sum(p2["modern"].values())
        chance_diff = (p1_mod_sum - p2_mod_sum) * 0.5
        p1_win_chance = max(10, min(90, 50 + chance_diff))

        if random.random() * 100 < p1_win_chance:
            winner, loser = p1, p2
            w_name, l_name = p1_name, p2_name
        else:
            winner, loser = p2, p1
            w_name, l_name = p2_name, p1_name

        win_coins = random.randint(100, 250)
        winner["cups"] += 1
        winner["coins"] += win_coins
        
        loser["cups"] = max(0, loser["cups"] - 1)

        w_up = update_level(winner)
        l_down = update_level(loser)
        save_db(db)

        skill_gap = abs(p1_mod_sum - p2_mod_sum)
        if skill_gap < 5:
            race_style = "Шампанское уже на финише! Это была битва бампер к бамперу."
        elif skill_gap < 15:
            race_style = "Отличный заезд! Техника и мастерство решили исход этой встречи."
        else:
            race_style = "Доминирующее превосходство! Разрыв в умениях был слишком велик."

        result_text = (
            f"🏁 <b>Финиш! Дым от покрышек постепенно рассеивается...</b>\n"
            f"<i>{race_style}</i>\n\n"
            f"🥇 <b>Итоги для победителя:</b>\n"
            f"├ Имя: <b>{w_name}</b>\n"
            f"├ Награда: <b>+{format_value(win_coins)}</b> 💰\n"
            f"└ Рейтинг: <b>+1</b> 🏆\n\n"
            f"🥈 <b>Итоги для проигравшего:</b>\n"
            f"├ Имя: <b>{l_name}</b>\n"
            f"└ Рейтинг: <b>-1</b> 🏆\n"
        )
        
        if w_up: 
            result_text += f"\n🆙 <b>{w_name} повысил свой уровень!</b>"
        
        bot.edit_message_text(result_text, call.message.chat.id, call.message.message_id, parse_mode='HTML')

def update_level(user):
    current_cups = user["cups"]
    calculated_level = current_cups // 5 + 1
    
    if calculated_level != user["level"]:
        user["level"] = calculated_level
        return True
    
    return False

commands = [
    types.BotCommand('car', 'Просмотреть автомобиль'),
    types.BotCommand('race', 'Начать состязание'),
    types.BotCommand('leads', 'Просмотреть список лидеров'),
    types.BotCommand('custom', 'Кастомизировать автомобиль'),
    types.BotCommand('modern', 'Модернизировать автомобиль'),
    types.BotCommand('get', 'Забрать билеты'),
    types.BotCommand('daily', 'Забрать ежедневную награду'),
    types.BotCommand('auc', 'Просмотреть аукцион'),
]

bot.set_my_commands(commands)

load_layers()

if __name__ == '__main__':
    print("Запуск...")
    bot.infinity_polling(skip_pending=True)