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

TMDB_GENRE_MAP = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy",
    80: "Crime", 99: "Documentary", 18: "Drama", 10751: "Family",
    14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music",
    9648: "Mystery", 10749: "Romance", 878: "Sci-Fi", 53: "Thriller",
    10752: "War", 37: "Western", 10759: "Action & Adventure",
    10765: "Sci-Fi & Fantasy"
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

TARGET_RESOLUTIONS = ["1080P", "720P", "480P", "360P", "2160P"]
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
ACTIVE_TARGET_YEAR = None
ACTIVE_SEASON_TAG = "S01"
ACTIVE_EP_TAG = ""
ACTIVE_RES_TAG = ""

# ============================================================================
# 2. STRICT TITLE, YEAR & NATURAL LANGUAGE COMMAND PARSER
# ============================================================================
def extract_year_from_text(text: str):
    """Extracts a 4-digit release year (1950-2035) if present in text."""
    m = re.search(r"\b(19[5-9]\d|20[0-3]\d)\b", text or "")
    return int(m.group(1)) if m else None

def normalize_title_key(title: str) -> str:
    clean = re.sub(r"\(\d{4}\)|\[\d{4}\]|\b(19[5-9]\d|20[0-3]\d)\b", "", title or "")
    clean = re.sub(
        r"\b(S\d{1,2}|Season\s*\d+|E\d{1,3}|EP\.?\s*\d+|Episode\s*\d+|360p|480p|540p|720p|1080p|1440p|2160p|4K|BluRay|WEB-?DL|WEBRip|HDRip|Pahe|MKV|MP4|HEVC|x265|x264|10Bit|6CH|8CH|AAC|DDP\d?\.\d?|ESub|Dual\s*Audio|Multi\s*Audio|PSA)\b.*$",
        "",
        clean,
        flags=re.I
    )
    clean = re.sub(r"[^a-zA-Z0-9]+", " ", clean).strip().lower()
    return clean or "unknown"

def is_strict_title_and_year_match(candidate_text: str, target_title: str, target_year: int = None) -> bool:
    """
    Strictly verifies that:
    1. Every significant word in target_title appears in candidate_text (handling 'spiderman' vs 'spider man').
    2. If target_year is specified (e.g. 2026) and candidate_text contains a year, the years MUST match!
    """
    cand_years = [int(y) for y in re.findall(r"\b(19[5-9]\d|20[0-3]\d)\b", candidate_text or "")]
    if target_year and cand_years:
        if target_year not in cand_years:
            return False

    norm_target = normalize_title_key(target_title)
    norm_cand = normalize_title_key(candidate_text)
    if not norm_target or not norm_cand:
        return False

    # Compact comparison (handles "spiderman" == "spider man", "bluelock" == "blue lock")
    compact_target = norm_target.replace(" ", "")
    compact_cand = norm_cand.replace(" ", "")
    if compact_target in compact_cand:
        return True

    # Word-level strict check
    stop_words = {"the", "a", "an", "of", "and", "in", "to", "for", "part", "movie"}
    target_words = [w for w in norm_target.split() if w not in stop_words and len(w) > 1]
    if not target_words:
        target_words = norm_target.split()

    cand_words = set(norm_cand.split())
    for tw in target_words:
        if tw not in cand_words and tw not in compact_cand:
            return False

    return True

def parse_smart_user_command(raw_text: str) -> dict:
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

    # 2. Detect Season + Episode Range specifications
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

            ep_str = re.sub(r"\bs(?:eason)?\s*0*\d{1,2}\b", " ", tail_str, flags=re.I)

            for r_m in re.finditer(r"(?:ep?(?:isode)?\.?\s*)?0*(\d{1,3})\s*(?:\-|to|–|~)\s*(?:ep?(?:isode)?\.?\s*)?0*(\d{1,3})", ep_str, re.I):
                start_e, end_e = int(r_m.group(1)), int(r_m.group(2))
                if start_e <= end_e and (end_e - start_e) <= 200:
                    target_episodes.update(range(start_e, end_e + 1))

            for single_m in re.finditer(r"(?:ep?(?:isode)?\.?\s*)0*(\d{1,3})\b", ep_str, re.I):
                target_episodes.add(int(single_m.group(1)))

            if season_num is not None or target_episodes:
                text = title_part
                if target_episodes and mode != "missing":
                    mode = "range"

    # 3. Extract explicit release year if user provided one (e.g. "Spiderman 2026")
    target_year = extract_year_from_text(text)
    clean_search_title = re.sub(r"\(\d{4}\)|\[\d{4}\]|\b(19[5-9]\d|20[0-3]\d)\b", "", text).strip(" ,.-:")

    return {
        "raw": raw_text.strip(),
        "title": clean_search_title or text.strip(" ,."),
        "year": target_year,
        "mode": mode,
        "season": season_num,
        "episodes": sorted(list(target_episodes)) if target_episodes else None
    }

def split_bulk_commands(raw_message: str) -> list:
    commands = []
    for line in (raw_message or "").splitlines():
        line = line.strip()
        if not line or re.match(r"^[\.\s,]+$", line):
            continue
        if "," in line and not re.search(r"\b(?:ep?|episode|s\d{1,2})\b", line, re.I):
            parts = [p.strip(" .,-") for p in line.split(",") if len(p.strip(" .,-")) >= 2]
            commands.extend(parts)
        else:
            cleaned = line.strip(" .,-")
            if len(cleaned) >= 2:
                commands.append(cleaned)
    return commands

# ============================================================================
# 3. CROSS-CHANNEL DEDUPLICATION DATABASE & EXISTING RESOLUTION TRACKER
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

