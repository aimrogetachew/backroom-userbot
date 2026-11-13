import os
import re
import json
import asyncio
import sqlite3
from collections import deque
from urllib.parse import urlparse, parse_qs, quote_plus
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
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()

SOURCE_BOT = os.environ.get("SOURCE_BOT", "fileserverxrobot").lstrip("@").strip()
PORT = int(os.environ.get("PORT", 10000))

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

INLINE_BUTTONS = [
    [
        Button.url("Series ↗", "https://t.me/thebackroomseries"),
        Button.url("Anime ↗", "https://t.me/thebackroomanime"),
        Button.url("Movies ↗", "https://t.me/thebackroommovies")
    ]
]

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

# Operational State & Synchronization
FILE_COMPLETED_EVENT = asyncio.Event()
CAPTURED_FILES = set()
SYNCED_TITLES = set()
DIALOG_CACHE_READY = False
META_CACHE = {}
TITLE_QUEUE = deque()

CURRENT_PIPELINE_ACTIVE = False
SKIP_CURRENT_TITLE = False
ME_USER_ID = None

ACTIVE_CLEAN_TITLE = ""
ACTIVE_SEASON_TAG = "S01"
ACTIVE_EP_TAG = ""
ACTIVE_RES_TAG = ""

# ============================================================================
# 2. NATURAL LANGUAGE COMMAND PARSER (RANGES, MISSING MODE, BULK LISTS)
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

def parse_smart_user_command(raw_text: str) -> dict:
    """
    Parses natural language commands such as:
    - 'missing files from solo leveling' -> mode='missing', title='solo leveling'
    - 'Solo leveling from S2 ep 4 -5 EP 7' -> mode='range', title='Solo leveling', season=2, episodes={4, 5, 7}
    - 'Blue Lock' -> mode='normal', title='Blue Lock'
    """
    text = re.sub(r"^\.get\s+", "", (raw_text or "").strip(), flags=re.I).strip()
    text = re.sub(r"^[\d\-\*\•\.]+\s*", "", text).strip(" ,.;:")

    mode = "normal"
    season_num = None
    target_episodes = set()

    # 1. Detect "missing files from <Title>" mode
    missing_m = re.match(
        r"^(?:find\s+|check\s+|fill\s+|download\s+)?missing(?:\s+files?|\s+episodes?|\s+ep|\s+parts?|\s+movies?)?(?:\s+from|\s+in|\s+for|\s+of)?\s+(.+)$",
        text,
        flags=re.I
    )
    if missing_m:
        mode = "missing"
        text = missing_m.group(1).strip()
    elif re.search(r"\bmissing(?:\s+files?|\s+episodes?|\s+ep|\s+parts?)?\b$", text, flags=re.I):
        mode = "missing"
        text = re.sub(r"\b(?:find|check|fill)?\s*missing(?:\s+files?|\s+episodes?|\s+ep|\s+parts?)?\b$", "", text, flags=re.I).strip()

    # 2. Detect Season + Episode Range specifications (e.g. "from S2 ep 4 -5 EP 7")
    tail_match = re.search(
        r"\b(?:from\s+)?(?:(s(?:eason)?\s*0*\d{1,2})\b.*|(?:ep?(?:isode)?\.?\s*0*\d{1,3})\b.*)$",
        text,
        flags=re.I
    )
    if tail_match:
        tail_str = tail_match.group(0)
        title_part = text[:tail_match.start()].strip(" ,-:")
        if len(title_part) >= 2:
            s_m = re.search(r"\bs(?:eason)?\s*0*(\d{1,2})\b", tail_str, re.I)
            if s_m:
                season_num = int(s_m.group(1))

            # Remove the season token before extracting episode numbers/ranges
            ep_str = re.sub(r"\bs(?:eason)?\s*0*\d{1,2}\b", " ", tail_str, flags=re.I)

            # Extract ranges like "ep 4 - 5", "4-7", "ep 2 to 6"
            for r_m in re.finditer(r"(?:ep?(?:isode)?\.?\s*)?0*(\d{1,3})\s*(?:\-|to|–|~)\s*(?:ep?(?:isode)?\.?\s*)?0*(\d{1,3})", ep_str, re.I):
                start_e, end_e = int(r_m.group(1)), int(r_m.group(2))
                if start_e <= end_e and (end_e - start_e) <= 200:
                    target_episodes.update(range(start_e, end_e + 1))

            # Extract individual episodes like "EP 7", "ep 9", or comma-separated numbers
            for single_m in re.finditer(r"(?:ep?(?:isode)?\.?\s*)0*(\d{1,3})\b", ep_str, re.I):
                target_episodes.add(int(single_m.group(1)))

            if season_num is not None or target_episodes:
                text = title_part
                if target_episodes and mode != "missing":
                    mode = "range"

    return {
        "raw": raw_text.strip(),
        "title": text.strip(" ,.") or raw_text.strip(),
        "mode": mode,
        "season": season_num,
        "episodes": sorted(list(target_episodes)) if target_episodes else None
    }

