import os
import re
import asyncio
import sqlite3
from urllib.parse import urlparse, parse_qs
from aiohttp import web
import aiohttp

# ============================================================================
# PYTHON 3.10+ / 3.12+ ASYNCIO EVENT LOOP SETUP
# ============================================================================
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

# ============================================================================
# 1. CONFIGURATION (READ FROM ENVIRONMENT VARIABLES)
# ============================================================================
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "")

# Strip '@' if accidentally included in environment variable
SOURCE_BOT = os.environ.get("SOURCE_BOT", "fileserverxrobot").lstrip("@").strip()
PORT = int(os.environ.get("PORT", 10000))

# Destination Storage Channels Mapping
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

INLINE_BUTTONS = [
    [
        Button.url("Series ↗", "https://t.me/thebackroomseries"),
        Button.url("Anime ↗", "https://t.me/thebackroomanime"),
        Button.url("Movies ↗", "https://t.me/thebackroommovies")
    ]
]

INDIAN_LANG_CODES = {"hi", "te", "ta", "ml", "kn", "bn", "mr", "pa", "gu", "ur"}

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)

# Operational State Synchronization
FILE_COMPLETED_EVENT = asyncio.Event()
CAPTURED_FILES = set()
CURRENT_PIPELINE_ACTIVE = False
ACTIVE_CLEAN_TITLE = ""

# ============================================================================
# 2. LOCAL DEDUPLICATION DATABASE (SQLITE)
# ============================================================================
def normalize_title_key(title: str) -> str:
    clean = re.sub(r"\(\d{4}\)", "", title or "")
    clean = re.sub(r"\b(S\d{1,2}|E\d{1,3}|1080p|720p|480p|360p|BluRay|Pahe|MKV|MP4|HEVC|x265|x264|10Bit)\b.*$", "", clean, flags=re.I)
    clean = re.sub(r"[^a-zA-Z0-9]+", " ", clean).strip().lower()
    return clean or "unknown"

def init_storage_db():
    conn = sqlite3.connect("storage_index.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS indexed_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            clean_title TEXT NOT NULL,
            ep_tag TEXT NOT NULL,
            file_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(channel_id, clean_title, ep_tag)
        )
    """)
    conn.commit()
    conn.close()

def is_already_indexed(channel_id: int, clean_title: str, ep_tag: str) -> bool:
    conn = sqlite3.connect("storage_index.db")
    row = conn.execute(
        "SELECT 1 FROM indexed_files WHERE channel_id=? AND clean_title=? AND ep_tag=?",
        (channel_id, normalize_title_key(clean_title), ep_tag.upper().strip())
    ).fetchone()
    conn.close()
    return bool(row)

def record_indexed_file(channel_id: int, clean_title: str, ep_tag: str, file_name: str):
    conn = sqlite3.connect("storage_index.db")
    conn.execute(
        "INSERT OR IGNORE INTO indexed_files (channel_id, clean_title, ep_tag, file_name) VALUES (?, ?, ?, ?)",
        (channel_id, normalize_title_key(clean_title), ep_tag.upper().strip(), file_name)
    )
    conn.commit()
    conn.close()

init_storage_db()

# ============================================================================
# 3. TECHNICAL SPEC PARSER & CLEAN CAPTION FORMATTER
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

    dur_m = re.search(r"Duration\s*:\s*([^\r\n]+)", combined, re.I) or re.search(r"⏳\s*(\d{1,2}:\d{2}(?::\d{2})?)", combined)
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

# ============================================================
# 4. METADATA RESOLVER & CATEGORIZATION (TMDB + OMDB)
# ============================================================
async def fetch_meta(title):
    clean = normalize_title_key(title)
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
# 5. DYNAMIC BUTTON EVALUATION (RANKING RESOLUTIONS & EPISODE SIZES)
# ============================================================================
def score_quality_button(text: str) -> int:
    t = text.upper()
    score = 0
    if "1080P" in t: score += 1000
    elif "720P" in t: score += 700
    elif "480P" in t: score += 400
    elif "360P" in t: score += 100

    if "BLURAY" in t or "BD" in t: score += 200
    elif "WEB-DL" in t: score += 150

    if "HEVC" in t or "X265" in t or "10BIT" in t: score += 50
    return score

def select_best_quality_button(buttons):
    candidates = []
    for row in buttons:
        for btn in row:
            txt = btn.text or ""
            if any(skip in txt.lower() for skip in ["back", "close", "next", "page"]):
                continue
            s = score_quality_button(txt)
            if s > 0:
                candidates.append((s, btn))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None

def parse_size_mb(text: str) -> float:
    """Extracts file size in MB from button text like 'E01 [1.54 GB]' or 'E01 [269.21 MB]'."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(GB|MB|KB)", text, re.I)
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = m.group(2).upper()
    if unit == "GB":
        return val * 1024.0
    elif unit == "KB":
        return val / 1024.0
    return val