def get_existing_resolutions(clean_title: str, ep_tag: str = "MOVIE") -> set:
    """Returns all resolutions (e.g. {'720P'}) already stored for this movie/episode."""
    t_key = normalize_title_key(clean_title)
    conn = sqlite3.connect("storage_index.db")
    rows = conn.execute(
        "SELECT DISTINCT res_tag FROM indexed_files_v2 WHERE clean_title=? AND ep_tag=?",
        (t_key, ep_tag.upper().strip())
    ).fetchall()
    conn.close()
    return {r[0].upper() for r in rows}

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
    global DIALOG_CACHE_READY
    if DIALOG_CACHE_READY:
        return
    try:
        await client.get_dialogs(limit=100)
        DIALOG_CACHE_READY = True
    except Exception as e:
        print(f"⚠️ Dialog cache warmup notice: {e}")

async def sync_title_across_all_channels(query: str, target_year: int = None):
    clean_q = normalize_title_key(query)
    if not clean_q or clean_q == "unknown" or clean_q in SYNCED_TITLES:
        return

    await ensure_dialogs_cached()
    SYNCED_TITLES.add(clean_q)

    meta = await fetch_meta(query, target_year)
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
                real_fn = extract_real_filename(fn, msg.text or "")
                if not is_strict_title_and_year_match(real_fn, clean_q, target_year):
                    continue

                ep_tag = extract_season_ep_tag(txt, "S01")
                res_tag = extract_resolution_tag(txt, "HD")
                record_indexed_file(ch_id, clean_q, ep_tag, res_tag, fn or "existing_post")
                found_count += 1
            await asyncio.sleep(0.2)
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds + 1)
        except Exception:
            pass

    if found_count:
        existing_res = get_existing_resolutions(clean_q, "MOVIE")
        print(f"✅ Synced {found_count} existing files for '{clean_q}' (Existing movie resolutions: {existing_res or 'Series/None'}).")

init_storage_db()

# ============================================================================
# 4. OFFICIAL METADATA, PUBLIC NAME, YEAR & GENRE RESOLVER (TMDB + OMDB + IMDB)
# ============================================================================
async def fetch_meta(title: str, target_year: int = None):
    clean = normalize_title_key(title)
    cache_key = f"{clean}:{target_year or 'any'}"
    if cache_key in META_CACHE:
        return META_CACHE[cache_key]

    public_title = title.strip().title()
    resolved_year = target_year
    genres_list = []
    lang = ""
    runtime = ""
    rating = ""
    is_series = bool(re.search(r"\b(S\d{1,2}|Season|E\d{1,3}|EP\.?\s*\d+|Episode)\b", title, re.I))

    async with aiohttp.ClientSession() as session:
        # 1. TMDB Search (Supports Year Filtering & Direct Genre ID Mapping)
        if TMDB_API_KEY:
            try:
                url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote_plus(clean)}"
                async with session.get(url, timeout=4) as r:
                    if r.status == 200:
                        results = (await r.json()).get("results") or []
                        chosen = None
                        for item in results:
                            rel_date = item.get("release_date") or item.get("first_air_date") or ""
                            item_year = int(rel_date[:4]) if len(rel_date) >= 4 and rel_date[:4].isdigit() else None
                            if target_year and item_year and item_year != target_year:
                                continue
                            chosen = item
                            resolved_year = item_year or resolved_year
                            break

                        if not chosen and results and not target_year:
                            chosen = results[0]
                            rel_date = chosen.get("release_date") or chosen.get("first_air_date") or ""
                            if len(rel_date) >= 4 and rel_date[:4].isdigit():
                                resolved_year = int(rel_date[:4])

                        if chosen:
                            public_title = chosen.get("title") or chosen.get("name") or public_title
                            lang = chosen.get("original_language", "")
                            if chosen.get("media_type") == "tv":
                                is_series = True
                            vote = chosen.get("vote_average")
                            if vote and float(vote) > 0:
                                rating = f"{float(vote):.1f}/10"
                            for gid in (chosen.get("genre_ids") or []):
                                if gid in TMDB_GENRE_MAP:
                                    genres_list.append(TMDB_GENRE_MAP[gid])
            except Exception:
                pass

        # 2. OMDb Lookup (With Optional Year Parameter)
        if OMDB_API_KEY:
            try:
                y_param = f"&y={target_year}" if target_year else ""
                url = f"https://www.omdbapi.com/?apikey={OMDB_API_KEY}&t={quote_plus(clean)}{y_param}"
                async with session.get(url, timeout=4) as r:
                    if r.status == 200:
                        data = await r.json()
                        if data.get("Response") == "True":
                            public_title = data.get("Title") or public_title
                            y_str = re.search(r"\b(19\d\d|20\d\d)\b", data.get("Year", ""))
                            if y_str:
                                resolved_year = resolved_year or int(y_str.group(1))
                            omdb_genres = [g.strip() for g in (data.get("Genre") or "").split(",") if g.strip() and g.strip() != "N/A"]
                            for og in omdb_genres:
                                if og not in genres_list:
                                    genres_list.append(og)
                            if data.get("Runtime") and data.get("Runtime") != "N/A":
                                runtime = data.get("Runtime")
                            if data.get("imdbRating") and data.get("imdbRating") != "N/A":
                                rating = f"{data.get('imdbRating')}/10"
                            if data.get("Type") == "series":
                                is_series = True
            except Exception:
                pass

        # 3. IMDb Free Public Suggestion API Fallback (No Key Needed, Great for 2026 Releases)
        if not resolved_year or public_title.lower() == clean:
            try:
                first_char = clean[0] if clean else "a"
                imdb_url = f"https://v3.sg.media-imdb.com/suggestion/{first_char}/{quote_plus(clean)}.json"
                async with session.get(imdb_url, timeout=4) as ir:
                    if ir.status == 200:
                        idata = await ir.json()
                        for item in (idata.get("d") or []):
                            lbl = item.get("l", "")
                            iy = item.get("y")
                            if target_year and iy and int(iy) != target_year:
                                continue
                            if is_strict_title_and_year_match(lbl, clean, target_year):
                                public_title = lbl
                                if iy:
                                    resolved_year = int(iy)
                                q_type = (item.get("q") or "").lower()
                                if "series" in q_type or "tv" in q_type:
                                    is_series = True
                                break
            except Exception:
                pass

    result = {
        "title": clean,
        "public_title": public_title,
        "year": resolved_year,
        "genres": ", ".join(genres_list),
        "language": lang,
        "runtime": runtime,
        "rating": rating,
        "is_series": is_series
    }
    META_CACHE[cache_key] = result
    return result