def split_bulk_commands(raw_message: str) -> list:
    """Splits multi-line and comma-separated titles while preserving episode lists like 'ep 4, 5, 7'."""
    commands = []
    for line in (raw_message or "").splitlines():
        line = line.strip()
        if not line or re.match(r"^[\.\s,]+$", line):
            continue
        # If the line has a trailing comma or multiple titles separated by commas (without 'ep' numbers)
        if "," in line and not re.search(r"\b(?:ep?|episode|s\d{1,2})\b", line, re.I):
            parts = [p.strip(" .,-") for p in line.split(",") if len(p.strip(" .,-")) >= 2]
            commands.extend(parts)
        else:
            cleaned = line.strip(" .,-")
            if len(cleaned) >= 2:
                commands.append(cleaned)
    return commands

# ============================================================================
# 3. LOCAL DEDUPLICATION DATABASE & SAFE CHANNEL SCANNER
# ============================================================================
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

def is_already_in_any_channel(clean_title: str, ep_tag: str, res_tag: str = "ANY") -> bool:
    t_key = normalize_title_key(clean_title)
    e_key = ep_tag.upper().strip()
    r_key = (res_tag or "ANY").upper().strip()

    conn = sqlite3.connect("storage_index.db")
    if r_key != "ANY":
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

def get_indexed_episodes_for_title(clean_title: str) -> set:
    t_key = normalize_title_key(clean_title)
    conn = sqlite3.connect("storage_index.db")
    rows = conn.execute(
        "SELECT DISTINCT ep_tag FROM indexed_files_v2 WHERE clean_title=?",
        (t_key,)
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}

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

async def ensure_dialogs_cached():
    """Warms up Telethon's entity cache once so PeerChannel(-100...) never fails."""
    global DIALOG_CACHE_READY
    if DIALOG_CACHE_READY:
        return
    try:
        await client.get_dialogs(limit=100)
        DIALOG_CACHE_READY = True
    except Exception as e:
        print(f"⚠️ Dialog cache warmup notice: {e}")

async def sync_title_across_all_channels(query: str):
    """
    Safely scans relevant storage channels on Telegram for existing posts of this title
    with pacing so it never triggers FloodWait.
    """
    clean_q = normalize_title_key(query)
    if not clean_q or clean_q == "unknown" or clean_q in SYNCED_TITLES:
        return

    await ensure_dialogs_cached()
    SYNCED_TITLES.add(clean_q)

    # Prioritize likely channels first to minimize RPC calls
    meta = await fetch_meta(query)
    primary_cat = detect_category(query, "", meta)
    primary_ch = DEFAULT_CHANNELS.get(primary_cat, DEFAULT_CHANNELS["other"])
    channels_to_scan = [primary_ch] + [c for c in ALL_STORAGE_CHANNELS if c != primary_ch]

    found_count = 0
    for ch_id in channels_to_scan:
        try:
            async for msg in client.iter_messages(ch_id, search=clean_q, limit=50):
                if not (msg.video or msg.document or msg.text):
                    continue
                fn = (msg.file.name if msg.file else "") or ""
                txt = f"{fn}\n{msg.text or ''}"
                msg_title = normalize_title_key(extract_real_filename(fn, msg.text or ""))
                if clean_q not in msg_title and msg_title not in clean_q:
                    continue

                ep_tag = extract_season_ep_tag(txt, "S01")
                res_tag = extract_resolution_tag(txt, "HD")
                record_indexed_file(ch_id, clean_q, ep_tag, res_tag, fn or "existing_post")
                found_count += 1
            await asyncio.sleep(0.25)
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds + 1)
        except Exception:
            pass

    if found_count:
        print(f"✅ Synced {found_count} existing files for '{clean_q}' from storage channels.")

init_storage_db()

