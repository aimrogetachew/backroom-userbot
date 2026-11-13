import os
import re
import asyncio
from urllib.parse import urlparse, parse_qs
from aiohttp import web
import aiohttp

# ============================================================================
# PYTHON 3.10+ / 3.12+ EVENT LOOP
# ============================================================================
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

from telethon import TelegramClient, events
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

# Target Storage Channels Mapping
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

INDIAN_LANG_CODES = {"hi", "te", "ta", "ml", "kn", "bn", "mr", "pa", "gu", "ur"}
SOURCE_BOT = "fileserverxrobot"
PORT = int(os.environ.get("PORT", 10000))

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)

NAV_LOCK = asyncio.Lock()
PROCESSED_BUTTONS = set()
CAPTURED_FILES = set()

# ============================================================================
# 2. MULTI-DUB, SPEC & TECHNICAL CAPTION EXTRACTION
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

    # Resolution detection (360p, 480p, 720p, 1080p, 2160p / 4K)
    qual_m = re.search(r"\b(360p|480p|540p|720p|1080p|1440p|2160p|4K)\b", combined, re.I)
    quality = qual_m.group(1).upper() if qual_m else "HD"

    # Codec detection
    codec = "x264"
    if re.search(r"\b(x265|h265|hevc)\b", combined, re.I):
        codec = "HEVC x265"
    elif re.search(r"\bav1\b", combined, re.I):
        codec = "AV1"

    # Bitrate & Duration
    br_m = re.search(r"Video\s*Bitrate\s*:\s*([^\r\n]+)", combined, re.I) or re.search(r"\b(\d{3,5}\s*kbps)\b", combined, re.I)
    bitrate = br_m.group(1).strip() if br_m else ""

    dur_m = re.search(r"Duration\s*:\s*([^\r\n]+)", combined, re.I)
    duration = dur_m.group(1).strip() if dur_m else ""

    for_tv = bool(re.search(r"Can\s*be\s*played\s*on\s*TV|FOR\s*TV", combined, re.I) or "x264" in codec)
    has_sub = bool(re.search(r"Has\s*[a-zA-Z]*\s*subtitles|\b(?:sub|subs|esub|multisub)\b|\.mkv\b", combined, re.I))

    # Multi-Audio list parsing
    audio_lines = []
    aud_section = re.search(r"Audio\s*List\s*:\s*((?:\r?\n\s*\*[^\r\n]+)+)", caption or "", re.I)
    if aud_section:
        for ln in aud_section.group(1).splitlines():
            if ln.strip().startswith("*"):
                audio_lines.append(ln.strip())

    # Fallback multi-dub detection from text
    if not audio_lines:
        detected_dubs = []
        for pat, name in [
            (r"\b(?:hindi|hin)\b", "Hindi"),
            (r"\b(?:english|eng)\b", "English"),
            (r"\b(?:japanese|jap|jpn)\b", "Japanese"),
            (r"\b(?:korean|kor)\b", "Korean"),
            (r"\b(?:tamil|tam)\b", "Tamil"),
            (r"\b(?:telugu|tel)\b", "Telugu"),
            (r"\b(?:amharic|amh|ትርጉም)\b", "Amharic"),
        ]:
            if re.search(pat, combined, re.I):
                detected_dubs.append(name)
        if detected_dubs:
            audio_lines = [f"* AAC 2.0 — {' + '.join(detected_dubs)}"]
        else:
            audio_lines = ["* AAC 2.0 — Dual Audio"]

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