async def fetch_official_catalog(title: str) -> dict:
    clean = normalize_title_key(title)
    seasons_map = {}
    franchise_movies = []

    async with aiohttp.ClientSession() as session:
        try:
            url = f"https://api.tvmaze.com/singlesearch/shows?q={quote_plus(clean)}&embed=episodes"
            async with session.get(url, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    eps = data.get("_embedded", {}).get("episodes", [])
                    for ep in eps:
                        s_num, e_num = ep.get("season"), ep.get("number")
                        if s_num and e_num:
                            seasons_map.setdefault(int(s_num), []).append(int(e_num))
        except Exception:
            pass

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

    return {"seasons": seasons_map, "franchise_movies": franchise_movies[:10]}

# ============================================================================
# 5. SUBTITLE-SAFE AUDIO PARSER & GENRE-BASED CHANNEL ROUTER
# ============================================================================
def strip_subtitle_sections(caption: str) -> str:
    """
    Removes Subtitle lines/sections from the bot's caption so subtitle languages
    (like Korean/Turkish subtitles on an English movie) are NEVER mistaken for Audio!
    """
    if not caption:
        return ""
    cleaned_lines = []
    skip_sub_block = False

    for line in caption.splitlines():
        low = line.strip().lower()
        # Detect start of a Subtitle section
        if re.match(r"^(?:💬|📝|🔤)?\s*(?:subtitles?|subs?|esub|mmsub)\s*[:\-]", low) or "subtitles:" in low:
            skip_sub_block = True
            continue
        # If a new non-subtitle section starts (e.g., Audio, Duration, File), stop skipping
        if skip_sub_block and re.match(r"^(?:🔊|🎬|⏳|📁|\*)?\s*(?:audio|video|duration|size|quality|specs)", low):
            skip_sub_block = False
        if not skip_sub_block:
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines)

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

def extract_tech_specs(filename="", caption="", file_size_bytes=0, meta=None):
    meta = meta or {}
    real_fn = extract_real_filename(filename, caption)
    # Strip subtitle sections BEFORE checking for audio languages!
    audio_safe_caption = strip_subtitle_sections(caption or "")
    combined_safe = f"{real_fn}\n{audio_safe_caption}"
    full_combined = f"{real_fn}\n{caption or ''}"

    qual = extract_resolution_tag(full_combined, ACTIVE_RES_TAG or "1080P")

    source = ""
    for pat, label in [
        (r"\b(?:bluray|bdrip|brrip|bd)\b", "BluRay"),
        (r"\bweb-?dl\b", "WEB-DL"),
        (r"\bwebrip\b", "WEBRip"),
        (r"\bhdrip\b", "HDRip"),
        (r"\bamzn\b", "AMZN"),
        (r"\bnf|netflix\b", "NF")
    ]:
        if re.search(pat, full_combined, re.I):
            source = label
            break

    codec = "x264"
    if re.search(r"\b(x265|h265|hevc)\b", full_combined, re.I):
        codec = "HEVC x265"
    elif re.search(r"\bav1\b", full_combined, re.I):
        codec = "AV1"
    if re.search(r"\b10\s*-?bit\b", full_combined, re.I) and "10-Bit" not in codec:
        codec += " 10-Bit"

    aud_codec = "AAC 2.0"
    if re.search(r"\b6ch\b|\bddp\s*5\.1\b|\b5\.1\b", full_combined, re.I):
        aud_codec = "DDP 5.1 (6CH)"
    elif re.search(r"\bddp\s*2\.0\b", full_combined, re.I):
        aud_codec = "DDP 2.0"

    br_m = re.search(r"Video\s*Bitrate\s*:\s*([^\r\n]+)", full_combined, re.I) or re.search(r"\b(\d{3,5}\s*kbps)\b", full_combined, re.I)
    bitrate = br_m.group(1).strip() if br_m else ""

    dur_m = re.search(r"Duration\s*:\s*([^\r\n]+)", full_combined, re.I) or re.search(r"⏳\s*(\d{1,2}:\d{2}(?::\d{2})?)", full_combined)
    duration = dur_m.group(1).strip() if dur_m else ""

    for_tv = bool(re.search(r"Can\s*be\s*played\s*on\s*TV|FOR\s*TV", full_combined, re.I) or "x264" in codec)
    has_sub = bool(re.search(r"Has\s*[a-zA-Z]*\s*subtitles|\b(?:sub|subs|esub|multisub|mmsub)\b|\.mkv\b", full_combined, re.I))

    # Only detect audio languages from filename, explicit Audio lines, or TMDB original_language
    audio_only_text = real_fn
    for ln in audio_safe_caption.splitlines():
        if re.search(r"(?:🔊|audio|language|track|\*\s*aac|\*\s*ddp)", ln, re.I):
            audio_only_text += "\n" + ln

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
        if re.search(pat, audio_only_text, re.I):
            detected_langs.append(name)

    # Fallback to Official Original Language from TMDB/OMDb if filename didn't state audio language
    if not detected_langs:
        orig_lang_map = {
            "en": "English", "ja": "Japanese", "ko": "Korean", "hi": "Hindi",
            "tr": "Turkish", "am": "Amharic", "ta": "Tamil", "te": "Telugu",
            "ml": "Malayalam", "es": "Spanish", "fr": "French", "zh": "Chinese"
        }
        mapped = orig_lang_map.get((meta.get("language") or "").lower(), "English")
        detected_langs.append(mapped)

    audio_lines = [f"* {aud_codec} — {lang_name}" for lang_name in detected_langs]

    audio_mode = ""
    if len(detected_langs) >= 3 or re.search(r"\bmulti\s*audio\b", audio_only_text, re.I):
        audio_mode = f"Multi Audio ({', '.join(detected_langs[:4])})"
    elif len(detected_langs) == 2 or re.search(r"\bdual\s*audio\b", audio_only_text, re.I):
        audio_mode = f"Dual Audio ({' + '.join(detected_langs)})"
    elif detected_langs:
        audio_mode = f"{detected_langs[0]} ({aud_codec})"

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
        "audio_lines": audio_lines,
        "detected_langs": detected_langs
    }

