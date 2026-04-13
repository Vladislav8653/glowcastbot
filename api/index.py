import os
import io
import struct
import asyncio
from flask import Flask, request
import paho.mqtt.client as mqtt
from PIL import Image, ImageDraw, ImageFont
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from dotenv import load_dotenv

load_dotenv()

# --- КОНФИГУРАЦИЯ ---
MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT = int(os.getenv("MQTT_PORT", 8883))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")
MQTT_TOPIC = os.getenv("MQTT_TOPIC")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

WIDTH, HEIGHT = 160, 128

app = Flask(__name__)

# Инициализируем приложение Telegram (без запуска!)
tg_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

def convert_to_rgb565(img):
    img = img.convert("RGB")
    img.thumbnail((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    background = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    offset = ((WIDTH - img.width) // 2, (HEIGHT - img.height) // 2)
    background.paste(img, offset)
    pixels = background.load()
    output = bytearray()
    for y in range(HEIGHT):
        for x in range(WIDTH):
            r, g, b = pixels[x, y]
            rgb = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            output.extend(struct.pack("<H", rgb))
    return output

def create_emoji_image(emoji_text):
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    # На Vercel путь к файлу будет относительным корня проекта
    font_path = os.path.join(os.getcwd(), "arial.ttf")
    try:
        font = ImageFont.truetype(font_path, 80)
    except:
        font = ImageFont.load_default()
    left, top, right, bottom = draw.textbbox((0, 0), emoji_text, font=font)
    draw.text(((WIDTH - (right - left)) / 2, (HEIGHT - (bottom - top)) / 2),
              emoji_text, font=font, embedded_color=True)
    return img

async def send_to_mqtt(raw_data):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.tls_set()
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.publish(MQTT_TOPIC, raw_data, qos=1)
    # В serverless важно дождаться отправки перед дисконнектом
    client.loop_start()
    await asyncio.sleep(1)
    client.loop_stop()
    client.disconnect()

async def handle_update(update: Update):
    raw_data = None
    msg = update.message
    if not msg: return

    if msg.photo or msg.sticker or msg.animation:
        if msg.photo:
            file = await msg.photo[-1].get_file()
        elif msg.sticker:
            if msg.sticker.is_animated:
                await msg.reply_text("Анимированные стикеры не поддерживаются.")
                return
            file = await msg.sticker.get_file()
        else:
            file = await msg.animation.get_file()

        img_bytes = await file.download_as_bytearray()
        img = Image.open(io.BytesIO(img_bytes))
        if getattr(img, "is_animated", False):
            img.seek(0)
        raw_data = convert_to_rgb565(img)

    elif msg.text:
        img = create_emoji_image(msg.text)
        raw_data = convert_to_rgb565(img)

    if raw_data:
        await send_to_mqtt(raw_data)
        await msg.reply_text("✅ Доставлено на ТВ!")

# Flask route для приема вебхуков
@app.route('/', methods=['POST'])
async def webhook():
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), tg_app.bot)
        await handle_update(update)
        return "OK", 200
    return "Method Not Allowed", 405

@app.route('/health', methods=['GET'])
def health():
    return "Alive", 200