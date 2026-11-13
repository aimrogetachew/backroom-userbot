import os
import re
import asyncio
from aiohttp import web
import aiohttp
from urllib.parse import urlparse, parse_qs

# ============================================================================
# PYTHON 3.10+ / 3.12+ COMPATIBILITY: EXPLICIT EVENT LOOP CREATION
# ============================================================================
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

# ============================================================================
# 1. CONFIGURATION (Read from Render Environment Variables)
# ============================================================================
API_ID = int(os.environ.get("API_ID", "7598862"))
API_HASH = os.environ.get("API_HASH", "fea8fb2a9cb4d31ff6f53e2d499801ee")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

# Your Cloudflare Worker URL
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

# ============================================================================
# 2. AUTO-JOIN CHANNELS (Force-Subscribe Bypass)
# ============================================================================
async def auto_join_channel(url: str):
    """Automatically joins required Telegram channels to bypass FSUB locks."""
    try:
        if "+" in url or "joinchat" in url:
            invite_hash = url.split("+")[-1].split("/")[-1].strip()
            await client(ImportChatInviteRequest(invite_hash))
            print(f"✅ Joined private channel via invite: {url}")
        else:
            uname = url.split("t.me/")[-1].split("/")[0].split("?")[0].strip()
            if uname:
                await client(JoinChannelRequest(uname))
                print(f"✅ Joined public channel: @{uname}")
        return True
    except Exception as e:
        print(f"⚠️ Channel join error for {url}: {e}")
        return False

# ============================================================================
# 3. INSTANT MEDIA FORWARDER (BEATS THE 1-MINUTE AUTO-DELETE)
# ============================================================================
@client.on(events.NewMessage(incoming=True))
async def on_incoming_file(event):
    sender = await event.get_sender()
    sender_username = (getattr(sender, "username", "") or "").lower()

    # Capture files sent by the fileserver bot
    if sender_username != SOURCE_BOT and not getattr(sender, "bot", False):
        return

    if not (event.video or event.document):
        return

    file_size = event.file.size if event.file else 0
    if file_size < 5 * 1024 * 1024:  # Ignore small stickers/clips (< 5MB)
        return

    dest = parse_target(TARGET_DESTINATION)
    raw_caption = event.message.text or ""

    try:
        # Forward immediately to your target storage bot/channel
        await client.send_file(
            dest,
            file=event.message.media,
            caption=raw_caption
        )
        print(f"🚀 [AUTO-CAPTURED] Forwarded {file_size // (1024*1024)} MB to {dest} before auto-delete!")
    except Exception as e:
        print(f"❌ Forwarding error: {e}")