def build_final_caption(filename, caption, meta=None, file_size_bytes=0, ep_tag="MOVIE"):
    meta = meta or {}
    sp = extract_tech_specs(filename, caption, file_size_bytes, meta)
    dur = sp["duration"] or meta.get("runtime", "")

    pub_title = meta.get("public_title") or normalize_title_key(filename).title()
    pub_year = meta.get("year") or extract_year_from_text(filename)
    year_str = f" ({pub_year})" if pub_year and str(pub_year) not in pub_title else ""
    ep_str = f" • {ep_tag}" if ep_tag and ep_tag != "MOVIE" else ""

    # Clean Public Display Filename using official title + year + specs
    clean_public_file = f"{pub_title}{year_str}{ep_str} [{sp['quality']} {sp['source']} {sp['codec']}].mkv".replace("  ", " ")

    lines = [
        f"🎬 <b>Title:</b> <code>{pub_title}{year_str}{ep_str}</code>"
    ]
    if meta.get("genres") or meta.get("rating"):
        g_part = f"🎭 <b>Genre:</b> {meta['genres']}" if meta.get("genres") else ""
        r_part = f"⭐ <b>{meta['rating']}</b>" if meta.get("rating") else ""
        lines.append(" • ".join(p for p in [g_part, r_part] if p))

    lines.extend([
        "",
        "<b>File Name:</b>",
        f"<code>{clean_public_file}</code>"
    ])

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

def detect_category(filename, caption, meta):
    """
    Routes files strictly using Official TMDB/OMDb Genres & Original Language + Filename
    (NEVER using the caption's subtitle list!).
    """
    meta = meta or {}
    genres_low = (meta.get("genres") or "").lower()
    lang = (meta.get("language") or "").lower()
    fn_low = (filename or "").lower()

    # 1. Anime check
    if re.search(r"\b(anime|demon slayer|solo leveling|jujutsu|naruto|one piece|bleach|attack on titan|kimetsu|blue lock)\b", f"{fn_low} {meta.get('public_title', '').lower()}") or (
        "animation" in genres_low and lang in ("ja", "zh")
    ):
        return "anime"

    # 2. Animation check
    if "animation" in genres_low:
        return "animation"

    # 3. Series check
    if meta.get("is_series") or re.search(r"\b(S\d{1,2}\s*E\d{1,3}|Season\s*\d+|Episode\s*\d+|EP\.?\s*\d+)\b", fn_low, re.I):
        return "series"

    # 4. Regional Languages (Strictly from Official Original Language or Filename, NOT Subtitles!)
    if lang == "am" or re.search(r"[\u1200-\u137F]+|amharic|ትርጉም", fn_low):
        return "amharic"

    if lang in INDIAN_LANG_CODES or re.search(r"\b(hindi|bollywood|telugu|tamil|malayalam)\b", fn_low):
        return "hindi"

    if lang == "ko" or re.search(r"\b(korean|k-drama)\b", fn_low):
        return "korean"

    if lang == "tr" or re.search(r"\bturkish\b", fn_low):
        return "turkish"

    # 5. Movie Genre Routing (Horror / Action / English)
    if "horror" in genres_low or "thriller" in genres_low:
        return "horror"

    if any(g in genres_low for g in ["action", "adventure", "war", "sci-fi"]):
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
# 6. DEEP MULTI-PAGE BUTTON SCORING (STRICT TITLE/YEAR & MULTI-RESOLUTION)
# ============================================================================
def find_strict_title_button_on_page(buttons, query: str, target_year: int = None):
    """
    Finds a button on the current Title Selection page that strictly matches
    both the title words and target_year (if a year is present).
    Returns None if no strict match exists on this page (so the bot clicks Next ⏩).
    """
    clean_q = normalize_title_key(query)
    candidates = []

    for row in buttons:
        for btn in row:
            txt = (btn.text or "").strip()
            low = txt.lower()
            if any(skip in low for skip in ["back", "close", "next", "page", "⏩", "⏪"]):
                continue

            if not is_strict_title_and_year_match(txt, query, target_year):
                continue

            score = 100
            btn_clean = normalize_title_key(txt)
            if btn_clean == clean_q:
                score += 500
            elif btn_clean.startswith(clean_q):
                score += 300

            # Bonus if explicit target_year is visibly confirmed in the button text
            if target_year and str(target_year) in txt:
                score += 400

            if "📺" in txt or "season" in low:
                score += 200
            elif "🎬" in txt and not re.search(r"\b\d{2}\s*mkv\b", low):
                score += 150
            if re.search(r"\b\d{2}\s*mkv\b", low):
                score -= 150

            candidates.append((score, btn))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None

