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

from config import BOT_TOKEN, CHANNEL_URL

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

bot = telebot.TeleBot(BOT_TOKEN)

commands_list = ['start', 'help', 'car', 'info', 'trade', 'sell', 'auc', 'custom', 'modern', 'get', 'daily', 'leads', 'race']

@bot.message_handler(commands=commands_list)
def start(message):
    text = (
        "🚧 <b>В настоящее время проводятся технические работы, в связи с чем бот временно недоступен. Вся актуальная информация публикуется в официальном новостном канале:</b>"
    )

    keyboard = types.InlineKeyboardMarkup()
    channel = types.InlineKeyboardButton(
        text="📢 Наш канал",
        url=CHANNEL_URL  
    )
    keyboard.add(channel)

    bot.send_message(message.chat.id, text, reply_markup=keyboard, parse_mode="HTML", reply_to_message_id=message.message_id)

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

print("Запуск...")
bot.infinity_polling()