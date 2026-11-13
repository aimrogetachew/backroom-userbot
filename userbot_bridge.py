import os
import asyncio
import aiohttp
from aiohttp import web
from urllib.parse import urlparse, parse_qs
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ============================================================================
# 1. CONFIGURATION (Read from Render Environment Variables)
# ============================================================================
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

# Your Cloudflare Worker URL
WORKER_URL = os.environ.get("WORKER_URL", "https://autumn-violet-afc2.aimrogetachew771.workers.dev")
USERBOT_SECRET = os.environ.get("USERBOT_SECRET", "backroom_secret_2026")
TARGET_DESTINATION = os.environ.get("TARGET_DESTINATION", "@YourBotUsername")
SOURCE_BOT = "fileserverxrobot"

# Port assigned by Render (defaults to 10000)
PORT = int(os.environ.get("PORT", 10000))

# Initialize Telethon Client using StringSession
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

def parse_target(dest):
    if str(dest).lstrip("-").isdigit():
        return int(dest)
    return dest

# ============================================================================
# 2. AUTO-FORWARD FILES SENT BY @fileserverxrobot
# ============================================================================
@client.on(events.NewMessage(incoming=True))
async def on_incoming_file(event):
    sender = await event.get_sender()
    if not getattr(sender, "bot", False):
        return
    if not (event.video or event.document):
        return

    file_size = event.file.size if event.file else 0
    if file_size < 5 * 1024 * 1024:  # Ignore small files / stickers (< 5MB)
        return

    dest = parse_target(TARGET_DESTINATION)
    try:
        await client.send_file(
            dest,
            file=event.message.media,
            caption=event.message.text or ""
        )
        print(f"✅ Forwarded {file_size // (1024*1024)} MB to {dest}")
    except Exception as e:
        print(f"❌ Forward error: {e}")

# ============================================================================
# 3. AUTOMATE BOT INTERACTION (Search & Click Buttons)
# ============================================================================
async def pull_movie_from_fileserver(query: str, max_files: int = 15) -> int:
    print(f"🔎 Searching @{SOURCE_BOT} for: {query}")
    clicked = 0

    try:
        async with client.conversation(SOURCE_BOT, timeout=45) as conv:
            await conv.send_message(query)
            await asyncio.sleep(2)
            resp = await conv.get_response()

            if not resp or not resp.buttons:
                print("⚠️ No buttons found in bot response.")
                return 0

            for row in resp.buttons:
                for btn in row:
                    if clicked >= max_files:
                        break
                    btn_text = btn.text or ""

                    # Skip non-media buttons
                    if any(skip in btn_text.lower() for skip in ["join", "channel", "help", "close", "donate"]):
                        continue

                    # Deep-link start button (e.g. t.me/bot?start=...)
                    if getattr(btn, "url", None) and "t.me/" in btn.url and "start=" in btn.url:
                        parsed_url = urlparse(btn.url)
                        bot_uname = parsed_url.path.strip("/")
                        start_param = parse_qs(parsed_url.query).get("start", [""])[0]
                        if bot_uname and start_param:
                            print(f"🔗 Triggering file link on @{bot_uname}")
                            await client.send_message(bot_uname, f"/start {start_param}")
                            clicked += 1
                            await asyncio.sleep(2)

                    # Inline callback buttons
                    elif getattr(btn, "data", None):
                        if any(nav in btn_text.lower() for nav in ["next", "back", "page"]):
                            continue
                        print(f"🔘 Clicking button: {btn_text}")
                        try:
                            await btn.click()
                            clicked += 1
                            await asyncio.sleep(2)
                        except Exception as err:
                            print(f"⚠️ Button error: {err}")
    except Exception as e:
        print(f"⚠️ Conversation error: {e}")

    return clicked

# ============================================================================
# 4. MANUAL TRIGGER: Type .get <Movie Name> in Saved Messages
# ============================================================================
@client.on(events.NewMessage(outgoing=True, pattern=r"^\.get\s+(.+)"))
async def manual_get_command(event):
    query = event.pattern_match.group(1).strip()
    await event.edit(f"⏳ Pulling <b>{query}</b> from @{SOURCE_BOT}...", parse_mode="html")
    count = await pull_movie_from_fileserver(query)
    await event.edit(f"✅ Triggered <b>{count}</b> file actions for <b>{query}</b>!", parse_mode="html")

# ============================================================================
# 5. BACKGROUND WORKER: Poll Cloudflare Worker Queue
# ============================================================================
async def poll_cloudflare_worker():
    headers = {"x-userbot-secret": USERBOT_SECRET}
    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            try:
                async with session.get(f"{WORKER_URL}/api/userbot/jobs", timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for job in data.get("jobs", []):
                            job_id = job["id"]
                            query = job["query"]
                            count = await pull_movie_from_fileserver(query)

                            await session.post(f"{WORKER_URL}/api/userbot/jobs", json={
                                "action": "complete",
                                "id": job_id,
                                "status": "done" if count > 0 else "not_found",
                                "found_count": count,
                                "chat_id": job.get("chat_id"),
                                "notify_text": f"✅ <b>Finished:</b> <code>{query}</code> ({count} actions triggered)."
                            })
            except Exception as e:
                pass
            await asyncio.sleep(6)

# ============================================================================
# 6. RENDER HEALTH CHECK SERVER (Port 10000)
# ============================================================================
async def handle_ping(request):
    return web.json_response({
        "status": "online",
        "service": "The Backroom Userbot Bridge",
        "worker_connected": WORKER_URL
    })

async def start_health_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🌐 HTTP Health Check Server running on port {PORT}")

# ============================================================================
# 7. MAIN ENTRYPOINT
# ============================================================================
async def main():
    # Start web server for Render
    await start_health_server()

    # Start Telegram client
    await client.start()
    print("🚀 Telethon Userbot is running and connected!")
    print(f"📡 Forwarding files from @{SOURCE_BOT} -> {TARGET_DESTINATION}")

    # Start polling Cloudflare Worker in background
    asyncio.create_task(poll_cloudflare_worker())

    # Keep the userbot active
    await client.run_until_disconnected()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
