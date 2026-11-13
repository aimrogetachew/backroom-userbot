import os
import re
import asyncio
import sqlite3
from collections import deque
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
from telethon.errors import FloodWaitError
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

# Operational State & Queue Synchronization
FILE_COMPLETED_EVENT = asyncio.Event()
CAPTURED_FILES = set()
META_CACHE = {}
TITLE_QUEUE = deque()
CURRENT_PIPELINE_ACTIVE = False
ACTIVE_CLEAN_TITLE = ""
ACTIVE_SEASON_TAG = "S01"
ACTIVE_EP_TAG = ""

# ============================================================================
# 2. LOCAL DEDUPLICATION DATABASE (SQLITE)
# ============================================================================
def normalize_title_key(title: str) -> str:
    clean = re.sub(r"\(\d{4}\)", "", title or "")
    clean = re.sub(
        r"\b(S\d{1,2}|Season\s*\d+|E\d{1,3}|Episode\s*\d+|1080p|720p|480p|360p|BluRay|Pahe|MKV|MP4|HEVC|x265|x264|10Bit|WEB-DL|HDRip)\b.*$",
        "",
        clean,
        flags=re.I
    )
    clean = re.sub(r"[^a-zA-Z0-9]+", " ", clean).strip().lower()
    return clean or "unknown"

def extract_season_ep_tag(text: str, default_season: str = "S01") -> str:
    """Normalizes episode tags to S01E01 format so Season 1 and Season 2 never collide."""
    s_m = re.search(r"\b(?:S|Season\s*)0*(\d{1,2})\b", text or "", re.I)
    e_m = re.search(r"\b(?:E|EP|Episode\s*)0*(\d{1,3})\b", text or "", re.I)

    s_tag = f"S{int(s_m.group(1)):02d}" if s_m else default_season.upper()
    if e_m:
        e_tag = f"E{int(e_m.group(1)):02d}"
        return f"{s_tag}{e_tag}"
    return "MOVIE"

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
# 4. METADATA RESOLVER & CATEGORIZATION (WITH MEMORY CACHE)
# ============================================================
async def fetch_meta(title):
    clean = normalize_title_key(title)
    if clean in META_CACHE:
        return META_CACHE[clean]

    genres, lang, runtime = "", "", ""
    is_series = bool(re.search(r"\b(S\d{1,2}|Season|E\d{1,3}|Episode)\b", title, re.I))

    async with aiohttp.ClientSession() as session:
        if TMDB_API_KEY:
            try:
                url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={clean}"
                async with session.get(url, timeout=3) as r:
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
                async with session.get(url, timeout=3) as r:
                    if r.status == 200:
                        data = await r.json()
                        if data.get("Response") == "True":
                            genres = data.get("Genre", "")
                            runtime = data.get("Runtime", "")
                            if data.get("Type") == "series":
                                is_series = True
            except Exception:
                pass

    result = {"title": clean, "genres": genres, "language": lang, "runtime": runtime, "is_series": is_series}
    META_CACHE[clean] = result
    return result

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
# 5. SMART BUTTON SCORING (TITLES, SEASONS, QUALITIES, SIZES)
# ============================================================================
def select_best_title_button(buttons, query: str):
    """Selects the best matching title button (preferring 📺 Series packs over raw single MKVs)."""
    clean_q = normalize_title_key(query)
    candidates = []

    for row in buttons:
        for btn in row:
            txt = (btn.text or "").strip()
            low = txt.lower()
            if any(skip in low for skip in ["back", "close", "next", "page"]):
                continue

            score = 0
            btn_clean = normalize_title_key(txt)
            if btn_clean == clean_q:
                score += 500
            elif clean_q in btn_clean:
                score += 300

            # Prefer multi-season / series entries ("📺 Solo Leveling - 3 Seasons") over raw single files ("01 Mkv")
            if "📺" in txt or "season" in low:
                score += 250
            if re.search(r"\b\d{2}\s*mkv\b", low):
                score -= 200

            candidates.append((score, btn))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return buttons[0][0]