# ============================================================================
# 6. AUTO-JOIN CHANNELS & CALLBACK DEEP-LINK HANDLER
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
        print(f"⚠️ Channel join: {e}")
        return False

async def trigger_start_url(url: str) -> bool:
    """Parses a t.me/bot?start=XYZ deep-link and sends '/start XYZ' to the target bot."""
    if not url or "start=" not in url:
        return False
    parsed = urlparse(url)
    target_bot = parsed.path.strip("/") or SOURCE_BOT
    start_param = parse_qs(parsed.query).get("start", [""])[0]
    if target_bot and start_param:
        print(f"🔗 Following callback deep-link: @{target_bot} -> /start {start_param}")
        await client.send_message(target_bot, f"/start {start_param}")
        return True
    return False

async def smart_click_button(btn):
    """Clicks a button and automatically follows callback URL redirects if returned."""
    if getattr(btn, "url", None):
        if "start=" in btn.url:
            return await trigger_start_url(btn.url)
        return False

    res = await btn.click()
    # Critical Fix: If the bot answers the callback query with a ?start= URL, trigger it!
    res_url = getattr(res, "url", None)
    if res_url and "start=" in res_url:
        await trigger_start_url(res_url)
    return True

# ============================================================================
# 7. INCOMING MEDIA EVENT (INTERCEPT, FORWARD & UNBLOCK QUEUE)
# ============================================================================
@client.on(events.NewMessage(incoming=True))
async def on_media_arrival(event):
    if not event.is_private:
        return

    sender = await event.get_sender()
    if not getattr(sender, "bot", False):
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
        await client.send_file(
            dest_channel,
            file=msg.media,
            caption=final_caption,
            buttons=INLINE_BUTTONS,
            parse_mode="HTML"
        )
        print(f"🚀 Published to {cat_emoji} {cat.upper()} channel ({dest_channel})")

        ep_m = re.search(r"\b(E\d{1,3})\b", f"{raw_fn} {caption}", re.I)
        ep_tag = ep_m.group(1).upper() if ep_m else "MOVIE"
        title_key = ACTIVE_CLEAN_TITLE or meta["title"]
        record_indexed_file(dest_channel, title_key, ep_tag, raw_fn)

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
        FILE_COMPLETED_EVENT.set()

# ============================================================================
# 8. STATE-DRIVEN NAVIGATION & SEQUENTIAL DELIVERY LOOP
# ============================================================================
async def find_latest_menu_message(bot_entity, tracked_msg_id=None):
    """Finds the active inline menu message even if files/warnings were sent below it."""
    if tracked_msg_id:
        msg = await client.get_messages(bot_entity, ids=tracked_msg_id)
        if msg and msg.buttons:
            return msg

    msgs = await client.get_messages(bot_entity, limit=8)
    for m in msgs:
        if not m or not m.buttons:
            continue
        # Ignore the single red "Auto-Delete in 1 min" button on delivered files
        btn_texts = [b.text or "" for r in m.buttons for b in r]
        if len(btn_texts) == 1 and "auto-delete" in btn_texts[0].lower():
            continue
        return m
    return None