def score_quality_button(text: str) -> int:
    t = text.upper()
    score = 0
    if "2160P" in t or "4K" in t: score += 1200
    elif "1080P" in t: score += 1000
    elif "720P" in t: score += 700
    elif "480P" in t: score += 400
    elif "360P" in t: score += 200

    # High priority for Multi-Audio / Dual-Audio options
    if "MULTI" in t or "DUAL" in t: score += 350
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
            if any(skip in low for skip in ["back", "close", "next", "page", "⏩", "⏪"]):
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
# 7. CLICK & PAGINATION HELPERS
# ============================================================================
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

def get_next_page_button(msg):
    if not msg or not msg.buttons:
        return None
    for row in msg.buttons:
        for btn in row:
            txt = (btn.text or "").strip().lower()
            if "next" in txt or "⏩" in txt or "➡" in txt:
                return btn
    return None

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
# 8. INCOMING MEDIA EVENT (STRICT YEAR/TITLE CHECK + PUBLIC CAPTION + GENRE ROUTE)
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
    real_fn = extract_real_filename(raw_fn, caption)
    combined_text = f"{real_fn}\n{caption}"

    print(f"📥 Received file: {real_fn} ({format_size_human(file_size)})")

    # Strict Title & Release Year Verification before uploading!
    if ACTIVE_CLEAN_TITLE and not is_strict_title_and_year_match(real_fn, ACTIVE_CLEAN_TITLE, ACTIVE_TARGET_YEAR):
        print(f"🚫 Strict Filter Rejected: '{real_fn}' does not match requested '{ACTIVE_CLEAN_TITLE}' (Year: {ACTIVE_TARGET_YEAR or 'Any'}). Passing!")
        FILE_COMPLETED_EVENT.set()
        return

    title_for_meta = ACTIVE_CLEAN_TITLE or real_fn
    meta = await fetch_meta(title_for_meta, ACTIVE_TARGET_YEAR)
    cat = detect_category(real_fn, caption, meta)
    dest_channel = DEFAULT_CHANNELS.get(cat, DEFAULT_CHANNELS["other"])
    cat_emoji = CATEGORY_EMOJIS.get(cat, "📁")

    ep_tag = ACTIVE_EP_TAG or extract_season_ep_tag(combined_text, ACTIVE_SEASON_TAG)
    res_tag = extract_resolution_tag(combined_text, ACTIVE_RES_TAG or "1080P")
    title_key = ACTIVE_CLEAN_TITLE or meta["title"]

    if is_already_in_any_channel(title_key, ep_tag, res_tag):
        print(f"⏭️ Duplicate detected ({title_key} • {ep_tag} • {res_tag}) — skipping upload.")
        FILE_COMPLETED_EVENT.set()
        return

    final_caption = build_final_caption(raw_fn, caption, meta, file_size, ep_tag)

    try:
        await publish_file_with_green_buttons(dest_channel, msg.media, final_caption)
        print(f"🚀 Published {meta['public_title']} [{ep_tag} • {res_tag}] to {cat_emoji} {cat.upper()}")

        record_indexed_file(dest_channel, title_key, ep_tag, res_tag, real_fn)

        await client.send_message(
            "me",
            f"✅ <b>Published to Storage!</b>\n"
            f"• <b>Public Name:</b> <code>{meta['public_title']} ({meta.get('year') or 'N/A'})</code>\n"
            f"• <b>Spec:</b> <code>{ep_tag} • {res_tag} ({format_size_human(file_size)})</code>\n"
            f"• <b>Genre Route:</b> {cat_emoji} <code>{cat.upper()}</code> ({meta.get('genres') or 'General'})",
            parse_mode="HTML"
        )
    except FloodWaitError as fw:
        await asyncio.sleep(fw.seconds + 1)
    except Exception as e:
        print(f"❌ Storage Forward error: {e}")
    finally:
        FILE_COMPLETED_EVENT.set()