def score_quality_button(text: str) -> int:
    t = text.upper()
    score = 0
    if "1080P" in t: score += 1000
    elif "720P" in t: score += 700
    elif "480P" in t: score += 400
    elif "360P" in t: score += 100

    if "BLURAY" in t or "BD" in t: score += 200
    elif "WEB-DL" in t: score += 150
    elif "HDRIP" in t: score += 120

    if "HEVC" in t or "X265" in t or "10BIT" in t: score += 60
    # Slight penalty to PAHE so pure BluRay HEVC ("1080P BLURAY HEVC QUALITY") wins first
    if "PAHE" in t: score -= 10
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
    m = re.search(r"(\d+(?:\.\d+)?)\s*(GB|MB|KB)", text or "", re.I)
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
# 6. AUTO-JOIN CHANNELS & NON-HANGING BUTTON CLICKER
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
    """Clicks an inline button with a 2.5s timeout so Telethon never hangs waiting for a popup."""
    if getattr(btn, "url", None):
        if "start=" in btn.url:
            return await trigger_start_url(btn.url)
        return False

    try:
        res = await asyncio.wait_for(btn.click(), timeout=2.5)
        res_url = getattr(res, "url", None)
        if res_url and "start=" in res_url:
            await trigger_start_url(res_url)
    except asyncio.TimeoutError:
        pass
    except FloodWaitError as fw:
        print(f"⏳ FloodWait during click: sleeping {fw.seconds}s...")
        await asyncio.sleep(fw.seconds + 1)
    except Exception as e:
        print(f"⚠️ Button click notice: {e}")
    return True

def get_menu_signature(msg) -> str:
    if not msg or not msg.buttons:
        return ""
    btns = "|".join(b.text or "" for r in msg.buttons for b in r)
    return f"{msg.id}:{msg.text or ''}:{btns}"

async def find_latest_menu_message(bot_entity, tracked_msg_id=None):
    """Finds the active menu message even when video files or warning messages are posted below it."""
    if tracked_msg_id:
        msg = await client.get_messages(bot_entity, ids=tracked_msg_id)
        if msg and msg.buttons:
            return msg

    msgs = await client.get_messages(bot_entity, limit=10)
    for m in msgs:
        if not m or not m.buttons:
            continue
        btn_texts = [b.text or "" for r in m.buttons for b in r]
        if len(btn_texts) == 1 and "auto-delete" in btn_texts[0].lower():
            continue
        return m
    return None

async def wait_for_menu_update(bot_entity, tracked_msg_id, old_sig: str, max_wait: float = 4.5):
    """Polls every 0.4s and returns immediately the moment the menu buttons change."""
    loops = int(max_wait / 0.4)
    for _ in range(loops):
        await asyncio.sleep(0.4)
        msg = await find_latest_menu_message(bot_entity, tracked_msg_id)
        if msg and get_menu_signature(msg) != old_sig:
            return msg
    return await find_latest_menu_message(bot_entity, tracked_msg_id)

# ============================================================================
# 7. INCOMING MEDIA EVENT (INTERCEPT, DEDUPLICATE, FORWARD & UNBLOCK)
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

    title_for_meta = ACTIVE_CLEAN_TITLE or raw_fn
    meta = await fetch_meta(title_for_meta)
    cat = detect_category(raw_fn, caption, meta)
    dest_channel = DEFAULT_CHANNELS.get(cat, DEFAULT_CHANNELS["other"])
    cat_emoji = CATEGORY_EMOJIS.get(cat, "📁")

    # Determine exact Season+Episode tag (e.g., S01E01)
    ep_tag = ACTIVE_EP_TAG or extract_season_ep_tag(f"{raw_fn} {caption}", ACTIVE_SEASON_TAG)
    title_key = ACTIVE_CLEAN_TITLE or meta["title"]

    # Double-check duplicate before uploading
    if is_already_indexed(dest_channel, title_key, ep_tag):
        print(f"⏭️ Duplicate file intercepted ({title_key} {ep_tag}) — skipping upload.")
        FILE_COMPLETED_EVENT.set()
        return

    final_caption = build_final_caption(raw_fn, caption, meta.get("runtime", ""))

    try:
        await client.send_file(
            dest_channel,
            file=msg.media,
            caption=final_caption,
            buttons=INLINE_BUTTONS,
            parse_mode="HTML"
        )
        print(f"🚀 Published {ep_tag} to {cat_emoji} {cat.upper()} channel ({dest_channel})")

        record_indexed_file(dest_channel, title_key, ep_tag, raw_fn)

        await client.send_message(
            "me",
            f"✅ <b>Published to Storage!</b>\n"
            f"• <b>Title:</b> <code>{title_key.title()} ({ep_tag})</code>\n"
            f"• <b>File:</b> <code>{extract_real_filename(raw_fn, caption)}</code>\n"
            f"• <b>Channel:</b> {cat_emoji} <code>{cat.upper()}</code> ➔ <code>{dest_channel}</code>",
            parse_mode="HTML"
        )
    except FloodWaitError as fw:
        print(f"⏳ FloodWait on upload: sleeping {fw.seconds}s...")
        await asyncio.sleep(fw.seconds + 1)
    except Exception as e:
        print(f"❌ Storage Forward error: {e}")
    finally:
        FILE_COMPLETED_EVENT.set()