async def execute_autonomous_pipeline(query: str, send_initial_query: bool = True):
    global CURRENT_PIPELINE_ACTIVE, ACTIVE_CLEAN_TITLE
    if CURRENT_PIPELINE_ACTIVE:
        print("⚠️ Pipeline already active, ignoring duplicate trigger.")
        return

    CURRENT_PIPELINE_ACTIVE = True
    ACTIVE_CLEAN_TITLE = normalize_title_key(query)
    print(f"🎬 Initializing autonomous pipeline for: {query}")

    try:
        if send_initial_query:
            await client.send_message(SOURCE_BOT, query)
        await asyncio.sleep(3.5)

        tracked_menu_id = None

        # Stage 1: Traverse Menus (Title ➔ Season ➔ Quality ➔ Episode Grid)
        for step in range(15):
            msg = await find_latest_menu_message(SOURCE_BOT, tracked_menu_id)
            if not msg or not msg.buttons:
                await asyncio.sleep(2.5)
                continue

            tracked_menu_id = msg.id
            text = (msg.text or "").lower()

            # Check for Force-Subscribe (FSUB) join buttons
            joined = False
            for row in msg.buttons:
                for btn in row:
                    if getattr(btn, "url", None) and "t.me/" in btn.url:
                        t = (btn.text or "").lower()
                        if any(w in t for w in ["join", "channel", "subscribe", "backup"]):
                            await auto_join_channel(btn.url)
                            joined = True
                            await asyncio.sleep(1.5)

            if joined:
                for row in msg.buttons:
                    for btn in row:
                        if any(w in (btn.text or "").lower() for w in ["try again", "joined", "refresh", "check"]):
                            await smart_click_button(btn)
                            await asyncio.sleep(3.5)
                            break
                continue

            # Screen D: Episode Grid Reached (Check this FIRST in case header contains 'Season' or 'Quality')
            has_ep_buttons = any(re.search(r"\bE\d{1,3}\b", b.text or "", re.I) for r in msg.buttons for b in r)
            if "total episodes" in text or has_ep_buttons:
                print("📦 Episode Matrix identified. Handing off to sequential downloader...")
                await run_episode_queue(msg, query)
                break

            # Screen A: Title Selection
            if "choose the correct title" in text or "matching results" in text or "search results for" in text:
                first_btn = msg.buttons[0][0]
                print(f"👉 [Screen A] Selecting Title: {first_btn.text}")
                await smart_click_button(first_btn)
                await asyncio.sleep(3.5)
                continue

            # Screen B: Season Selection
            if "choose the season" in text or any("season" in (b.text or "").lower() for r in msg.buttons for b in r):
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
                    target_season = msg.buttons[0][0]

                print(f"👉 [Screen B] Selecting Season: {target_season.text}")
                await smart_click_button(target_season)
                await asyncio.sleep(3.5)
                continue

            # Screen C: Quality Selection
            if "select the available quality" in text or "quality" in text:
                best_qual = select_best_quality_button(msg.buttons)
                if best_qual:
                    print(f"👉 [Screen C] Selecting Quality: {best_qual.text}")
                    await smart_click_button(best_qual)
                    await asyncio.sleep(3.5)
                    continue
                else:
                    print("❌ No valid quality options found.")
                    break

            await asyncio.sleep(2.5)

    except Exception as e:
        print(f"⚠️ Pipeline execution error: {e}")
    finally:
        CURRENT_PIPELINE_ACTIVE = False

