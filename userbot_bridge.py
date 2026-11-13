import os
import re
import asyncio
from aiohttp import web
import aiohttp
from urllib.parse import urlparse, parse_qs

# Explicit loop for Python 3.10+ / 3.12+ compatibility on Render
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

# ============================================================================
# 1. CONFIGURATION
# ============================================================================
API_ID = int(os.environ.get("API_ID", "7598862"))
API_HASH = os.environ.get("API_HASH", "fea8fb2a9cb4d31ff6f53e2d499801ee")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

WORKER_URL = os.environ.get("WORKER_URL", "https://autumn-violet-afc2.aimrogetachew771.workers.dev")
USERBOT_SECRET = os.environ.get("USERBOT_SECRET", "backroom_secret_2026")
TARGET_DESTINATION = os.environ.get("TARGET_DESTINATION", "@Thebackroom_1")
SOURCE_BOT = "fileserverxrobot"
PORT = int(os.environ.get("PORT", 10000))

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)

def parse_target(dest):
    if str(dest).lstrip("-").isdigit():
        return int(dest)
    return dest

# State trackers to prevent duplicate clicks
PROCESSING_MSGS = set()
CLICKED_EPISODES = set()

# ============================================================================
# 2. AUTO-JOIN CHANNELS (Bypass Force-Subscribe Locks)
# ============================================================================
async def auto_join_channel(url: str):
    try:
        if "+" in url or "joinchat" in url:
            invite_hash = url.split("+")[-1].split("/")[-1].strip()
            await client(ImportChatInviteRequest(invite_hash))
            print(f"✅ Auto-joined private channel: {url}")
        else:
            uname = url.split("t.me/")[-1].split("/")[0].split("?")[0].strip()
            if uname:
                await client(JoinChannelRequest(uname))
                print(f"✅ Auto-joined public channel: @{uname}")
        return True
    except Exception as e:
        print(f"⚠️ Channel join error: {e}")
        return False

# ============================================================================
# 3. AUTONOMOUS STEP-BY-STEP NAVIGATOR (Handles New & Edited Messages)
# ============================================================================
@client.on(events.NewMessage(chats=SOURCE_BOT))
@client.on(events.MessageEdited(chats=SOURCE_BOT))
async def on_fileserver_message(event):
    msg = event.message
    if not msg:
        return

    # A. IF A MEDIA FILE ARRIVED -> FORWARD IMMEDIATELY BEFORE AUTO-DELETE
    if msg.video or msg.document:
        file_size = msg.file.size if msg.file else 0
        if file_size > 5 * 1024 * 1024:  # Ignore small clips (< 5MB)
            dest = parse_target(TARGET_DESTINATION)
            try:
                await client.send_file(
                    dest,
                    file=msg.media,
                    caption=msg.text or ""
                )
                print(f"🚀 [AUTO-CAPTURED] Forwarded {file_size // (1024*1024)} MB to {dest}!")
            except Exception as e:
                print(f"❌ Forward error: {e}")
        return

    # B. IF INLINE BUTTONS ARE PRESENT -> NAVIGATE AUTONOMOUSLY
    if not msg.buttons:
        return

    msg_id = msg.id
    msg_text = (msg.text or "").lower()

    # Prevent concurrent clicking on the exact same message update
    if msg_id in PROCESSING_MSGS:
        return
    PROCESSING_MSGS.add(msg_id)

    try:
        # --- 1. FORCE-SUBSCRIBE CHECK ---
        joined_any = False
        for row in msg.buttons:
            for btn in row:
                if btn.url and "t.me/" in btn.url:
                    b_txt = (btn.text or "").lower()
                    if any(w in b_txt for w in ["join", "channel", "subscribe", "group"]):
                        await auto_join_channel(btn.url)
                        joined_any = True
                        await asyncio.sleep(1.5)

        if joined_any:
            for row in msg.buttons:
                for btn in row:
                    b_txt = (btn.text or "").lower()
                    if any(w in b_txt for w in ["try again", "joined", "continue", "refresh", "check"]):
                        print("🔘 Clicking 'Try Again' after joining channels...")
                        await btn.click()
                        return

        # --- 2. TITLE SELECTION (if bot gives matching titles list) ---
        if "choose the correct title" in msg_text or "matching results" in msg_text:
            first_btn = msg.buttons[0][0]
            print(f"🔘 Step 1: Selecting Title -> {first_btn.text}")
            await first_btn.click()
            return

        # --- 3. SEASON SELECTION (Specifically pick Season 01, ignore 'Season ALL') ---
        if "choose the season" in msg_text or "season" in msg_text and not "quality" in msg_text:
            target_season = None
            for row in msg.buttons:
                for btn in row:
                    b_txt = (btn.text or "").lower()
                    # Specifically look for Season 01 or Season 1
                    if "season 01" in b_txt or "season 1" in b_txt or "s01" in b_txt:
                        target_season = btn
                        break
                if target_season:
                    break

            # Fallback to first season if Season 01 label is slightly different
            if not target_season:
                for row in msg.buttons:
                    for btn in row:
                        b_txt = (btn.text or "").lower()
                        if "season" in b_txt and "all" not in b_txt:
                            target_season = btn
                            break
                    if target_season:
                        break

            if target_season:
                print(f"🔘 Step 2: Selecting Season -> {target_season.text}")
                await target_season.click()
                return

        # --- 4. QUALITY SELECTION (1080p Bluray/Pahe or 720p) ---
        if "select the available quality" in msg_text or "quality" in msg_text:
            target_quality = None
            for row in msg.buttons:
                for btn in row:
                    b_txt = (btn.text or "").upper()
                    if "1080P" in b_txt and ("PAHE" in b_txt or "BLURAY" in b_txt):
                        target_quality = btn
                        break
                    elif "720P" in b_txt and not target_quality:
                        target_quality = btn
                if target_quality:
                    break

            if not target_quality and msg.buttons:
                # Pick the first quality button that isn't Back/Close
                for row in msg.buttons:
                    for btn in row:
                        if not any(nav in (btn.text or "").lower() for nav in ["back", "close", "next"]):
                            target_quality = btn
                            break
                    if target_quality:
                        break

            if target_quality:
                print(f"🔘 Step 3: Selecting Quality -> {target_quality.text}")
                await target_quality.click()
                return

        # --- 5. EPISODE DOWNLOAD BUTTONS (E01, E02...) ---
        if "total episodes" in msg_text or any(re.search(r"\bE\d{1,3}\b", btn.text or "", re.I) for r in msg.buttons for btn in r):
            print("📦 Step 4: Episode grid detected! Clicking episode links...")
            count = 0

            for row in msg.buttons:
                for btn in row:
                    if count >= 15:  # Max 15 episodes per query to avoid flood limits
                        break
                    b_txt = btn.text or ""

                    if any(nav in b_txt.lower() for nav in ["next", "back", "close", "page"]):
                        continue

                    # Extract episode number (e.g. E01, E02)
                    ep_match = re.search(r"\b(E\d{1,3})\b", b_txt, re.I)
                    ep_key = f"{msg_id}_{ep_match.group(1).upper()}" if ep_match else f"{msg_id}_{b_txt}"

                    # Skip duplicate buttons on the same grid
                    if ep_key in CLICKED_EPISODES:
                        continue
                    CLICKED_EPISODES.add(ep_key)

                    print(f"📥 Clicking Episode -> {b_txt}")
                    try:
                        # Deep-link start parameter
                        if getattr(btn, "url", None) and "start=" in btn.url:
                            parsed_url = urlparse(btn.url)
                            bot_uname = parsed_url.path.strip("/")
                            start_param = parse_qs(parsed_url.query).get("start", [""])[0]
                            if bot_uname and start_param:
                                await client.send_message(bot_uname, f"/start {start_param}")
                                count += 1
                                await asyncio.sleep(2.5)
                        elif getattr(btn, "data", None):
                            await btn.click()
                            count += 1
                            await asyncio.sleep(2.5)
                    except Exception as err:
                        print(f"⚠️ Click error: {err}")

    finally:
        await asyncio.sleep(1)
        PROCESSING_MSGS.discard(msg_id)

