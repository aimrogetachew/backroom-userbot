import os
import re
import json
import asyncio
import sqlite3
from datetime import datetime, timezone
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
from telethon.tl.functions.channels import JoinChannelRequest, CreateChannelRequest, EditAdminRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, ToggleNoForwardsRequest
from telethon.tl.types import ChatAdminRights

# ============================================================================
# 1. CONFIGURATION & DUAL-BOT SETUP (PRIMARY + BACKUP)
# ============================================================================
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()

SOURCE_BOT = os.environ.get("SOURCE_BOT", "fileserverxrobot").lstrip("@").strip()
BACKUP_BOT = os.environ.get("BACKUP_BOT", "Hislordship_Movies_bot").lstrip("@").strip()
SERIES_INDEX_CHANNEL = int(os.environ.get("SERIES_INDEX_CHANNEL", "0"))
PORT = int(os.environ.get("PORT", 10000))
MAX_STORAGE_CHANNELS = 30
MIN_VIDEO_SIZE_BYTES = 15 * 1024 * 1024  # Ignore <15MB .srt subtitle files

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

DETAILED_GENRE_TITLES = {
    "scifi": "Vault • Sci-Fi & Cyberpunk",
    "thriller": "Vault • Suspense & Thriller",
    "comedy": "Vault • Comedy Central",
    "romance": "Vault • Romance & Drama",
    "crime": "Vault • Crime & Noir",
    "fantasy": "Vault • Fantasy & Mythology",
    "mystery": "Vault • Mystery & Detective",
    "adventure": "Vault • Adventure & Survival",
    "documentary": "Vault • Documentary & Biopic",
    "family": "Vault • Family & Kids",
    "war_history": "Vault • War & Historical Epic",
    "south_indian": "Vault • South Indian Cinema",
    "chinese_donghua": "Vault • Chinese & Donghua",
    "european": "Vault • European Cinema",
    "classic_90s": "Vault • 90s & Classic Cinema",
    "franchise_vault": "Vault • Complete Movie Franchises",
    "series_index": "The Backroom • Series Season Index"
}

CATEGORY_EMOJIS = {
    "hindi": "🇮🇳", "english": "🌎", "series": "📺", "amharic": "🇪🇹",
    "korean": "🇰🇷", "horror": "👻", "action": "💥", "anime": "⛩",
    "animation": "🧸", "upcoming": "🔔", "turkish": "🇹🇷", "scifi": "🚀",
    "thriller": "🔪", "comedy": "😂", "romance": "❤️", "crime": "🕵️",
    "fantasy": "🐉", "mystery": "🧩", "adventure": "🧭", "documentary": "🌍",
    "family": "👨‍👩‍👧", "war_history": "⚔️", "south_indian": "🌴",
    "chinese_donghua": "🏮", "european": "🏰", "classic_90s": "📼",
    "franchise_vault": "🏆", "other": "🎬"
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

GREEN_BRAND_ROW = [
    {"text": "Series", "url": "https://t.me/thebackroomseries", "style": "success"},
    {"text": "Anime", "url": "https://t.me/thebackroomanime", "style": "success"},
    {"text": "Movies", "url": "https://t.me/thebackroommovies", "style": "success"}
]

GREEN_BOT_API_MARKUP = {"inline_keyboard": [GREEN_BRAND_ROW]}

TARGET_RESOLUTIONS = ["1080P", "720P", "480P", "360P", "2160P"]
INDIAN_LANG_CODES = {"hi", "te", "ta", "ml", "kn", "bn", "mr", "pa", "gu", "ur"}

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)

FILE_COMPLETED_EVENT = asyncio.Event()
CAPTURED_FILES = set()
SYNCED_TITLES = set()
DIALOG_CACHE_READY = False
META_CACHE = {}
TITLE_QUEUE = deque()

OMDB_CALL_DAY = ""
OMDB_CALL_COUNT = 0
OMDB_DAILY_SAFE_LIMIT = 900
TMDB_SEMAPHORE = asyncio.Semaphore(3)

CURRENT_PIPELINE_ACTIVE = False
SKIP_CURRENT_TITLE = False
AUTO_DISCOVERY_ENABLED = True
AUTO_CREATE_CHANNELS = False
LAST_REALTIME_RADAR_TS = 0.0
ME_USER_ID = None
BOT_USERNAME = ""

ACTIVE_CLEAN_TITLE = ""
ACTIVE_TARGET_YEAR = None
ACTIVE_SEASON_TAG = "S01"
ACTIVE_EP_TAG = ""
ACTIVE_RES_TAG = ""

def current_utc_year() -> int:
    return datetime.now(timezone.utc).year

def current_utc_date_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ============================================================================
# 2. CLEAN TITLE, COPYRIGHT GUARD & NATURAL LANGUAGE PARSER
# ============================================================================
def obfuscate_copyright_text(text: str) -> str:
    """Inserts invisible zero-width joiners inside studio/brand words to block DMCA keyword scrapers."""
    if not text:
        return ""
    # Protect common DMCA keyword triggers while keeping text 100% human-readable
    for word in ["BluRay", "WEB-DL", "WEBRip", "Netflix", "Marvel", "Disney", "Sony", "Warner"]:
        text = re.sub(f"(?i)({word[:2]})({word[2:]})", r"\1&#8203;\2", text)
    return text

def extract_year_from_text(text: str):
    m = re.search(r"\b(19[5-9]\d|20[0-3]\d)\b", text or "")
    return int(m.group(1)) if m else None

def normalize_title_key(title: str) -> str:
    s = re.sub(r"['’`]", "", title or "")
    s = re.sub(r"[._\-·•]+", " ", s).strip()
    s = re.sub(r"^[^\w]+", "", s).strip()

    yr_m = re.search(r"^(.{2,}?)\s+\b(?:19[5-9]\d|20[0-3]\d)\b", s)
    if yr_m:
        s = yr_m.group(1)

    s = re.sub(
        r"\b(S\d{1,2}|Season\s*\d+|E\d{1,3}|EP\.?\s*\d+|Episode\s*\d+|360p|480p|540p|720p|1080p|1440p|2160p|4K|BluRay|WEB-?DL|WEBRip|HDRip|NF|AMZN|DSNP|Pahe|MKV|MP4|HEVC|x265|x264|10Bit|6CH|8CH|AAC\d*|DDP\d*|ESub|Dual\s*Audio|Multi\s*Audio|Dubbed|Dub)\b.*$",
        "",
        s,
        flags=re.I
    )
    clean = re.sub(r"[^a-zA-Z0-9]+", " ", s).strip().lower()
    return clean or "unknown"

def make_slug_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_title_key(title))[:24] or "show"

def is_strict_title_and_year_match(candidate_text: str, target_title: str, target_year: int = None) -> bool:
    cand_years = [int(y) for y in re.findall(r"\b(19[5-9]\d|20[0-3]\d)\b", candidate_text or "")]
    if target_year and cand_years:
        if target_year not in cand_years:
            return False

    norm_target = normalize_title_key(target_title)
    norm_cand = normalize_title_key(candidate_text)
    if not norm_target or not norm_cand or norm_cand == "unknown":
        return False

    compact_target = norm_target.replace(" ", "")
    compact_cand = norm_cand.replace(" ", "")
    if compact_target == compact_cand or compact_target in compact_cand:
        return True

    stop_words = {"the", "a", "an", "of", "and", "in", "to", "for", "part", "movie", "seasons", "season"}
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
# 3. DATABASE: MULTI-OPTION DEDUP, 30-CHANNEL MAP & SERIES INDEX HUB
# ============================================================================
def extract_resolution_tag(text: str, default_res: str = "HD") -> str:
    m = re.search(r"\b(360p|480p|540p|720p|1080p|1440p|2160p|4K)\b", text or "", re.I)
    if m:
        r = m.group(1).upper()
        return "2160P" if r == "4K" else r
    return default_res.upper()

def extract_audio_tier(text: str, num_langs: int = 1) -> int:
    t = (text or "").lower()
    if num_langs >= 3 or re.search(r"\b(multi\s*audio|multi\s*dub|multi|dubbed|hin.*eng.*jap|tam.*tel.*hin)\b", t):
        return 3
    if num_langs == 2 or re.search(r"\b(dual\s*audio|dual\s*dub|dual|hin[\s\-\+]+eng|eng[\s\-\+]+jap)\b", t):
        return 2
    return 1

def extract_movie_variant_key(text: str, res_tag: str, audio_tier: int) -> str:
    """
    For Movies & Franchises, allows storing multiple distinct options even in the same resolution!
    e.g., 1080P_MULTI, 1080P_DUAL, 1080P_HEVC, 1080P_X264
    """
    t = (text or "").upper()
    aud = "MULTI" if audio_tier == 3 else ("DUAL" if audio_tier == 2 else "STD")
    codec = "HEVC" if any(k in t for k in ["HEVC", "X265", "10BIT", "AV1", "ATMOS"]) else "X264"
    return f"{res_tag}_{aud}_{codec}"

def extract_season_ep_tag(text: str, default_season: str = "S01") -> str:
    s_m = re.search(r"\b(?:S|Season\s*)0*(\d{1,2})\b", text or "", re.I)
    e_m = re.search(r"\b(?:E|EP\.?|Episode\s*)0*(\d{1,3})\b", text or "", re.I)

    s_tag = f"S{int(s_m.group(1)):02d}" if s_m else default_season.upper()
    if e_m:
        e_tag = f"E{int(e_m.group(1)):02d}"
        return f"{s_tag}{e_tag}"
    return "MOVIE"