def build_final_caption(filename, caption, meta_runtime="", meta_lang=""):
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
# 3. METADATA & GENRE-BASED CATEGORIZATION ENGINE
# ============================================================================
async def fetch_meta(title):
    clean = re.sub(r"\b(S\d{1,2}|E\d{1,3}|1080p|720p|480p|360p|BluRay|Pahe|MKV|MP4|HEVC)\b.*$", "", title, flags=re.I)
    clean = re.sub(r"[._\-]+", " ", clean).strip()

    genres = ""
    lang = ""
    runtime = ""
    is_series = bool(re.search(r"\b(S\d{1,2}\s*E\d{1,3}|Season|Episode)\b", title, re.I))

    async with aiohttp.ClientSession() as session:
        if TMDB_API_KEY:
            try:
                tmdb_url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={clean}"
                async with session.get(tmdb_url, timeout=5) as r:
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
                omdb_url = f"https://www.omdbapi.com/?apikey={OMDB_API_KEY}&t={clean}"
                async with session.get(omdb_url, timeout=5) as r:
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
# 4. AUTO-JOIN CHANNELS (Force-Subscribe Bypass)
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
# 5. INCOMING MEDIA RECEIVER: PARSE & DISPATCH TO STORAGE CHANNELS
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

    print(f"📥 Captured media file: {raw_fn}")

    # 1. Fetch Metadata
    meta = await fetch_meta(raw_fn)

    # 2. Determine Channel Category
    cat = detect_category(raw_fn, caption, meta)
    dest_channel = DEFAULT_CHANNELS.get(cat, DEFAULT_CHANNELS["other"])
    cat_emoji = CATEGORY_EMOJIS.get(cat, "📁")

    # 3. Build Spec Caption
    final_caption = build_final_caption(raw_fn, caption, meta.get("runtime", ""), meta.get("language", ""))

    # 4. Forward directly to target channel before 60s auto-delete expires
    try:
        await client.send_file(
            dest_channel,
            file=msg.media,
            caption=final_caption,
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

# ============================================================================
# 6. ADVANCED AUTONOMOUS BUTTON ENGINE (All Qualities, Seasons & Episodes)
# ============================================================================
@client.on(events.NewMessage(chats=SOURCE_BOT))
@client.on(events.MessageEdited(chats=SOURCE_BOT))
async def handle_bot_menus(event):
    msg = event.message
    if not msg or not msg.buttons:
        return

    msg_id = msg.id
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

        # B. Title Matching List
        if "choose the correct title" in msg_text or "matching results" in msg_text:
            btn = msg.buttons[0][0]
            print(f"🔘 Step 1: Selecting Title -> {btn.text}")
            await btn.click()
            return

        # C. Season Selection (Defaults to Season 01, handles multi-seasons)
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

        # D. Quality Selection (360p, 480p, 720p, 1080p)
        if "select the available quality" in msg_text or "quality" in msg_text:
            target_quality = None
            # Check highest quality down to 480p/360p
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

        # E. Multi-Episode Downloads (E01, E02, E03...)
        if "total episodes" in msg_text or any(re.search(r"\bE\d{1,3}\b", b.text or "", re.I) for r in msg.buttons for b in r):
            unique_eps = {}
            for row in msg.buttons:
                for btn in row:
                    txt = btn.text or ""
                    if any(nav in txt.lower() for nav in ["next", "back", "close", "page"]):
                        continue
                    m = re.search(r"\b(E\d{1,3})\b", txt, re.I)
                    ep_tag = m.group(1).upper() if m else txt
                    if ep_tag not in unique_eps:
                        unique_eps[ep_tag] = btn

            for ep_tag, btn in unique_eps.items():
                btn_key = f"{msg_id}_{ep_tag}"
                if btn_key in PROCESSED_BUTTONS:
                    continue
                PROCESSED_BUTTONS.add(btn_key)

                print(f"📥 Requesting file: {btn.text}")
                try:
                    if getattr(btn, "url", None) and "start=" in btn.url:
                        parsed = urlparse(btn.url)
                        b_uname = parsed.path.strip("/")
                        param = parse_qs(parsed.query).get("start", [""])[0]
                        if b_uname and param:
                            await client.send_message(b_uname, f"/start {param}")
                            await asyncio.sleep(4)
                    elif getattr(btn, "data", None):
                        await btn.click()
                        await asyncio.sleep(4)
                except Exception as err:
                    print(f"⚠️ Button error: {err}")

# ============================================================================
# 7. TRIGGER FROM SAVED MESSAGES (Type Title to Search)
# ============================================================================
@client.on(events.NewMessage(chats="me", outgoing=True))
async def on_saved_messages_trigger(event):
    text = (event.message.text or "").strip()
    if not text or text.startswith("✅") or text.startswith("⏳") or text.startswith("/"):
        return

    query = re.sub(r"^\.get\s+", "", text, flags=re.I).strip()
    PROCESSED_BUTTONS.clear()

    await event.reply(f"⏳ <b>Searching @{SOURCE_BOT} for:</b> <code>{query}</code>...", parse_mode="HTML")
    await client.send_message(SOURCE_BOT, query)

# ============================================================================
# 8. RENDER HEALTH CHECK SERVER & ENTRYPOINT
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
    print("💡 Send any movie/series title into your Telegram 'Saved Messages' to begin.")
    await client.run_until_disconnected()

if __name__ == "__main__":
    loop.run_until_complete(main())