# ============================================================================
# 9. DEEP MULTI-PAGE NAVIGATOR (CLICKS 'NEXT ⏩' ON TITLE & QUALITY PAGES)
# ============================================================================
async def navigate_to_grid(
    query: str,
    target_season_num: int = 1,
    target_res: str = None,
    target_year: int = None,
    send_fresh: bool = True
):
    """
    Deeply navigates through @fileserverxrobot:
    - Paginates Title results (Next ⏩) to find a strict Title + Year match.
    - Paginates Quality pages (Page 1 of 5 ➔ Next ⏩) to find 360P, 480P, 720P, 1080P, or Multi-Audio.
    """
    search_str = f"{query} {target_year}".strip() if target_year else query
    if send_fresh:
        print(f"📨 Querying @{SOURCE_BOT}: '{search_str}' (Season {target_season_num:02d} • Target Res: {target_res or 'ALL'})")
        await client.send_message(SOURCE_BOT, search_str)
        await asyncio.sleep(2.0)

    tracked_menu_id = None
    available_seasons = []
    discovered_resolutions = []
    retried_without_year = False
    title_page_tries = 0
    quality_page_tries = 0

    for _ in range(25):
        if SKIP_CURRENT_TITLE:
            break
        msg = await find_latest_menu_message(SOURCE_BOT, tracked_menu_id)
        if not msg or not msg.buttons:
            # If searching "Title 2026" returned no buttons in the bot, retry once with just "Title"
            # and still enforce target_year strictly on the button/file results!
            if target_year and not retried_without_year:
                retried_without_year = True
                print(f"🔄 Retrying search with base title '{query}' (while strictly enforcing year {target_year})...")
                await client.send_message(SOURCE_BOT, query)
                await asyncio.sleep(2.2)
                continue
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

        # 2. Screen D: File / Episode Grid Reached
        if is_file_or_episode_grid(msg):
            return msg, available_seasons, (discovered_resolutions or TARGET_RESOLUTIONS)

        # 3. Screen A: Title Selection (With Strict Year Match & 'Next ⏩' Pagination!)
        if "choose the correct title" in text or "matching results" in text or "search results for" in text:
            strict_btn = find_strict_title_button_on_page(msg.buttons, query, target_year)
            if strict_btn:
                print(f"👉 [Screen 1/Title] Strict Match Found: '{strict_btn.text}'")
                await smart_click_button(strict_btn)
                await wait_for_menu_update(SOURCE_BOT, tracked_menu_id, old_sig)
                continue
            else:
                nxt_btn = get_next_page_button(msg)
                if nxt_btn and title_page_tries < 5:
                    title_page_tries += 1
                    print(f"📄 [Screen 1/Title] Exact match not on page {title_page_tries}, clicking Next ⏩...")
                    await smart_click_button(nxt_btn)
                    await wait_for_menu_update(SOURCE_BOT, tracked_menu_id, old_sig)
                    continue
                else:
                    print(f"🚫 Strict Title/Year Check: '{query} ({target_year or 'Any'})' not found on @{SOURCE_BOT}. Passing!")
                    return None, [], []

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
                        season_btns[int(m.group(1))] = btn
                    elif "season all" in low:
                        fallback_btn = btn

            available_seasons = sorted(season_btns.keys())
            chosen_btn = season_btns.get(target_season_num) or fallback_btn or msg.buttons[0][0]
            print(f"👉 [Screen 2/Season] Clicking: '{chosen_btn.text}'")
            await smart_click_button(chosen_btn)
            await wait_for_menu_update(SOURCE_BOT, tracked_menu_id, old_sig)
            continue

        # 5. Screen C: Quality Selection (Clicks 'Next ⏩' across Quality Pages 1..5 to find target_res!)
        if "select the available quality" in text or "quality" in text:
            res_options = collect_resolution_quality_buttons(msg.buttons)
            for r_tag, _, _ in res_options:
                if r_tag not in discovered_resolutions:
                    discovered_resolutions.append(r_tag)

            chosen_qual_btn = None
            if target_res:
                for r_tag, _, r_btn in res_options:
                    if r_tag == target_res:
                        chosen_qual_btn = r_btn
                        break

                # If target_res (e.g. 720P, 480P, 360P) is on Page 2..5 of Quality Menu, click Next ⏩!
                if not chosen_qual_btn:
                    nxt_qual_btn = get_next_page_button(msg)
                    if nxt_qual_btn and quality_page_tries < 6:
                        quality_page_tries += 1
                        print(f"📄 [Screen 3/Quality] '{target_res}' not on Quality Page {quality_page_tries} — clicking Next ⏩...")
                        await smart_click_button(nxt_qual_btn)
                        await wait_for_menu_update(SOURCE_BOT, tracked_menu_id, old_sig)
                        continue
                    else:
                        print(f"⏭️ Resolution '{target_res}' not available across Quality pages.")
                        return None, available_seasons, discovered_resolutions
            else:
                # Pick first resolution not yet in storage if possible
                for r_tag, _, r_btn in res_options:
                    if not is_already_in_any_channel(query, "MOVIE", r_tag):
                        chosen_qual_btn = r_btn
                        break
                if not chosen_qual_btn and res_options:
                    chosen_qual_btn = res_options[0][2]

            if chosen_qual_btn:
                print(f"👉 [Screen 3/Quality] Clicking: '{chosen_qual_btn.text}'")
                await smart_click_button(chosen_qual_btn)
                await wait_for_menu_update(SOURCE_BOT, tracked_menu_id, old_sig)
                continue
            else:
                break

        await asyncio.sleep(1.0)

    return None, available_seasons, discovered_resolutions

