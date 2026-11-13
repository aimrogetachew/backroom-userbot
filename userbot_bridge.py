import os
import re
import asyncio
from urllib.parse import urlparse, parse_qs
from aiohttp import web
import aiohttp

# ============================================================================
# EVENT LOOP COMPATIBILITY
# ============================================================================
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

# ============================================================================
# 1. ENVIRONMENT CONFIGURATION (SECURE - NO HARDCODED SECRETS)
# ============================================================================
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "")

DEFAULT_CHANNELS = {
    "hindi": -1004364094932,
    "english": -1003900997247,
    "series": -1004399588627,
    "amharic": -1004308570128,
    "korean": -1003974632071,
    "horror": -1004416107106,
    "action": -1004426568341,
    "anime": -1004390808391,
    "animation": -1003778945725,
    "upcoming": -1004356513920,
    "turkish": -1003605533639,
    "other": -1003900997247
}

CATEGORY_EMOJIS = {
    "hindi": "🇮🇳", "english": "🌎", "series": "📺", "amharic": "🇪🇹",
    "korean": "🇰🇷", "horror": "👻", "action": "💥", "anime": "⛩",
    "animation": "🧸", "upcoming": "🔔", "turkish": "🇹🇷", "other": "🎬"
}

# The 3 reference inline buttons
INLINE_BUTTONS = [
    [
        Button.url("Series ↗", "https://t.me/thebackroomseries"),
        Button.url("Anime ↗", "https://t.me/thebackroomanime"),
        Button.url("Movies ↗", "https://t.me/thebackroommovies")
    ]
]

INDIAN_LANG_CODES = {"hi", "te", "ta", "ml", "kn", "bn", "mr", "pa", "gu", "ur"}
SOURCE_BOT = "fileserverxrobot"
PORT = int(os.environ.get("PORT", 10000))

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)

NAV_LOCK = asyncio.Lock()
FILE_COMPLETED_EVENT = asyncio.Event()
PROCESSED_EPISODES = set()
CAPTURED_FILES = set()
IS_DOWNLOADING_QUEUE = False

# ============================================================================
# 2. METADATA & TECHNICAL CAPTION PARSER
# ============================================================================
def extract_real_filename(filename="", caption=""):
    cap = str(caption or "")
    m = re.search(r"File\s*Name\s*:\s*\r?\n?([^\r\n]+)", cap, re.I)
    if m and len(m.group(1).strip()) >= 4:
        fn = m.group(1).strip()
    else:
        fn = str(filename or "Media.mkv").strip()
    return re.sub(r"https?://\S+|t\.me/\S+|@[A-Za-z0-9_]+", "", fn).strip()