# ============================================================================
# 8. AUTO-RECOVERY NAVIGATOR (RE-SENDS TITLE IF MENU EXPIRES OR NEXT SEASON)
# ============================================================================
async def navigate_to_episode_grid(query: str, target_season_num: int = 1, send_fresh: bool = True):
    """
    Sends the title (if send_fresh=True) and clicks through:
    Title ➔ Season (target_season_num) ➔ Best Quality ➔ Returns the Episode Grid message.
    """
    if send_fresh:
        print(f"📨 Sending search query to @{SOURCE_BOT}: '{query}' (Target: Season {target_season_num:02d})")
        await client.send_message(SOURCE_BOT, query)
        await asyncio.sleep(2.0)

    tracked_menu_id = None
    available_seasons = []

    for _ in range(15):
        msg = await find_latest_menu_message(SOURCE_BOT, tracked_menu_id)
        if not msg or not msg.buttons:
            await asyncio.sleep(1.0)
            continue

        tracked_menu_id = msg.id
        text = (msg.text or "").lower()
        old_sig = get_menu_signature(msg)

        # 1. Bypass Force-Subscribe (FSUB) if prompted
        joined = False
        for row in msg.buttons:
            for btn in row:
                if getattr(btn, "url", None) and "t.me/" in btn.url:
                    t = (btn.text or "").lower()
                    if any(w in t for w in ["join", "channel", "subscribe", "backup"]):
                        await auto_join_channel(btn.url)
                        joined = True
                        await asyncio.sleep(1.0)

        if joined:
            for row in msg.buttons:
                for btn in row:
                    if any(w in (btn.text or "").lower() for w in ["try again", "joined", "refresh", "check"]):
                        await smart_click_button(btn)
                        await wait_for_menu_update(SOURCE_BOT, tracked_menu_id, old_sig)
                        break
            continue

        # 2. Screen D: Episode Matrix Reached
        has_ep_buttons = any(re.search(r"\bE\d{1,3}\b", b.text or "", re.I) for r in msg.buttons for b in r)
        if "total episodes" in text or has_ep_buttons:
            return msg, available_seasons

        # 3. Screen A: Title Selection
        if "choose the correct title" in text or "matching results" in text or "search results for" in text:
            best_title_btn = select_best_title_button(msg.buttons, query)
            print(f"👉 [Screen 1/Title] Clicking: {best_title_btn.text}")
            await smart_click_button(best_title_btn)
            await wait_for_menu_update(SOURCE_BOT, tracked_menu_id, old_sig)
            continue

        # 4. Screen B: Season Selection
        if "choose the season" in text or any("season" in (b.text or "").lower() for r in msg.buttons for b in r):
            season_btns = {}
            fallback_btn = None
            for row in msg.buttons:
                for btn in row:
                    t = (btn.text or "").strip()
                    low = t.lower()
                    if "close" in low or "back" in low:
                        continue
                    m = re.search(r"season\s*0*(\d+)", low)
                    if m:
                        s_num = int(m.group(1))
                        season_btns[s_num] = btn
                    elif "season all" in low:
                        fallback_btn = btn

            available_seasons = sorted(season_btns.keys())
            chosen_btn = season_btns.get(target_season_num) or fallback_btn or msg.buttons[0][0]
            print(f"👉 [Screen 2/Season] Clicking: {chosen_btn.text}")
            await smart_click_button(chosen_btn)
            await wait_for_menu_update(SOURCE_BOT, tracked_menu_id, old_sig)
            continue

        # 5. Screen C: Quality Selection
        if "select the available quality" in text or "quality" in text:
            best_qual = select_best_quality_button(msg.buttons)
            if best_qual:
                print(f"👉 [Screen 3/Quality] Clicking: {best_qual.text}")
                await smart_click_button(best_qual)
                await wait_for_menu_update(SOURCE_BOT, tracked_menu_id, old_sig)
                continue
            else:
                print("❌ No valid quality buttons found.")
                break

        await asyncio.sleep(1.0)

    return None, available_seasons

