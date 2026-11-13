import os
import re
import json
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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

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

ALL_STORAGE_CHANNELS = sorted(list(set(DEFAULT_CHANNELS.values())))

CATEGORY_EMOJIS = {
    "hindi": "🇮🇳", "english": "🌎", "series": "📺", "amharic": "🇪🇹",
    "korean": "🇰🇷", "horror": "👻", "action": "💥", "anime": "⛩",
    "animation": "🧸", "upcoming": "🔔", "turkish": "🇹🇷", "other": "🎬"
}

# Fallback Telethon buttons (used if BOT_TOKEN is not set)
INLINE_BUTTONS = [
    [
        Button.url("Series ↗", "https://t.me/thebackroomseries"),
        Button.url("Anime ↗", "https://t.me/thebackroomanime"),
        Button.url("Movies ↗", "https://t.me/thebackroommovies")
    ]
]

# Bot API 9.4 Exact Green ("style": "success") Inline Keyboard Markup
GREEN_BOT_API_MARKUP = {
    "inline_keyboard": [
        [
            {"text": "Series", "url": "https://t.me/thebackroomseries", "style": "success"},
            {"text": "Anime", "url": "https://t.me/thebackroomanime", "style": "success"},
            {"text": "Movies", "url": "https://t.me/thebackroommovies", "style": "success"}
        ]
    ]
}

TARGET_RESOLUTIONS = ["360P", "480P", "720P", "1080P", "2160P"]
INDIAN_LANG_CODES = {"hi", "te", "ta", "ml", "kn", "bn", "mr", "pa", "gu", "ur"}

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)

# Operational State & Queue Synchronization
FILE_COMPLETED_EVENT = asyncio.Event()
CAPTURED_FILES = set()
SYNCED_TITLES = set()
META_CACHE = {}
TITLE_QUEUE = deque()

CURRENT_PIPELINE_ACTIVE = False
ACTIVE_CLEAN_TITLE = ""
ACTIVE_SEASON_TAG = "S01"
ACTIVE_EP_TAG = ""
ACTIVE_RES_TAG = ""

# ============================================================================
# 2. CROSS-CHANNEL DEDUPLICATION DATABASE + LIVE CHANNEL SCANNER
# ============================================================================
def normalize_title_key(title: str) -> str:
    clean = re.sub(r"\(\d{4}\)|\[\d{4}\]", "", title or "")
    clean = re.sub(
        r"\b(S\d{1,2}|Season\s*\d+|E\d{1,3}|EP\.?\s*\d+|Episode\s*\d+|360p|480p|540p|720p|1080p|1440p|2160p|4K|BluRay|WEB-?DL|WEBRip|HDRip|Pahe|MKV|MP4|HEVC|x265|x264|10Bit|AAC|DDP\d?\.\d?|ESub|Dual\s*Audio|Multi\s*Audio)\b.*$",
        "",
        clean,
        flags=re.I
    )
    clean = re.sub(r"[^a-zA-Z0-9]+", " ", clean).strip().lower()
    return clean or "unknown"

def extract_resolution_tag(text: str, default_res: str = "HD") -> str:
    m = re.search(r"\b(360p|480p|540p|720p|1080p|1440p|2160p|4K)\b", text or "", re.I)
    if m:
        r = m.group(1).upper()
        return "2160P" if r == "4K" else r
    return default_res.upper()

def extract_season_ep_tag(text: str, default_season: str = "S01") -> str:
    s_m = re.search(r"\b(?:S|Season\s*)0*(\d{1,2})\b", text or "", re.I)
    e_m = re.search(r"\b(?:E|EP\.?|Episode\s*)0*(\d{1,3})\b", text or "", re.I)

    s_tag = f"S{int(s_m.group(1)):02d}" if s_m else default_season.upper()
    if e_m:
        e_tag = f"E{int(e_m.group(1)):02d}"
        return f"{s_tag}{e_tag}"
    return "MOVIE"