def extract_tech_specs(filename="", caption=""):
    real_fn = extract_real_filename(filename, caption)
    combined = f"{real_fn}\n{caption or ''}"

    qual_m = re.search(r"\b(360p|480p|540p|720p|1080p|1440p|2160p|4K)\b", combined, re.I)
    quality = qual_m.group(1).upper() if qual_m else "HD"

    codec = "x264"
    if re.search(r"\b(x265|h265|hevc)\b", combined, re.I):
        codec = "HEVC x265"
    elif re.search(r"\bav1\b", combined, re.I):
        codec = "AV1"

    br_m = re.search(r"Video\s*Bitrate\s*:\s*([^\r\n]+)", combined, re.I) or re.search(r"\b(\d{3,5}\s*kbps)\b", combined, re.I)
    bitrate = br_m.group(1).strip() if br_m else ""

    dur_m = re.search(r"Duration\s*:\s*([^\r\n]+)", combined, re.I)
    duration = dur_m.group(1).strip() if dur_m else ""

    for_tv = bool(re.search(r"Can\s*be\s*played\s*on\s*TV|FOR\s*TV", combined, re.I) or "x264" in codec)
    has_sub = bool(re.search(r"Has\s*[a-zA-Z]*\s*subtitles|\b(?:sub|subs|esub|multisub)\b|\.mkv\b", combined, re.I))

    audio_lines = []
    aud_section = re.search(r"Audio\s*List\s*:\s*((?:\r?\n\s*\*[^\r\n]+)+)", caption or "", re.I)
    if aud_section:
        for ln in aud_section.group(1).splitlines():
            if ln.strip().startswith("*"):
                audio_lines.append(ln.strip())

    if not audio_lines:
        detected = []
        for pat, name in [
            (r"\b(?:hindi|hin)\b", "Hindi"),
            (r"\b(?:english|eng)\b", "English"),
            (r"\b(?:japanese|jap|jpn)\b", "Japanese"),
            (r"\b(?:korean|kor)\b", "Korean"),
            (r"\b(?:tamil|tam)\b", "Tamil"),
            (r"\b(?:telugu|tel)\b", "Telugu"),
            (r"\b(?:amharic|amh|ትርጉም)\b", "Amharic")
        ]:
            if re.search(pat, combined, re.I):
                detected.append(name)
        audio_lines = [f"* AAC 2.0 — {' + '.join(detected) if detected else 'Dual Audio'}"]

    return {
        "real_filename": real_fn,
        "quality": quality,
        "codec": codec,
        "duration": duration,
        "bitrate": bitrate,
        "for_tv": for_tv,
        "has_sub": has_sub,
        "audio_lines": audio_lines
    }

def build_final_caption(filename, caption, meta_runtime=""):
    sp = extract_tech_specs(filename, caption)
    dur = sp["duration"] or meta_runtime

    lines = [
        "<b>File Name:</b>",
        f"<code>{sp['real_filename']}</code>"
    ]
    tech = []
    if dur:
        tech.append(f"<b>Duration:</b> {dur}")
    if sp["bitrate"]:
        tech.append(f"<b>Video Bitrate:</b> {sp['bitrate']}")
    tech.append(f"<b>Specs:</b> {sp['quality']} • {sp['codec']}")
    lines.append("")
    lines.extend(tech)

    checks = []
    if sp["for_tv"]:
        checks.append("✅ Can be played on TV")
    if sp["has_sub"]:
        checks.append("✅ Has English subtitles")
    if checks:
        lines.append("")
        lines.extend(checks)

    if sp["audio_lines"]:
        lines.append("")
        lines.append("<b>Audio List:</b>")
        for al in sp["audio_lines"][:8]:
            lines.append(al)

    return "\n".join(lines)

# ============================================================================
# 3. TMDB/OMDB METADATA & GENRE ROUTING
# ============================================================================
async def fetch_meta(title):
    clean = re.sub(r"\b(S\d{1,2}|E\d{1,3}|1080p|720p|480p|360p|BluRay|Pahe|MKV|MP4|HEVC)\b.*$", "", title, flags=re.I)
    clean = re.sub(r"[._\-]+", " ", clean).strip()

    genres, lang, runtime = "", "", ""
    is_series = bool(re.search(r"\b(S\d{1,2}\s*E\d{1,3}|Season|Episode)\b", title, re.I))

    async with aiohttp.ClientSession() as session:
        if TMDB_API_KEY:
            try:
                url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={clean}"
                async with session.get(url, timeout=5) as r:
                    if r.status == 200:
                        data = await r.json()
                        res = (data.get("results") or [{}])[0]
                        lang = res.get("original_language", "")
                        if res.get("media_type") == "tv":
                            is_series = True
            except Exception:
                pass

        if OMDB_API_KEY:
            try:
                url = f"https://www.omdbapi.com/?apikey={OMDB_API_KEY}&t={clean}"
                async with session.get(url, timeout=5) as r:
                    if r.status == 200:
                        data = await r.json()
                        if data.get("Response") == "True":
                            genres = data.get("Genre", "")
                            runtime = data.get("Runtime", "")
                            if data.get("Type") == "series":
                                is_series = True
            except Exception:
                pass

    return {"title": clean, "genres": genres, "language": lang, "runtime": runtime, "is_series": is_series}