# ============================================================================
# 9. SEQUENTIAL EPISODE DOWNLOADER WITH PAGE-SPLIT & AUTO-CONTINUATION
# ============================================================================
async def download_season_episodes(initial_msg, query: str, season_num: int):
    global ACTIVE_SEASON_TAG, ACTIVE_EP_TAG
    ACTIVE_SEASON_TAG = f"S{season_num:02d}"
    menu_msg_id = initial_msg.id
    current_msg = initial_msg
    current_page = 1

    meta = await fetch_meta(query)
    cat = detect_category(query, "", meta)
    dest_channel = DEFAULT_CHANNELS.get(cat, DEFAULT_CHANNELS["other"])

    while True:
        best_ep_map = {}
        ep_counts = {}
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

                m = re.search(r"\bE0*(\d{1,3})\b", txt, re.I)
                if m:
                    ep_tag = f"{ACTIVE_SEASON_TAG}E{int(m.group(1)):02d}"
                else:
                    ep_tag = f"{ACTIVE_SEASON_TAG}_{txt}"

                size_mb = parse_size_mb(txt)
                ep_counts[ep_tag] = ep_counts.get(ep_tag, 0) + 1

                if ep_tag not in best_ep_map:
                    ep_order.append(ep_tag)
                    best_ep_map[ep_tag] = (size_mb, btn)
                else:
                    if size_mb > best_ep_map[ep_tag][0]:
                        best_ep_map[ep_tag] = (size_mb, btn)

        # Page-Boundary Split Fix (e.g., E04 [368 MB] at end of Page 1 when E04 [1.6 GB] is on Page 2)
        if next_button and len(ep_order) >= 2:
            last_tag = ep_order[-1]
            last_size = best_ep_map[last_tag][0]
            prev_sizes = [best_ep_map[t][0] for t in ep_order[:-1] if best_ep_map[t][0] > 0]
            avg_prev_size = (sum(prev_sizes) / len(prev_sizes)) if prev_sizes else 0

            if ep_counts.get(last_tag, 0) == 1 and avg_prev_size > 0 and last_size < (avg_prev_size * 0.55):
                print(f"💡 Deferring {last_tag} ({last_size:.0f} MB) to next page so we grab its full-size BluRay version.")
                ep_order.pop()

        episode_buttons = [(tag, best_ep_map[tag][1]) for tag in ep_order]
        print(f"📋 [{ACTIVE_SEASON_TAG} • Page {current_page}] Found {len(episode_buttons)} episodes to process.")

        for ep_tag, btn in episode_buttons:
            if is_already_indexed(dest_channel, ACTIVE_CLEAN_TITLE, ep_tag):
                print(f"⏭️ Skipping {ep_tag} — already indexed in channel ({dest_channel}).")
                continue

            ACTIVE_EP_TAG = ep_tag
            FILE_COMPLETED_EVENT.clear()
            print(f"⏳ Requesting {ep_tag} ➔ Clicking '{btn.text}'...")

            await smart_click_button(btn)

            try:
                await asyncio.wait_for(FILE_COMPLETED_EVENT.wait(), timeout=90)
                print(f"✅ {ep_tag} forwarded and indexed!")
            except asyncio.TimeoutError:
                print(f"⚠️ Timeout on {ep_tag}. Checking if menu expired or FSUB appeared...")

            await asyncio.sleep(1.5)

            # Check if the menu message is still alive; if deleted/expired, re-send title & recover!
            check_msg = await client.get_messages(SOURCE_BOT, ids=menu_msg_id)
            if not check_msg or not check_msg.buttons:
                print(f"🔄 Menu message expired! Re-sending title '{query}' to continue remaining episodes...")
                recovered_msg, _ = await navigate_to_episode_grid(query, season_num, send_fresh=True)
                if recovered_msg:
                    menu_msg_id = recovered_msg.id
                    current_msg = recovered_msg
                    # Navigate back to current_page if we were on page > 1
                    for _ in range(1, current_page):
                        nxt = next((b for r in current_msg.buttons for b in r if "next" in (b.text or "").lower() or "⏩" in (b.text or "")), None)
                        if nxt:
                            old_s = get_menu_signature(current_msg)
                            await smart_click_button(nxt)
                            current_msg = await wait_for_menu_update(SOURCE_BOT, menu_msg_id, old_s)

        # Refresh menu before clicking Next Page
        refreshed = await client.get_messages(SOURCE_BOT, ids=menu_msg_id)
        if refreshed and refreshed.buttons:
            current_msg = refreshed
            next_button = next((b for r in current_msg.buttons for b in r if "next" in (b.text or "").lower() or "⏩" in (b.text or "")), None)

        if next_button:
            current_page += 1
            print(f"📄 Clicking Next ⏩ to load Page {current_page}...")
            old_sig = get_menu_signature(current_msg)
            await smart_click_button(next_button)
            current_msg = await wait_for_menu_update(SOURCE_BOT, menu_msg_id, old_sig)
            if not current_msg or not current_msg.buttons:
                break
        else:
            print(f"✅ Finished all pages for {query} ({ACTIVE_SEASON_TAG}).")
            break