# ============================================================================
# 4. MANUAL & CLOUDFLARE TRIGGERS
# ============================================================================
@client.on(events.NewMessage(outgoing=True, pattern=r"^\.get\s+(.+)"))
async def manual_get_command(event):
    query = event.pattern_match.group(1).strip()
    CLICKED_EPISODES.clear()
    await event.edit(f"⏳ <b>Auto-pulling:</b> <code>{query}</code>...", parse_mode="html")
    await client.send_message(SOURCE_BOT, query)

async def poll_cloudflare_worker():
    headers = {"x-userbot-secret": USERBOT_SECRET}
    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            try:
                async with session.get(f"{WORKER_URL}/api/userbot/jobs", timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for job in data.get("jobs", []):
                            CLICKED_EPISODES.clear()
                            await client.send_message(SOURCE_BOT, job["query"])
                            await session.post(f"{WORKER_URL}/api/userbot/jobs", json={
                                "action": "complete",
                                "id": job["id"],
                                "status": "done",
                                "found_count": 1,
                                "chat_id": job.get("chat_id"),
                                "notify_text": f"🚀 <b>Autopilot triggered for:</b> <code>{job['query']}</code>"
                            })
            except Exception:
                pass
            await asyncio.sleep(6)

# ============================================================
# 5. RENDER HEALTH CHECK SERVER & ENTRYPOINT
# ============================================================
async def handle_ping(request):
    return web.json_response({"status": "online", "service": "Autopilot Bridge"})

async def start_health_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🌐 HTTP Health Server listening on port {PORT}")

async def main():
    await start_health_server()
    if not SESSION_STRING:
        print("❌ ERROR: SESSION_STRING missing in Render!")
        return

    await client.start()
    print("🚀 Autopilot Userbot is live and monitoring @fileserverxrobot!")
    print(f"📡 Media destination: {TARGET_DESTINATION}")

    asyncio.create_task(poll_cloudflare_worker())
    await client.run_until_disconnected()

if __name__ == "__main__":
    loop.run_until_complete(main())