def detect_category(filename, caption, meta):
    combined = f"{filename} {caption} {meta.get('genres', '')}".lower()
    lang = (meta.get("language") or "").lower()

    if re.search(r"\b(anime|demon slayer|solo leveling|jujutsu|naruto|one piece|bleach|attack on titan)\b", combined) or (
        "animation" in meta.get("genres", "").lower() and lang in ("ja", "zh")
    ):
        return "anime"

    if "animation" in meta.get("genres", "").lower():
        return "animation"

    if meta.get("is_series") or re.search(r"\b(S\d{1,2}\s*E\d{1,3}|Season\s*\d+|Episode\s*\d+)\b", combined):
        return "series"

    if lang == "am" or re.search(r"[\u1200-\u137F]+|amharic|ትርጉም", combined):
        return "amharic"

    if lang in INDIAN_LANG_CODES or re.search(r"\b(hindi|bollywood|telugu|tamil|malayalam)\b", combined):
        return "hindi"

    if lang == "ko" or "korean" in combined:
        return "korean"

    if lang == "tr" or "turkish" in combined:
        return "turkish"

    if "horror" in meta.get("genres", "").lower() or "thriller" in meta.get("genres", "").lower():
        return "horror"

    if "action" in meta.get("genres", "").lower() or "adventure" in meta.get("genres", "").lower():
        return "action"

    return "english"

# ============================================================================
# 4. AUTO-JOIN CHANNELS (FSUB BYPASS)
# ============================================================================
async def auto_join_channel(url: str):
    try:
        if "+" in url or "joinchat" in url:
            invite_hash = url.split("+")[-1].split("/")[-1].strip()
            await client(ImportChatInviteRequest(invite_hash))
            print(f"✅ Joined private channel: {url}")
        else:
            uname = url.split("t.me/")[-1].split("/")[0].split("?")[0].strip()
            if uname:
                await client(JoinChannelRequest(uname))
                print(f"✅ Joined public channel: @{uname}")
        return True
    except Exception as e:
        print(f"⚠️ Join note: {e}")
        return False