# ============================================================================
# 10. MASTER PIPELINE & QUEUE WORKER
# ============================================================================
async def process_queue():
    global CURRENT_PIPELINE_ACTIVE, ACTIVE_CLEAN_TITLE, ACTIVE_EP_TAG
    if CURRENT_PIPELINE_ACTIVE:
        return

    CURRENT_PIPELINE_ACTIVE = True
    try:
        while TITLE_QUEUE:
            query, send_initial = TITLE_QUEUE.popleft()
            ACTIVE_CLEAN_TITLE = normalize_title_key(query)
            ACTIVE_EP_TAG = ""

            # Check if user explicitly asked for a specific season (e.g., "Solo Leveling S02")
            explicit_s = re.search(r"\b(?:S|Season\s*)0*(\d{1,2})\b", query, re.I)
            start_season = int(explicit_s.group(1)) if explicit_s else 1

            ep_msg, all_seasons = await navigate_to_episode_grid(query, start_season, send_fresh=send_initial)
            if not ep_msg:
                print(f"❌ Could not reach episode grid for: {query}")
                continue

            await download_season_episodes(ep_msg, query, start_season)

            # If the user didn't specify a single season and the series has more seasons (Season 02, 03...),
            # automatically re-send the title to download the remaining seasons!
            if not explicit_s and len(all_seasons) > 1:
                for next_s in all_seasons:
                    if next_s <= start_season:
                        continue
                    print(f"🔁 Re-sending '{query}' to start Season {next_s:02d}...")
                    await asyncio.sleep(2.0)
                    next_ep_msg, _ = await navigate_to_episode_grid(query, next_s, send_fresh=True)
                    if next_ep_msg:
                        await download_season_episodes(next_ep_msg, query, next_s)

            await client.send_message(
                "me",
                f"🏁 <b>Extraction Finished:</b> All episodes of <code>{query}</code> have been processed!",
                parse_mode="HTML"
            )
    except Exception as e:
        print(f"⚠️ Master pipeline error: {e}")
    finally:
        CURRENT_PIPELINE_ACTIVE = False
        ACTIVE_EP_TAG = ""

def enqueue_title(query: str, send_initial: bool = True):
    TITLE_QUEUE.append((query, send_initial))
    asyncio.create_task(process_queue())

# ============================================================================
# 11. TRIGGERS: SAVED MESSAGES OR TYPING IN SOURCE_BOT CHAT
# ============================================================================
@client.on(events.NewMessage(outgoing=True))
async def on_outgoing_trigger(event):
    text = (event.message.text or "").strip()
    if not text or text.startswith(("/", "✅", "⏳", "🏁", "⚠️", "🔗")):
        return

    me = await client.get_me()
    if event.chat_id == me.id:
        query = re.sub(r"^\.get\s+", "", text, flags=re.I).strip()
        pos = len(TITLE_QUEUE) + (1 if CURRENT_PIPELINE_ACTIVE else 0)
        await event.reply(
            f"⏳ <b>Added to Autonomous Queue (#{pos}):</b> <code>{query}</code>",
            parse_mode="HTML"
        )
        enqueue_title(query, send_initial=True)
        return

    chat = await event.get_chat()
    chat_username = (getattr(chat, "username", "") or "").lower()
    if chat_username == SOURCE_BOT.lower() and not CURRENT_PIPELINE_ACTIVE:
        print(f"🎯 Manual search detected in @{SOURCE_BOT}: {text}")
        enqueue_title(text, send_initial=False)

# ============================================================================
# 12. RENDER HEALTH CHECK SERVER & MAIN ENTRYPOINT
# ============================================================================
async def handle_ping(request):
    return web.json_response({
        "status": "online",
        "pipeline_active": CURRENT_PIPELINE_ACTIVE,
        "queue_length": len(TITLE_QUEUE),
        "active_title": ACTIVE_CLEAN_TITLE,
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