# ============================================================================
# 4. OFFICIAL CATALOG & FRANCHISE RESOLVER (TVMAZE + TMDB + IMDB)
# ============================================================================
async def fetch_official_catalog(title: str) -> dict:
    """
    Queries TVMaze (free, no key needed), TMDB, and IMDb Suggestion API
    to discover total seasons/episodes or movie franchise sequels.
    """
    clean = normalize_title_key(title)
    seasons_map = {}      # {1: [1, 2, ...], 2: [1, 2, ...]}
    franchise_movies = [] # ["John Wick", "John Wick Chapter 2", ...]

    async with aiohttp.ClientSession() as session:
        # 1. TVMaze Episode Guide (100% Free, No Key Required, Accurate Episode Lists)
        try:
            url = f"https://api.tvmaze.com/singlesearch/shows?q={quote_plus(clean)}&embed=episodes"
            async with session.get(url, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    eps = data.get("_embedded", {}).get("episodes", [])
                    for ep in eps:
                        s_num = ep.get("season")
                        e_num = ep.get("number")
                        if s_num and e_num:
                            seasons_map.setdefault(int(s_num), []).append(int(e_num))
        except Exception:
            pass

        # 2. TMDB Series / Movie Collection Lookup (if TMDB_API_KEY is configured)
        if TMDB_API_KEY:
            try:
                s_url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote_plus(clean)}"
                async with session.get(s_url, timeout=4) as r:
                    if r.status == 200:
                        res_list = (await r.json()).get("results") or []
                        if res_list and res_list[0].get("media_type") == "tv" and not seasons_map:
                            tv_id = res_list[0]["id"]
                            tv_url = f"https://api.themoviedb.org/3/tv/{tv_id}?api_key={TMDB_API_KEY}"
                            async with session.get(tv_url, timeout=4) as tr:
                                if tr.status == 200:
                                    tv_data = await tr.json()
                                    for s_info in tv_data.get("seasons", []):
                                        sn = s_info.get("season_number", 0)
                                        ec = s_info.get("episode_count", 0)
                                        if sn > 0 and ec > 0:
                                            seasons_map[sn] = list(range(1, ec + 1))

                # Also check TMDB Movie Collections for Movie Franchises
                c_url = f"https://api.themoviedb.org/3/search/collection?api_key={TMDB_API_KEY}&query={quote_plus(clean)}"
                async with session.get(c_url, timeout=4) as cr:
                    if cr.status == 200:
                        c_results = (await cr.json()).get("results") or []
                        if c_results:
                            col_id = c_results[0]["id"]
                            col_url = f"https://api.themoviedb.org/3/collection/{col_id}?api_key={TMDB_API_KEY}"
                            async with session.get(col_url, timeout=4) as d_resp:
                                if d_resp.status == 200:
                                    parts = (await d_resp.json()).get("parts") or []
                                    for p in parts:
                                        if p.get("title"):
                                            franchise_movies.append(p["title"])
            except Exception:
                pass

        # 3. IMDb Free Public API Fallback for Movie Franchises
        if not seasons_map and not franchise_movies:
            try:
                first_char = clean[0] if clean else "a"
                imdb_url = f"https://v3.sg.media-imdb.com/suggestion/{first_char}/{quote_plus(clean)}.json"
                async with session.get(imdb_url, timeout=4) as ir:
                    if ir.status == 200:
                        idata = await ir.json()
                        for item in idata.get("d", []):
                            lbl = item.get("l", "")
                            q_type = (item.get("q") or "").lower()
                            if clean in normalize_title_key(lbl) and ("feature" in q_type or "movie" in q_type):
                                franchise_movies.append(lbl)
            except Exception:
                pass

    return {
        "seasons": seasons_map,
        "franchise_movies": franchise_movies[:10]
    }