def init_storage_db():
    now_yr = current_utc_year()
    conn = sqlite3.connect("storage_index.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS indexed_files_v4 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            message_id INTEGER DEFAULT 0,
            clean_title TEXT NOT NULL,
            slug_key TEXT NOT NULL,
            ep_tag TEXT NOT NULL,
            res_tag TEXT NOT NULL,
            variant_key TEXT NOT NULL,
            audio_tier INTEGER DEFAULT 1,
            file_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(clean_title, ep_tag, variant_key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dynamic_channels (
            cat_key TEXT PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            title TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS series_index_cards (
            slug_key TEXT PRIMARY KEY,
            clean_title TEXT NOT NULL,
            public_title TEXT NOT NULL,
            index_channel_id INTEGER NOT NULL,
            index_message_id INTEGER NOT NULL,
            seasons_json TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS persistent_meta_cache (
            cache_key TEXT PRIMARY KEY,
            json_data TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS crawler_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            current_year INTEGER DEFAULT {now_yr},
            current_page INTEGER DEFAULT 1,
            media_kind TEXT DEFAULT 'movie'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crawler_history (
            clean_title TEXT PRIMARY KEY,
            year INTEGER,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO crawler_state (id, current_year, current_page, media_kind) VALUES (1, ?, 1, 'movie')",
        (now_yr,)
    )
    for k, cid in DEFAULT_CHANNELS.items():
        conn.execute("INSERT OR IGNORE INTO dynamic_channels (cat_key, channel_id, title) VALUES (?, ?, ?)", (k, cid, k.upper()))
    conn.commit()
    conn.close()

def load_all_channels_from_db() -> dict:
    conn = sqlite3.connect("storage_index.db")
    rows = conn.execute("SELECT cat_key, channel_id FROM dynamic_channels").fetchall()
    conn.close()
    ch_map = dict(DEFAULT_CHANNELS)
    for k, cid in rows:
        if cid:
            ch_map[k] = int(cid)
    return ch_map

def count_unique_storage_channels() -> int:
    ch_map = load_all_channels_from_db()
    return len(set(ch_map.values()))

async def get_or_create_genre_channel(cat_key: str) -> int:
    """
    Returns the channel_id for cat_key.
    If cat_key is a new detailed genre and AUTO_CREATE_CHANNELS is enabled (up to 30 channels max),
    automatically creates a private Copyright-Safe Telegram channel and promotes BOT_TOKEN as Admin!
    """
    ch_map = load_all_channels_from_db()
    if cat_key in ch_map and ch_map[cat_key]:
        return ch_map[cat_key]

    if not AUTO_CREATE_CHANNELS or count_unique_storage_channels() >= MAX_STORAGE_CHANNELS:
        # Fallback to closest main channel
        fallback_map = {
            "scifi": "action", "adventure": "action", "war_history": "action",
            "thriller": "horror", "mystery": "horror", "crime": "action",
            "south_indian": "hindi", "chinese_donghua": "anime",
            "family": "animation", "comedy": "english", "romance": "english",
            "fantasy": "action", "documentary": "english", "european": "english",
            "classic_90s": "english", "franchise_vault": "english"
        }
        fb = fallback_map.get(cat_key, "other")
        return ch_map.get(fb, DEFAULT_CHANNELS["other"])

    ch_title = DETAILED_GENRE_TITLES.get(cat_key, f"Vault • {cat_key.replace('_', ' ').title()}")
    try:
        res = await client(CreateChannelRequest(
            title=ch_title,
            about=f"Private Protected Storage Vault ({cat_key.upper()})",
            megagroup=False,
            broadcast=True
        ))
        raw_id = res.chats[0].id
        full_channel_id = int(f"-100{raw_id}")

        # Enable Anti-Forwarding Copyright Protection on the new channel
        try:
            await client(ToggleNoForwardsRequest(peer=full_channel_id, enabled=True))
        except Exception:
            pass

        # Promote BOT_TOKEN bot to Admin so Green Inline Buttons work in the new channel
        if BOT_USERNAME:
            try:
                rights = ChatAdminRights(
                    post_messages=True, edit_messages=True, delete_messages=True,
                    invite_users=True, pin_messages=True
                )
                await client(EditAdminRequest(channel=full_channel_id, user_id=BOT_USERNAME, admin_rights=rights, rank="Bot"))
            except Exception as e:
                print(f"⚠️ Admin promote notice on {ch_title}: {e}")

        conn = sqlite3.connect("storage_index.db")
        conn.execute("INSERT OR REPLACE INTO dynamic_channels (cat_key, channel_id, title) VALUES (?, ?, ?)", (cat_key, full_channel_id, ch_title))
        conn.commit()
        conn.close()
        print(f"🆕 Created New Genre Storage Channel: {ch_title} ({full_channel_id})")
        return full_channel_id
    except Exception as e:
        print(f"⚠️ Could not auto-create channel for {cat_key}: {e}")
        return DEFAULT_CHANNELS["other"]

def get_stored_file_record(clean_title: str, ep_tag: str, variant_or_res: str = "ANY"):
    t_key = normalize_title_key(clean_title)
    e_key = ep_tag.upper().strip()
    v_key = (variant_or_res or "ANY").upper().strip()
    conn = sqlite3.connect("storage_index.db")
    if v_key == "ANY":
        row = conn.execute(
            "SELECT channel_id, message_id, audio_tier FROM indexed_files_v4 WHERE clean_title=? AND ep_tag=?",
            (t_key, e_key)
        ).fetchone()
    elif "_" in v_key:
        row = conn.execute(
            "SELECT channel_id, message_id, audio_tier FROM indexed_files_v4 WHERE clean_title=? AND ep_tag=? AND variant_key=?",
            (t_key, e_key, v_key)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT channel_id, message_id, audio_tier FROM indexed_files_v4 WHERE clean_title=? AND ep_tag=? AND res_tag=?",
            (t_key, e_key, v_key)
        ).fetchone()
    conn.close()
    return row

def is_already_in_any_channel(clean_title: str, ep_tag: str, variant_or_res: str = "ANY", min_audio_tier: int = 1) -> bool:
    row = get_stored_file_record(clean_title, ep_tag, variant_or_res)
    if not row:
        return False
    _, _, stored_tier = row
    return (stored_tier or 1) >= min_audio_tier

def record_indexed_file(channel_id: int, message_id: int, clean_title: str, ep_tag: str, res_tag: str, variant_key: str, audio_tier: int, file_name: str):
    norm_t = normalize_title_key(clean_title)
    slug = make_slug_key(norm_t)
    conn = sqlite3.connect("storage_index.db")
    conn.execute(
        """INSERT OR REPLACE INTO indexed_files_v4
           (channel_id, message_id, clean_title, slug_key, ep_tag, res_tag, variant_key, audio_tier, file_name)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            channel_id,
            message_id or 0,
            norm_t,
            slug,
            ep_tag.upper().strip(),
            (res_tag or "HD").upper().strip(),
            (variant_key or res_tag or "HD").upper().strip(),
            int(audio_tier or 1),
            file_name
        )
    )
    conn.commit()
    conn.close()

def get_existing_resolutions(clean_title: str, ep_tag: str = "MOVIE") -> set:
    t_key = normalize_title_key(clean_title)
    conn = sqlite3.connect("storage_index.db")
    rows = conn.execute(
        "SELECT DISTINCT res_tag FROM indexed_files_v4 WHERE clean_title=? AND ep_tag=?",
        (t_key, ep_tag.upper().strip())
    ).fetchall()
    conn.close()
    return {r[0].upper() for r in rows}

def get_indexed_episodes_for_title(clean_title: str) -> set:
    t_key = normalize_title_key(clean_title)
    conn = sqlite3.connect("storage_index.db")
    rows = conn.execute(
        "SELECT DISTINCT ep_tag FROM indexed_files_v4 WHERE clean_title=?",
        (t_key,)
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}

def get_season_messages_for_slug(slug_key: str, season_prefix: str) -> list:
    """Returns all stored (channel_id, message_id, ep_tag, res_tag) for a Series Season (e.g. S01)."""
    conn = sqlite3.connect("storage_index.db")
    rows = conn.execute(
        """SELECT channel_id, message_id, ep_tag, res_tag FROM indexed_files_v4
           WHERE slug_key=? AND ep_tag LIKE ? AND message_id > 0
           ORDER BY ep_tag ASC""",
        (slug_key, f"{season_prefix.upper()}%")
    ).fetchall()
    conn.close()
    return rows

def get_crawler_progress():
    conn = sqlite3.connect("storage_index.db")
    row = conn.execute("SELECT current_year, current_page, media_kind FROM crawler_state WHERE id=1").fetchone()
    conn.close()
    return row if row else (current_utc_year(), 1, "movie")

def save_crawler_progress(year: int, page: int, media_kind: str):
    conn = sqlite3.connect("storage_index.db")
    conn.execute("UPDATE crawler_state SET current_year=?, current_page=?, media_kind=? WHERE id=1", (year, page, media_kind))
    conn.commit()
    conn.close()

def is_title_crawled_recently(clean_title: str, hours: int = 12) -> bool:
    conn = sqlite3.connect("storage_index.db")
    row = conn.execute(
        f"SELECT 1 FROM crawler_history WHERE clean_title=? AND checked_at >= datetime('now', '-{int(hours)} hours')",
        (normalize_title_key(clean_title),)
    ).fetchone()
    conn.close()
    return bool(row)

def mark_title_crawled(clean_title: str, year: int = None):
    conn = sqlite3.connect("storage_index.db")
    conn.execute(
        "INSERT OR REPLACE INTO crawler_history (clean_title, year, checked_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (normalize_title_key(clean_title), year)
    )
    conn.commit()
    conn.close()

async def ensure_dialogs_cached():
    global DIALOG_CACHE_READY
    if DIALOG_CACHE_READY:
        return
    try:
        await client.get_dialogs(limit=120)
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
    pub_clean = normalize_title_key(meta.get("public_title") or query)
    SYNCED_TITLES.add(pub_clean)

    correct_cat = detect_category(query, "", meta)
    correct_ch = await get_or_create_genre_channel(correct_cat)
    all_chs = sorted(list(set(load_all_channels_from_db().values())))
    channels_to_scan = [correct_ch] + [c for c in all_chs if c != correct_ch]

    found_count = 0
    for ch_id in channels_to_scan:
        try:
            async for msg in client.iter_messages(ch_id, search=meta.get("public_title") or clean_q, limit=35):
                if not (msg.video or msg.document):
                    continue
                if msg.file and (msg.file.size or 0) < MIN_VIDEO_SIZE_BYTES:
                    continue
                fn = (msg.file.name if msg.file else "") or ""
                txt = f"{fn}\n{msg.text or ''}"
                real_fn = extract_real_filename(fn, msg.text or "")
                if not (is_strict_title_and_year_match(real_fn, clean_q, target_year) or is_strict_title_and_year_match(real_fn, pub_clean, target_year)):
                    continue

                ep_tag = extract_season_ep_tag(txt, "S01")
                res_tag = extract_resolution_tag(txt, "HD")
                aud_tier = extract_audio_tier(txt)
                v_key = extract_movie_variant_key(txt, res_tag, aud_tier) if ep_tag == "MOVIE" else res_tag

                record_indexed_file(ch_id, msg.id, clean_q, ep_tag, res_tag, v_key, aud_tier, real_fn)
                record_indexed_file(ch_id, msg.id, pub_clean, ep_tag, res_tag, v_key, aud_tier, real_fn)
                found_count += 1
            await asyncio.sleep(0.2)
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds + 1)
        except Exception:
            pass

    if found_count:
        print(f"✅ Synced {found_count} existing posts for '{meta.get('public_title') or clean_q}'.")

init_storage_db()

# ============================================================================
# 4. 24/7 RATE-LIMIT-PROOF METADATA & POPULAR PUBLIC TITLE ENGINE
# ============================================================================
def get_db_cached_meta(cache_key: str):
    conn = sqlite3.connect("storage_index.db")
    row = conn.execute("SELECT json_data FROM persistent_meta_cache WHERE cache_key=?", (cache_key,)).fetchone()
    conn.close()
    if row:
        try:
            return json.loads(row[0])
        except Exception:
            pass
    return None

def save_db_cached_meta(cache_key: str, data: dict):
    conn = sqlite3.connect("storage_index.db")
    conn.execute(
        "INSERT OR REPLACE INTO persistent_meta_cache (cache_key, json_data, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (cache_key, json.dumps(data))
    )
    conn.commit()
    conn.close()

async def fetch_meta(title: str, target_year: int = None):
    global OMDB_CALL_DAY, OMDB_CALL_COUNT
    clean = normalize_title_key(title)
    if not target_year:
        target_year = extract_year_from_text(title)
    cache_key = f"{clean}:{target_year or 'any'}"

    if cache_key in META_CACHE:
        return META_CACHE[cache_key]

    db_cached = get_db_cached_meta(cache_key)
    if db_cached:
        META_CACHE[cache_key] = db_cached
        return db_cached

    public_title = clean.title()
    original_title = ""
    resolved_year = target_year
    genres_list = []
    lang = ""
    runtime = ""
    rating = ""
    is_series = bool(re.search(r"\b(S\d{1,2}|Season|E\d{1,3}|EP\.?\s*\d+|Episode)\b", title, re.I))

    today_str = current_utc_date_str()
    if OMDB_CALL_DAY != today_str:
        OMDB_CALL_DAY = today_str
        OMDB_CALL_COUNT = 0

    async with aiohttp.ClientSession() as session:
        if TMDB_API_KEY:
            async with TMDB_SEMAPHORE:
                try:
                    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&language=en-US&query={quote_plus(clean)}"
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
                                original_title = chosen.get("original_title") or chosen.get("original_name") or ""
                                lang = chosen.get("original_language", "")
                                if chosen.get("media_type") == "tv":
                                    is_series = True
                                vote = chosen.get("vote_average")
                                if vote and float(vote) > 0:
                                    rating = f"{float(vote):.1f}/10"
                                for gid in (chosen.get("genre_ids") or []):
                                    if gid in TMDB_GENRE_MAP and TMDB_GENRE_MAP[gid] not in genres_list:
                                        genres_list.append(TMDB_GENRE_MAP[gid])
                    await asyncio.sleep(0.25)
                except Exception:
                    pass

        if OMDB_API_KEY and OMDB_CALL_COUNT < OMDB_DAILY_SAFE_LIMIT and (not genres_list or not runtime):
            try:
                OMDB_CALL_COUNT += 1
                y_param = f"&y={target_year}" if target_year else ""
                url = f"https://www.omdbapi.com/?apikey={OMDB_API_KEY}&t={quote_plus(clean)}{y_param}"
                async with session.get(url, timeout=4) as r:
                    if r.status == 200:
                        data = await r.json()
                        if data.get("Response") == "True":
                            if public_title.lower() == clean and data.get("Title"):
                                public_title = data.get("Title")
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

        if not genres_list or public_title.lower() == clean:
            try:
                itunes_url = f"https://itunes.apple.com/search?term={quote_plus(clean)}&limit=5"
                async with session.get(itunes_url, timeout=4) as ir:
                    if ir.status == 200:
                        idata = await ir.json(content_type=None)
                        for item in (idata.get("results") or []):
                            t_name = item.get("trackName") or item.get("collectionName") or ""
                            rel = item.get("releaseDate") or ""
                            iy = int(rel[:4]) if len(rel) >= 4 and rel[:4].isdigit() else None
                            if target_year and iy and abs(iy - target_year) > 1:
                                continue
                            if is_strict_title_and_year_match(t_name, clean, target_year):
                                if public_title.lower() == clean and t_name:
                                    public_title = t_name
                                resolved_year = resolved_year or iy
                                p_genre = item.get("primaryGenreName")
                                if p_genre and p_genre not in genres_list:
                                    genres_list.append(p_genre)
                                break
            except Exception:
                pass

        if not genres_list or public_title.lower() == clean or not resolved_year:
            try:
                tvm_url = f"https://api.tvmaze.com/singlesearch/shows?q={quote_plus(clean)}"
                async with session.get(tvm_url, timeout=3) as tvr:
                    if tvr.status == 200:
                        tvd = await tvr.json()
                        if tvd and is_strict_title_and_year_match(tvd.get("name", ""), clean, target_year):
                            if public_title.lower() == clean and tvd.get("name"):
                                public_title = tvd["name"]
                            for g in (tvd.get("genres") or []):
                                if g not in genres_list:
                                    genres_list.append(g)
            except Exception:
                pass

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
                                if public_title.lower() == clean and lbl:
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
        "original_title": original_title,
        "year": resolved_year,
        "genres": ", ".join(genres_list),
        "language": lang,
        "runtime": runtime,
        "rating": rating,
        "is_series": is_series
    }
    META_CACHE[cache_key] = result
    save_db_cached_meta(cache_key, result)
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

        if TMDB_API_KEY and not franchise_movies:
            async with TMDB_SEMAPHORE:
                try:
                    c_url = f"https://api.themoviedb.org/3/search/collection?api_key={TMDB_API_KEY}&language=en-US&query={quote_plus(clean)}"
                    async with session.get(c_url, timeout=4) as cr:
                        if cr.status == 200:
                            c_res = (await cr.json()).get("results") or []
                            if c_res:
                                col_id = c_res[0]["id"]
                                d_url = f"https://api.themoviedb.org/3/collection/{col_id}?api_key={TMDB_API_KEY}&language=en-US"
                                async with session.get(d_url, timeout=4) as dr:
                                    if dr.status == 200:
                                        for p in (await dr.json()).get("parts") or []:
                                            if p.get("title"):
                                                franchise_movies.append(p["title"])
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

    return {"seasons": seasons_map, "franchise_movies": franchise_movies[:12]}

# ============================================================================
# 5. CAPTION BUILDER, DETAILED 30-GENRE ROUTER & SERIES SEASON HUB PUBLISHER
# ============================================================================
def strip_subtitle_sections(caption: str) -> str:
    if not caption:
        return ""
    no_bubble = caption.split("💬")[0]
    cleaned_lines = []
    for line in no_bubble.splitlines():
        low = line.strip().lower()
        if re.match(r"^(?:📝|🔤|🍿|╰)?\s*(?:subtitles?|subs?|esub|mmsub|join:)\b", low):
            break
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
    audio_safe_caption = strip_subtitle_sections(caption or "")
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
    elif re.search(r"\b(av1|vp9)\b", full_combined, re.I):
        codec = "AV1/VP9"
    if re.search(r"\b10\s*-?bit\b", full_combined, re.I) and "10-Bit" not in codec:
        codec += " 10-Bit"

    aud_codec = "AAC 2.0"
    if re.search(r"\batmos\b", full_combined, re.I):
        aud_codec = "Dolby Atmos 5.1"
    elif re.search(r"\b6ch\b|\bddp\s*5\.1\b|\baac\s*5[\.\s]1\b|\b5[\.\s]1\b", full_combined, re.I):
        aud_codec = "DDP 5.1 (6CH)"
    elif re.search(r"\bddp\s*2\.0\b", full_combined, re.I):
        aud_codec = "DDP 2.0"

    br_m = re.search(r"Video\s*Bitrate\s*:\s*([^\r\n]+)", full_combined, re.I) or re.search(r"\b(\d{3,5}\s*kbps)\b", full_combined, re.I)
    bitrate = br_m.group(1).strip() if br_m else ""

    dur_m = re.search(r"Duration\s*:\s*([^\r\n]+)", full_combined, re.I) or re.search(r"⏳\s*(\d{1,2}:\d{2}(?::\d{2})?)", full_combined)
    duration = dur_m.group(1).strip() if dur_m else ""

    for_tv = bool(re.search(r"Can\s*be\s*played\s*on\s*TV|FOR\s*TV", full_combined, re.I) or "x264" in codec)
    has_sub = bool(re.search(r"💬|Has\s*[a-zA-Z]*\s*subtitles|\b(?:sub|subs|esub|multisub|mmsub)\b|\.mkv\b", full_combined, re.I))

    speaker_m = re.search(r"🔊\s*([^\r\n💬╰]+)", audio_safe_caption, re.DOTALL)
    audio_only_text = f"{real_fn}\n{speaker_m.group(1) if speaker_m else ''}"

    detected_langs = []
    for pat, name in [
        (r"\b(?:hindi|hin)\b", "Hindi"),
        (r"\b(?:japanese|jap|jpn)\b", "Japanese"),
        (r"\b(?:english|eng)\b", "English"),
        (r"\b(?:korean|kor)\b", "Korean"),
        (r"\b(?:tamil|tam)\b", "Tamil"),
        (r"\b(?:telugu|tel)\b", "Telugu"),
        (r"\b(?:malayalam|mal)\b", "Malayalam"),
        (r"\b(?:kannada|kan)\b", "Kannada"),
        (r"\b(?:turkish|tur)\b", "Turkish"),
        (r"\b(?:amharic|amh|ትርጉም)\b", "Amharic")
    ]:
        if re.search(pat, audio_only_text, re.I):
            detected_langs.append(name)

    if not detected_langs:
        orig_lang_map = {
            "en": "English", "ja": "Japanese", "ko": "Korean", "hi": "Hindi",
            "tr": "Turkish", "am": "Amharic", "ta": "Tamil", "te": "Telugu",
            "ml": "Malayalam", "es": "Spanish", "fr": "French", "zh": "Chinese"
        }
        mapped = orig_lang_map.get((meta.get("language") or "").lower(), "English")
        detected_langs.append(mapped)

    audio_tier = extract_audio_tier(audio_only_text, len(detected_langs))
    audio_lines = [f"* {aud_codec} — {lang_name}" for lang_name in detected_langs]

    audio_mode = ""
    if audio_tier == 3:
        audio_mode = f"Multi Audio / Dubbed ({', '.join(detected_langs[:5])})"
    elif audio_tier == 2:
        audio_mode = f"Dual Audio ({' + '.join(detected_langs)})"
    elif detected_langs:
        audio_mode = f"{detected_langs[0]} ({aud_codec})"

    return {
        "real_filename": real_fn,
        "quality": qual,
        "source": source,
        "codec": codec,
        "audio_mode": audio_mode,
        "audio_tier": audio_tier,
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
    ep_str = f" - {ep_tag}" if ep_tag and ep_tag != "MOVIE" else ""

    dub_badge = " [Multi-Audio]" if sp["audio_tier"] == 3 else (" [Dual-Audio]" if sp["audio_tier"] == 2 else "")
    spec_tag = " ".join(p for p in [sp["quality"], sp["source"], sp["codec"]] if p)
    clean_public_file = obfuscate_copyright_text(f"{pub_title}{year_str}{ep_str}{dub_badge} [{spec_tag}].mkv")

    lines = [
        f"🎬 <b>{pub_title}{year_str}{ep_str}</b>"
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
        spec_parts.append(obfuscate_copyright_text(sp["source"]))
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
    meta = meta or {}
    genres_low = (meta.get("genres") or "").lower()
    lang = (meta.get("language") or "").lower()
    fn_low = (filename or "").lower()
    speaker_m = re.search(r"🔊\s*([^\r\n💬╰]+)", strip_subtitle_sections(caption or ""))
    audio_line = (speaker_m.group(1) if speaker_m else "").lower()

    if re.search(r"\b(anime|demon slayer|solo leveling|jujutsu|naruto|one piece|bleach|attack on titan|kimetsu|blue lock)\b", f"{fn_low} {meta.get('public_title', '').lower()}") or (
        "animation" in genres_low and lang in ("ja", "zh")
    ):
        return "anime"

    if "animation" in genres_low:
        return "animation"

    if meta.get("is_series") or re.search(r"\b(S\d{1,2}\s*E\d{1,3}|Season\s*\d+|Episode\s*\d+|EP\.?\s*\d+)\b", fn_low, re.I):
        return "series"

    if lang == "am" or re.search(r"[\u1200-\u137F]+|amharic|ትርጉም", f"{fn_low} {audio_line}"):
        return "amharic"

    # Detailed Genre Routing (Uses dedicated channel if created, else falls back cleanly)
    ch_map = load_all_channels_from_db()
    if "sci-fi" in genres_low and "scifi" in ch_map:
        return "scifi"
    if "horror" in genres_low:
        return "horror"
    if "thriller" in genres_low:
        return "thriller" if "thriller" in ch_map else "horror"
    if any(g in genres_low for g in ["action", "adventure", "war", "sci-fi", "science fiction", "crime", "western"]):
        return "action"
    if "comedy" in genres_low and "comedy" in ch_map:
        return "comedy"
    if "romance" in genres_low and "romance" in ch_map:
        return "romance"
    if "documentary" in genres_low and "documentary" in ch_map:
        return "documentary"

    if lang in ("ta", "te", "ml", "kn") and "south_indian" in ch_map:
        return "south_indian"
    if lang in INDIAN_LANG_CODES or re.search(r"\b(hindi|bollywood|telugu|tamil|malayalam|kannada)\b", f"{fn_low} {audio_line}"):
        return "hindi"
    if lang == "ko" or re.search(r"\b(korean|k-drama)\b", f"{fn_low} {audio_line}"):
        return "korean"
    if lang == "tr" or re.search(r"\bturkish\b", f"{fn_low} {audio_line}"):
        return "turkish"

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
            "protect_content": "true",
            "reply_markup": json.dumps(GREEN_BOT_API_MARKUP)
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, data=payload, timeout=10) as resp:
                data = await resp.json()
                if data.get("ok"):
                    new_msg_id = (data.get("result") or {}).get("message_id")
                    await client.delete_messages(dest_channel, [sent_msg.id])
                    if new_msg_id:
                        sent_msg.id = int(new_msg_id)
    except Exception as e:
        print(f"⚠️ Green button attach notice: {e}")

    return sent_msg

async def update_series_index_card(meta: dict, ep_tag: str):
    """
    Posts or updates a Series Card in the dedicated SERIES_INDEX_CHANNEL with Colored Season Buttons!
    When a user clicks '[ 📺 Season 01 ]', the bot forwards all episodes of Season 01 to them.
    """
    if not BOT_TOKEN or not BOT_USERNAME or not ep_tag or ep_tag == "MOVIE":
        return

    index_ch = SERIES_INDEX_CHANNEL or load_all_channels_from_db().get("series_index", 0)
    if not index_ch:
        if AUTO_CREATE_CHANNELS:
            index_ch = await get_or_create_genre_channel("series_index")
        if not index_ch or index_ch == DEFAULT_CHANNELS["other"]:
            return

    pub_title = meta.get("public_title") or "Series"
    clean_t = normalize_title_key(pub_title)
    slug = make_slug_key(clean_t)
    s_m = re.match(r"^(S\d{2})", ep_tag.upper())
    season_code = s_m.group(1) if s_m else "S01"

    conn = sqlite3.connect("storage_index.db")
    row = conn.execute("SELECT index_message_id, seasons_json FROM series_index_cards WHERE slug_key=?", (slug,)).fetchone()
    seasons_set = set(json.loads(row[1])) if row else set()
    seasons_set.add(season_code)
    sorted_seasons = sorted(list(seasons_set))

    # Build Colored Season Deep-Link Buttons (Blue 'primary' for Seasons + Green 'success' for Brand Links)
    season_rows = []
    cur_row = []
    for s_code in sorted_seasons:
        s_num = int(s_code[1:])
        deep_link = f"https://t.me/{BOT_USERNAME}?start=season_{slug}_{s_code}"
        cur_row.append({"text": f"📺 Season {s_num:02d} ↗", "url": deep_link, "style": "primary"})
        if len(cur_row) == 2:
            season_rows.append(cur_row)
            cur_row = []
    if cur_row:
        season_rows.append(cur_row)

    season_rows.append(GREEN_BRAND_ROW)
    markup = {"inline_keyboard": season_rows}

    yr_str = f" ({meta['year']})" if meta.get("year") else ""
    card_text = (
        f"📺 <b>{pub_title}{yr_str}</b>\n"
        f"🎭 <b>Genre:</b> {meta.get('genres') or 'Series'} • ⭐ <b>{meta.get('rating') or 'HD'}</b>\n"
        f"📦 <b>Available Seasons:</b> <code>{', '.join(sorted_seasons)}</code>\n\n"
        f"👇 <i>Tap any Season button below to receive all episodes of that Season!</i>"
    )

    try:
        async with aiohttp.ClientSession() as session:
            if row and row[0]:
                edit_url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
                payload = {
                    "chat_id": index_ch,
                    "message_id": row[0],
                    "text": card_text,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps(markup)
                }
                async with session.post(edit_url, data=payload, timeout=8) as r:
                    if (await r.json()).get("ok"):
                        conn.execute("UPDATE series_index_cards SET seasons_json=? WHERE slug_key=?", (json.dumps(sorted_seasons), slug))
                        conn.commit()
                        conn.close()
                        return

            send_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": index_ch,
                "text": card_text,
                "parse_mode": "HTML",
                "reply_markup": json.dumps(markup)
            }
            async with session.post(send_url, data=payload, timeout=8) as r:
                d = await r.json()
                if d.get("ok"):
                    msg_id = d["result"]["message_id"]
                    conn.execute(
                        "INSERT OR REPLACE INTO series_index_cards (slug_key, clean_title, public_title, index_channel_id, index_message_id, seasons_json) VALUES (?, ?, ?, ?, ?, ?)",
                        (slug, clean_t, pub_title, index_ch, msg_id, json.dumps(sorted_seasons))
                    )
                    conn.commit()
    except Exception as e:
        print(f"⚠️ Series Index Card update notice: {e}")
    finally:
        conn.close()

# ============================================================================
# 6. BOT API SEASON DELIVERY WORKER (FORWARDS SEASON FILES WHEN USER CLICKS)
# ============================================================================
async def bot_season_delivery_worker():
    """
    Listens for '/start season_<slug>_S01' clicks on your BOT_TOKEN bot
    and automatically forwards all stored episodes of that Season to the user!
    """
    global BOT_USERNAME
    if not BOT_TOKEN:
        return

    offset = 0
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=8) as r:
                me_data = await r.json()
                if me_data.get("ok"):
                    BOT_USERNAME = me_data["result"].get("username", "")
                    print(f"🤖 Season Delivery Bot Linked: @{BOT_USERNAME}")
        except Exception:
            pass

        while True:
            try:
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=20"
                async with session.get(url, timeout=25) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(5)
                        continue
                    data = await resp.json()
                    for upd in (data.get("result") or []):
                        offset = upd["update_id"] + 1
                        msg = upd.get("message") or {}
                        text = (msg.get("text") or "").strip()
                        chat_id = (msg.get("chat") or {}).get("id")
                        if not chat_id or not text.startswith("/start season_"):
                            continue

                        m = re.match(r"^/start\s+season_([a-z0-9]+)_(S\d{2})", text, re.I)
                        if not m:
                            continue
                        slug, s_code = m.group(1).lower(), m.group(2).upper()
                        rows = get_season_messages_for_slug(slug, s_code)
                        if not rows:
                            await session.post(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                data={"chat_id": chat_id, "text": f"⚠️ No episodes indexed yet for {s_code}."}
                            )
                            continue

                        await session.post(
                            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                            data={
                                "chat_id": chat_id,
                                "text": f"📺 <b>Sending {len(rows)} episode file(s) for {s_code}...</b>\n🔒 <i>Protected Delivery</i>",
                                "parse_mode": "HTML"
                            }
                        )
                        for src_ch, src_msg_id, _, _ in rows:
                            await session.post(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/copyMessage",
                                data={
                                    "chat_id": chat_id,
                                    "from_chat_id": src_ch,
                                    "message_id": src_msg_id,
                                    "protect_content": "true",
                                    "reply_markup": json.dumps(GREEN_BOT_API_MARKUP)
                                }
                            )
                            await asyncio.sleep(0.6)
            except Exception:
                await asyncio.sleep(5)

# ============================================================================
# 7. DEEP MULTI-PAGE BUTTON SCORING (SUPPORTS BOTH BOTS)
# ============================================================================
SKIP_NAV_WORDS = [
    "back", "close", "next", "prev", "page", "seasons", "watch show",
    "subtitles", "⏩", "⏪", "»", "«", "‹", "❌", "⭐"
]

def score_quality_button(text: str) -> int:
    t = text.upper()
    score = 0
    if "2160P" in t or "4K" in t: score += 1200
    elif "1080P" in t: score += 1000
    elif "720P" in t: score += 700
    elif "480P" in t: score += 400
    elif "360P" in t: score += 200

    if any(k in t for k in ["MULTI", "DUAL", "DUB", "HIN", "ENG", "ATMOS", "DDP5.1"]):
        score += 450
    if "BLURAY" in t or "BD" in t: score += 200
    elif "WEB-DL" in t: score += 150
    elif "WEBRIP" in t or "HDRIP" in t: score += 120

    if "HEVC" in t or "X265" in t or "10BIT" in t or "AV1" in t: score += 60
    if "PAHE" in t: score -= 10
    return score

def find_matching_title_buttons_on_page(buttons, query: str, target_year: int = None, visited_keys: set = None):
    visited_keys = visited_keys or set()
    clean_q = normalize_title_key(query)
    candidates = []

    for r_idx, row in enumerate(buttons):
        for c_idx, btn in enumerate(row):
            txt = (btn.text or "").strip()
            low = txt.lower()
            if any(skip in low for skip in SKIP_NAV_WORDS):
                continue

            btn_key = f"{txt}_{r_idx}_{c_idx}"
            if btn_key in visited_keys or txt in visited_keys:
                continue

            if not is_strict_title_and_year_match(txt, query, target_year):
                continue

            score = 100
            btn_clean = normalize_title_key(txt)
            if btn_clean == clean_q:
                score += 500
            elif btn_clean.startswith(clean_q):
                score += 300

            if target_year and str(target_year) in txt:
                score += 400

            if "📺" in txt or "season" in low:
                score += 250
            elif "🎬" in txt:
                score += 150

            score += score_quality_button(txt) // 4
            candidates.append((score, txt, btn))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates

def collect_resolution_quality_buttons(buttons, visited_qualities: set = None):
    visited_qualities = visited_qualities or set()
    res_list = []
    for row in buttons:
        for btn in row:
            txt = (btn.text or "").strip()
            low = txt.lower()
            if any(skip in low for skip in SKIP_NAV_WORDS):
                continue
            if txt in visited_qualities:
                continue
            s = score_quality_button(txt)
            if s <= 0:
                continue
            res = extract_resolution_tag(txt, "OTHER")
            res_list.append((s, res, txt, btn))

    res_list.sort(key=lambda x: x[0], reverse=True)
    return [(r, txt, btn) for _, r, txt, btn in res_list]

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
# 8. CLICK & PAGINATION HELPERS (SUPPORTS 'Next ⏩' AND 'Next »')
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

async def trigger_start_url(url: str, default_bot: str = None) -> bool:
    if not url or "start=" not in url:
        return False
    parsed = urlparse(url)
    target_bot = parsed.path.strip("/") or default_bot or SOURCE_BOT
    start_param = parse_qs(parsed.query).get("start", [""])[0]
    if target_bot and start_param:
        await client.send_message(target_bot, f"/start {start_param}")
        return True
    return False

async def smart_click_button(btn, bot_entity: str = None):
    if getattr(btn, "url", None):
        if "start=" in btn.url:
            return await trigger_start_url(btn.url, bot_entity)
        return False

    try:
        res = await asyncio.wait_for(btn.click(), timeout=2.5)
        res_url = getattr(res, "url", None)
        if res_url and "start=" in res_url:
            await trigger_start_url(res_url, bot_entity)
    except asyncio.TimeoutError:
        pass
    except FloodWaitError as fw:
        await asyncio.sleep(fw.seconds + 2)
    except Exception as e:
        print(f"⚠️ Button click notice: {e}")
    return True

def get_next_page_button(msg):
    if not msg or not msg.buttons:
        return None
    for row in msg.buttons:
        for btn in row:
            txt = (btn.text or "").strip().lower()
            if any(k in txt for k in ["next", "⏩", "➡", "⏭", "»"]):
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
        if len(btn_texts) == 1 and any(x in btn_texts[0].lower() for x in ["auto-delete", "close", "subtitles"]):
            continue
        return m
    return None

async def wait_for_menu_update(bot_entity, tracked_msg_id, old_sig: str, max_wait: float = 4.0):
    loops = int(max_wait / 0.4)
    for _ in range(loops):
        await asyncio.sleep(0.4)
        msg = await find_latest_menu_message(bot_entity, tracked_msg_id)
        if msg and get_menu_signature(msg) != old_sig:
            return msg
    return await find_latest_menu_message(bot_entity, tracked_msg_id)

def is_season_menu(msg) -> bool:
    """Detects both FiLE Ai ('Choose the season') and Hislordship ('S1 · 9 eps', '4 seasons · 31 episodes')."""
    if not msg or not msg.buttons:
        return False
    text = (msg.text or "").lower()
    if "choose the season" in text:
        return True
    for r in msg.buttons:
        for b in r:
            bt = (b.text or "").strip()
            if re.search(r"^(?:📺\s*)?(?:Season\s*\d+|S\d{1,2}\s*[·•\-]\s*\d+\s*eps?)", bt, re.I):
                return True
    return False

def is_file_or_episode_grid(msg) -> bool:
    if not msg or not msg.buttons:
        return False
    text = (msg.text or "").lower()
    if "choose the correct title" in text or "search results for" in text or is_season_menu(msg):
        return False
    if "total episodes" in text or "total files" in text:
        return True
    for r in msg.buttons:
        for b in r:
            bt = b.text or ""
            if re.search(r"\b(?:S\d{1,2})?E\d{1,3}\b", bt, re.I):
                return True
            if re.search(r"(?:\[|\·|\•)\s*\d+(?:\.\d+)?\s*(?:MB|GB)\b", bt, re.I) and "quality" not in bt.lower():
                return True
    return False

# ============================================================================
# 9. INCOMING MEDIA EVENT (IGNORES .SRT FILES, SUPPORTS MULTI-VARIANT MOVIES)
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
    raw_fn = (msg.file.name if msg.file else None) or (msg.text or "Media.mkv").split("\n")[0]

    # Ignore tiny .srt / .sub subtitle files (like the 120 KB SRT in Screenshot 6!)
    if file_size < MIN_VIDEO_SIZE_BYTES or raw_fn.lower().endswith((".srt", ".sub", ".ass", ".zip", ".rar", ".txt")):
        print(f"⏭️ Ignoring non-video/subtitle file: {raw_fn} ({format_size_human(file_size)})")
        return

    file_id = f"{msg.id}_{file_size}"
    if file_id in CAPTURED_FILES:
        return
    CAPTURED_FILES.add(file_id)

    caption = msg.text or ""
    real_fn = extract_real_filename(raw_fn, caption)
    combined_text = f"{real_fn}\n{caption}"

    print(f"📥 Received video: {real_fn} ({format_size_human(file_size)})")

    if ACTIVE_CLEAN_TITLE and not is_strict_title_and_year_match(real_fn, ACTIVE_CLEAN_TITLE, ACTIVE_TARGET_YEAR):
        print(f"🚫 Strict Filter Rejected: '{real_fn}' does not match '{ACTIVE_CLEAN_TITLE}' ({ACTIVE_TARGET_YEAR or 'Any'}).")
        FILE_COMPLETED_EVENT.set()
        return

    file_year = ACTIVE_TARGET_YEAR or extract_year_from_text(real_fn)
    title_for_meta = ACTIVE_CLEAN_TITLE or normalize_title_key(real_fn)
    meta = await fetch_meta(title_for_meta, file_year)
    sp = extract_tech_specs(raw_fn, caption, file_size, meta)
    new_audio_tier = sp["audio_tier"]

    cat = detect_category(real_fn, caption, meta)
    dest_channel = await get_or_create_genre_channel(cat)
    cat_emoji = CATEGORY_EMOJIS.get(cat, "📁")

    ep_tag = ACTIVE_EP_TAG or extract_season_ep_tag(combined_text, ACTIVE_SEASON_TAG)
    res_tag = extract_resolution_tag(combined_text, ACTIVE_RES_TAG or "1080P")
    pub_key = normalize_title_key(meta.get("public_title") or title_for_meta)
    title_key = ACTIVE_CLEAN_TITLE or pub_key

    # For Movies/Franchises, use variant_key (allows Multiple Options like Dual/Multi/HEVC/x264 in same resolution!)
    variant_key = extract_movie_variant_key(combined_text, res_tag, new_audio_tier) if ep_tag == "MOVIE" else res_tag

    existing = get_stored_file_record(pub_key, ep_tag, variant_key) or get_stored_file_record(title_key, ep_tag, variant_key)
    if existing:
        old_ch, old_msg_id, old_tier = existing
        if new_audio_tier <= (old_tier or 1) and old_ch == dest_channel:
            print(f"⏭️ Duplicate variant ({meta.get('public_title')} • {ep_tag} • {variant_key}) — skipping.")
            FILE_COMPLETED_EVENT.set()
            return
        else:
            if old_ch and old_msg_id:
                try:
                    await client.delete_messages(old_ch, [old_msg_id])
                except Exception:
                    pass

    final_caption = build_final_caption(raw_fn, caption, meta, file_size, ep_tag)

    try:
        sent_msg = await publish_file_with_green_buttons(dest_channel, msg.media, final_caption)
        sent_id = sent_msg.id if sent_msg else 0
        print(f"🚀 Published '{meta['public_title']}' [{ep_tag} • {variant_key}] ➔ {cat_emoji} {cat.upper()}")

        record_indexed_file(dest_channel, sent_id, title_key, ep_tag, res_tag, variant_key, new_audio_tier, real_fn)
        record_indexed_file(dest_channel, sent_id, pub_key, ep_tag, res_tag, variant_key, new_audio_tier, real_fn)

        # If this is a Series Episode, automatically update the Series Season Index Card!
        if ep_tag != "MOVIE":
            await update_series_index_card(meta, ep_tag)

        await client.send_message(
            "me",
            f"✅ <b>Published to Storage!</b>\n"
            f"• <b>Public Title:</b> <code>{meta['public_title']} ({meta.get('year') or 'N/A'})</code>\n"
            f"• <b>Option:</b> <code>{ep_tag} • {variant_key} • {sp['audio_mode']} ({format_size_human(file_size)})</code>\n"
            f"• <b>Genre Channel:</b> {cat_emoji} <code>{cat.upper()}</code> ({meta.get('genres') or 'General'})",
            parse_mode="HTML"
        )
    except FloodWaitError as fw:
        await asyncio.sleep(fw.seconds + 2)
    except Exception as e:
        print(f"❌ Storage Forward error: {e}")
    finally:
        FILE_COMPLETED_EVENT.set()

# ============================================================================
# 10. DUAL-BOT DEEP MULTI-PAGE NAVIGATOR & DOWNLOADER
# ============================================================================
async def navigate_to_grid(
    query: str,
    target_season_num: int = 1,
    target_res: str = None,
    target_year: int = None,
    send_fresh: bool = True,
    visited_title_btns: set = None,
    visited_quality_btns: set = None,
    bot_entity: str = None
):
    bot_entity = bot_entity or SOURCE_BOT
    visited_title_btns = visited_title_btns if visited_title_btns is not None else set()
    visited_quality_btns = visited_quality_btns if visited_quality_btns is not None else set()

    search_str = f"{query} {target_year}".strip() if target_year else query
    if send_fresh:
        print(f"📨 Querying @{bot_entity}: '{search_str}' (Season {target_season_num:02d} • Res: {target_res or 'ALL'})")
        await client.send_message(bot_entity, search_str)
        await asyncio.sleep(2.2)

    tracked_menu_id = None
    available_seasons = []
    discovered_resolutions = []
    retried_without_year = False
    title_page_tries = 0
    quality_page_tries = 0

    for _ in range(38):
        if SKIP_CURRENT_TITLE:
            break
        msg = await find_latest_menu_message(bot_entity, tracked_menu_id)
        if not msg or not msg.buttons:
            if target_year and not retried_without_year:
                retried_without_year = True
                await client.send_message(bot_entity, query)
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
                        await smart_click_button(btn, bot_entity)
                        await wait_for_menu_update(bot_entity, tracked_menu_id, old_sig)
                        break
            continue

        # 2. Season Selection (Supports both 'Season 01' and Hislordship's 'S1 · 9 eps'!)
        if is_season_menu(msg):
            season_btns = {}
            fallback_btn = None
            for row in msg.buttons:
                for btn in row:
                    t = (btn.text or "").strip()
                    low = t.lower()
                    if any(skip in low for skip in SKIP_NAV_WORDS):
                        continue
                    m = re.search(r"(?:season\s*|s)0*(\d+)", low)
                    if m:
                        season_btns[int(m.group(1))] = btn
                    elif "season all" in low or "episodes" in low:
                        fallback_btn = btn

            available_seasons = sorted(season_btns.keys())
            chosen_btn = season_btns.get(target_season_num) or fallback_btn or msg.buttons[0][0]
            print(f"👉 [@{bot_entity} Season] Clicking: '{chosen_btn.text}'")
            await smart_click_button(chosen_btn, bot_entity)
            await wait_for_menu_update(bot_entity, tracked_menu_id, old_sig)
            continue

        # 3. Dedicated File / Episode Grid Reached
        if is_file_or_episode_grid(msg):
            return msg, available_seasons, (discovered_resolutions or TARGET_RESOLUTIONS)

        # 4. Title Selection / Direct Movie Options + Next » / Next ⏩
        if "choose the correct title" in text or "matching results" in text or "search results for" in text or len(msg.buttons) >= 2:
            if "select the available quality" not in text and "quality" not in text:
                candidates = find_matching_title_buttons_on_page(msg.buttons, query, target_year, visited_title_btns)
                if candidates:
                    _, btn_txt, chosen_btn = candidates[0]
                    visited_title_btns.add(btn_txt)
                    btn_res = extract_resolution_tag(btn_txt, "HD")
                    btn_tier = extract_audio_tier(btn_txt)
                    v_key = extract_movie_variant_key(btn_txt, btn_res, btn_tier)

                    if btn_res != "HD" and is_already_in_any_channel(query, "MOVIE", v_key, min_audio_tier=btn_tier):
                        continue

                    print(f"👉 [@{bot_entity} Title/Option] Clicking: '{btn_txt}'")
                    FILE_COMPLETED_EVENT.clear()
                    await smart_click_button(chosen_btn, bot_entity)
                    updated_msg = await wait_for_menu_update(bot_entity, tracked_menu_id, old_sig, max_wait=3.5)

                    if not updated_msg or get_menu_signature(updated_msg) == old_sig:
                        try:
                            await asyncio.wait_for(FILE_COMPLETED_EVENT.wait(), timeout=45)
                        except asyncio.TimeoutError:
                            pass
                        await asyncio.sleep(1.2)
                    continue
                else:
                    nxt_btn = get_next_page_button(msg)
                    if nxt_btn and title_page_tries < 6:
                        title_page_tries += 1
                        print(f"📄 [@{bot_entity} Title] Clicking Next » to Page {title_page_tries + 1}...")
                        await smart_click_button(nxt_btn, bot_entity)
                        await wait_for_menu_update(bot_entity, tracked_menu_id, old_sig)
                        continue
                    else:
                        return None, [], []

        # 5. Quality Selection (Clicks Next ⏩ across Quality Pages 1..6)
        if "select the available quality" in text or "quality" in text:
            res_options = collect_resolution_quality_buttons(msg.buttons, visited_quality_btns)
            for r_tag, _, _ in res_options:
                if r_tag not in discovered_resolutions:
                    discovered_resolutions.append(r_tag)

            chosen_qual_btn = None
            chosen_qual_txt = ""
            if target_res:
                for r_tag, r_txt, r_btn in res_options:
                    if r_tag == target_res:
                        chosen_qual_btn = r_btn
                        chosen_qual_txt = r_txt
                        break

                if not chosen_qual_btn:
                    nxt_qual_btn = get_next_page_button(msg)
                    if nxt_qual_btn and quality_page_tries < 6:
                        quality_page_tries += 1
                        print(f"📄 [@{bot_entity} Quality] '{target_res}' not on Page {quality_page_tries} — clicking Next »...")
                        await smart_click_button(nxt_qual_btn, bot_entity)
                        await wait_for_menu_update(bot_entity, tracked_menu_id, old_sig)
                        continue
                    else:
                        return None, available_seasons, discovered_resolutions
            else:
                for r_tag, r_txt, r_btn in res_options:
                    q_tier = extract_audio_tier(r_txt)
                    v_key = extract_movie_variant_key(r_txt, r_tag, q_tier)
                    if not is_already_in_any_channel(query, "MOVIE", v_key, min_audio_tier=q_tier):
                        chosen_qual_btn = r_btn
                        chosen_qual_txt = r_txt
                        break
                if not chosen_qual_btn and res_options:
                    chosen_qual_txt = res_options[0][1]
                    chosen_qual_btn = res_options[0][2]

                if not chosen_qual_btn:
                    nxt_qual_btn = get_next_page_button(msg)
                    if nxt_qual_btn and quality_page_tries < 6:
                        quality_page_tries += 1
                        await smart_click_button(nxt_qual_btn, bot_entity)
                        await wait_for_menu_update(bot_entity, tracked_menu_id, old_sig)
                        continue

            if chosen_qual_btn:
                visited_quality_btns.add(chosen_qual_txt)
                print(f"👉 [@{bot_entity} Quality] Clicking: '{chosen_qual_btn.text}'")
                await smart_click_button(chosen_qual_btn, bot_entity)
                await wait_for_menu_update(bot_entity, tracked_menu_id, old_sig)
                continue
            else:
                break

        await asyncio.sleep(1.0)

    return None, available_seasons, discovered_resolutions

async def download_from_grid(
    initial_msg,
    query: str,
    season_num: int,
    active_res: str,
    target_episodes: set = None,
    only_missing: bool = False,
    target_year: int = None,
    bot_entity: str = None
):
    global ACTIVE_SEASON_TAG, ACTIVE_EP_TAG, ACTIVE_RES_TAG
    bot_entity = bot_entity or SOURCE_BOT
    ACTIVE_SEASON_TAG = f"S{season_num:02d}"
    ACTIVE_RES_TAG = active_res or "1080P"
    menu_msg_id = initial_msg.id
    current_msg = initial_msg
    current_page = 1
    remaining_targets = set(target_episodes) if target_episodes else None

    while not SKIP_CURRENT_TITLE:
        best_item_map = {}
        item_order = []
        next_button = get_next_page_button(current_msg)

        for row in current_msg.buttons:
            for btn in row:
                txt = (btn.text or "").strip()
                low = txt.lower()
                if any(nav in low for nav in SKIP_NAV_WORDS):
                    continue

                if target_year:
                    btn_years = [int(y) for y in re.findall(r"\b(19[5-9]\d|20[0-3]\d)\b", txt)]
                    if btn_years and target_year not in btn_years:
                        continue

                ep_m = re.search(r"\b(?:S\d{1,2})?(?:E|EP\.?)0*(\d{1,3})\b", txt, re.I)
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
                btn_tier = extract_audio_tier(txt)
                # For Movies/Franchises, group by variant_key so we grab Dual, Multi, HEVC & x264 even in same resolution!
                v_key = extract_movie_variant_key(txt, btn_res, btn_tier) if ep_tag == "MOVIE" else btn_res
                size_mb = parse_size_mb(txt) + (btn_tier * 250.0)
                item_key = (ep_tag, btn_res, v_key, ep_num)

                if item_key not in best_item_map:
                    item_order.append(item_key)
                    best_item_map[item_key] = (size_mb, btn, btn_tier)
                else:
                    if size_mb > best_item_map[item_key][0]:
                        best_item_map[item_key] = (size_mb, btn, btn_tier)

        for ep_tag, btn_res, v_key, ep_num in item_order:
            if SKIP_CURRENT_TITLE:
                break
            _, btn, btn_tier = best_item_map[(ep_tag, btn_res, v_key, ep_num)]

            check_key = "ANY" if only_missing else v_key
            if is_already_in_any_channel(ACTIVE_CLEAN_TITLE, ep_tag, check_key, min_audio_tier=btn_tier):
                if remaining_targets is not None and ep_num in remaining_targets:
                    remaining_targets.discard(ep_num)
                continue

            ACTIVE_EP_TAG = ep_tag
            ACTIVE_RES_TAG = btn_res
            FILE_COMPLETED_EVENT.clear()
            print(f"⏳ [@{bot_entity}] Requesting {ep_tag} ({v_key}) ➔ '{btn.text}'...")

            await smart_click_button(btn, bot_entity)

            try:
                await asyncio.wait_for(FILE_COMPLETED_EVENT.wait(), timeout=85)
                if remaining_targets is not None and ep_num in remaining_targets:
                    remaining_targets.discard(ep_num)
            except asyncio.TimeoutError:
                print(f"⚠️ Timeout on {ep_tag} ({v_key}) — continuing...")

            await asyncio.sleep(1.5)

            check_msg = await client.get_messages(bot_entity, ids=menu_msg_id)
            if not check_msg or not check_msg.buttons:
                recovered_msg, _, _ = await navigate_to_grid(
                    query, season_num, active_res, target_year, send_fresh=True, bot_entity=bot_entity
                )
                if recovered_msg:
                    menu_msg_id = recovered_msg.id
                    current_msg = recovered_msg
                    for _ in range(1, current_page):
                        nxt = get_next_page_button(current_msg)
                        if nxt:
                            old_s = get_menu_signature(current_msg)
                            await smart_click_button(nxt, bot_entity)
                            current_msg = await wait_for_menu_update(bot_entity, menu_msg_id, old_s)

        if remaining_targets is not None and len(remaining_targets) == 0:
            break

        refreshed = await client.get_messages(bot_entity, ids=menu_msg_id)
        if refreshed and refreshed.buttons:
            current_msg = refreshed
            next_button = get_next_page_button(current_msg)

        if next_button and current_page < 15:
            current_page += 1
            print(f"📄 [@{bot_entity}] Clicking Next » on grid to Page {current_page}...")
            old_sig = get_menu_signature(current_msg)
            await smart_click_button(next_button, bot_entity)
            current_msg = await wait_for_menu_update(bot_entity, menu_msg_id, old_sig)
            if not current_msg or not current_msg.buttons:
                break
        else:
            break

# ============================================================================
# 11. REAL-TIME RADAR + MULTI-INDUSTRY CRAWLER (TODAY ➔ 1990s)
# ============================================================================
CRAWLER_MODES = [
    "movie",           # Hollywood / Global Movies
    "anime",           # Japanese Anime Movies & Series
    "animation",       # Animated Movies (Disney, Pixar, DreamWorks)
    "bollywood",       # Bollywood & South Indian (Hindi, Tamil, Telugu, Malayalam)
    "korean",          # Korean Movies & K-Dramas
    "turkish",         # Turkish Movies & Series
    "amharic",         # Ethiopian / Amharic Movies
    "horror",          # Horror & Thriller Movies
    "action",          # Action & Sci-Fi Movies
    "scifi",           # Sci-Fi & Fantasy
    "crime",           # Crime & Mystery
    "chinese_donghua", # Chinese Cinema & Donghua
    "tv"               # Global TV Series
]

async def fetch_realtime_live_releases() -> list:
    live_items = []
    today_str = current_utc_date_str()
    now_yr = current_utc_year()

    async with aiohttp.ClientSession() as session:
        if TMDB_API_KEY:
            for ep_path in [
                f"trending/all/day?api_key={TMDB_API_KEY}&language=en-US",
                f"movie/now_playing?api_key={TMDB_API_KEY}&language=en-US&page=1",
                f"tv/airing_today?api_key={TMDB_API_KEY}&language=en-US&page=1",
                f"movie/top_rated?api_key={TMDB_API_KEY}&language=en-US&page=1"
            ]:
                async with TMDB_SEMAPHORE:
                    try:
                        url = f"https://api.themoviedb.org/3/{ep_path}"
                        async with session.get(url, timeout=4) as r:
                            if r.status == 200:
                                for it in (await r.json()).get("results", []):
                                    name = it.get("title") or it.get("name")
                                    r_date = it.get("release_date") or it.get("first_air_date") or ""
                                    yr = int(r_date[:4]) if len(r_date) >= 4 and r_date[:4].isdigit() else now_yr
                                    is_tv = it.get("media_type") == "tv" or "tv/" in ep_path
                                    if name:
                                        live_items.append((name, None if is_tv else yr))
                        await asyncio.sleep(0.25)
                    except Exception:
                        pass

        try:
            rss_url = "https://itunes.apple.com/us/rss/topmovies/limit=25/json"
            async with session.get(rss_url, timeout=4) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    for ent in (data.get("feed", {}).get("entry") or []):
                        name = (ent.get("im:name") or {}).get("label")
                        rel = ((ent.get("im:releaseDate") or {}).get("label") or "")[:4]
                        yr = int(rel) if rel.isdigit() else now_yr
                        if name:
                            live_items.append((name, yr))
        except Exception:
            pass

        try:
            sched_url = f"https://api.tvmaze.com/schedule/web?date={today_str}"
            async with session.get(sched_url, timeout=4) as r:
                if r.status == 200:
                    for item in (await r.json())[:20]:
                        show = item.get("_embedded", {}).get("show") or item.get("show") or {}
                        name = show.get("name")
                        if name:
                            live_items.append((name, None))
        except Exception:
            pass

    return live_items

async def fetch_discover_batch(year: int, page: int, media_kind: str) -> list:
    discovered = []
    today_str = current_utc_date_str()
    now_yr = current_utc_year()

    async with aiohttp.ClientSession() as session:
        if TMDB_API_KEY and media_kind != "amharic":
            async with TMDB_SEMAPHORE:
                try:
                    endpoint = "tv" if media_kind in ("tv", "anime") else "movie"
                    yr_key = "first_air_date_year" if endpoint == "tv" else "primary_release_year"
                    date_lte_key = "first_air_date.lte" if endpoint == "tv" else "primary_release_date.lte"

                    date_filter = f"&{yr_key}={year}"
                    if year >= now_yr:
                        date_filter += f"&{date_lte_key}={today_str}"

                    if page == 1:
                        sort_param = "&sort_by=popularity.desc"
                    elif page == 2:
                        sort_param = "&sort_by=vote_average.desc&vote_count.gte=80"
                    else:
                        sort_param = "&sort_by=vote_average.desc&vote_count.gte=15"

                    extra_params = ""
                    if media_kind == "anime":
                        extra_params = "&with_genres=16&with_original_language=ja"
                    elif media_kind == "animation":
                        extra_params = "&with_genres=16"
                    elif media_kind == "bollywood":
                        extra_params = "&with_original_language=hi|ta|te|ml|kn"
                    elif media_kind == "korean":
                        extra_params = "&with_original_language=ko"
                    elif media_kind == "turkish":
                        extra_params = "&with_original_language=tr"
                    elif media_kind == "chinese_donghua":
                        extra_params = "&with_original_language=zh"
                    elif media_kind == "horror":
                        extra_params = "&with_genres=27|53"
                    elif media_kind == "action":
                        extra_params = "&with_genres=28|12"
                    elif media_kind == "scifi":
                        extra_params = "&with_genres=878|14"
                    elif media_kind == "crime":
                        extra_params = "&with_genres=80|9648"

                    d_url = (
                        f"https://api.themoviedb.org/3/discover/{endpoint}"
                        f"?api_key={TMDB_API_KEY}&language=en-US{date_filter}{sort_param}{extra_params}&page={page}"
                    )
                    async with session.get(d_url, timeout=5) as dr:
                        if dr.status == 200:
                            for it in (await dr.json()).get("results", []):
                                name = it.get("title") or it.get("name")
                                if name:
                                    discovered.append((name, None if endpoint == "tv" else year))
                    await asyncio.sleep(0.25)
                except Exception:
                    pass

        if not discovered:
            try:
                term_map = {
                    "movie": f"{year}", "anime": f"anime {year}", "animation": f"animation {year}",
                    "bollywood": f"indian {year}", "korean": f"korean {year}", "turkish": f"turkish {year}",
                    "amharic": f"ethiopian movie {year}", "horror": f"horror {year}",
                    "action": f"action {year}", "scifi": f"sci-fi {year}", "crime": f"crime {year}",
                    "chinese_donghua": f"chinese {year}", "tv": f"season {year}"
                }
                search_term = term_map.get(media_kind, f"{year}")
                ent = "tvSeason" if media_kind in ("tv", "anime") else "movie"
                i_url = f"https://itunes.apple.com/search?term={quote_plus(search_term)}&entity={ent}&limit=25"
                async with session.get(i_url, timeout=5) as ir:
                    if ir.status == 200:
                        idata = await ir.json(content_type=None)
                        for it in (idata.get("results") or []):
                            name = it.get("trackName") or it.get("collectionName")
                            rel = it.get("releaseDate") or ""
                            yr = int(rel[:4]) if len(rel) >= 4 and rel[:4].isdigit() else year
                            if name:
                                clean_n = re.sub(r"\s*\(?\bSeason\s*\d+.*$", "", name, flags=re.I).strip()
                                discovered.append((clean_n, None if ent == "tvSeason" else yr))
            except Exception:
                pass

    return discovered

async def auto_discovery_crawler_worker():
    global LAST_REALTIME_RADAR_TS
    await asyncio.sleep(8.0)
    print(f"🤖 24/7 Dual-Bot Crawler (@{SOURCE_BOT} + @{BACKUP_BOT}) LIVE!")

    while True:
        try:
            if not AUTO_DISCOVERY_ENABLED or CURRENT_PIPELINE_ACTIVE or len(TITLE_QUEUE) > 0:
                await asyncio.sleep(5.0)
                continue

            now_ts = asyncio.get_event_loop().time()
            queued_one = False

            # 1. Real-Time Live Radar (Every 15 mins)
            if (now_ts - LAST_REALTIME_RADAR_TS) >= 900:
                live_batch = await fetch_realtime_live_releases()
                for title_name, yr in live_batch:
                    clean_t = normalize_title_key(title_name)
                    if not clean_t or clean_t == "unknown" or is_title_crawled_recently(clean_t, hours=6):
                        continue
                    mark_title_crawled(clean_t, yr)
                    if len(get_existing_resolutions(clean_t, "MOVIE")) >= 4:
                        continue

                    cmd = {
                        "raw": f"{title_name} ({yr})" if yr else title_name,
                        "title": title_name,
                        "year": yr,
                        "mode": "normal",
                        "season": None,
                        "episodes": None
                    }
                    print(f"⚡ [Real-Time Radar] Queued: {cmd['raw']}")
                    TITLE_QUEUE.append((cmd, True))
                    asyncio.create_task(process_queue())
                    queued_one = True
                    break

                if not queued_one:
                    LAST_REALTIME_RADAR_TS = now_ts

            if queued_one:
                await asyncio.sleep(6.0)
                continue

            # 2. Multi-Industry & Rating-Tier Historical Crawler (Current Year ➔ 1990)
            year, page, kind = get_crawler_progress()
            if kind not in CRAWLER_MODES:
                kind = "movie"

            batch = await fetch_discover_batch(year, page, kind)
            for title_name, yr in batch:
                clean_t = normalize_title_key(title_name)
                if not clean_t or clean_t == "unknown" or is_title_crawled_recently(clean_t, hours=12):
                    continue

                mark_title_crawled(clean_t, yr)
                if len(get_existing_resolutions(clean_t, "MOVIE")) >= 4:
                    continue

                cmd = {
                    "raw": f"{title_name} ({yr})" if yr else title_name,
                    "title": title_name,
                    "year": yr,
                    "mode": "normal",
                    "season": None,
                    "episodes": None
                }
                tier_lbl = "POPULAR" if page == 1 else ("HIGHEST-RATED" if page == 2 else "GEMS/LATEST")
                print(f"🌟 [Auto-Crawler {year} • {kind.upper()} • {tier_lbl}] Queued: {cmd['raw']}")
                TITLE_QUEUE.append((cmd, True))
                asyncio.create_task(process_queue())
                queued_one = True
                break

            if not queued_one:
                next_idx = (CRAWLER_MODES.index(kind) + 1) % len(CRAWLER_MODES)
                next_kind = CRAWLER_MODES[next_idx]
                if next_idx != 0:
                    save_crawler_progress(year, page, next_kind)
                else:
                    if page < 3:
                        save_crawler_progress(year, page + 1, "movie")
                    else:
                        now_yr = current_utc_year()
                        next_year = year - 1 if year > 1990 else now_yr
                        save_crawler_progress(next_year, 1, "movie")

            await asyncio.sleep(6.0)
        except Exception as e:
            print(f"⚠️ Auto-crawler notice: {e}")
            await asyncio.sleep(10.0)

# ============================================================================
# 12. DUAL-BOT FAILOVER QUEUE WORKER, CHANNEL READJUSTER & REFILLER
# ============================================================================
async def readjust_all_storage_channels():
    await ensure_dialogs_cached()
    fixed_count = 0
    all_chs = sorted(list(set(load_all_channels_from_db().values())))
    for ch_id in all_chs:
        try:
            async for msg in client.iter_messages(ch_id, limit=35):
                if not (msg.video or msg.document):
                    continue
                if msg.file and (msg.file.size or 0) < MIN_VIDEO_SIZE_BYTES:
                    continue
                fn = (msg.file.name if msg.file else "") or ""
                txt = msg.text or ""
                real_fn = extract_real_filename(fn, txt)
                yr = extract_year_from_text(real_fn)
                meta = await fetch_meta(real_fn, yr)
                correct_cat = detect_category(real_fn, txt, meta)
                correct_ch = await get_or_create_genre_channel(correct_cat)

                if ch_id != correct_ch or "🎬" not in txt:
                    ep_tag = extract_season_ep_tag(f"{real_fn}\n{txt}", "S01")
                    res_tag = extract_resolution_tag(f"{real_fn}\n{txt}", "1080P")
                    aud_tier = extract_audio_tier(f"{real_fn}\n{txt}")
                    v_key = extract_movie_variant_key(f"{real_fn}\n{txt}", res_tag, aud_tier) if ep_tag == "MOVIE" else res_tag
                    sz = msg.file.size if msg.file else 0
                    new_cap = build_final_caption(fn or real_fn, txt, meta, sz, ep_tag)
                    sent = await publish_file_with_green_buttons(correct_ch, msg.media, new_cap)
                    await client.delete_messages(ch_id, [msg.id])
                    record_indexed_file(correct_ch, sent.id if sent else 0, meta["public_title"], ep_tag, res_tag, v_key, aud_tier, real_fn)
                    fixed_count += 1
                    await asyncio.sleep(1.5)
        except Exception:
            pass
    return fixed_count

async def run_extraction_on_bot(
    bot_entity: str,
    popular_title: str,
    raw_query: str,
    start_season: int,
    target_year: int = None,
    target_eps: set = None,
    only_missing: bool = False,
    send_initial: bool = True
) -> bool:
    """
    Runs full multi-page, multi-option extraction on a specific bot (SOURCE_BOT or BACKUP_BOT).
    Returns True if any grid or direct movie option was found and processed.
    """
    global ACTIVE_CLEAN_TITLE
    visited_title_btns = set()
    visited_quality_btns = set()
    before_res = get_existing_resolutions(ACTIVE_CLEAN_TITLE, "MOVIE")

    grid_msg, all_seasons, _ = await navigate_to_grid(
        popular_title,
        start_season,
        target_res=None,
        target_year=target_year,
        send_fresh=send_initial,
        visited_title_btns=visited_title_btns,
        visited_quality_btns=visited_quality_btns,
        bot_entity=bot_entity
    )

    if not grid_msg and not visited_title_btns and normalize_title_key(raw_query) != ACTIVE_CLEAN_TITLE:
        grid_msg, all_seasons, _ = await navigate_to_grid(
            raw_query,
            start_season,
            target_res=None,
            target_year=target_year,
            send_fresh=True,
            visited_title_btns=visited_title_btns,
            visited_quality_btns=visited_quality_btns,
            bot_entity=bot_entity
        )

    after_res = get_existing_resolutions(ACTIVE_CLEAN_TITLE, "MOVIE")
    if not grid_msg:
        return len(after_res) > len(before_res) or bool(visited_title_btns)

    if target_eps:
        await download_from_grid(
            grid_msg, popular_title, start_season, ACTIVE_RES_TAG or "1080P",
            target_episodes=target_eps, only_missing=only_missing, target_year=target_year, bot_entity=bot_entity
        )
        return True

    seasons_to_run = [start_season] if only_missing else (all_seasons if all_seasons else [start_season])
    first_season = seasons_to_run[0]

    await download_from_grid(
        grid_msg, popular_title, first_season, ACTIVE_RES_TAG or "1080P",
        only_missing=only_missing, target_year=target_year, bot_entity=bot_entity
    )

    for res_tag in TARGET_RESOLUTIONS:
        if SKIP_CURRENT_TITLE:
            break
        await asyncio.sleep(1.5)
        res_grid, _, _ = await navigate_to_grid(
            popular_title,
            first_season,
            target_res=res_tag,
            target_year=target_year,
            send_fresh=True,
            visited_quality_btns=visited_quality_btns,
            bot_entity=bot_entity
        )
        if res_grid and not SKIP_CURRENT_TITLE:
            await download_from_grid(
                res_grid, popular_title, first_season, res_tag,
                only_missing=only_missing, target_year=target_year, bot_entity=bot_entity
            )

    for next_s in seasons_to_run[1:]:
        if SKIP_CURRENT_TITLE or next_s <= first_season:
            continue
        s_visited_qual = set()
        for res_tag in TARGET_RESOLUTIONS:
            if SKIP_CURRENT_TITLE:
                break
            await asyncio.sleep(1.5)
            r_grid, _, _ = await navigate_to_grid(
                popular_title,
                next_s,
                target_res=res_tag,
                target_year=target_year,
                send_fresh=True,
                visited_quality_btns=s_visited_qual,
                bot_entity=bot_entity
            )
            if r_grid and not SKIP_CURRENT_TITLE:
                await download_from_grid(
                    r_grid, popular_title, next_s, res_tag,
                    only_missing=only_missing, target_year=target_year, bot_entity=bot_entity
                )
    return True

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
            f"🔎 <b>Franchise Refill Audit:</b> <code>{title.title()}</code>\n"
            f"• <b>Total Parts Found:</b> <code>{len(catalog['franchise_movies'])}</code>\n"
            f"• <b>Missing Parts Queued ({len(missing_movies)}):</b> <code>{', '.join(missing_movies) or 'None!'}</code>",
            parse_mode="HTML"
        )
        for m_title in reversed(missing_movies):
            TITLE_QUEUE.appendleft((
                {"raw": m_title, "title": m_title, "year": None, "mode": "normal", "season": None, "episodes": None},
                True
            ))
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
        summary = ", ".join(f"S{s:02d} ({len(eps)} missing)" for s, eps in sorted(missing_by_season.items()))
        await client.send_message(
            "me",
            f"🔎 <b>Series Refill Audit:</b> <code>{title.title()}</code>\n"
            f"• <b>In Storage:</b> <code>{len(existing_eps)}</code> episodes\n"
            f"• <b>Refilling Missing:</b> <code>{summary}</code>",
            parse_mode="HTML"
        )
        for s_num, miss_eps in sorted(missing_by_season.items()):
            if SKIP_CURRENT_TITLE:
                break
            found = await run_extraction_on_bot(SOURCE_BOT, title, title, s_num, target_year, miss_eps, only_missing=True)
            if not found and BACKUP_BOT:
                print(f"🔄 Switching to Backup Bot @{BACKUP_BOT} for missing episodes of '{title}' S{s_num:02d}...")
                await run_extraction_on_bot(BACKUP_BOT, title, title, s_num, target_year, miss_eps, only_missing=True)
    else:
        s_num = cmd["season"] or 1
        found = await run_extraction_on_bot(SOURCE_BOT, title, title, s_num, target_year, None, only_missing=True)
        if not found and BACKUP_BOT:
            await run_extraction_on_bot(BACKUP_BOT, title, title, s_num, target_year, None, only_missing=True)

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
                bot_override = cmd.get("bot_entity")

                meta_info = await fetch_meta(query, target_year)
                popular_title = meta_info.get("public_title") or query

                ACTIVE_CLEAN_TITLE = normalize_title_key(popular_title)
                ACTIVE_TARGET_YEAR = target_year
                ACTIVE_EP_TAG = ""
                ACTIVE_RES_TAG = ""

                print(f"🎬 Processing: '{popular_title}' (Year: {target_year or 'Any'}) | Mode: {cmd['mode']}")

                if cmd["mode"] == "missing":
                    await execute_missing_audit_and_fill(cmd)
                    continue

                await sync_title_across_all_channels(popular_title, target_year)
                if SKIP_CURRENT_TITLE:
                    continue

                # Auto-check if this movie belongs to a Franchise Collection with missing parts
                catalog = await fetch_official_catalog(popular_title)
                if not catalog["seasons"] and len(catalog["franchise_movies"]) > 1:
                    for f_movie in catalog["franchise_movies"]:
                        if normalize_title_key(f_movie) != ACTIVE_CLEAN_TITLE and not is_already_in_any_channel(f_movie, "MOVIE", "ANY"):
                            if not is_title_crawled_recently(f_movie, hours=24):
                                mark_title_crawled(f_movie)
                                TITLE_QUEUE.append((
                                    {"raw": f_movie, "title": f_movie, "year": None, "mode": "normal", "season": None, "episodes": None},
                                    True
                                ))

                start_season = cmd["season"] or 1
                target_eps = set(cmd["episodes"]) if cmd["episodes"] else None
                primary_bot = bot_override or SOURCE_BOT

                # Step 1: Try Primary Bot (@fileserverxrobot)
                found_primary = await run_extraction_on_bot(
                    primary_bot, popular_title, query, start_season,
                    target_year=target_year, target_eps=target_eps, send_initial=send_initial
                )

                # Step 2: Automatic Backup Bot Failover (@Hislordship_Movies_bot) if not found or more options needed!
                stored_res = get_existing_resolutions(ACTIVE_CLEAN_TITLE, "MOVIE")
                if BACKUP_BOT and primary_bot != BACKUP_BOT and not SKIP_CURRENT_TITLE:
                    if not found_primary or (not meta_info.get("is_series") and len(stored_res) < 3):
                        print(f"🔄 Primary Bot finished/missed ➔ Trying Backup Bot @{BACKUP_BOT} for '{popular_title}'...")
                        await asyncio.sleep(2.0)
                        await run_extraction_on_bot(
                            BACKUP_BOT, popular_title, query, start_season,
                            target_year=target_year, target_eps=target_eps, send_initial=True
                        )

                await client.send_message(
                    "me",
                    f"🏁 <b>Finished:</b> <code>{popular_title}</code>\n"
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

def enqueue_commands_bulk(raw_text: str, send_initial: bool = True, bot_entity: str = None) -> list:
    raw_items = split_bulk_commands(raw_text)
    parsed_list = []
    for item in raw_items:
        cmd = parse_smart_user_command(item)
        if bot_entity:
            cmd["bot_entity"] = bot_entity
        if cmd["title"] and len(cmd["title"]) >= 2:
            TITLE_QUEUE.append((cmd, send_initial))
            parsed_list.append(cmd)

    if parsed_list:
        asyncio.create_task(process_queue())
    return parsed_list

# ============================================================================
# 13. DETAILED SAVED MESSAGES COMMANDS & TRIGGERS
# ============================================================================
@client.on(events.NewMessage(outgoing=True))
async def on_outgoing_trigger(event):
    global SKIP_CURRENT_TITLE, AUTO_DISCOVERY_ENABLED, AUTO_CREATE_CHANNELS, ME_USER_ID
    text = (event.message.text or "").strip()
    if not text or text.startswith(("/", "✅", "⏳", "🏁", "⚠️", "🔗", "📋", "🛑", "🔎", "🎬", "🤖", "⚡", "♻️", "🆕", "📖")):
        return

    if ME_USER_ID is None:
        ME_USER_ID = (await client.get_me()).id

    if event.chat_id == ME_USER_ID:
        low = text.lower()

        if low in (".help", "help"):
            await event.reply(
                "📖 <b>Detailed Command Guide</b>\n\n"
                "• <code>.status</code> or <code>.queue</code> — Live Dashboard & Crawler Progress\n"
                "• <code>.channels</code> — View all Genre & Industry Storage Channels (Max 30)\n"
                "• <code>.autochannels on</code> / <code>off</code> — Auto-create detailed genre channels\n"
                "• <code>.createchannel series_index</code> — Create Series Season Hub Channel\n"
                "• <code>.createchannel scifi</code> — Create any detailed genre channel\n"
                "• <code>.readjust</code> — Fix old channel files (genres, titles & green buttons)\n"
                "• <code>.auto on</code> / <code>.auto off</code> — Start/Pause 24/7 Crawler\n"
                "• <code>.auto 2025</code> — Jump Crawler to specific Year (1990-2026)\n"
                "• <code>.auto bollywood</code> — Jump Crawler to industry (anime, korean, etc.)\n"
                "• <code>.skip</code> / <code>.clear</code> — Skip current or clear queue\n\n"
                "<b>Search Examples:</b>\n"
                "• <code>Spiderman 2026</code> (Strict Year Lock)\n"
                "• <code>missing files from Solo Leveling</code> (Refill Audit)\n"
                "• <code>Squid Game from S2 ep 1 - 4 ep 7</code> (Selective Range)",
                parse_mode="HTML"
            )
            return

        if low in (".queue", ".status", "queue", "status"):
            c_yr, c_pg, c_kind = get_crawler_progress()
            tier_lbl = "Popular" if c_pg == 1 else ("Highest-Rated" if c_pg == 2 else "Gems/Latest")
            ch_count = count_unique_storage_channels()
            q_list = "\n".join(
                f"  {i+1}. <code>{t[0]['raw'] if isinstance(t[0], dict) else t[0]}</code>"
                for i, t in enumerate(TITLE_QUEUE)
            ) or "  <i>(Empty — 24/7 Dual-Bot Crawler Active)</i>"
            await event.reply(
                f"📋 <b>24/7 Dual-Bot Pipeline Status</b>\n"
                f"• <b>Source Bots:</b> <code>@{SOURCE_BOT}</code> ➔ Backup: <code>@{BACKUP_BOT}</code>\n"
                f"• <b>Active Title:</b> <code>{ACTIVE_CLEAN_TITLE or 'Idle'}</code> ({ACTIVE_RES_TAG or '-'})\n"
                f"• <b>Auto-Crawler:</b> <code>{'ON 🟢' if AUTO_DISCOVERY_ENABLED else 'OFF 🔴'}</code> ({c_yr} • {c_kind.upper()} • {tier_lbl})\n"
                f"• <b>Storage Vaults:</b> <code>{ch_count}/{MAX_STORAGE_CHANNELS}</code> channels (Auto-Create: <code>{'ON' if AUTO_CREATE_CHANNELS else 'OFF'}</code>)\n"
                f"• <b>OMDb Daily Shield:</b> <code>{OMDB_CALL_COUNT}/{OMDB_DAILY_SAFE_LIMIT}</code> calls\n"
                f"• <b>Manual Queue ({len(TITLE_QUEUE)}):</b>\n{q_list}",
                parse_mode="HTML"
            )
            return

        if low in (".channels", "channels"):
            ch_map = load_all_channels_from_db()
            lines = [f"• <b>{k.upper()}:</b> <code>{v}</code>" for k, v in sorted(ch_map.items())]
            await event.reply(
                f"📋 <b>Configured Storage Channels ({count_unique_storage_channels()}/{MAX_STORAGE_CHANNELS} Max):</b>\n" + "\n".join(lines),
                parse_mode="HTML"
            )
            return

        if low.startswith(".autochannels"):
            AUTO_CREATE_CHANNELS = "on" in low
            await event.reply(
                f"🆕 <b>Auto-Create Detailed Genre Channels (Max {MAX_STORAGE_CHANNELS}):</b> <code>{'ENABLED 🟢' if AUTO_CREATE_CHANNELS else 'DISABLED 🔴'}</code>",
                parse_mode="HTML"
            )
            return

        if low.startswith(".createchannel"):
            parts = low.split(maxsplit=1)
            cat_k = parts[1].strip() if len(parts) > 1 else "series_index"
            old_flag = AUTO_CREATE_CHANNELS
            AUTO_CREATE_CHANNELS = True
            cid = await get_or_create_genre_channel(cat_k)
            AUTO_CREATE_CHANNELS = old_flag
            await event.reply(f"🆕 <b>Channel Ready for <code>{cat_k.upper()}</code>:</b> <code>{cid}</code>", parse_mode="HTML")
            return

        if low in (".readjust", "readjust"):
            await event.reply("♻️ <b>Scanning & readjusting storage channels...</b>", parse_mode="HTML")
            fixed = await readjust_all_storage_channels()
            await event.reply(f"✅ <b>Readjustment Complete:</b> Fixed & re-routed <code>{fixed}</code> files!", parse_mode="HTML")
            return

        if low.startswith(".auto") or low.startswith("auto "):
            if "off" in low:
                AUTO_DISCOVERY_ENABLED = False
                await event.reply("🤖 <b>Auto-Crawler Paused (OFF).</b>", parse_mode="HTML")
                return
            elif "on" in low:
                AUTO_DISCOVERY_ENABLED = True
                await event.reply("🤖 <b>Auto-Crawler Resumed (ON)!</b>", parse_mode="HTML")
                return
            yr_m = re.search(r"\b(19[5-9]\d|20[0-3]\d)\b", low)
            c_yr, _, c_kind = get_crawler_progress()
            for mode_name in CRAWLER_MODES:
                if mode_name in low:
                    c_kind = mode_name
                    break
            if yr_m:
                c_yr = int(yr_m.group(1))
            save_crawler_progress(c_yr, 1, c_kind)
            AUTO_DISCOVERY_ENABLED = True
            await event.reply(f"🤖 <b>Auto-Crawler set to:</b> Year <code>{c_yr}</code> • Category <code>{c_kind.upper()}</code>!", parse_mode="HTML")
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
                    summary_lines.append(f"• 🔍 <b>Refill Missing:</b> <code>{c['title']}{y_badge}</code>")
                elif c["mode"] == "range":
                    s_lbl = f"S{c['season']:02d}" if c["season"] else "S01"
                    e_lbl = ", ".join(f"E{e:02d}" for e in (c["episodes"] or [])[:8])
                    summary_lines.append(f"• 🎯 <b>Selective:</b> <code>{c['title']}{y_badge}</code> ({s_lbl}: {e_lbl})")
                else:
                    summary_lines.append(f"• 🎬 <b>Dual-Bot Multi-Option Sync:</b> <code>{c['title']}{y_badge}</code>")

            if len(added) > 8:
                summary_lines.append(f"<i>...and +{len(added)-8} more</i>")

            await event.reply(
                f"⏳ <b>Queued {len(added)} Task(s):</b>\n" + "\n".join(summary_lines),
                parse_mode="HTML"
            )
        return

    chat = await event.get_chat()
    chat_username = (getattr(chat, "username", "") or "").lower()
    if chat_username in (SOURCE_BOT.lower(), BACKUP_BOT.lower()) and not CURRENT_PIPELINE_ACTIVE:
        enqueue_commands_bulk(text, send_initial=False, bot_entity=chat_username)

# ============================================================================
# 14. 24/7 KEEP-ALIVE, SEASON DELIVERY BOT & MAIN ENTRYPOINT
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
    c_yr, c_pg, c_kind = get_crawler_progress()
    return web.json_response({
        "status": "online",
        "primary_bot": SOURCE_BOT,
        "backup_bot": BACKUP_BOT,
        "pipeline_active": CURRENT_PIPELINE_ACTIVE,
        "auto_crawler_enabled": AUTO_DISCOVERY_ENABLED,
        "crawler_progress": f"{c_yr} ({c_kind} page {c_pg})",
        "storage_channels": f"{count_unique_storage_channels()}/{MAX_STORAGE_CHANNELS}",
        "omdb_calls_today": f"{OMDB_CALL_COUNT}/{OMDB_DAILY_SAFE_LIMIT}",
        "queue_length": len(TITLE_QUEUE),
        "active_title": ACTIVE_CLEAN_TITLE
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
    asyncio.create_task(bot_season_delivery_worker())
    asyncio.create_task(auto_discovery_crawler_worker())

    print(f"🚀 24/7 Dual-Bot Pipeline LIVE! Primary: @{SOURCE_BOT} | Backup: @{BACKUP_BOT}")
    await client.run_until_disconnected()

if __name__ == "__main__":
    loop.run_until_complete(main())
