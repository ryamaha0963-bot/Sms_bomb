import os
import json
import asyncio
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import aiohttp
import async_timeout

load_dotenv()

# --- Configuration ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USERS = [int(id) for id in os.getenv("ALLOWED_USERS", "").split(",") if id]  # optional
SERVICES_FILE = "services.json"
REQUEST_TIMEOUT = 10  # seconds per service
MAX_CONCURRENT = 50   # number of simultaneous requests

# --- Load services ---
with open(SERVICES_FILE, "r", encoding="utf-8") as f:
    services = json.load(f)  # expects a list of dicts

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- Helper: send one request ---
async def send_sms(session, service, phone):
    url = service["url"]
    method = service.get("method", "POST").upper()
    headers = service.get("headers", {})
    # Replace {phone} placeholder in the data_template
    data_str = service["data_template"].format(phone=phone)
    try:
        async with async_timeout.timeout(REQUEST_TIMEOUT):
            if method == "GET":
                resp = await session.get(url, headers=headers, params=json.loads(data_str))
            else:
                resp = await session.request(method, url, headers=headers, json=json.loads(data_str))
            status = resp.status
            await resp.text()  # read body to free connection
            return service["name"], status, None
    except asyncio.TimeoutError:
        return service["name"], None, "Timeout"
    except Exception as e:
        return service["name"], None, str(e)

# --- Main sending function ---
async def trigger_sms(phone):
    results = []
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [send_sms(session, svc, phone) for svc in services]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    # Process results
    success = [r for r in results if r is not None and isinstance(r, tuple) and r[1] and 200 <= r[1] < 300]
    failed = [r for r in results if not (isinstance(r, tuple) and r[1] and 200 <= r[1] < 300)]
    return success, failed

# --- Telegram command handler ---
async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if ALLOWED_USERS and user.id not in ALLOWED_USERS:
        await update.message.reply_text("Sorry, you are not authorised.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /send <10-digit phone number>")
        return
    phone = context.args[0]
    if not phone.isdigit() or len(phone) != 10:
        await update.message.reply_text("Please provide a valid 10-digit phone number.")
        return

    status_msg = await update.message.reply_text(f"⏳ Sending OTPs to +91{phone} ... (may take a while)")
    try:
        success, failed = await trigger_sms(phone)
        total = len(success) + len(failed)
        reply = f"✅ Done! Sent to {len(success)} out of {total} services.\n"
        if failed:
            reply += f"❌ Failed: {len(failed)} services (timeouts/errors).\n"
            # Show first 5 failures for brevity
            sample = [f"{f[0]}: {f[2]}" for f in failed[:5] if isinstance(f, tuple)]
            if sample:
                reply += "Sample failures:\n" + "\n".join(sample)
        await status_msg.edit_text(reply)
    except Exception as e:
        await status_msg.edit_text(f"⚠️ Error: {e}")

# --- Start bot ---
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("send", send_command))
    app.run_polling()

if __name__ == "__main__":
    main()