# ============================================================================
# 4. HIERARCHICAL NAVIGATION (Title -> Season -> Quality -> Episodes)
# ============================================================================
async def pull_movie_from_fileserver(query: str, max_episodes: int = 25) -> int:
    print(f"🔎 Starting deep navigation for: {query}")
    total_triggered = 0

    try:
        # 1. Send query
        sent = await client.send_message(SOURCE_BOT, query)
        await asyncio.sleep(3)

        # 2. Get latest response
        msgs = await client.get_messages(SOURCE_BOT, limit=2)
        if not msgs or not msgs[0].buttons:
            print("⚠️ No buttons received from search query.")
            return 0
        current_msg = msgs[0]

        # 3. CHECK FOR FORCE-SUBSCRIBE (Must join channels first)
        joined_any = False
        for row in current_msg.buttons:
            for btn in row:
                if btn.url and ("t.me/" in btn.url):
                    text_lower = (btn.text or "").lower()
                    if any(w in text_lower for w in ["join", "channel", "subscribe", "group"]):
                        await auto_join_channel(btn.url)
                        joined_any = True
                        await asyncio.sleep(2)

        if joined_any:
            # Click Try Again / Joined / Refresh button
            for row in current_msg.buttons:
                for btn in row:
                    t_low = (btn.text or "").lower()
                    if any(w in t_low for w in ["try again", "joined", "continue", "refresh", "check"]):
                        await btn.click()
                        await asyncio.sleep(3)
                        msgs = await client.get_messages(SOURCE_BOT, limit=1)
                        current_msg = msgs[0]
                        break

        # 4. STEP A: SELECT TITLE (if search returns a list of results)
        if current_msg.buttons:
            first_btn = current_msg.buttons[0][0]
            btn_txt = first_btn.text or ""
            # If buttons represent search results (e.g. "Demon Slayer - 5 Seasons")
            if not any(w in btn_txt.lower() for w in ["season 01", "season 1", "1080p", "720p", "e01"]):
                print(f"🔘 Selecting title: {btn_txt}")
                await first_btn.click()
                await asyncio.sleep(3)
                msgs = await client.get_messages(SOURCE_BOT, limit=1)
                current_msg = msgs[0]

        # 5. STEP B: SELECT SEASON (Choose Season 01 by default)
        season_clicked = False
        if current_msg.buttons:
            for row in current_msg.buttons:
                for btn in row:
                    b_text = (btn.text or "").lower()
                    if "season 01" in b_text or "season 1" in b_text or "season" in b_text:
                        print(f"🔘 Selecting season: {btn.text}")
                        await btn.click()
                        season_clicked = True
                        await asyncio.sleep(3)
                        msgs = await client.get_messages(SOURCE_BOT, limit=1)
                        current_msg = msgs[0]
                        break
                if season_clicked:
                    break

        # 6. STEP C: SELECT QUALITY (Prefer 1080p or 720p PAHE/Bluray)
        quality_clicked = False
        if current_msg.buttons:
            # Check for best quality available
            target_btn = None
            for row in current_msg.buttons:
                for btn in row:
                    b_text = (btn.text or "").upper()
                    if "1080P" in b_text and ("PAHE" in b_text or "BLURAY" in b_text):
                        target_btn = btn
                        break
                    elif "720P" in b_text and not target_btn:
                        target_btn = btn
                if target_btn:
                    break

            # If preferred isn't found, pick the first quality row
            if not target_btn and current_msg.buttons:
                target_btn = current_msg.buttons[0][0]

            if target_btn and not any(nav in (target_btn.text or "").lower() for nav in ["back", "close"]):
                print(f"🔘 Selecting quality: {target_btn.text}")
                await target_btn.click()
                quality_clicked = True
                await asyncio.sleep(3)
                msgs = await client.get_messages(SOURCE_BOT, limit=1)
                current_msg = msgs[0]

        # 7. STEP D: CLICK EPISODES (E01, E02, E03...)
        if current_msg.buttons:
            print(f"📦 Extracting episode files from: {current_msg.buttons[0][0].text or 'Menu'}")
            clicked_episodes = set()

            for row in current_msg.buttons:
                for btn in row:
                    if total_triggered >= max_episodes:
                        break

                    b_text = btn.text or ""
                    # Skip navigation buttons
                    if any(nav in b_text.lower() for nav in ["next", "back", "close", "page", "cancel"]):
                        continue

                    # Avoid clicking duplicate episode qualities in the same row
                    ep_match = re.search(r"\b(E\d{1,3})\b", b_text, re.I)
                    ep_key = ep_match.group(1).upper() if ep_match else b_text

                    if ep_key in clicked_episodes:
                        continue

                    clicked_episodes.add(ep_key)
                    print(f"📥 Triggering Episode Button: {b_text}")

                    try:
                        # Deep-link button
                        if getattr(btn, "url", None) and "t.me/" in btn.url and "start=" in btn.url:
                            parsed_url = urlparse(btn.url)
                            bot_uname = parsed_url.path.strip("/")
                            start_param = parse_qs(parsed_url.query).get("start", [""])[0]
                            if bot_uname and start_param:
                                await client.send_message(bot_uname, f"/start {start_param}")
                                total_triggered += 1
                                await asyncio.sleep(2.5)

                        # Callback inline button
                        elif getattr(btn, "data", None):
                            await btn.click()
                            total_triggered += 1
                            await asyncio.sleep(2.5)

                    except Exception as err:
                        print(f"⚠️ Episode click error: {err}")

    except Exception as e:
        print(f"⚠️ Navigation sequence error: {e}")

    return total_triggered

# ============================================================
# 5. MANUAL TRIGGER: Type .get <Name> in Telegram Saved Messages
# ============================================================
@client.on(events.NewMessage(outgoing=True, pattern=r"^\.get\s+(.+)"))
async def manual_get_command(event):
    query = event.pattern_match.group(1).strip()
    await event.edit(f"⏳ <b>Navigating @{SOURCE_BOT} for:</b> <code>{query}</code>...", parse_mode="html")
    count = await pull_movie_from_fileserver(query)
    await event.edit(f"✅ Triggered <b>{count}</b> episode files for <b>{query}</b>!", parse_mode="html")

# ============================================================
# 6. CLOUDFLARE QUEUE POLLER (/get commands)
# ============================================================
async def poll_cloudflare_worker():
    headers = {"x-userbot-secret": USERBOT_SECRET}
    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            try:
                async with session.get(f"{WORKER_URL}/api/userbot/jobs", timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for job in data.get("jobs", []):
                            count = await pull_movie_from_fileserver(job["query"])
                            await session.post(f"{WORKER_URL}/api/userbot/jobs", json={
                                "action": "complete",
                                "id": job["id"],
                                "status": "done" if count > 0 else "not_found",
                                "found_count": count,
                                "chat_id": job.get("chat_id"),
                                "notify_text": f"✅ <b>Finished downloading:</b> <code>{job['query']}</code> ({count} episodes triggered)."
                            })
            except Exception:
                pass
            await asyncio.sleep(6)

# ============================================================
# 7. RENDER HEALTH CHECK SERVER
# ============================================================
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

# ============================================================
# 8. MAIN ENTRYPOINT
# ============================================================
async def main():
    await start_health_server()

    if not SESSION_STRING:
        print("❌ ERROR: SESSION_STRING environment variable is missing in Render!")
        return

    await client.start()
    print("🚀 Telethon Userbot is running and connected!")
    print(f"📡 Forwarding files from @{SOURCE_BOT} -> {TARGET_DESTINATION}")

    asyncio.create_task(poll_cloudflare_worker())
    await client.run_until_disconnected()

if __name__ == "__main__":
    loop.run_until_complete(main())