# ============================================================================
# 10. MULTI-PAGE FILE & EPISODE DOWNLOADER
# ============================================================================
async def download_from_grid(
    initial_msg,
    query: str,
    season_num: int,
    active_res: str,
    target_episodes: set = None,
    only_missing: bool = False,
    target_year: int = None
):
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
        next_button = get_next_page_button(current_msg)

        for row in current_msg.buttons:
            for btn in row:
                txt = (btn.text or "").strip()
                low = txt.lower()
                if any(nav in low for nav in ["back", "close", "page", "next", "⏩", "⏪"]):
                    continue

                # If button text has a year that conflicts with target_year, skip it immediately
                if target_year:
                    btn_years = [int(y) for y in re.findall(r"\b(19[5-9]\d|20[0-3]\d)\b", txt)]
                    if btn_years and target_year not in btn_years:
                        continue

                ep_m = re.search(r"\b(?:E|EP\.?)0*(\d{1,3})\b", txt, re.I)
                ep_num = int(ep_m.group(1)) if ep_m else None
                if ep_num is not None:
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

        if next_button and len(item_order) >= 2 and remaining_targets is None:
            last_key = item_order[-1]
            last_size = best_item_map[last_key][0]
            prev_sizes = [best_item_map[k][0] for k in item_order[:-1] if best_item_map[k][0] > 0]
            avg_prev = (sum(prev_sizes) / len(prev_sizes)) if prev_sizes else 0
            if item_counts.get(last_key, 0) == 1 and avg_prev > 0 and last_size < (avg_prev * 0.55) and last_key[0] != "MOVIE":
                item_order.pop()

        for ep_tag, btn_res, ep_num in item_order:
            if SKIP_CURRENT_TITLE:
                break
            _, btn = best_item_map[(ep_tag, btn_res, ep_num)]

            check_res = "ANY" if only_missing else btn_res
            if is_already_in_any_channel(ACTIVE_CLEAN_TITLE, ep_tag, check_res):
                print(f"⏭️ Already have {ACTIVE_CLEAN_TITLE} [{ep_tag} • {btn_res}] in storage — checking other resolutions/options.")
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
                recovered_msg, _, _ = await navigate_to_grid(query, season_num, active_res, target_year, send_fresh=True)
                if recovered_msg:
                    menu_msg_id = recovered_msg.id
                    current_msg = recovered_msg
                    for _ in range(1, current_page):
                        nxt = get_next_page_button(current_msg)
                        if nxt:
                            old_s = get_menu_signature(current_msg)
                            await smart_click_button(nxt)
                            current_msg = await wait_for_menu_update(SOURCE_BOT, menu_msg_id, old_s)

        if remaining_targets is not None and len(remaining_targets) == 0:
            break

        refreshed = await client.get_messages(SOURCE_BOT, ids=menu_msg_id)
        if refreshed and refreshed.buttons:
            current_msg = refreshed
            next_button = get_next_page_button(current_msg)

        if next_button:
            current_page += 1
            print(f"📄 Clicking Next ⏩ on file grid to load Page {current_page}...")
            old_sig = get_menu_signature(current_msg)
            await smart_click_button(next_button)
            current_msg = await wait_for_menu_update(SOURCE_BOT, menu_msg_id, old_sig)
            if not current_msg or not current_msg.buttons:
                break
        else:
            break

# ============================================================================
# 11. MASTER UNSTOPPABLE QUEUE WORKER & MISSING-FILES AUDITOR
# ============================================================================
async def execute_missing_audit_and_fill(cmd: dict):
    title = cmd["title"]
    target_year = cmd.get("year")
    clean_t = normalize_title_key(title)
    await sync_title_across_all_channels(title, target_year)

    catalog = await fetch_official_catalog(title)
    existing_eps = get_indexed_episodes_for_title(clean_t)

    if not catalog["seasons"] and len(catalog["franchise_movies"]) > 1:
        missing_movies = []
        for m_title in catalog["franchise_movies"]:
            await sync_title_across_all_channels(m_title)
            if not is_already_in_any_channel(m_title, "MOVIE", "ANY"):
                missing_movies.append(m_title)

        await client.send_message(
            "me",
            f"🔎 <b>Franchise Audit:</b> <code>{title.title()}</code>\n"
            f"• <b>Total Parts Found:</b> <code>{len(catalog['franchise_movies'])}</code>\n"
            f"• <b>Missing Parts ({len(missing_movies)}):</b> <code>{', '.join(missing_movies) or 'None!'}</code>",
            parse_mode="HTML"
        )
        for m_title in missing_movies:
            TITLE_QUEUE.appendleft(({"raw": m_title, "title": m_title, "year": None, "mode": "normal", "season": None, "episodes": None}, True))
        return

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
            f"S{s:02d} ({len(eps)} missing)"
            for s, eps in sorted(missing_by_season.items())
        )
        await client.send_message(
            "me",
            f"🔎 <b>Missing Files Audit:</b> <code>{title.title()}</code>\n"
            f"• <b>In Storage:</b> <code>{len(existing_eps)}</code> episodes\n"
            f"• <b>Missing:</b> <code>{summary}</code>\n"
            f"⚡ <i>Downloading missing episodes now...</i>",
            parse_mode="HTML"
        )
        for s_num, miss_eps in sorted(missing_by_season.items()):
            if SKIP_CURRENT_TITLE:
                break
            grid_msg, _, _ = await navigate_to_grid(title, s_num, target_res=None, target_year=target_year, send_fresh=True)
            if grid_msg:
                await download_from_grid(grid_msg, title, s_num, "1080P", target_episodes=miss_eps, only_missing=True, target_year=target_year)
    else:
        grid_msg, all_seasons, _ = await navigate_to_grid(title, cmd["season"] or 1, target_res=None, target_year=target_year, send_fresh=True)
        if grid_msg:
            seasons = [cmd["season"]] if cmd["season"] else (all_seasons or [1])
            await download_from_grid(grid_msg, title, seasons[0], "1080P", only_missing=True, target_year=target_year)