def init_storage_db():
    conn = sqlite3.connect("storage_index.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS indexed_files_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            clean_title TEXT NOT NULL,
            ep_tag TEXT NOT NULL,
            res_tag TEXT NOT NULL,
            file_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(clean_title, ep_tag, res_tag)
        )
    """)
    conn.commit()
    conn.close()

def is_already_in_any_channel(clean_title: str, ep_tag: str, res_tag: str = "") -> bool:
    """Checks if this title + episode/movie + resolution already exists in ANY storage channel."""
    t_key = normalize_title_key(clean_title)
    e_key = ep_tag.upper().strip()
    r_key = (res_tag or "").upper().strip()

    conn = sqlite3.connect("storage_index.db")
    if r_key and r_key != "ANY":
        row = conn.execute(
            "SELECT channel_id FROM indexed_files_v2 WHERE clean_title=? AND ep_tag=? AND res_tag=?",
            (t_key, e_key, r_key)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT channel_id FROM indexed_files_v2 WHERE clean_title=? AND ep_tag=?",
            (t_key, e_key)
        ).fetchone()
    conn.close()
    return bool(row)

def record_indexed_file(channel_id: int, clean_title: str, ep_tag: str, res_tag: str, file_name: str):
    conn = sqlite3.connect("storage_index.db")
    conn.execute(
        "INSERT OR IGNORE INTO indexed_files_v2 (channel_id, clean_title, ep_tag, res_tag, file_name) VALUES (?, ?, ?, ?, ?)",
        (
            channel_id,
            normalize_title_key(clean_title),
            ep_tag.upper().strip(),
            (res_tag or "HD").upper().strip(),
            file_name
        )
    )
    conn.commit()
    conn.close()

async def sync_title_across_all_channels(query: str):
    """
    Scans ALL storage channels on Telegram for existing posts of this title
    so we never duplicate even after a server restart.
    """
    clean_q = normalize_title_key(query)
    if not clean_q or clean_q == "unknown" or clean_q in SYNCED_TITLES:
        return

    SYNCED_TITLES.add(clean_q)
    print(f"🔍 Scanning all {len(ALL_STORAGE_CHANNELS)} storage channels for existing '{clean_q}' files...")
    found_count = 0

    for ch_id in ALL_STORAGE_CHANNELS:
        try:
            async for msg in client.iter_messages(ch_id, search=clean_q, limit=80):
                if not (msg.video or msg.document or msg.text):
                    continue
                fn = (msg.file.name if msg.file else "") or ""
                txt = f"{fn}\n{msg.text or ''}"
                msg_title = normalize_title_key(extract_real_filename(fn, msg.text or ""))
                if clean_q not in msg_title and msg_title not in clean_q:
                    continue

                ep_tag = extract_season_ep_tag(txt, "S01")
                res_tag = extract_resolution_tag(txt, "HD")
                record_indexed_file(ch_id, clean_q, ep_tag, res_tag, fn or "existing_channel_post")
                found_count += 1
        except Exception:
            pass

    if found_count:
        print(f"✅ Synced {found_count} existing files for '{clean_q}' across storage channels.")

init_storage_db()

# ============================================================================
# 3. ADVANCED TECHNICAL SPEC PARSER & CAPTION BUILDER
# ============================================================================
def format_size_human(size_bytes: int) -> str:
    if not size_bytes:
        return ""
    mb = size_bytes / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.1f} MB"

def extract_real_filename(filename="", caption=""):
    cap = str(caption or "")
    m = re.search(r"File\s*Name\s*:\s*\r?\n?([^\r\n]+)", cap, re.I)
    if m and len(m.group(1).strip()) >= 4:
        fn = m.group(1).strip()
    else:
        fn = str(filename or "Media.mkv").strip()
    return re.sub(r"https?://\S+|t\.me/\S+|@[A-Za-z0-9_]+", "", fn).strip()

def extract_tech_specs(filename="", caption="", file_size_bytes=0):
    real_fn = extract_real_filename(filename, caption)
    combined = f"{real_fn}\n{caption or ''}"

    qual = extract_resolution_tag(combined, ACTIVE_RES_TAG or "1080P")

    # Source / Rip Type
    source = ""
    for pat, label in [
        (r"\b(?:bluray|bdrip|brrip|bd)\b", "BluRay"),
        (r"\bweb-?dl\b", "WEB-DL"),
        (r"\bwebrip\b", "WEBRip"),
        (r"\bhdrip\b", "HDRip"),
        (r"\bamzn\b", "AMZN"),
        (r"\bnf|netflix\b", "NF")
    ]:
        if re.search(pat, combined, re.I):
            source = label
            break

    # Video Codec & Bit Depth
    codec = "x264"
    if re.search(r"\b(x265|h265|hevc)\b", combined, re.I):
        codec = "HEVC x265"
    elif re.search(r"\bav1\b", combined, re.I):
        codec = "AV1"
    if re.search(r"\b10\s*-?bit\b", combined, re.I) and "10-Bit" not in codec:
        codec += " 10-Bit"

    # Audio codec detection
    aud_codec = "AAC 2.0"
    if re.search(r"\bddp\s*5\.1\b", combined, re.I):
        aud_codec = "DDP 5.1"
    elif re.search(r"\bddp\s*2\.0\b", combined, re.I):
        aud_codec = "DDP 2.0"
    elif re.search(r"\bac3\b", combined, re.I):
        aud_codec = "AC3"

    br_m = re.search(r"Video\s*Bitrate\s*:\s*([^\r\n]+)", combined, re.I) or re.search(r"\b(\d{3,5}\s*kbps)\b", combined, re.I)
    bitrate = br_m.group(1).strip() if br_m else ""

    dur_m = re.search(r"Duration\s*:\s*([^\r\n]+)", combined, re.I) or re.search(r"⏳\s*(\d{1,2}:\d{2}(?::\d{2})?)", combined)
    duration = dur_m.group(1).strip() if dur_m else ""

    for_tv = bool(re.search(r"Can\s*be\s*played\s*on\s*TV|FOR\s*TV", combined, re.I) or "x264" in codec)
    has_sub = bool(re.search(r"Has\s*[a-zA-Z]*\s*subtitles|\b(?:sub|subs|esub|multisub|mmsub)\b|\.mkv\b", combined, re.I))

    # Extract each language on its own bullet line (like in your screenshot!)
    audio_lines = []
    aud_section = re.search(r"Audio\s*List\s*:\s*((?:\r?\n\s*\*[^\r\n]+)+)", caption or "", re.I)
    if aud_section:
        for ln in aud_section.group(1).splitlines():
            if ln.strip().startswith("*"):
                audio_lines.append(ln.strip())

    detected_langs = []
    for pat, name in [
        (r"\b(?:hindi|hin)\b", "Hindi"),
        (r"\b(?:japanese|jap|jpn)\b", "Japanese"),
        (r"\b(?:english|eng)\b", "English"),
        (r"\b(?:korean|kor)\b", "Korean"),
        (r"\b(?:tamil|tam)\b", "Tamil"),
        (r"\b(?:telugu|tel)\b", "Telugu"),
        (r"\b(?:malayalam|mal)\b", "Malayalam"),
        (r"\b(?:turkish|tur)\b", "Turkish"),
        (r"\b(?:amharic|amh|ትርጉም)\b", "Amharic")
    ]:
        if re.search(pat, combined, re.I):
            detected_langs.append(name)

    if not audio_lines:
        if detected_langs:
            for lang_name in detected_langs:
                audio_lines.append(f"* {aud_codec} — {lang_name}")
        else:
            audio_lines = [f"* {aud_codec} — Original Audio"]

    # Determine Dual / Multi Audio badge
    audio_mode = ""
    if len(detected_langs) >= 3 or re.search(r"\bmulti\s*audio\b", combined, re.I):
        audio_mode = f"Multi Audio ({', '.join(detected_langs[:4])})" if detected_langs else "Multi Audio"
    elif len(detected_langs) == 2 or re.search(r"\bdual\s*audio\b", combined, re.I):
        audio_mode = f"Dual Audio ({' + '.join(detected_langs)})" if detected_langs else "Dual Audio"

    return {
        "real_filename": real_fn,
        "quality": qual,
        "source": source,
        "codec": codec,
        "audio_mode": audio_mode,
        "size_str": format_size_human(file_size_bytes),
        "duration": duration,
        "bitrate": bitrate,
        "for_tv": for_tv,
        "has_sub": has_sub,
        "audio_lines": audio_lines
    }

def build_final_caption(filename, caption, meta_runtime="", file_size_bytes=0):
    sp = extract_tech_specs(filename, caption, file_size_bytes)
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

    spec_parts = [sp["quality"]]
    if sp["source"]:
        spec_parts.append(sp["source"])
    spec_parts.append(sp["codec"])
    if sp["size_str"]:
        spec_parts.append(sp["size_str"])
    tech.append(f"<b>Specs:</b> {' • '.join(spec_parts)}")

    if sp["audio_mode"]:
        tech.append(f"<b>Audio Track:</b> {sp['audio_mode']}")

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
# 4. METADATA RESOLVER & CATEGORIZATION
# ============================================================================
async def fetch_meta(title):
    clean = normalize_title_key(title)
    if clean in META_CACHE:
        return META_CACHE[clean]

    genres, lang, runtime = "", "", ""
    is_series = bool(re.search(r"\b(S\d{1,2}|Season|E\d{1,3}|EP\.?\s*\d+|Episode)\b", title, re.I))

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

    if re.search(r"\b(anime|demon slayer|solo leveling|jujutsu|naruto|one piece|bleach|attack on titan|kimetsu)\b", combined) or (
        "animation" in meta.get("genres", "").lower() and lang in ("ja", "zh")
    ):
        return "anime"

    if "animation" in meta.get("genres", "").lower():
        return "animation"

    if meta.get("is_series") or re.search(r"\b(S\d{1,2}\s*E\d{1,3}|Season\s*\d+|Episode\s*\d+|EP\.?\s*\d+)\b", combined):
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
# 5. EXACT GREEN INLINE BUTTON PUBLISHER (USERBOT + BOT API HYBRID)
# ============================================================================
async def publish_file_with_green_buttons(dest_channel: int, media, final_caption: str):
    """
    1. Userbot sends the file (even 2GB+) to dest_channel (bypasses 50MB Bot API upload limit).
    2. If BOT_TOKEN is set, Bot API copies the message in-place with 'style': 'success' (Green)
       inline buttons [Series] [Anime] [Movies] and deletes the temporary userbot message.
    """
    sent_msg = await client.send_file(
        dest_channel,
        file=media,
        caption=final_caption,
        buttons=INLINE_BUTTONS,
        parse_mode="HTML"
    )

    if not BOT_TOKEN or not sent_msg:
        return sent_msg

    try:
        api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/copyMessage"
        payload = {
            "chat_id": dest_channel,
            "from_chat_id": dest_channel,
            "message_id": sent_msg.id,
            "caption": final_caption,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(GREEN_BOT_API_MARKUP)
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, data=payload, timeout=10) as resp:
                data = await resp.json()
                if data.get("ok"):
                    # Delete the temporary un-buttoned userbot message
                    await client.delete_messages(dest_channel, [sent_msg.id])
                else:
                    print(f"⚠️ Bot API copyMessage notice: {data.get('description')}")
    except Exception as e:
        print(f"⚠️ Green button attach notice (file still published): {e}")

    return sent_msg

# ============================================================================
# 6. SMART BUTTON SCORING & MULTI-RESOLUTION GROUPING
# ============================================================================
def select_best_title_button(buttons, query: str):
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

            if "📺" in txt or "season" in low:
                score += 250
            elif "🎬" in txt and not re.search(r"\b\d{2}\s*mkv\b", low):
                score += 150
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
    if "2160P" in t or "4K" in t: score += 1200
    elif "1080P" in t: score += 1000
    elif "720P" in t: score += 700
    elif "480P" in t: score += 400
    elif "360P" in t: score += 200

    if "MULTI" in t or "DUAL" in t: score += 250
    if "BLURAY" in t or "BD" in t: score += 200
    elif "WEB-DL" in t: score += 150
    elif "HDRIP" in t: score += 120

    if "HEVC" in t or "X265" in t or "10BIT" in t: score += 60
    if "PAHE" in t: score -= 10
    return score

def collect_resolution_quality_buttons(buttons):
    """
    Groups quality buttons by resolution (360P, 480P, 720P, 1080P, 2160P)
    and picks the highest-scored option (preferring Dual/Multi-Audio & BluRay/HEVC) for EACH resolution.
    """
    res_map = {}
    for row in buttons:
        for btn in row:
            txt = (btn.text or "").strip()
            low = txt.lower()
            if any(skip in low for skip in ["back", "close", "next", "page"]):
                continue
            s = score_quality_button(txt)
            if s <= 0:
                continue
            res = extract_resolution_tag(txt, "OTHER")
            if res not in res_map or s > res_map[res][0]:
                res_map[res] = (s, txt, btn)

    ordered = []
    for r in TARGET_RESOLUTIONS + ["OTHER"]:
        if r in res_map:
            ordered.append((r, res_map[r][1], res_map[r][2]))
    return ordered

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
# 7. AUTO-JOIN CHANNELS & NON-HANGING CLICK HELPERS
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
    loops = int(max_wait / 0.4)
    for _ in range(loops):
        await asyncio.sleep(0.4)
        msg = await find_latest_menu_message(bot_entity, tracked_msg_id)
        if msg and get_menu_signature(msg) != old_sig:
            return msg
    return await find_latest_menu_message(bot_entity, tracked_msg_id)

def is_file_or_episode_grid(msg) -> bool:
    """Detects both Series Episode Grids (E01, E02) and Movie File Grids ([450 MB], [1.4 GB], .mkv)."""
    if not msg or not msg.buttons:
        return False
    text = (msg.text or "").lower()
    if "total episodes" in text or "total files" in text:
        return True
    for r in msg.buttons:
        for b in r:
            bt = b.text or ""
            if re.search(r"\bE\d{1,3}\b", bt, re.I):
                return True
            if re.search(r"\[\s*\d+(?:\.\d+)?\s*(?:MB|GB)\s*\]", bt, re.I) and "quality" not in bt.lower():
                return True
    return False

# ============================================================================
# 8. INCOMING MEDIA EVENT (INTERCEPT, CROSS-CHANNEL CHECK, FORWARD & UNBLOCK)
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
    file_size = msg.file.size if msg.file else 0
    file_id = f"{msg.id}_{file_size}"
    if file_id in CAPTURED_FILES:
        return
    CAPTURED_FILES.add(file_id)

    raw_fn = (msg.file.name if msg.file else None) or (msg.text or "Media.mkv").split("\n")[0]
    caption = msg.text or ""
    combined_text = f"{raw_fn}\n{caption}"

    print(f"📥 Received file: {raw_fn} ({format_size_human(file_size)})")

    title_for_meta = ACTIVE_CLEAN_TITLE or raw_fn
    meta = await fetch_meta(title_for_meta)
    cat = detect_category(raw_fn, caption, meta)
    dest_channel = DEFAULT_CHANNELS.get(cat, DEFAULT_CHANNELS["other"])
    cat_emoji = CATEGORY_EMOJIS.get(cat, "📁")

    ep_tag = ACTIVE_EP_TAG or extract_season_ep_tag(combined_text, ACTIVE_SEASON_TAG)
    res_tag = extract_resolution_tag(combined_text, ACTIVE_RES_TAG or "1080P")
    title_key = ACTIVE_CLEAN_TITLE or meta["title"]

    # Check across ALL storage channels before uploading
    if is_already_in_any_channel(title_key, ep_tag, res_tag):
        print(f"⏭️ Duplicate detected across storage channels ({title_key} • {ep_tag} • {res_tag}) — cancelling upload & continuing.")
        FILE_COMPLETED_EVENT.set()
        return

    final_caption = build_final_caption(raw_fn, caption, meta.get("runtime", ""), file_size)

    try:
        await publish_file_with_green_buttons(dest_channel, msg.media, final_caption)
        print(f"🚀 Published {title_key.title()} [{ep_tag} • {res_tag}] to {cat_emoji} {cat.upper()} ({dest_channel})")

        record_indexed_file(dest_channel, title_key, ep_tag, res_tag, raw_fn)

        await client.send_message(
            "me",
            f"✅ <b>Published to Storage!</b>\n"
            f"• <b>Title:</b> <code>{title_key.title()} ({ep_tag} • {res_tag})</code>\n"
            f"• <b>File:</b> <code>{extract_real_filename(raw_fn, caption)}</code>\n"
            f"• <b>Size:</b> <code>{format_size_human(file_size)}</code>\n"
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
# 9. MULTI-RESOLUTION & MULTI-SEASON STATE NAVIGATOR
# ============================================================================
async def navigate_to_grid(query: str, target_season_num: int = 1, target_res: str = None, send_fresh: bool = True):
    """
    Navigates: Title ➔ Season (target_season_num) ➔ Quality (target_res or discovers all resolutions)
    Returns: (grid_msg, available_seasons, available_resolutions)
    """
    if send_fresh:
        print(f"📨 Sending search query to @{SOURCE_BOT}: '{query}' (Season {target_season_num:02d} • Res: {target_res or 'AUTO'})")
        await client.send_message(SOURCE_BOT, query)
        await asyncio.sleep(2.0)

    tracked_menu_id = None
    available_seasons = []
    available_resolutions = []

    for _ in range(16):
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

        # 2. Screen D: File / Episode Grid Reached
        if is_file_or_episode_grid(msg):
            return msg, available_seasons, available_resolutions

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

        # 5. Screen C: Quality / Resolution Selection
        if "select the available quality" in text or "quality" in text:
            res_options = collect_resolution_quality_buttons(msg.buttons)
            available_resolutions = [r[0] for r in res_options]

            chosen_qual_btn = None
            if target_res:
                for r_tag, r_label, r_btn in res_options:
                    if r_tag == target_res:
                        chosen_qual_btn = r_btn
                        break

            if not chosen_qual_btn and res_options:
                # Default to highest or first available resolution option
                chosen_qual_btn = res_options[0][2]

            if chosen_qual_btn:
                print(f"👉 [Screen 3/Quality] Clicking: {chosen_qual_btn.text}")
                await smart_click_button(chosen_qual_btn)
                await wait_for_menu_update(SOURCE_BOT, tracked_menu_id, old_sig)
                continue
            else:
                print("❌ No valid quality buttons found.")
                break

        await asyncio.sleep(1.0)

    return None, available_seasons, available_resolutions

# ============================================================================
# 10. UNSTOPPABLE EPISODE & MOVIE DOWNLOADER (ALL RESOLUTIONS)
# ============================================================================
async def download_from_grid(initial_msg, query: str, season_num: int, active_res: str):
    global ACTIVE_SEASON_TAG, ACTIVE_EP_TAG, ACTIVE_RES_TAG
    ACTIVE_SEASON_TAG = f"S{season_num:02d}"
    ACTIVE_RES_TAG = active_res or "1080P"
    menu_msg_id = initial_msg.id
    current_msg = initial_msg
    current_page = 1

    while True:
        # Group buttons by (ep_or_movie_tag, resolution_tag)
        best_item_map = {}
        item_counts = {}
        item_order = []
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

                ep_m = re.search(r"\b(?:E|EP\.?)0*(\d{1,3})\b", txt, re.I)
                if ep_m:
                    ep_tag = f"{ACTIVE_SEASON_TAG}E{int(ep_m.group(1)):02d}"
                else:
                    ep_tag = "MOVIE"

                btn_res = extract_resolution_tag(txt, ACTIVE_RES_TAG)
                size_mb = parse_size_mb(txt)
                item_key = (ep_tag, btn_res)

                item_counts[item_key] = item_counts.get(item_key, 0) + 1
                if item_key not in best_item_map:
                    item_order.append(item_key)
                    best_item_map[item_key] = (size_mb, btn)
                else:
                    if size_mb > best_item_map[item_key][0]:
                        best_item_map[item_key] = (size_mb, btn)

        # Page-Boundary Split Protection for Series Episodes
        if next_button and len(item_order) >= 2:
            last_key = item_order[-1]
            last_size = best_item_map[last_key][0]
            prev_sizes = [best_item_map[k][0] for k in item_order[:-1] if best_item_map[k][0] > 0]
            avg_prev = (sum(prev_sizes) / len(prev_sizes)) if prev_sizes else 0
            if item_counts.get(last_key, 0) == 1 and avg_prev > 0 and last_size < (avg_prev * 0.55):
                print(f"💡 Deferring {last_key[0]} ({last_size:.0f} MB) to next page for full BluRay size.")
                item_order.pop()

        print(f"📋 [{ACTIVE_SEASON_TAG} • {ACTIVE_RES_TAG} • Page {current_page}] Found {len(item_order)} items.")

        for ep_tag, btn_res in item_order:
            _, btn = best_item_map[(ep_tag, btn_res)]

            # Check across ALL storage channels before clicking
            if is_already_in_any_channel(ACTIVE_CLEAN_TITLE, ep_tag, btn_res):
                print(f"⏭️ Skipping {ACTIVE_CLEAN_TITLE} [{ep_tag} • {btn_res}] — already in storage channels.")
                continue

            ACTIVE_EP_TAG = ep_tag
            ACTIVE_RES_TAG = btn_res
            FILE_COMPLETED_EVENT.clear()
            print(f"⏳ Requesting {ep_tag} ({btn_res}) ➔ Clicking '{btn.text}'...")

            await smart_click_button(btn)

            try:
                await asyncio.wait_for(FILE_COMPLETED_EVENT.wait(), timeout=90)
                print(f"✅ Completed {ep_tag} ({btn_res}).")
            except asyncio.TimeoutError:
                print(f"⚠️ Timeout on {ep_tag} ({btn_res}) — continuing automatically...")

            await asyncio.sleep(1.5)

            # Auto-Recovery: If menu message expired or disappeared, re-send title & return to exact page
            check_msg = await client.get_messages(SOURCE_BOT, ids=menu_msg_id)
            if not check_msg or not check_msg.buttons:
                print(f"🔄 Menu expired! Re-sending '{query}' to recover {ACTIVE_SEASON_TAG} ({ACTIVE_RES_TAG}) Page {current_page}...")
                recovered_msg, _, _ = await navigate_to_grid(query, season_num, active_res, send_fresh=True)
                if recovered_msg:
                    menu_msg_id = recovered_msg.id
                    current_msg = recovered_msg
                    for _ in range(1, current_page):
                        nxt = next((b for r in current_msg.buttons for b in r if "next" in (b.text or "").lower() or "⏩" in (b.text or "")), None)
                        if nxt:
                            old_s = get_menu_signature(current_msg)
                            await smart_click_button(nxt)
                            current_msg = await wait_for_menu_update(SOURCE_BOT, menu_msg_id, old_s)

        # Refresh menu before checking Next Page
        refreshed = await client.get_messages(SOURCE_BOT, ids=menu_msg_id)
        if refreshed and refreshed.buttons:
            current_msg = refreshed
            next_button = next((b for r in current_msg.buttons for b in r if "next" in (b.text or "").lower() or "⏩" in (b.text or "")), None)

        if next_button:
            current_page += 1
            print(f"📄 Clicking Next ⏩ to Page {current_page}...")
            old_sig = get_menu_signature(current_msg)
            await smart_click_button(next_button)
            current_msg = await wait_for_menu_update(SOURCE_BOT, menu_msg_id, old_sig)
            if not current_msg or not current_msg.buttons:
                break
        else:
            break

# ============================================================================
# 11. MASTER PIPELINE & UNSTOPPABLE QUEUE WORKER
# ============================================================================
async def process_queue():
    global CURRENT_PIPELINE_ACTIVE, ACTIVE_CLEAN_TITLE, ACTIVE_EP_TAG, ACTIVE_RES_TAG
    if CURRENT_PIPELINE_ACTIVE:
        return

    CURRENT_PIPELINE_ACTIVE = True
    try:
        while TITLE_QUEUE:
            query, send_initial = TITLE_QUEUE.popleft()
            ACTIVE_CLEAN_TITLE = normalize_title_key(query)
            ACTIVE_EP_TAG = ""
            ACTIVE_RES_TAG = ""

            # Step 1: Scan all Telegram storage channels for existing files of this title
            await sync_title_across_all_channels(query)

            explicit_s = re.search(r"\b(?:S|Season\s*)0*(\d{1,2})\b", query, re.I)
            start_season = int(explicit_s.group(1)) if explicit_s else 1

            # Step 2: Initial navigation to discover seasons and available resolutions (360p, 480p, 720p, 1080p)
            grid_msg, all_seasons, all_resolutions = await navigate_to_grid(
                query, start_season, target_res=None, send_fresh=send_initial
            )
            if not grid_msg:
                print(f"❌ Could not reach file grid for: {query}. Moving to next in queue.")
                continue

            seasons_to_run = [start_season] if explicit_s else (all_seasons if all_seasons else [start_season])
            resolutions_to_run = all_resolutions if all_resolutions else ["1080P"]

            # Step 3: Process first discovered grid immediately
            first_season = seasons_to_run[0]
            first_res = resolutions_to_run[0]
            await download_from_grid(grid_msg, query, first_season, first_res)

            # Step 4: Loop through remaining resolutions (360p, 480p, 720p, 1080p) for first season
            for res_tag in resolutions_to_run[1:]:
                print(f"🎞️ Switching to resolution {res_tag} for '{query}' (Season {first_season:02d})...")
                await asyncio.sleep(1.5)
                res_grid, _, _ = await navigate_to_grid(query, first_season, target_res=res_tag, send_fresh=True)
                if res_grid:
                    await download_from_grid(res_grid, query, first_season, res_tag)

            # Step 5: Loop through remaining Seasons (Season 02, Season 03...) and their resolutions
            for next_s in seasons_to_run[1:]:
                if next_s <= first_season:
                    continue
                print(f"🔁 Starting Season {next_s:02d} for '{query}'...")
                await asyncio.sleep(2.0)
                s_grid, _, s_res_list = await navigate_to_grid(query, next_s, target_res=None, send_fresh=True)
                if not s_grid:
                    continue
                s_resolutions = s_res_list if s_res_list else ["1080P"]
                await download_from_grid(s_grid, query, next_s, s_resolutions[0])

                for res_tag in s_resolutions[1:]:
                    await asyncio.sleep(1.5)
                    r_grid, _, _ = await navigate_to_grid(query, next_s, target_res=res_tag, send_fresh=True)
                    if r_grid:
                        await download_from_grid(r_grid, query, next_s, res_tag)

            await client.send_message(
                "me",
                f"🏁 <b>Finished Everything for:</b> <code>{query}</code>\n"
                f"All available seasons and resolutions have been verified and published!",
                parse_mode="HTML"
            )
    except Exception as e:
        print(f"⚠️ Master pipeline error: {e}")
    finally:
        CURRENT_PIPELINE_ACTIVE = False
        ACTIVE_EP_TAG = ""
        ACTIVE_RES_TAG = ""

def enqueue_title(query: str, send_initial: bool = True):
    TITLE_QUEUE.append((query, send_initial))
    asyncio.create_task(process_queue())

# ============================================================================
# 12. TRIGGERS: SAVED MESSAGES OR TYPING IN SOURCE_BOT CHAT
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
            f"⏳ <b>Queued (#{pos}):</b> <code>{query}</code> (All Resolutions + Cross-Channel Dedup)",
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
# 13. RENDER HEALTH CHECK SERVER & MAIN ENTRYPOINT
# ============================================================================
async def handle_ping(request):
    return web.json_response({
        "status": "online",
        "pipeline_active": CURRENT_PIPELINE_ACTIVE,
        "queue_length": len(TITLE_QUEUE),
        "active_title": ACTIVE_CLEAN_TITLE,
        "active_season": ACTIVE_SEASON_TAG,
        "active_resolution": ACTIVE_RES_TAG,
        "green_buttons_enabled": bool(BOT_TOKEN)
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

    if not BOT_TOKEN:
        print("⚠️ WARNING: BOT_TOKEN is not set! Add BOT_TOKEN in Render Environment so Green Inline Buttons appear on files.")

    await client.start()
    print(f"🚀 Unstoppable Media Pipeline LIVE! Watching @{SOURCE_BOT} and Saved Messages.")
    await client.run_until_disconnected()

if __name__ == "__main__":
    loop.run_until_complete(main())