async def run_episode_queue(initial_msg, clean_title: str):
    menu_msg_id = initial_msg.id
    current_msg = initial_msg

    meta = await fetch_meta(clean_title)
    cat = detect_category(clean_title, "", meta)
    dest_channel = DEFAULT_CHANNELS.get(cat, DEFAULT_CHANNELS["other"])

    while True:
        # Group episodes by tag (E01, E02...) and pick the largest file size for each
        best_ep_map = {}
        ep_order = []
        next_button = None

        for row in current_msg.buttons:
            for btn in row:
                txt = (btn.text or "").strip()
                low = txt.lower()
                if any(nav in low for nav in ["back", "close", "page"]):
                    continue
                if "next" in low or "⏩" in txt:
                    next_button = btn
                    continue

                m = re.search(r"\b(E\d{1,3})\b", txt, re.I)
                ep_tag = m.group(1).upper() if m else txt
                size_mb = parse_size_mb(txt)

                if ep_tag not in best_ep_map:
                    ep_order.append(ep_tag)
                    best_ep_map[ep_tag] = (size_mb, btn)
                else:
                    # Keep the button with the largest file size (e.g., 1.54 GB over 269 MB)
                    if size_mb > best_ep_map[ep_tag][0]:
                        best_ep_map[ep_tag] = (size_mb, btn)

        episode_buttons = [(tag, best_ep_map[tag][1]) for tag in ep_order]
        print(f"📋 Selected {len(episode_buttons)} unique episodes on current page.")

        for ep_tag, btn in episode_buttons:
            if is_already_indexed(dest_channel, clean_title, ep_tag):
                print(f"⏭️ Skipping {ep_tag} — already in storage channel ({dest_channel}).")
                continue

            FILE_COMPLETED_EVENT.clear()
            print(f"⏳ Triggering {ep_tag} ({btn.text})...")

            try:
                await smart_click_button(btn)
            except Exception as err:
                print(f"⚠️ Click error on {ep_tag}: {err}")
                continue

            try:
                await asyncio.wait_for(FILE_COMPLETED_EVENT.wait(), timeout=90)
                print(f"✅ {ep_tag} completed successfully.")
            except asyncio.TimeoutError:
                print(f"⚠️ Delivery timed out for {ep_tag}. Checking if FSUB appeared...")
                # Check if clicking the episode prompted an FSUB join message
                latest = await client.get_messages(SOURCE_BOT, limit=2)
                for lm in latest:
                    if lm and lm.buttons:
                        for r in lm.buttons:
                            for b in r:
                                if getattr(b, "url", None) and "t.me/" in b.url:
                                    await auto_join_channel(b.url)

            await asyncio.sleep(4.0)
            # Refresh menu message reference by its exact ID so buttons stay fresh
            refreshed = await client.get_messages(SOURCE_BOT, ids=menu_msg_id)
            if refreshed and refreshed.buttons:
                current_msg = refreshed

        # Pagination: Move to next page using the tracked menu message ID
        if next_button:
            print("📄 Navigating to Next Page...")
            try:
                await smart_click_button(next_button)
                await asyncio.sleep(4.0)
                current_msg = await client.get_messages(SOURCE_BOT, ids=menu_msg_id)
                if not current_msg or not current_msg.buttons:
                    break
            except Exception as err:
                print(f"⚠️ Pagination error: {err}")
                break
        else:
            print(f"🎉 Completed all pages and episodes for: {clean_title}")
            await client.send_message(
                "me",
                f"🏁 <b>Extraction Finished:</b> All available episodes of <code>{clean_title}</code> processed.",
                parse_mode="HTML"
            )
            break

# ============================================================================
# 9. TRIGGERS: SAVED MESSAGES OR DIRECTLY IN SOURCE_BOT CHAT
# ============================================================================
@client.on(events.NewMessage(outgoing=True))
async def on_outgoing_trigger(event):
    text = (event.message.text or "").strip()
    if not text or text.startswith(("/", "✅", "⏳", "🏁", "⚠️")):
        return

    # Case 1: Sent to Saved Messages ("me")
    if event.chat_id == (await client.get_me()).id:
        query = re.sub(r"^\.get\s+", "", text, flags=re.I).strip()
        await event.reply(f"⏳ <b>Autonomous pipeline started:</b> <code>{query}</code>", parse_mode="HTML")
        asyncio.create_task(execute_autonomous_pipeline(query, send_initial_query=True))
        return

    # Case 2: Typed directly inside the SOURCE_BOT chat (like in your screenshots!)
    chat = await event.get_chat()
    chat_username = (getattr(chat, "username", "") or "").lower()
    if chat_username == SOURCE_BOT.lower():
        print(f"🎯 Detected manual search in @{SOURCE_BOT}: {text}")
        asyncio.create_task(execute_autonomous_pipeline(text, send_initial_query=False))

# ============================================================================
# 10. RENDER HEALTH CHECK SERVER & MAIN ENTRYPOINT
# ============================================================================
async def handle_ping(request):
    return web.json_response({
        "status": "online",
        "pipeline_active": CURRENT_PIPELINE_ACTIVE,
        "service": "Autonomous Media Syndication Pipeline"
    })

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🌐 HTTP Health server running on port {PORT}")

async def main():
    await start_web_server()
    if not SESSION_STRING:
        print("❌ ERROR: SESSION_STRING environment variable is missing!")
        return

    await client.start()
    print(f"🚀 Autonomous Media Pipeline is LIVE! Watching @{SOURCE_BOT} and Saved Messages.")
    await client.run_until_disconnected()

if __name__ == "__main__":
    loop.run_until_complete(main())