async def process_queue():
    global CURRENT_PIPELINE_ACTIVE, ACTIVE_CLEAN_TITLE, ACTIVE_TARGET_YEAR, ACTIVE_EP_TAG, ACTIVE_RES_TAG, SKIP_CURRENT_TITLE
    if CURRENT_PIPELINE_ACTIVE:
        return

    CURRENT_PIPELINE_ACTIVE = True
    try:
        while TITLE_QUEUE:
            try:
                SKIP_CURRENT_TITLE = False
                cmd, send_initial = TITLE_QUEUE.popleft()
                if isinstance(cmd, str):
                    cmd = parse_smart_user_command(cmd)

                query = cmd["title"]
                target_year = cmd.get("year")
                ACTIVE_CLEAN_TITLE = normalize_title_key(query)
                ACTIVE_TARGET_YEAR = target_year
                ACTIVE_EP_TAG = ""
                ACTIVE_RES_TAG = ""

                print(f"🎬 Starting: '{query}' (Year: {target_year or 'Any'}) | Mode: {cmd['mode']}")

                if cmd["mode"] == "missing":
                    await execute_missing_audit_and_fill(cmd)
                    continue

                await sync_title_across_all_channels(query, target_year)
                if SKIP_CURRENT_TITLE:
                    continue

                start_season = cmd["season"] or 1
                target_eps = set(cmd["episodes"]) if cmd["episodes"] else None

                # Initial deep navigation (paginates Title & Quality menus as needed)
                grid_msg, all_seasons, _ = await navigate_to_grid(
                    query, start_season, target_res=None, target_year=target_year, send_fresh=send_initial
                )
                if not grid_msg:
                    await client.send_message(
                        "me",
                        f"⚠️ <b>Strictly Passed:</b> Exact match for <code>{cmd['raw']}</code> was not found on @{SOURCE_BOT}.",
                        parse_mode="HTML"
                    )
                    continue

                if target_eps:
                    await download_from_grid(
                        grid_msg, query, start_season, ACTIVE_RES_TAG or "1080P",
                        target_episodes=target_eps, target_year=target_year
                    )
                else:
                    seasons_to_run = [start_season] if cmd["season"] else (all_seasons if all_seasons else [start_season])
                    first_season = seasons_to_run[0]

                    # Download from first reached grid
                    await download_from_grid(grid_msg, query, first_season, ACTIVE_RES_TAG or "1080P", target_year=target_year)

                    # Deep Multi-Resolution Hunt: Check all TARGET_RESOLUTIONS (1080P, 720P, 480P, 360P, 2160P)
                    # clicking Next ⏩ across Quality pages 1..5 to find every resolution not yet in storage!
                    for res_tag in TARGET_RESOLUTIONS:
                        if SKIP_CURRENT_TITLE:
                            break
                        if not all_seasons and is_already_in_any_channel(query, "MOVIE", res_tag):
                            print(f"⏭️ Already have '{query}' in {res_tag} — skipping {res_tag}.")
                            continue

                        print(f"🔎 Hunting resolution {res_tag} across Quality pages for '{query}'...")
                        await asyncio.sleep(1.5)
                        res_grid, _, _ = await navigate_to_grid(
                            query, first_season, target_res=res_tag, target_year=target_year, send_fresh=True
                        )
                        if res_grid and not SKIP_CURRENT_TITLE:
                            await download_from_grid(res_grid, query, first_season, res_tag, target_year=target_year)

                    # Process remaining seasons (Season 02, 03...) across resolutions
                    for next_s in seasons_to_run[1:]:
                        if SKIP_CURRENT_TITLE or next_s <= first_season:
                            continue
                        for res_tag in TARGET_RESOLUTIONS:
                            if SKIP_CURRENT_TITLE:
                                break
                            await asyncio.sleep(1.5)
                            r_grid, _, _ = await navigate_to_grid(
                                query, next_s, target_res=res_tag, target_year=target_year, send_fresh=True
                            )
                            if r_grid and not SKIP_CURRENT_TITLE:
                                await download_from_grid(r_grid, query, next_s, res_tag, target_year=target_year)

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
        ACTIVE_TARGET_YEAR = None
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
# 12. SAVED MESSAGES & SOURCE_BOT TRIGGERS
# ============================================================================
@client.on(events.NewMessage(outgoing=True))
async def on_outgoing_trigger(event):
    global SKIP_CURRENT_TITLE, ME_USER_ID
    text = (event.message.text or "").strip()
    if not text or text.startswith(("/", "✅", "⏳", "🏁", "⚠️", "🔗", "📋", "🛑", "🔎", "🎬")):
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
                f"• <b>Active:</b> <code>{ACTIVE_CLEAN_TITLE or 'Idle'}</code> (Year: {ACTIVE_TARGET_YEAR or 'Any'} • {ACTIVE_RES_TAG or '-'})\n"
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
                y_badge = f" [{c['year']}]" if c.get("year") else ""
                if c["mode"] == "missing":
                    summary_lines.append(f"• 🔍 <b>Missing Audit:</b> <code>{c['title']}{y_badge}</code>")
                elif c["mode"] == "range":
                    s_lbl = f"S{c['season']:02d}" if c["season"] else "S01"
                    e_lbl = ", ".join(f"E{e:02d}" for e in (c["episodes"] or [])[:8])
                    summary_lines.append(f"• 🎯 <b>Selective:</b> <code>{c['title']}{y_badge}</code> ({s_lbl}: {e_lbl})")
                else:
                    summary_lines.append(f"• 🎬 <b>Deep Multi-Res Sync:</b> <code>{c['title']}{y_badge}</code>")

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
# 13. 24/7 KEEP-ALIVE SELF-PING & RENDER HEALTH SERVER
# ============================================================================
async def keep_alive_worker():
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
        "active_year": ACTIVE_TARGET_YEAR,
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

    print(f"🚀 Deep Multi-Page Media Pipeline LIVE! Watching @{SOURCE_BOT} and Saved Messages.")
    await client.run_until_disconnected()

if __name__ == "__main__":
    loop.run_until_complete(main())