# ============================================================================
# 5. TECHNICAL SPEC PARSER & CAPTION BUILDER
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

    codec = "x264"
    if re.search(r"\b(x265|h265|hevc)\b", combined, re.I):
        codec = "HEVC x265"
    elif re.search(r"\bav1\b", combined, re.I):
        codec = "AV1"
    if re.search(r"\b10\s*-?bit\b", combined, re.I) and "10-Bit" not in codec:
        codec += " 10-Bit"

    aud_codec = "AAC 2.0"
    if re.search(r"\bddp\s*5\.1\b", combined, re.I):
        aud_codec = "DDP 5.1"
    elif re.search(r"\bddp\s*2\.0\b", combined, re.I):
        aud_codec = "DDP 2.0"

    br_m = re.search(r"Video\s*Bitrate\s*:\s*([^\r\n]+)", combined, re.I) or re.search(r"\b(\d{3,5}\s*kbps)\b", combined, re.I)
    bitrate = br_m.group(1).strip() if br_m else ""

    dur_m = re.search(r"Duration\s*:\s*([^\r\n]+)", combined, re.I) or re.search(r"⏳\s*(\d{1,2}:\d{2}(?::\d{2})?)", combined)
    duration = dur_m.group(1).strip() if dur_m else ""

    for_tv = bool(re.search(r"Can\s*be\s*played\s*on\s*TV|FOR\s*TV", combined, re.I) or "x264" in codec)
    has_sub = bool(re.search(r"Has\s*[a-zA-Z]*\s*subtitles|\b(?:sub|subs|esub|multisub|mmsub)\b|\.mkv\b", combined, re.I))

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
# 6. METADATA & CATEGORY ENGINE + GREEN INLINE BUTTON PUBLISHER
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
                url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote_plus(clean)}"
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
                url = f"https://www.omdbapi.com/?apikey={OMDB_API_KEY}&t={quote_plus(clean)}"
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

    if re.search(r"\b(anime|demon slayer|solo leveling|jujutsu|naruto|one piece|bleach|attack on titan|kimetsu|blue lock)\b", combined) or (
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

async def publish_file_with_green_buttons(dest_channel: int, media, final_caption: str):
    await ensure_dialogs_cached()
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
                    await client.delete_messages(dest_channel, [sent_msg.id])
                else:
                    print(f"⚠️ Bot API copyMessage notice: {data.get('description')}")
    except Exception as e:
        print(f"⚠️ Green button attach notice: {e}")

    return sent_msg

# ============================================================================
# 7. BUTTON SCORING & NAVIGATION HELPERS
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

async def auto_join_channel(url: str):
    try:
        if "+" in url or "joinchat" in url:
            invite_hash = url.split("+")[-1].split("/")[-1].strip()
            await client(ImportChatInviteRequest(invite_hash))
        else:
            uname = url.split("t.me/")[-1].split("/")[0].split("?")[0].strip()
            if uname:
                await client(JoinChannelRequest(uname))
        return True
    except Exception:
        return False

async def trigger_start_url(url: str) -> bool:
    if not url or "start=" not in url:
        return False
    parsed = urlparse(url)
    target_bot = parsed.path.strip("/") or SOURCE_BOT
    start_param = parse_qs(parsed.query).get("start", [""])[0]
    if target_bot and start_param:
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
# 8. INCOMING MEDIA EVENT
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

    if is_already_in_any_channel(title_key, ep_tag, res_tag):
        print(f"⏭️ Duplicate detected ({title_key} • {ep_tag} • {res_tag}) — skipping.")
        FILE_COMPLETED_EVENT.set()
        return

    final_caption = build_final_caption(raw_fn, caption, meta.get("runtime", ""), file_size)

    try:
        await publish_file_with_green_buttons(dest_channel, msg.media, final_caption)
        print(f"🚀 Published {title_key.title()} [{ep_tag} • {res_tag}] to {cat_emoji} {cat.upper()}")

        record_indexed_file(dest_channel, title_key, ep_tag, res_tag, raw_fn)

        await client.send_message(
            "me",
            f"✅ <b>Published to Storage!</b>\n"
            f"• <b>Title:</b> <code>{title_key.title()} ({ep_tag} • {res_tag})</code>\n"
            f"• <b>File:</b> <code>{extract_real_filename(raw_fn, caption)}</code>\n"
            f"• <b>Size:</b> <code>{format_size_human(file_size)}</code>\n"
            f"• <b>Channel:</b> {cat_emoji} <code>{cat.upper()}</code>",
            parse_mode="HTML"
        )
    except FloodWaitError as fw:
        await asyncio.sleep(fw.seconds + 1)
    except Exception as e:
        print(f"❌ Storage Forward error: {e}")
    finally:
        FILE_COMPLETED_EVENT.set()

# ============================================================================
# 9. NAVIGATOR & SELECTIVE/MISSING EPISODE DOWNLOADER
# ============================================================================
async def navigate_to_grid(query: str, target_season_num: int = 1, target_res: str = None, send_fresh: bool = True):
    if send_fresh:
        print(f"📨 Querying @{SOURCE_BOT}: '{query}' (Season {target_season_num:02d} • Res: {target_res or 'AUTO'})")
        await client.send_message(SOURCE_BOT, query)
        await asyncio.sleep(2.0)

    tracked_menu_id = None
    available_seasons = []
    available_resolutions = []

    for _ in range(16):
        if SKIP_CURRENT_TITLE:
            break
        msg = await find_latest_menu_message(SOURCE_BOT, tracked_menu_id)
        if not msg or not msg.buttons:
            await asyncio.sleep(1.0)
            continue

        tracked_menu_id = msg.id
        text = (msg.text or "").lower()
        old_sig = get_menu_signature(msg)

        # 1. FSUB Bypass
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

        # 2. Episode / File Grid Reached
        if is_file_or_episode_grid(msg):
            return msg, available_seasons, available_resolutions

        # 3. Title Selection
        if "choose the correct title" in text or "matching results" in text or "search results for" in text:
            best_title_btn = select_best_title_button(msg.buttons, query)
            print(f"👉 [Screen 1/Title] Clicking: {best_title_btn.text}")
            await smart_click_button(best_title_btn)
            await wait_for_menu_update(SOURCE_BOT, tracked_menu_id, old_sig)
            continue

        # 4. Season Selection
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
                        season_btns[int(m.group(1))] = btn
                    elif "season all" in low:
                        fallback_btn = btn

            available_seasons = sorted(season_btns.keys())
            chosen_btn = season_btns.get(target_season_num) or fallback_btn or msg.buttons[0][0]
            print(f"👉 [Screen 2/Season] Clicking: {chosen_btn.text}")
            await smart_click_button(chosen_btn)
            await wait_for_menu_update(SOURCE_BOT, tracked_menu_id, old_sig)
            continue

        # 5. Quality Selection
        if "select the available quality" in text or "quality" in text:
            res_options = collect_resolution_quality_buttons(msg.buttons)
            available_resolutions = [r[0] for r in res_options]

            chosen_qual_btn = None
            if target_res:
                for r_tag, _, r_btn in res_options:
                    if r_tag == target_res:
                        chosen_qual_btn = r_btn
                        break

            if not chosen_qual_btn and res_options:
                chosen_qual_btn = res_options[0][2]

            if chosen_qual_btn:
                print(f"👉 [Screen 3/Quality] Clicking: {chosen_qual_btn.text}")
                await smart_click_button(chosen_qual_btn)
                await wait_for_menu_update(SOURCE_BOT, tracked_menu_id, old_sig)
                continue
            else:
                break

        await asyncio.sleep(1.0)

    return None, available_seasons, available_resolutions

async def download_from_grid(
    initial_msg,
    query: str,
    season_num: int,
    active_res: str,
    target_episodes: set = None,
    only_missing: bool = False
):
    """
    Downloads episodes/movies from the grid.
    - If target_episodes is set (e.g. {4, 5, 7}), ONLY downloads those episode numbers!
    - If only_missing is True, skips any episode that already exists in ANY resolution in storage.
    """
    global ACTIVE_SEASON_TAG, ACTIVE_EP_TAG, ACTIVE_RES_TAG
    ACTIVE_SEASON_TAG = f"S{season_num:02d}"
    ACTIVE_RES_TAG = active_res or "1080P"
    menu_msg_id = initial_msg.id
    current_msg = initial_msg
    current_page = 1
    remaining_targets = set(target_episodes) if target_episodes else None

    while not SKIP_CURRENT_TITLE:
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
                ep_num = int(ep_m.group(1)) if ep_m else None
                if ep_num is not None:
                    # Filter by user's requested episode range/list if specified
                    if remaining_targets is not None and ep_num not in remaining_targets:
                        continue
                    ep_tag = f"{ACTIVE_SEASON_TAG}E{ep_num:02d}"
                else:
                    if remaining_targets is not None:
                        continue
                    ep_tag = "MOVIE"

                btn_res = extract_resolution_tag(txt, ACTIVE_RES_TAG)
                size_mb = parse_size_mb(txt)
                item_key = (ep_tag, btn_res, ep_num)

                item_counts[item_key] = item_counts.get(item_key, 0) + 1
                if item_key not in best_item_map:
                    item_order.append(item_key)
                    best_item_map[item_key] = (size_mb, btn)
                else:
                    if size_mb > best_item_map[item_key][0]:
                        best_item_map[item_key] = (size_mb, btn)

        # Page-Boundary Split Protection
        if next_button and len(item_order) >= 2 and remaining_targets is None:
            last_key = item_order[-1]
            last_size = best_item_map[last_key][0]
            prev_sizes = [best_item_map[k][0] for k in item_order[:-1] if best_item_map[k][0] > 0]
            avg_prev = (sum(prev_sizes) / len(prev_sizes)) if prev_sizes else 0
            if item_counts.get(last_key, 0) == 1 and avg_prev > 0 and last_size < (avg_prev * 0.55):
                item_order.pop()

        for ep_tag, btn_res, ep_num in item_order:
            if SKIP_CURRENT_TITLE:
                break
            _, btn = best_item_map[(ep_tag, btn_res, ep_num)]

            check_res = "ANY" if only_missing else btn_res
            if is_already_in_any_channel(ACTIVE_CLEAN_TITLE, ep_tag, check_res):
                print(f"⏭️ Skipping {ACTIVE_CLEAN_TITLE} [{ep_tag} • {btn_res}] — already in storage.")
                if remaining_targets is not None and ep_num in remaining_targets:
                    remaining_targets.discard(ep_num)
                continue

            ACTIVE_EP_TAG = ep_tag
            ACTIVE_RES_TAG = btn_res
            FILE_COMPLETED_EVENT.clear()
            print(f"⏳ Requesting {ep_tag} ({btn_res}) ➔ Clicking '{btn.text}'...")

            await smart_click_button(btn)

            try:
                await asyncio.wait_for(FILE_COMPLETED_EVENT.wait(), timeout=90)
                if remaining_targets is not None and ep_num in remaining_targets:
                    remaining_targets.discard(ep_num)
            except asyncio.TimeoutError:
                print(f"⚠️ Timeout on {ep_tag} ({btn_res}) — continuing...")

            await asyncio.sleep(1.5)

            check_msg = await client.get_messages(SOURCE_BOT, ids=menu_msg_id)
            if not check_msg or not check_msg.buttons:
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

        # Stop immediately if all specifically requested episodes on this season were downloaded
        if remaining_targets is not None and len(remaining_targets) == 0:
            print(f"🎯 All requested target episodes for {ACTIVE_SEASON_TAG} completed!")
            break

        refreshed = await client.get_messages(SOURCE_BOT, ids=menu_msg_id)
        if refreshed and refreshed.buttons:
            current_msg = refreshed
            next_button = next((b for r in current_msg.buttons for b in r if "next" in (b.text or "").lower() or "⏩" in (b.text or "")), None)

        if next_button:
            current_page += 1
            old_sig = get_menu_signature(current_msg)
            await smart_click_button(next_button)
            current_msg = await wait_for_menu_update(SOURCE_BOT, menu_msg_id, old_sig)
            if not current_msg or not current_msg.buttons:
                break
        else:
            break

# ============================================================================
# 10. MASTER UNSTOPPABLE QUEUE WORKER & MISSING-FILES AUDITOR
# ============================================================================
async def execute_missing_audit_and_fill(cmd: dict):
    """
    1. Queries IMDb / TVMaze / TMDB for official episodes or movie franchise parts.
    2. Scans storage channels.
    3. Reports and downloads ONLY the missing episodes or missing franchise movies.
    """
    title = cmd["title"]
    clean_t = normalize_title_key(title)
    await sync_title_across_all_channels(title)

    catalog = await fetch_official_catalog(title)
    existing_eps = get_indexed_episodes_for_title(clean_t)

    # Case A: Movie Franchise (multiple parts/sequels)
    if not catalog["seasons"] and len(catalog["franchise_movies"]) > 1:
        missing_movies = []
        for m_title in catalog["franchise_movies"]:
            await sync_title_across_all_channels(m_title)
            if not is_already_in_any_channel(m_title, "MOVIE", "ANY"):
                missing_movies.append(m_title)

        await client.send_message(
            "me",
            f"🔎 <b>Franchise Audit:</b> <code>{title.title()}</code>\n"
            f"• <b>Total Parts Found (IMDb/TMDB):</b> <code>{len(catalog['franchise_movies'])}</code>\n"
            f"• <b>Missing Parts ({len(missing_movies)}):</b> <code>{', '.join(missing_movies) or 'None! All present.'}</code>",
            parse_mode="HTML"
        )
        for m_title in missing_movies:
            TITLE_QUEUE.appendleft(({"raw": m_title, "title": m_title, "mode": "normal", "season": None, "episodes": None}, True))
        return

    # Case B: Series / Anime Missing Episodes
    missing_by_season = {}
    if catalog["seasons"]:
        for s_num, ep_list in sorted(catalog["seasons"].items()):
            if cmd["season"] and s_num != cmd["season"]:
                continue
            for e_num in ep_list:
                tag = f"S{s_num:02d}E{e_num:02d}"
                if tag not in existing_eps:
                    missing_by_season.setdefault(s_num, set()).add(e_num)

    if missing_by_season:
        summary = ", ".join(
            f"S{s:02d} ({len(eps)} missing: E{min(eps):02d}-E{max(eps):02d})"
            for s, eps in sorted(missing_by_season.items())
        )
        await client.send_message(
            "me",
            f"🔎 <b>Missing Files Audit (IMDb/TVMaze/TMDB):</b> <code>{title.title()}</code>\n"
            f"• <b>Already in Storage:</b> <code>{len(existing_eps)}</code> episodes\n"
            f"• <b>Missing Detected:</b> <code>{summary}</code>\n"
            f"⚡ <i>Filling missing episodes now...</i>",
            parse_mode="HTML"
        )
        for s_num, miss_eps in sorted(missing_by_season.items()):
            if SKIP_CURRENT_TITLE:
                break
            grid_msg, _, _ = await navigate_to_grid(title, s_num, target_res=None, send_fresh=True)
            if grid_msg:
                await download_from_grid(grid_msg, title, s_num, "1080P", target_episodes=miss_eps, only_missing=True)
    else:
        # Fallback: Even if external catalog didn't list episodes, scan bot directly and fill any un-indexed episodes
        await client.send_message(
            "me",
            f"🔎 <b>Scanning @{SOURCE_BOT} directly for any missing episodes of:</b> <code>{title.title()}</code>\n"
            f"• <b>Currently in Storage:</b> <code>{len(existing_eps)}</code> episodes",
            parse_mode="HTML"
        )
        grid_msg, all_seasons, _ = await navigate_to_grid(title, cmd["season"] or 1, target_res=None, send_fresh=True)
        if grid_msg:
            seasons = [cmd["season"]] if cmd["season"] else (all_seasons or [1])
            await download_from_grid(grid_msg, title, seasons[0], "1080P", only_missing=True)
            for nxt_s in seasons[1:]:
                s_grid, _, _ = await navigate_to_grid(title, nxt_s, target_res=None, send_fresh=True)
                if s_grid:
                    await download_from_grid(s_grid, title, nxt_s, "1080P", only_missing=True)

async def process_queue():
    global CURRENT_PIPELINE_ACTIVE, ACTIVE_CLEAN_TITLE, ACTIVE_EP_TAG, ACTIVE_RES_TAG, SKIP_CURRENT_TITLE
    if CURRENT_PIPELINE_ACTIVE:
        return

    CURRENT_PIPELINE_ACTIVE = True
    try:
        while TITLE_QUEUE:
            # Isolated try/except per item so one failure NEVER stops the rest of the queue
            try:
                SKIP_CURRENT_TITLE = False
                cmd, send_initial = TITLE_QUEUE.popleft()
                if isinstance(cmd, str):
                    cmd = parse_smart_user_command(cmd)

                query = cmd["title"]
                ACTIVE_CLEAN_TITLE = normalize_title_key(query)
                ACTIVE_EP_TAG = ""
                ACTIVE_RES_TAG = ""

                print(f"🎬 Processing: '{query}' | Mode: {cmd['mode']} | Season: {cmd['season']} | Eps: {cmd['episodes']}")

                if cmd["mode"] == "missing":
                    await execute_missing_audit_and_fill(cmd)
                    continue

                await sync_title_across_all_channels(query)
                if SKIP_CURRENT_TITLE:
                    continue

                start_season = cmd["season"] or 1
                target_eps = set(cmd["episodes"]) if cmd["episodes"] else None

                grid_msg, all_seasons, all_resolutions = await navigate_to_grid(
                    query, start_season, target_res=None, send_fresh=send_initial
                )
                if not grid_msg or SKIP_CURRENT_TITLE:
                    print(f"⚠️ Could not reach file grid for '{query}', continuing to next item...")
                    continue

                # If specific episodes were requested (e.g., "from S2 ep 4 - 5 ep 7"), grab them fast!
                if target_eps:
                    first_res = all_resolutions[0] if all_resolutions else "1080P"
                    await download_from_grid(
                        grid_msg, query, start_season, first_res, target_episodes=target_eps
                    )
                else:
                    seasons_to_run = [start_season] if cmd["season"] else (all_seasons if all_seasons else [start_season])
                    resolutions_to_run = all_resolutions if all_resolutions else ["1080P"]

                    first_season = seasons_to_run[0]
                    first_res = resolutions_to_run[0]
                    await download_from_grid(grid_msg, query, first_season, first_res)

                    for res_tag in resolutions_to_run[1:]:
                        if SKIP_CURRENT_TITLE:
                            break
                        await asyncio.sleep(1.5)
                        res_grid, _, _ = await navigate_to_grid(query, first_season, target_res=res_tag, send_fresh=True)
                        if res_grid and not SKIP_CURRENT_TITLE:
                            await download_from_grid(res_grid, query, first_season, res_tag)

                    for next_s in seasons_to_run[1:]:
                        if SKIP_CURRENT_TITLE or next_s <= first_season:
                            continue
                        await asyncio.sleep(2.0)
                        s_grid, _, s_res_list = await navigate_to_grid(query, next_s, target_res=None, send_fresh=True)
                        if not s_grid or SKIP_CURRENT_TITLE:
                            continue
                        s_resolutions = s_res_list if s_res_list else ["1080P"]
                        await download_from_grid(s_grid, query, next_s, s_resolutions[0])

                        for res_tag in s_resolutions[1:]:
                            if SKIP_CURRENT_TITLE:
                                break
                            await asyncio.sleep(1.5)
                            r_grid, _, _ = await navigate_to_grid(query, next_s, target_res=res_tag, send_fresh=True)
                            if r_grid and not SKIP_CURRENT_TITLE:
                                await download_from_grid(r_grid, query, next_s, res_tag)

                await client.send_message(
                    "me",
                    f"🏁 <b>Finished:</b> <code>{cmd['raw']}</code>\n"
                    f"📋 <b>Remaining in Queue:</b> <code>{len(TITLE_QUEUE)}</code>",
                    parse_mode="HTML"
                )
                await asyncio.sleep(2.0)
            except Exception as item_err:
                print(f"⚠️ Error on queue item (recovering & continuing): {item_err}")
                await asyncio.sleep(2.0)
    finally:
        CURRENT_PIPELINE_ACTIVE = False
        ACTIVE_EP_TAG = ""
        ACTIVE_RES_TAG = ""

def enqueue_commands_bulk(raw_text: str, send_initial: bool = True) -> list:
    raw_items = split_bulk_commands(raw_text)
    parsed_list = []
    for item in raw_items:
        cmd = parse_smart_user_command(item)
        if cmd["title"] and len(cmd["title"]) >= 2:
            TITLE_QUEUE.append((cmd, send_initial))
            parsed_list.append(cmd)

    if parsed_list:
        asyncio.create_task(process_queue())
    return parsed_list

# ============================================================================
# 11. SAVED MESSAGES & SOURCE_BOT TRIGGERS
# ============================================================================
@client.on(events.NewMessage(outgoing=True))
async def on_outgoing_trigger(event):
    global SKIP_CURRENT_TITLE, ME_USER_ID
    text = (event.message.text or "").strip()
    if not text or text.startswith(("/", "✅", "⏳", "🏁", "⚠️", "🔗", "📋", "🛑", "🔎")):
        return

    if ME_USER_ID is None:
        ME_USER_ID = (await client.get_me()).id

    if event.chat_id == ME_USER_ID:
        low = text.lower()

        if low in (".queue", ".status", "queue", "status"):
            q_list = "\n".join(
                f"  {i+1}. <code>{t[0]['raw'] if isinstance(t[0], dict) else t[0]}</code>"
                for i, t in enumerate(TITLE_QUEUE)
            ) or "  <i>(Empty)</i>"
            await event.reply(
                f"📋 <b>Multi-Queue Status</b>\n"
                f"• <b>Active:</b> <code>{ACTIVE_CLEAN_TITLE or 'Idle'}</code> ({ACTIVE_SEASON_TAG} • {ACTIVE_RES_TAG or '-'})\n"
                f"• <b>Waiting ({len(TITLE_QUEUE)}):</b>\n{q_list}",
                parse_mode="HTML"
            )
            return

        if low in (".skip", "skip"):
            SKIP_CURRENT_TITLE = True
            FILE_COMPLETED_EVENT.set()
            await event.reply(f"🛑 <b>Skipping:</b> <code>{ACTIVE_CLEAN_TITLE}</code>", parse_mode="HTML")
            return

        if low in (".clear", "clear"):
            count = len(TITLE_QUEUE)
            TITLE_QUEUE.clear()
            await event.reply(f"🛑 <b>Cleared {count} items from queue.</b>", parse_mode="HTML")
            return

        added = enqueue_commands_bulk(text, send_initial=True)
        if added:
            summary_lines = []
            for c in added[:8]:
                if c["mode"] == "missing":
                    summary_lines.append(f"• 🔍 <b>Missing Audit:</b> <code>{c['title']}</code>")
                elif c["mode"] == "range":
                    s_lbl = f"S{c['season']:02d}" if c["season"] else "S01"
                    e_lbl = ", ".join(f"E{e:02d}" for e in (c["episodes"] or [])[:8])
                    summary_lines.append(f"• 🎯 <b>Selective:</b> <code>{c['title']}</code> ({s_lbl}: {e_lbl})")
                else:
                    summary_lines.append(f"• 🎬 <b>Full Sync:</b> <code>{c['title']}</code>")

            if len(added) > 8:
                summary_lines.append(f"<i>...and +{len(added)-8} more</i>")

            await event.reply(
                f"⏳ <b>Queued {len(added)} Task(s):</b>\n" + "\n".join(summary_lines),
                parse_mode="HTML"
            )
        return

    chat = await event.get_chat()
    chat_username = (getattr(chat, "username", "") or "").lower()
    if chat_username == SOURCE_BOT.lower() and not CURRENT_PIPELINE_ACTIVE:
        enqueue_commands_bulk(text, send_initial=False)

# ============================================================================
# 12. 24/7 KEEP-ALIVE SELF-PING & RENDER HEALTH SERVER
# ============================================================================
async def keep_alive_worker():
    """Pings the local/external HTTP server every 10 minutes so Render never sleeps."""
    target_url = RENDER_EXTERNAL_URL or f"http://127.0.0.1:{PORT}/"
    while True:
        await asyncio.sleep(600)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(target_url, timeout=10):
                    pass
        except Exception:
            pass

async def handle_ping(request):
    return web.json_response({
        "status": "online",
        "pipeline_active": CURRENT_PIPELINE_ACTIVE,
        "queue_length": len(TITLE_QUEUE),
        "active_title": ACTIVE_CLEAN_TITLE,
        "active_season": ACTIVE_SEASON_TAG,
        "active_resolution": ACTIVE_RES_TAG
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
    global ME_USER_ID
    await start_web_server()
    if not SESSION_STRING:
        print("❌ ERROR: SESSION_STRING environment variable is missing!")
        return

    await client.start()
    ME_USER_ID = (await client.get_me()).id
    await ensure_dialogs_cached()
    asyncio.create_task(keep_alive_worker())

    print(f"🚀 Non-Stop Autonomous Media Pipeline is LIVE! Watching @{SOURCE_BOT} and Saved Messages.")
    await client.run_until_disconnected()

if __name__ == "__main__":
    loop.run_until_complete(main())