# ============================================================================
# 5. INCOMING MEDIA RECEIVER: FORWARD WITH BUTTONS & TRIGGER NEXT EPISODE
# ============================================================================
@client.on(events.NewMessage(incoming=True))
async def on_media_arrival(event):
    sender = await event.get_sender()
    if (getattr(sender, "username", "") or "").lower() != SOURCE_BOT and not getattr(sender, "bot", False):
        return

    if not (event.video or event.document):
        return

    msg = event.message
    file_id = f"{msg.id}_{msg.file.size}"
    if file_id in CAPTURED_FILES:
        return
    CAPTURED_FILES.add(file_id)

    raw_fn = msg.file.name or (msg.text or "Media.mkv").split("\n")[0]
    caption = msg.text or ""

    print(f"📥 Received file: {raw_fn}")

    meta = await fetch_meta(raw_fn)
    cat = detect_category(raw_fn, caption, meta)
    dest_channel = DEFAULT_CHANNELS.get(cat, DEFAULT_CHANNELS["other"])
    cat_emoji = CATEGORY_EMOJIS.get(cat, "📁")

    final_caption = build_final_caption(raw_fn, caption, meta.get("runtime", ""))

    try:
        # Forward with the 3 inline buttons before 60s deletion
        await client.send_file(
            dest_channel,
            file=msg.media,
            caption=final_caption,
            buttons=INLINE_BUTTONS,
            parse_mode="HTML"
        )
        print(f"🚀 Published to {cat_emoji} {cat.upper()} channel ({dest_channel})")

        # Notify Saved Messages
        await client.send_message(
            "me",
            f"✅ <b>Published to Storage!</b>\n"
            f"• <b>File:</b> <code>{extract_real_filename(raw_fn, caption)}</code>\n"
            f"• <b>Channel:</b> {cat_emoji} <code>{cat.upper()}</code> ➔ <code>{dest_channel}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"❌ Storage Forward error: {e}")
    finally:
        # Signal the queue that this file is done; proceed to the next episode
        FILE_COMPLETED_EVENT.set()

# ============================================================================
# 6. SEQUENTIAL QUEUE PROCESSOR: DOWNLOADS EVERY EPISODE & HANDLES PAGES
# ============================================================================
async def process_all_episodes(bot_username, current_msg):
    global IS_DOWNLOADING_QUEUE
    if IS_DOWNLOADING_QUEUE:
        return
    IS_DOWNLOADING_QUEUE = True

    try:
        while True:
            # 1. Gather all unique episode buttons on the current page
            episode_buttons = []
            seen_tags = set()
            next_btn = None

            for row in current_msg.buttons:
                for btn in row:
                    txt = btn.text or ""
                    if any(nav in txt.lower() for nav in ["back", "close", "cancel"]):
                        continue
                    if "next" in txt.lower() or "⏩" in txt:
                        next_btn = btn
                        continue

                    m = re.search(r"\b(E\d{1,3})\b", txt, re.I)
                    ep_tag = m.group(1).upper() if m else txt

                    if ep_tag not in seen_tags and ep_tag not in PROCESSED_EPISODES:
                        seen_tags.add(ep_tag)
                        episode_buttons.append((ep_tag, btn))

            print(f"📋 Found {len(episode_buttons)} episodes to download on this page.")

            # 2. Iterate through each episode sequentially
            for ep_tag, btn in episode_buttons:
                PROCESSED_EPISODES.add(ep_tag)
                FILE_COMPLETED_EVENT.clear()

                print(f"⏳ Triggering episode {ep_tag} ({btn.text})...")

                try:
                    if getattr(btn, "url", None) and "start=" in btn.url:
                        parsed = urlparse(btn.url)
                        b_uname = parsed.path.strip("/")
                        param = parse_qs(parsed.query).get("start", [""])[0]
                        if b_uname and param:
                            await client.send_message(b_uname, f"/start {param}")
                    elif getattr(btn, "data", None):
                        await btn.click()
                except Exception as err:
                    print(f"⚠️ Click error on {ep_tag}: {err}")
                    continue

                # Wait until the file arrives and forwards (timeout after 90 seconds)
                try:
                    await asyncio.wait_for(FILE_COMPLETED_EVENT.wait(), timeout=90)
                    print(f"✅ Successfully finished {ep_tag}. Cooling down 4 seconds...")
                except asyncio.TimeoutError:
                    print(f"⚠️ Timeout waiting for {ep_tag}. Proceeding to next episode...")

                await asyncio.sleep(4)

            # 3. Check for Next Page (e.g. Page 1 of 4 -> Page 2)
            if next_btn and "next" in (next_btn.text or "").lower():
                print("📄 Moving to Next Page of episodes...")
                try:
                    await next_btn.click()
                    await asyncio.sleep(3)
                    msgs = await client.get_messages(SOURCE_BOT, limit=1)
                    current_msg = msgs[0]
                    if not current_msg.buttons:
                        break
                except Exception as err:
                    print(f"⚠️ Could not paginate: {err}")
                    break
            else:
                print("🎉 All episodes and pages completed!")
                break

    finally:
        IS_DOWNLOADING_QUEUE = False

# ============================================================================
# 7. NAVIGATION CONTROLLER: TITLE -> SEASON -> QUALITY -> QUEUE
# ============================================================================
@client.on(events.NewMessage(chats=SOURCE_BOT))
@client.on(events.MessageEdited(chats=SOURCE_BOT))
async def handle_bot_menus(event):
    msg = event.message
    if not msg or not msg.buttons:
        return

    msg_text = (msg.text or "").lower()

    async with NAV_LOCK:
        # A. Force-Subscribe Bypass
        joined = False
        for row in msg.buttons:
            for btn in row:
                if btn.url and "t.me/" in btn.url:
                    t = (btn.text or "").lower()
                    if any(w in t for w in ["join", "channel", "subscribe"]):
                        await auto_join_channel(btn.url)
                        joined = True
                        await asyncio.sleep(1.5)

        if joined:
            for row in msg.buttons:
                for btn in row:
                    if any(w in (btn.text or "").lower() for w in ["try again", "joined", "refresh"]):
                        await btn.click()
                        return

        # B. Title Selection
        if "choose the correct title" in msg_text or "matching results" in msg_text:
            btn = msg.buttons[0][0]
            print(f"🔘 Step 1: Selecting Title -> {btn.text}")
            await btn.click()
            return

        # C. Season Selection (Pick Season 01, ignore 'Season ALL')
        if "choose the season" in msg_text or ("season" in msg_text and "quality" not in msg_text):
            target_season = None
            for row in msg.buttons:
                for btn in row:
                    t = (btn.text or "").lower()
                    if "season 01" in t or "season 1" in t:
                        target_season = btn
                        break
                if target_season:
                    break

            if not target_season:
                for row in msg.buttons:
                    for btn in row:
                        t = (btn.text or "").lower()
                        if "season" in t and "all" not in t:
                            target_season = btn
                            break
                    if target_season:
                        break

            if target_season:
                print(f"🔘 Step 2: Selecting Season -> {target_season.text}")
                await target_season.click()
            return

        # D. Quality Selection (1080P BluRay/Pahe -> 720P -> 480P)
        if "select the available quality" in msg_text or "quality" in msg_text:
            target_quality = None
            for preferred in ["1080P", "720P", "480P", "360P"]:
                for row in msg.buttons:
                    for btn in row:
                        t = (btn.text or "").upper()
                        if preferred in t and not any(nav in t.lower() for nav in ["back", "close"]):
                            target_quality = btn
                            break
                    if target_quality:
                        break
                if target_quality:
                    break

            if not target_quality and msg.buttons:
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

        # E. Episodes Grid: Launch the Sequential Queue
        if "total episodes" in msg_text or any(re.search(r"\bE\d{1,3}\b", b.text or "", re.I) for r in msg.buttons for b in r):
            if not IS_DOWNLOADING_QUEUE:
                asyncio.create_task(process_all_episodes(SOURCE_BOT, msg))

# ============================================================================
# 8. SAVED MESSAGES TRIGGER
# ============================================================================
@client.on(events.NewMessage(chats="me", outgoing=True))
async def on_saved_messages_trigger(event):
    text = (event.message.text or "").strip()
    if not text or text.startswith("✅") or text.startswith("⏳") or text.startswith("/"):
        return

    query = re.sub(r"^\.get\s+", "", text, flags=re.I).strip()
    PROCESSED_EPISODES.clear()

    await event.reply(f"⏳ <b>Auto-downloading full series/movie:</b> <code>{query}</code>...", parse_mode="HTML")
    await client.send_message(SOURCE_BOT, query)

# ============================================================================
# 9. RENDER HEALTH CHECK SERVER & MAIN ENTRYPOINT
# ============================================================================
async def handle_ping(request):
    return web.json_response({"status": "running", "service": "Autonomous Media Engine"})

async def start_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

async def main():
    await start_server()
    if not SESSION_STRING:
        print("❌ ERROR: SESSION_STRING environment variable is missing!")
        return

    await client.start()
    print("🚀 Autonomous Media Engine is LIVE!")
    print("💡 Send any title to 'Saved Messages' to download all episodes sequentially.")
    await client.run_until_disconnected()

if __name__ == "__main__":
    loop.run_until_complete(main())
