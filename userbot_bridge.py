import os, sys, re, ast, json, shutil, asyncio, sqlite3
from datetime import datetime, timezone
from collections import deque
from urllib.parse import urlparse, parse_qs, quote_plus
from aiohttp import web
import aiohttp

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import (
    JoinChannelRequest, CreateChannelRequest, EditAdminRequest,
    DeleteChannelRequest, EditTitleRequest
)
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, ToggleNoForwardsRequest
from telethon.tl.types import ChatAdminRights

# ============================================================================
# 1. CONFIGURATION, WEBSITE BOT PROTECTION & 3-BOT FORWARDING POOL
# ============================================================================
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
BOT_TOKEN_2 = os.environ.get("BOT_TOKEN_2", "").strip()
BOT_TOKEN_3 = os.environ.get("BOT_TOKEN_3", "").strip()

WEBSITE_BOT_TOKEN = os.environ.get("WEBSITE_BOT_TOKEN", "").strip()
WEBSITE_BOT_USERNAME = os.environ.get("WEBSITE_BOT_USERNAME", "").lstrip("@").strip()
RAW_BOT_TOKENS = [t for t in [BOT_TOKEN, BOT_TOKEN_2, BOT_TOKEN_3] if t and t != WEBSITE_BOT_TOKEN]

GEMINI_KEYS = []
for k_name, k_val in sorted(os.environ.items()):
    if k_name.startswith("GEMINI_API_KEY") and k_val.strip():
        for part in k_val.split(","):
            if part.strip() and part.strip() not in GEMINI_KEYS:
                GEMINI_KEYS.append(part.strip())

TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "").strip()
OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "").strip()
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
WEB_API_URL = os.environ.get("WEB_API_URL", "").strip()

SOURCE_BOT = os.environ.get("SOURCE_BOT", "fileserverxrobot").lstrip("@").strip()
BACKUP_BOT = os.environ.get("BACKUP_BOT", "Hislordship_Movies_bot").lstrip("@").strip()
SERIES_INDEX_CHANNEL = int(os.environ.get("SERIES_INDEX_CHANNEL", "0"))
PORT = int(os.environ.get("PORT", 10000))
MAX_STORAGE_CHANNELS = 30
MIN_VIDEO_SIZE_BYTES = 15 * 1024 * 1024

os.makedirs("backups", exist_ok=True)
os.makedirs("development", exist_ok=True)

DEFAULT_CHANNELS = {
    "hindi": -1004364094932, "english": -1003900997247, "series": -1004399588627,
    "amharic": -1004308570128, "korean": -1003974632071, "horror": -1004416107106,
    "action": -1004426568341, "anime": -1004390808391, "animation": -1003778945725,
    "upcoming": -1004356513920, "turkish": -1003605533639, "other": -1003900997247
}

DETAILED_GENRE_TITLES = {
    "scifi": "Vault • Sci-Fi & Cyberpunk", "thriller": "Vault • Suspense & Thriller",
    "comedy": "Vault • Comedy Central", "romance": "Vault • Romance & Drama",
    "crime": "Vault • Crime & Noir", "fantasy": "Vault • Fantasy & Mythology",
    "mystery": "Vault • Mystery & Detective", "adventure": "Vault • Adventure & Survival",
    "documentary": "Vault • Documentary & Biopic", "family": "Vault • Family & Kids",
    "war_history": "Vault • War & Historical Epic", "south_indian": "Vault • South Indian Cinema",
    "chinese_donghua": "Vault • Chinese & Donghua", "european": "Vault • European Cinema",
    "classic_90s": "Vault • 90s & Classic Cinema", "franchise_vault": "Vault • Movie Franchises",
    "series_index": "The Backroom • Series Season Index"
}

CATEGORY_EMOJIS = {
    "hindi": "🇮🇳", "english": "🌎", "series": "📺", "amharic": "🇪🇹", "korean": "🇰🇷",
    "horror": "👻", "action": "💥", "anime": "⛩", "animation": "🧸", "upcoming": "🔔",
    "turkish": "🇹🇷", "scifi": "🚀", "thriller": "🔪", "comedy": "😂", "romance": "❤️",
    "crime": "🕵️", "fantasy": "🐉", "mystery": "🧩", "adventure": "🧭", "documentary": "🌍",
    "family": "👨‍👩‍👧", "war_history": "⚔️", "south_indian": "🌴", "chinese_donghua": "🏮",
    "european": "🏰", "classic_90s": "📼", "franchise_vault": "🏆", "other": "🎬"
}

TMDB_GENRE_MAP = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime",
    99: "Documentary", 18: "Drama", 10751: "Family", 14: "Fantasy", 36: "History",
    27: "Horror", 10402: "Music", 9648: "Mystery", 10749: "Romance", 878: "Sci-Fi",
    53: "Thriller", 10752: "War", 37: "Western", 10759: "Action & Adventure", 10765: "Sci-Fi & Fantasy"
}

ORIG_LANG_MAP = {
    "en": "English", "ja": "Japanese", "ko": "Korean", "hi": "Hindi", "tr": "Turkish",
    "am": "Amharic", "ta": "Tamil", "te": "Telugu", "ml": "Malayalam", "kn": "Kannada",
    "zh": "Mandarin", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian"
}

FRANCHISE_ALIASES = {
    "fast and furious 9": "F9", "f9 linkz": "F9", "fast 9": "F9",
    "the fast and furious 3": "The Fast and the Furious: Tokyo Drift",
    "fast and furious 3": "The Fast and the Furious: Tokyo Drift",
    "minions monsters": "Minions & Monsters"
}

INLINE_BUTTONS = [[
    Button.url("Series ↗", "https://t.me/thebackroomseries"),
    Button.url("Anime ↗", "https://t.me/thebackroomanime"),
    Button.url("Movies ↗", "https://t.me/thebackroommovies")
]]

GREEN_BRAND_ROW = [
    {"text": "Series", "url": "https://t.me/thebackroomseries", "style": "success"},
    {"text": "Anime", "url": "https://t.me/thebackroomanime", "style": "success"},
    {"text": "Movies", "url": "https://t.me/thebackroommovies", "style": "success"}
]
GREEN_BOT_API_MARKUP = {"inline_keyboard": [GREEN_BRAND_ROW]}

TARGET_RESOLUTIONS = ["1080P", "720P", "480P", "360P", "2160P"]
INDIAN_LANG_CODES = {"hi", "te", "ta", "ml", "kn", "bn", "mr", "pa", "gu", "ur"}
SKIP_NAV_WORDS = ["back", "close", "next", "prev", "page", "seasons", "watch show", "subtitles", "⏩", "⏪", "»", "«", "‹", "❌", "⭐"]

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)

FILE_COMPLETED_EVENT = asyncio.Event()
CAPTURED_FILES = set()
SYNCED_TITLES = set()
DIALOG_CACHE_READY = False
META_CACHE = {}
TITLE_QUEUE = deque()
AI_CHAT_HISTORY = deque(maxlen=18)
BOT_POOL = []
BOT_RR_INDEX = 0
GEMINI_KEY_INDEX = 0
PENDING_APPROVAL_ACTION = None

OMDB_CALL_DAY = ""
OMDB_CALL_COUNT = 0
OMDB_DAILY_SAFE_LIMIT = 900
GEMINI_CALL_COUNT = 0
TMDB_SEMAPHORE = asyncio.Semaphore(3)
GEMINI_SEMAPHORE = asyncio.Semaphore(1)

CURRENT_PIPELINE_ACTIVE = False
SKIP_CURRENT_TITLE = False
AUTO_DISCOVERY_ENABLED = True
AUTO_CREATE_CHANNELS = True
WEB_PULSE_ENABLED = False
LAST_REALTIME_RADAR_TS = 0.0
ME_USER_ID = None

ACTIVE_CLEAN_TITLE = ""
ACTIVE_TARGET_YEAR = None
ACTIVE_SEASON_TAG = "S01"
ACTIVE_EP_TAG = ""
ACTIVE_RES_TAG = ""

def current_utc_year() -> int:
    return datetime.now(timezone.utc).year

def current_utc_date_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def pick_bot_for_role(role: str = "any") -> dict:
    global BOT_RR_INDEX
    if not BOT_POOL:
        return {"token": BOT_TOKEN, "username": ""}
    if role == "series":
        return BOT_POOL[0]
    if role == "movies" and len(BOT_POOL) >= 2:
        return BOT_POOL[1]
    chosen = BOT_POOL[BOT_RR_INDEX % len(BOT_POOL)]
    BOT_RR_INDEX += 1
    return chosen

# ============================================================================
# 2. STRICT OFFICIAL PUBLIC TITLE CLEANER (STRIPS ALL UPLOADER & SCENE JUNK)
# ============================================================================
JUNK_PREFIX_RE = re.compile(
    r"^(?:\s*(?:\[(?:CF|MFA|CC|SO|AE|PSA|YTS|RARBG|PAHE|DFBC|TOONWORLD4ALL|[^\]]{1,16})\]|"
    r"\b(?:DFBC|Toonworld4all|Toonworld|KatmovieHD|Vegamovies|MoviesMod|Filmyzilla|HDHub4u|18\+)\b|"
    r"@[A-Za-z0-9_]+|S\d{1,2}E\d{1,3}\s*[\-–:]\s*))+",
    re.I
)

JUNK_TOKENS_RE = re.compile(
    r"\b(?:Linkz|Multi\s*Aud(?:io)?|Dual\s*Aud(?:io)?|ESub|MSub|UNCUT|EXTENDED|REMASTERED|"
    r"BluRay|BDRip|BRRip|WEB-?DL|WEBRip|HDRip|DVDRip|CAMRip|HDTS|NF|AMZN|DSNP|Pahe|PSA|YTS|RARBG|"
    r"DFBC|Toonworld4all|Toonworld|KatmovieHD|Vegamovies|MoviesMod|Filmyzilla|HDHub4u|"
    r"MKV|MP4|AVI|HEVC|x265|x264|h265|h264|AV1|VP9|10Bit|8Bit|6CH|8CH|AAC\d*(?:[\.\s]\d)?|"
    r"DDP\d*(?:[\.\s]\d)?|AC3|Atmos|Dubbed|Dub)\b.*$",
    re.I
)

def extract_year_from_text(text: str):
    m = re.search(r"\b(19[5-9]\d|20[0-3]\d)\b", text or "")
    return int(m.group(1)) if m else None

def strip_uploader_and_junk(raw_str: str) -> str:
    s = re.sub(r"https?://\S+|t\.me/\S+|@[A-Za-z0-9_]+", " ", str(raw_str or ""))
    s = JUNK_PREFIX_RE.sub(" ", s).strip()
    s = re.sub(r"\[(?:CF|MFA|CC|SO|AE|PSA|YTS|RARBG|PAHE|DFBC|TOONWORLD4ALL)\]", " ", s, flags=re.I)
    return s.strip()

def normalize_title_key(title: str) -> str:
    s = strip_uploader_and_junk(title)
    s = re.sub(r"['’`]", "", s)
    s = re.sub(r"[._\-·•]+", " ", s).strip()
    s = re.sub(r"^[^\w]+", "", s).strip()
    # Strip leading S01E06 if it was at the very start of the filename
    s = re.sub(r"^(?:S\d{1,2}E\d{1,3}|E\d{1,3})\s+", "", s, flags=re.I).strip()

    cut_m = re.search(
        r"^(.{2,}?)\s+\b(?:19[5-9]\d|20[0-3]\d|S\d{1,2}E\d{1,3}|S\d{1,2}|Season\s*\d+|E\d{1,3}|EP\.?\s*\d+|"
        r"360p|480p|540p|720p|1080p|1440p|2160p|4K|BluRay|WEB-?DL|WEBRip|HDRip|DVDRip|CAMRip|HDTS)\b",
        s, flags=re.I
    )
    if cut_m and not re.fullmatch(r"\d{4}", cut_m.group(1).strip()):
        s = cut_m.group(1)

    s = JUNK_TOKENS_RE.sub("", s)
    out_s = re.sub(r"[^a-zA-Z0-9&]+", " ", s).strip().lower()
    # Remove trailing standalone junk numbers like "108" unless part of a known sequel
    out_s = re.sub(r"\s+\b(?:108|72|48|36)\b$", "", out_s).strip()
    if out_s in FRANCHISE_ALIASES:
        out_s = FRANCHISE_ALIASES[out_s].lower()
    if not out_s or re.fullmatch(r"\d{1,4}", out_s):
        return "unknown"
    return out_s

def make_slug_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_title_key(title))[:22] or "show"

def is_strict_title_and_year_match(candidate_text: str, target_title: str, target_year: int = None) -> bool:
    cand_years = [int(y) for y in re.findall(r"\b(19[5-9]\d|20[0-3]\d)\b", candidate_text or "")]
    if target_year and cand_years and target_year not in cand_years:
        return False
    norm_target, norm_cand = normalize_title_key(target_title), normalize_title_key(candidate_text)
    if not norm_target or not norm_cand or norm_cand == "unknown":
        return False
    compact_target, compact_cand = norm_target.replace(" ", ""), norm_cand.replace(" ", "")
    if compact_target == compact_cand or compact_target in compact_cand:
        return True
    stop_words = {"the", "a", "an", "of", "and", "in", "to", "for", "part", "movie", "seasons", "season"}
    target_words = [w for w in norm_target.split() if w not in stop_words and len(w) > 1] or norm_target.split()
    cand_words = set(norm_cand.split())
    return all(tw in cand_words or tw in compact_cand for tw in target_words)

def parse_smart_user_command(raw_text: str) -> dict:
    text = re.sub(r"^\.(?:search|get|ai|s)\s+", "", (raw_text or "").strip(), flags=re.I).strip()
    text = re.sub(r"^[\d\-\*\•\.]+\s*", "", text).strip(" ,.;:")
    mode, season_num, target_episodes = "normal", None, set()

    missing_m = re.match(r"^(?:find\s+|check\s+|fill\s+|download\s+)?missing(?:\s+files?|\s+episodes?|\s+ep|\s+parts?|\s+movies?)?(?:\s+from|\s+in|\s+for|\s+of)?\s+(.+)$", text, flags=re.I)
    if missing_m:
        mode, text = "missing", missing_m.group(1).strip()
    elif re.search(r"\bmissing(?:\s+files?|\s+episodes?|\s+ep|\s+parts?)?\b$", text, flags=re.I):
        mode = "missing"
        text = re.sub(r"\b(?:find|check|fill)?\s*missing(?:\s+files?|\s+episodes?|\s+ep|\s+parts?)?\b$", "", text, flags=re.I).strip()

    tail_match = re.search(r"\b(?:from\s+)?(?:(s(?:eason)?\s*0*\d{1,2})\b.*|(?:ep?(?:isode)?\.?\s*0*\d{1,3})\b.*)$", text, flags=re.I)
    if tail_match:
        tail_str = tail_match.group(0)
        title_part = text[:tail_match.start()].strip(" ,-:")
        if len(title_part) >= 2:
            s_m = re.search(r"\bs(?:eason)?\s*0*(\d{1,2})\b", tail_str, re.I)
            if s_m:
                season_num = int(s_m.group(1))
            ep_str = re.sub(r"\bs(?:eason)?\s*0*\d{1,2}\b", " ", tail_str, flags=re.I)
            for r_m in re.finditer(r"(?:ep?(?:isode)?\.?\s*)?0*(\d{1,3})\s*(?:\-|to|–|~)\s*(?:ep?(?:isode)?\.?\s*)?0*(\d{1,3})", ep_str, re.I):
                s_e, e_e = int(r_m.group(1)), int(r_m.group(2))
                if s_e <= e_e and (e_e - s_e) <= 200:
                    target_episodes.update(range(s_e, e_e + 1))
            for single_m in re.finditer(r"(?:ep?(?:isode)?\.?\s*)0*(\d{1,3})\b", ep_str, re.I):
                target_episodes.add(int(single_m.group(1)))
            if season_num is not None or target_episodes:
                text = title_part
                if target_episodes and mode != "missing":
                    mode = "range"

    target_year = extract_year_from_text(text)
    clean_search_title = re.sub(r"\(\d{4}\)|\[\d{4}\]|\b(19[5-9]\d|20[0-3]\d)\b", "", text).strip(" ,.-:")
    if clean_search_title.lower() in FRANCHISE_ALIASES:
        clean_search_title = FRANCHISE_ALIASES[clean_search_title.lower()]
    return {
        "raw": raw_text.strip(), "title": clean_search_title or text.strip(" ,."),
        "year": target_year, "mode": mode, "season": season_num,
        "episodes": sorted(list(target_episodes)) if target_episodes else None
    }

def split_bulk_commands(raw_message: str) -> list:
    msg = re.sub(r"^\.(?:search|get|s)\s+", "", (raw_message or "").strip(), flags=re.I).strip()
    commands = []
    for line in msg.splitlines():
        line = line.strip()
        if not line or re.match(r"^[\.\s,]+$", line):
            continue
        if "," in line and not re.search(r"\b(?:ep?|episode|s\d{1,2})\b", line, re.I):
            commands.extend([p.strip(" .,-") for p in line.split(",") if len(p.strip(" .,-")) >= 2])
        else:
            cleaned = line.strip(" .,-")
            if len(cleaned) >= 2:
                commands.append(cleaned)
    return commands

# ============================================================================
# 3. DATABASE: MEMORY, PERMANENT RULES, SKILLS, TASKS & CHANNELS
# ============================================================================
def extract_resolution_tag(text: str, default_res: str = "HD") -> str:
    m = re.search(r"\b(360p|480p|540p|720p|1080p|1440p|2160p|4K)\b", text or "", re.I)
    return ("2160P" if m.group(1).upper() == "4K" else m.group(1).upper()) if m else default_res.upper()

def extract_audio_tier(text: str, num_langs: int = 1) -> int:
    t = (text or "").lower()
    if num_langs >= 3 or re.search(r"\b(multi\s*audio|multi\s*aud|multi\s*dub|multi|dubbed|hin.*eng.*jap|tam.*tel.*hin)\b", t):
        return 3
    if num_langs == 2 or re.search(r"\b(dual\s*audio|dual\s*aud|dual\s*dub|dual|hin[\s\-\+]+eng|eng[\s\-\+]+jap)\b", t):
        return 2
    return 1

def extract_movie_variant_key(text: str, res_tag: str, audio_tier: int) -> str:
    t = (text or "").upper()
    aud = "MULTI" if audio_tier == 3 else ("DUAL" if audio_tier == 2 else "STD")
    codec = "HEVC" if any(k in t for k in ["HEVC", "X265", "10BIT", "AV1", "ATMOS"]) else "X264"
    return f"{res_tag}_{aud}_{codec}"

def extract_season_ep_tag(text: str, default_season: str = "S01") -> str:
    s_m = re.search(r"\b(?:S|Season\s*)0*(\d{1,2})\b", text or "", re.I)
    e_m = re.search(r"\b(?:E|EP\.?|Episode\s*)0*(\d{1,3})\b", text or "", re.I)
    s_tag = f"S{int(s_m.group(1)):02d}" if s_m else default_season.upper()
    return f"{s_tag}E{int(e_m.group(1)):02d}" if e_m else "MOVIE"

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
    conn.execute("CREATE TABLE IF NOT EXISTS dynamic_channels (cat_key TEXT PRIMARY KEY, channel_id INTEGER NOT NULL, title TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS series_index_cards (slug_key TEXT PRIMARY KEY, clean_title TEXT NOT NULL, public_title TEXT NOT NULL, index_channel_id INTEGER NOT NULL, index_message_id INTEGER NOT NULL, seasons_json TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS persistent_meta_cache (cache_key TEXT PRIMARY KEY, json_data TEXT NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS extra_gemini_keys (api_key TEXT PRIMARY KEY)")
    conn.execute(f"CREATE TABLE IF NOT EXISTS crawler_state (id INTEGER PRIMARY KEY CHECK (id = 1), current_year INTEGER DEFAULT {now_yr}, current_page INTEGER DEFAULT 1, media_kind TEXT DEFAULT 'movie')")
    conn.execute("CREATE TABLE IF NOT EXISTS crawler_history (clean_title TEXT PRIMARY KEY, year INTEGER, checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS behavior_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, rule_text TEXT NOT NULL, category TEXT DEFAULT 'general', priority INTEGER DEFAULT 5, active INTEGER DEFAULT 1, version INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS rule_versions (rule_id INTEGER, version INTEGER, old_text TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS long_term_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, mem_key TEXT NOT NULL UNIQUE, mem_value TEXT NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS custom_capabilities (cap_name TEXT PRIMARY KEY, description TEXT, python_code TEXT NOT NULL, active INTEGER DEFAULT 0, version INTEGER DEFAULT 1, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS evolution_history (id INTEGER PRIMARY KEY AUTOINCREMENT, change_type TEXT, summary TEXT, backup_path TEXT, status TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS audit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_request TEXT, action_type TEXT, risk_level TEXT, status TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS scheduled_jobs (job_id TEXT PRIMARY KEY, task_prompt TEXT NOT NULL, interval_minutes INTEGER NOT NULL, last_run TIMESTAMP, active INTEGER DEFAULT 1)")
    conn.execute("INSERT OR IGNORE INTO crawler_state (id, current_year, current_page, media_kind) VALUES (1, ?, 1, 'movie')", (now_yr,))
    for k, cid in DEFAULT_CHANNELS.items():
        conn.execute("INSERT OR IGNORE INTO dynamic_channels (cat_key, channel_id, title) VALUES (?, ?, ?)", (k, cid, k.upper()))
    for r in conn.execute("SELECT api_key FROM extra_gemini_keys").fetchall():
        if r[0] and r[0] not in GEMINI_KEYS:
            GEMINI_KEYS.append(r[0])
    conn.commit()
    conn.close()

def manage_behavior_rule(op: str, rule_text: str = "", rule_id: int = None, category: str = "PERMANENT_INSTRUCTION") -> str:
    conn = sqlite3.connect("storage_index.db")
    op = (op or "ADD").upper()
    if op == "ADD" and rule_text:
        row = conn.execute("SELECT id FROM behavior_rules WHERE lower(rule_text)=?", (rule_text.lower().strip(),)).fetchone()
        if row:
            conn.close()
            return f"Rule #{row[0]} is already active."
        cur = conn.execute("INSERT INTO behavior_rules (rule_text, category) VALUES (?, ?)", (rule_text.strip(), category))
        rid = cur.lastrowid
        conn.commit()
        conn.close()
        return f"✅ Permanent Rule #{rid} saved: \"{rule_text.strip()}\""
    elif op == "UPDATE" and rule_id and rule_text:
        old = conn.execute("SELECT rule_text, version FROM behavior_rules WHERE id=?", (rule_id,)).fetchone()
        if old:
            conn.execute("INSERT INTO rule_versions (rule_id, version, old_text) VALUES (?, ?, ?)", (rule_id, old[1], old[0]))
            conn.execute("UPDATE behavior_rules SET rule_text=?, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (rule_text.strip(), rule_id))
            conn.commit()
        conn.close()
        return f"♻️ Updated Rule #{rule_id}."
    elif op in ("REMOVE", "DISABLE") and rule_id:
        conn.execute("UPDATE behavior_rules SET active=0, updated_at=CURRENT_TIMESTAMP WHERE id=?", (rule_id,))
        conn.commit()
        conn.close()
        return f"🛑 Disabled Rule #{rule_id}."
    elif op == "ENABLE" and rule_id:
        conn.execute("UPDATE behavior_rules SET active=1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (rule_id,))
        conn.commit()
        conn.close()
        return f"🟢 Enabled Rule #{rule_id}."
    conn.close()
    return "ℹ️ Rule operation completed."

def get_active_behavior_rules() -> list:
    conn = sqlite3.connect("storage_index.db")
    rows = conn.execute("SELECT id, rule_text, category, version FROM behavior_rules WHERE active=1 ORDER BY priority DESC, id ASC").fetchall()
    conn.close()
    return rows

def save_long_term_memory(category: str, mem_key: str, mem_value: str):
    conn = sqlite3.connect("storage_index.db")
    conn.execute("INSERT OR REPLACE INTO long_term_memory (category, mem_key, mem_value, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)", (category.upper().strip(), mem_key.lower().strip(), str(mem_value).strip()))
    conn.commit()
    conn.close()

def retrieve_relevant_memories(query: str = "", limit: int = 12) -> list:
    conn = sqlite3.connect("storage_index.db")
    rows = conn.execute("SELECT category, mem_key, mem_value FROM long_term_memory ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return rows

def log_audit_event(user_req: str, action_type: str, risk_level: str, status: str):
    conn = sqlite3.connect("storage_index.db")
    conn.execute("INSERT INTO audit_logs (user_request, action_type, risk_level, status) VALUES (?, ?, ?, ?)", (user_req[:300], action_type, risk_level, status))
    conn.commit()
    conn.close()

def stage_or_register_capability(cap_name: str, description: str, python_code: str, auto_activate: bool = False) -> str:
    try:
        ast.parse(python_code)
    except SyntaxError as se:
        return f"❌ Syntax validation failed: {se}"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_file = f"backups/userbot_backup_{ts}.py"
    try:
        if os.path.exists(__file__):
            shutil.copy2(__file__, backup_file)
    except Exception:
        pass
    with open(f"development/{cap_name}_{ts}.py", "w", encoding="utf-8") as f:
        f.write(python_code)
    conn = sqlite3.connect("storage_index.db")
    conn.execute("INSERT OR REPLACE INTO custom_capabilities (cap_name, description, python_code, active, version, updated_at) VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)", (cap_name, description, python_code, 1 if auto_activate else 0))
    conn.execute("INSERT INTO evolution_history (change_type, summary, backup_path, status) VALUES (?, ?, ?, ?)", ("SKILL_PROPOSAL", f"{cap_name}: {description}", backup_file, "ACTIVE" if auto_activate else "WAITING_FOR_APPROVAL"))
    conn.commit()
    conn.close()
    return (
        f"🧬 <b>Skill Staged:</b> <code>{cap_name}</code>\n"
        f"• <b>AST Check:</b> Passed ✅ | <b>Backup:</b> <code>{backup_file}</code>\n"
        f"• <b>Status:</b> {'Activated' if auto_activate else 'Waiting for Owner Approval (send .approve to activate)'}"
    )

async def search_account_intelligence(query: str, limit: int = 15) -> str:
    results = []
    try:
        async for dlg in client.iter_dialogs(limit=80):
            try:
                async for m in client.iter_messages(dlg.id, search=query, limit=4):
                    txt = (m.text or (m.file.name if m.file else "") or "").strip().replace("\n", " ")[:120]
                    dt = m.date.strftime("%Y-%m-%d") if m.date else ""
                    results.append(f"• <b>[{dlg.name[:22]}]</b> ({dt}): <code>{txt}</code>")
                    if len(results) >= limit:
                        break
            except Exception:
                pass
            if len(results) >= limit:
                break
    except Exception as e:
        return f"⚠️ Search notice: {e}"
    return "\n".join(results) if results else f"ℹ️ No messages found matching <code>{query}</code>."

async def search_telegram_contacts(query: str = "") -> str:
    try:
        res = await client(GetContactsRequest(hash=0))
        users = getattr(res, "users", [])
        matches = []
        for u in users:
            name = f"{getattr(u, 'first_name', '') or ''} {getattr(u, 'last_name', '') or ''}".strip()
            uname = getattr(u, "username", "") or ""
            if not query or query.lower() in f"{name} {uname}".lower():
                matches.append(f"• <b>{name or 'Contact'}</b> (@{uname or 'no_username'}) — ID: <code>{u.id}</code>")
        return "\n".join(matches[:20]) if matches else "ℹ️ No matching contacts found."
    except Exception as e:
        return f"⚠️ Contacts notice: {e}"

async def inspect_website_intelligence(title_query: str = None) -> str:
    report = ["🌐 <b>Website & Storage Reconciliation Report (Read-Only)</b>"]
    if WEB_API_URL:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(WEB_API_URL, timeout=6) as r:
                    report.append(f"• <b>Cloudflare Worker Health:</b> HTTP <code>{r.status}</code>")
        except Exception as e:
            report.append(f"• <b>Cloudflare Worker Health:</b> <code>{e}</code>")
    conn = sqlite3.connect("storage_index.db")
    total_files = conn.execute("SELECT COUNT(*) FROM indexed_files_v4 WHERE message_id > 0").fetchone()[0]
    total_series = conn.execute("SELECT COUNT(*) FROM series_index_cards").fetchone()[0]
    report.append(f"• <b>Indexed Storage Files:</b> <code>{total_files}</code> | <b>Series Cards:</b> <code>{total_series}</code>")
    if title_query:
        clean_q = normalize_title_key(title_query)
        rows = conn.execute("SELECT ep_tag, res_tag, channel_id, message_id FROM indexed_files_v4 WHERE clean_title LIKE ? LIMIT 10", (f"%{clean_q}%",)).fetchall()
        for ep, res, cid, mid in rows:
            report.append(f"  └ <code>{ep} ({res})</code> in <code>{cid}</code> (Msg #{mid})")
    conn.close()
    return "\n".join(report)

def add_backup_gemini_key(new_key: str) -> int:
    for part in (new_key or "").split(","):
        k = part.strip()
        if len(k) >= 20 and k not in GEMINI_KEYS:
            GEMINI_KEYS.append(k)
            conn = sqlite3.connect("storage_index.db")
            conn.execute("INSERT OR IGNORE INTO extra_gemini_keys (api_key) VALUES (?)", (k,))
            conn.commit()
            conn.close()
    return len(GEMINI_KEYS)

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
    return len(set(load_all_channels_from_db().values()))

async def promote_all_bots_in_channel(channel_id: int):
    rights = ChatAdminRights(post_messages=True, edit_messages=True, delete_messages=True, invite_users=True, pin_messages=True)
    usernames = [b.get("username") for b in BOT_POOL if b.get("username")]
    if WEBSITE_BOT_USERNAME and WEBSITE_BOT_USERNAME not in usernames:
        usernames.append(WEBSITE_BOT_USERNAME)
    for uname in usernames:
        try:
            await client(EditAdminRequest(channel=channel_id, user_id=uname, admin_rights=rights, rank="Bot"))
        except Exception:
            pass

async def sync_created_channels_from_dialogs():
    global DIALOG_CACHE_READY, SERIES_INDEX_CHANNEL
    title_to_key = {v.lower(): k for k, v in DETAILED_GENRE_TITLES.items()}
    conn = sqlite3.connect("storage_index.db")
    try:
        async for dlg in client.iter_dialogs(limit=250):
            if not dlg.is_channel:
                continue
            d_title = (dlg.name or "").strip().lower()
            for target_t, cat_k in title_to_key.items():
                if d_title == target_t or target_t in d_title:
                    cid = int(dlg.id)
                    conn.execute("INSERT OR REPLACE INTO dynamic_channels (cat_key, channel_id, title) VALUES (?, ?, ?)", (cat_k, cid, dlg.name))
                    if cat_k == "series_index" and not SERIES_INDEX_CHANNEL:
                        SERIES_INDEX_CHANNEL = cid
                    break
        conn.commit()
        DIALOG_CACHE_READY = True
    except Exception:
        pass
    finally:
        conn.close()

async def resolve_account_channel_target(target_str: str):
    t = (target_str or "").strip().lower()
    if not t:
        return []
    ch_map = load_all_channels_from_db()
    if t in ch_map:
        return [(t, ch_map[t], DETAILED_GENRE_TITLES.get(t, t.upper()))]
    matches = []
    async for dlg in client.iter_dialogs(limit=250):
        if not (dlg.is_channel or dlg.is_group):
            continue
        d_name = (dlg.name or "").strip()
        if t == str(dlg.id) or t in d_name.lower():
            matches.append((d_name, int(dlg.id), d_name))
    return matches

async def rename_telegram_channel_or_chat(target_str: str, new_title: str) -> str:
    matches = await resolve_account_channel_target(target_str)
    if not matches:
        return f"⚠️ Could not find channel matching <code>{target_str}</code>."
    _, cid, old_name = matches[0]
    try:
        await client(EditTitleRequest(channel=cid, title=new_title.strip()))
        conn = sqlite3.connect("storage_index.db")
        conn.execute("UPDATE dynamic_channels SET title=? WHERE channel_id=?", (new_title.strip(), cid))
        conn.commit()
        conn.close()
        return f"✏️ <b>Renamed Chat:</b> <code>{old_name}</code> ➔ <code>{new_title.strip()}</code> (<code>{cid}</code>)"
    except Exception as e:
        return f"⚠️ Rename error: <code>{e}</code>"

async def delete_movie_or_series_everywhere(query: str) -> int:
    await sync_created_channels_from_dialogs()
    clean_q = normalize_title_key(query)
    if not clean_q or clean_q == "unknown":
        return 0
    deleted_count = 0
    for cid in sorted(list(set(load_all_channels_from_db().values()))):
        try:
            to_del = []
            async for m in client.iter_messages(cid, search=query, limit=60):
                fn = (m.file.name if m.file else "") or ""
                if is_strict_title_and_year_match(f"{fn}\n{m.text or ''}", clean_q):
                    to_del.append(m.id)
            if to_del:
                await client.delete_messages(cid, to_del)
                deleted_count += len(to_del)
        except Exception:
            pass
    slug = make_slug_key(clean_q)
    conn = sqlite3.connect("storage_index.db")
    conn.execute("DELETE FROM indexed_files_v4 WHERE clean_title=? OR slug_key=?", (clean_q, slug))
    conn.execute("DELETE FROM series_index_cards WHERE slug_key=?", (slug,))
    conn.execute("DELETE FROM crawler_history WHERE clean_title=?", (clean_q,))
    conn.commit()
    conn.close()
    return deleted_count

async def get_or_create_genre_channel(cat_key: str) -> int:
    ch_map = load_all_channels_from_db()
    if cat_key in ch_map and ch_map[cat_key]:
        return ch_map[cat_key]
    if not AUTO_CREATE_CHANNELS or count_unique_storage_channels() >= MAX_STORAGE_CHANNELS:
        fallback_map = {
            "scifi": "action", "adventure": "action", "war_history": "action",
            "thriller": "horror", "mystery": "horror", "crime": "action",
            "south_indian": "hindi", "chinese_donghua": "anime", "family": "animation",
            "comedy": "english", "romance": "english", "fantasy": "action",
            "documentary": "english", "european": "english", "classic_90s": "english", "franchise_vault": "english"
        }
        return ch_map.get(fallback_map.get(cat_key, "other"), DEFAULT_CHANNELS["other"])

    ch_title = DETAILED_GENRE_TITLES.get(cat_key, f"Vault • {cat_key.replace('_', ' ').title()}")
    try:
        res = await client(CreateChannelRequest(title=ch_title, about=f"Protected Vault ({cat_key.upper()})", megagroup=False, broadcast=True))
        full_channel_id = int(f"-100{res.chats[0].id}")
        try:
            await client(ToggleNoForwardsRequest(peer=full_channel_id, enabled=True))
        except Exception:
            pass
        await promote_all_bots_in_channel(full_channel_id)
        conn = sqlite3.connect("storage_index.db")
        conn.execute("INSERT OR REPLACE INTO dynamic_channels (cat_key, channel_id, title) VALUES (?, ?, ?)", (cat_key, full_channel_id, ch_title))
        conn.commit()
        conn.close()
        return full_channel_id
    except Exception:
        return DEFAULT_CHANNELS["other"]

def get_stored_file_record(clean_title: str, ep_tag: str, variant_or_res: str = "ANY"):
    t_key, e_key, v_key = normalize_title_key(clean_title), ep_tag.upper().strip(), (variant_or_res or "ANY").upper().strip()
    conn = sqlite3.connect("storage_index.db")
    if v_key == "ANY":
        row = conn.execute("SELECT channel_id, message_id, audio_tier FROM indexed_files_v4 WHERE clean_title=? AND ep_tag=?", (t_key, e_key)).fetchone()
    elif "_" in v_key:
        row = conn.execute("SELECT channel_id, message_id, audio_tier FROM indexed_files_v4 WHERE clean_title=? AND ep_tag=? AND variant_key=?", (t_key, e_key, v_key)).fetchone()
    else:
        row = conn.execute("SELECT channel_id, message_id, audio_tier FROM indexed_files_v4 WHERE clean_title=? AND ep_tag=? AND res_tag=?", (t_key, e_key, v_key)).fetchone()
    conn.close()
    return row

def is_already_in_any_channel(clean_title: str, ep_tag: str, variant_or_res: str = "ANY", min_audio_tier: int = 1) -> bool:
    row = get_stored_file_record(clean_title, ep_tag, variant_or_res)
    return bool(row and (row[2] or 1) >= min_audio_tier)

def record_indexed_file(channel_id: int, message_id: int, clean_title: str, ep_tag: str, res_tag: str, variant_key: str, audio_tier: int, file_name: str):
    norm_t = normalize_title_key(clean_title)
    slug = make_slug_key(norm_t)
    conn = sqlite3.connect("storage_index.db")
    conn.execute(
        """INSERT OR REPLACE INTO indexed_files_v4
           (channel_id, message_id, clean_title, slug_key, ep_tag, res_tag, variant_key, audio_tier, file_name)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (channel_id, message_id or 0, norm_t, slug, ep_tag.upper().strip(), (res_tag or "HD").upper().strip(), (variant_key or res_tag or "HD").upper().strip(), int(audio_tier or 1), file_name)
    )
    conn.commit()
    conn.close()

def get_existing_resolutions(clean_title: str, ep_tag: str = "MOVIE") -> set:
    conn = sqlite3.connect("storage_index.db")
    rows = conn.execute("SELECT DISTINCT res_tag FROM indexed_files_v4 WHERE clean_title=? AND ep_tag=?", (normalize_title_key(clean_title), ep_tag.upper().strip())).fetchall()
    conn.close()
    return {r[0].upper() for r in rows}

def get_indexed_episodes_for_title(clean_title: str) -> set:
    conn = sqlite3.connect("storage_index.db")
    rows = conn.execute("SELECT DISTINCT ep_tag FROM indexed_files_v4 WHERE clean_title=?", (normalize_title_key(clean_title),)).fetchall()
    conn.close()
    return {r[0] for r in rows}

def get_season_messages_for_slug(slug_key: str, season_prefix: str, res_filter: str = None) -> list:
    conn = sqlite3.connect("storage_index.db")
    if res_filter and res_filter.upper() != "ALL":
        rows = conn.execute(
            """SELECT channel_id, message_id, ep_tag, res_tag FROM indexed_files_v4
               WHERE slug_key=? AND ep_tag LIKE ? AND res_tag=? AND message_id > 0
               GROUP BY ep_tag ORDER BY ep_tag ASC""",
            (slug_key, f"{season_prefix.upper()}%", res_filter.upper())
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT channel_id, message_id, ep_tag, res_tag FROM indexed_files_v4
               WHERE slug_key=? AND ep_tag LIKE ? AND message_id > 0
               GROUP BY ep_tag, res_tag ORDER BY ep_tag ASC""",
            (slug_key, f"{season_prefix.upper()}%")
        ).fetchall()
    conn.close()
    return rows

def get_available_season_qualities(slug_key: str, season_prefix: str) -> list:
    conn = sqlite3.connect("storage_index.db")
    rows = conn.execute("SELECT DISTINCT res_tag FROM indexed_files_v4 WHERE slug_key=? AND ep_tag LIKE ? AND message_id > 0", (slug_key, f"{season_prefix.upper()}%")).fetchall()
    conn.close()
    found = [r[0].upper() for r in rows if r[0]]
    return sorted(found, key=lambda x: TARGET_RESOLUTIONS.index(x) if x in TARGET_RESOLUTIONS else 99)

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
    row = conn.execute(f"SELECT 1 FROM crawler_history WHERE clean_title=? AND checked_at >= datetime('now', '-{int(hours)} hours')", (normalize_title_key(clean_title),)).fetchone()
    conn.close()
    return bool(row)

def mark_title_crawled(clean_title: str, year: int = None):
    conn = sqlite3.connect("storage_index.db")
    conn.execute("INSERT OR REPLACE INTO crawler_history (clean_title, year, checked_at) VALUES (?, ?, CURRENT_TIMESTAMP)", (normalize_title_key(clean_title), year))
    conn.commit()
    conn.close()

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
    conn.execute("INSERT OR REPLACE INTO persistent_meta_cache (cache_key, json_data, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)", (cache_key, json.dumps(data)))
    conn.commit()
    conn.close()

init_storage_db()

# ============================================================================
# 4. GEMINI COMMANDER BRAIN (STABLE FREE-TIER MODELS ONLY • ZERO 429 PREVIEW BUG)
# ============================================================================
LAST_GEMINI_ERROR = "None"
DISCOVERED_GEMINI_MODELS = []

async def get_available_gemini_models(session: aiohttp.ClientSession, api_key: str) -> list:
    """
    Strictly selects STABLE free-tier Flash models (excludes 'preview', 'exp', 'omni'
    which have 0 free-tier RPM and caused HTTP 429 in your screenshot!).
    """
    global DISCOVERED_GEMINI_MODELS
    if DISCOVERED_GEMINI_MODELS:
        return DISCOVERED_GEMINI_MODELS

    preferred_stable = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash"
    ]
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?pageSize=100&key={api_key}"
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                avail_names = set()
                extra_stable = []
                for m in (data.get("models") or []):
                    if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                        continue
                    m_name = (m.get("name") or "").replace("models/", "").strip()
                    # Exclude preview/exp/omni/pro models that return HTTP 429 (0 free quota)
                    if any(bad in m_name.lower() for bad in ["preview", "exp", "omni", "live", "tts", "image", "audio", "embedding", "vision", "pro", "thinking", "learnlm", "robotics"]):
                        continue
                    avail_names.add(m_name)
                    if "flash" in m_name and m_name not in preferred_stable:
                        extra_stable.append(m_name)
                ordered = [m for m in preferred_stable if m in avail_names] + sorted(extra_stable, reverse=True)
                if ordered:
                    DISCOVERED_GEMINI_MODELS = ordered[:6]
                    return DISCOVERED_GEMINI_MODELS
    except Exception:
        pass
    return preferred_stable

async def ask_gemini_json(prompt: str, cache_key: str = None):
    global GEMINI_KEY_INDEX, GEMINI_CALL_COUNT, LAST_GEMINI_ERROR
    if not GEMINI_KEYS:
        LAST_GEMINI_ERROR = "No GEMINI_API_KEY loaded (send .addgemini AIzaSy...)"
        return None
    if cache_key:
        cached = get_db_cached_meta(f"gemini:{cache_key}")
        if cached:
            return cached

    async with GEMINI_SEMAPHORE:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}
        }
        async with aiohttp.ClientSession() as session:
            for _ in range(len(GEMINI_KEYS)):
                api_key = GEMINI_KEYS[GEMINI_KEY_INDEX % len(GEMINI_KEYS)]
                GEMINI_KEY_INDEX += 1
                models_to_try = await get_available_gemini_models(session, api_key)
                for model in models_to_try:
                    try:
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                        async with session.post(url, json=payload, timeout=14) as resp:
                            if resp.status == 200:
                                GEMINI_CALL_COUNT += 1
                                data = await resp.json()
                                parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                                raw_txt = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                                m_json = re.search(r"(\{.*\}|\[.*\])", raw_txt.strip(), re.DOTALL)
                                if m_json:
                                    parsed = json.loads(m_json.group(1))
                                    if cache_key and parsed:
                                        save_db_cached_meta(f"gemini:{cache_key}", parsed)
                                    LAST_GEMINI_ERROR = f"OK ({model})"
                                    await asyncio.sleep(1.0)
                                    return parsed
                            elif resp.status in (404, 429, 503):
                                # Per-model rate limit or retired model -> continue to next stable model!
                                LAST_GEMINI_ERROR = f"HTTP {resp.status} ({model})"
                                continue
                            else:
                                LAST_GEMINI_ERROR = f"HTTP {resp.status} ({model})"
                                break
                    except Exception as e:
                        LAST_GEMINI_ERROR = str(e)[:80]
    return None

async def tmdb_smart_list_fallback(user_text: str) -> list:
    low = (user_text or "").lower().strip()
    if not re.search(r"\b(?:top|best)\s+\d{1,2}\b", low) or not TMDB_API_KEY:
        return []
    cnt_m = re.search(r"\b(?:top|best)\s+(\d{1,2})\b", low)
    limit = min(int(cnt_m.group(1)), 20) if cnt_m else 10
    years = [int(y) for y in re.findall(r"\b(19[5-9]\d|20[0-3]\d)\b", low)]
    yr_start = min(years) if years else current_utc_year()
    yr_end = max(years) if len(years) > 1 else yr_start
    is_tv = any(w in low for w in ["series", "shows", "anime", "k-drama", "kdrama", "season"])
    endpoint = "tv" if is_tv else "movie"
    genre_map_kw = {
        "sci-fi": "878", "scifi": "878", "science fiction": "878", "horror": "27",
        "thriller": "53", "action": "28", "comedy": "35", "romance": "10749",
        "animation": "16", "anime": "16", "crime": "80", "drama": "18",
        "fantasy": "14", "adventure": "12", "mystery": "9648", "documentary": "99", "war": "10752"
    }
    g_ids = [gid for kw, gid in genre_map_kw.items() if kw in low]
    g_param = f"&with_genres={'|'.join(set(g_ids))}" if g_ids else ""
    lang_param = "&with_original_language=ja" if "anime" in low else ("&with_original_language=ko" if "korean" in low else ("&with_original_language=hi|ta|te|ml" if any(w in low for w in ["bollywood", "hindi", "indian"]) else ""))
    sort_p = "&sort_by=vote_average.desc&vote_count.gte=50" if ("highest" in low or "rated" in low) else "&sort_by=popularity.desc"
    d_gte = "first_air_date.gte" if is_tv else "primary_release_date.gte"
    d_lte = "first_air_date.lte" if is_tv else "primary_release_date.lte"
    url = f"https://api.themoviedb.org/3/discover/{endpoint}?api_key={TMDB_API_KEY}&language=en-US&{d_gte}={yr_start}-01-01&{d_lte}={yr_end}-12-31{sort_p}{g_param}{lang_param}&page=1"
    tasks = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as r:
                if r.status == 200:
                    for it in ((await r.json()).get("results") or [])[:limit]:
                        name = it.get("title") or it.get("name")
                        rel = it.get("release_date") or it.get("first_air_date") or ""
                        yr = int(rel[:4]) if len(rel) >= 4 and rel[:4].isdigit() else None
                        if name:
                            tasks.append({"raw": f"{name} ({yr})" if yr else name, "title": name, "year": None if is_tv else yr, "mode": "normal", "season": None, "episodes": None})
    except Exception:
        pass
    return tasks

def local_commander_fallback(user_msg: str) -> dict:
    """Rich conversational fallback so even if all Gemini keys are cooling down, it chats naturally like ChatGPT!"""
    low = (user_msg or "").lower().strip()
    c_yr, c_pg, c_kind = get_crawler_progress()
    q_preview = ", ".join(t[0]["raw"] if isinstance(t[0], dict) else str(t[0]) for t in list(TITLE_QUEUE)[:5]) or "Empty"

    if re.search(r"\b(?:stop|pause|cancel|halt|clear)\b", low):
        return {"action": "STOP_ALL", "risk_level": "LOW", "crawler_on": False, "reply": "🛑 <b>Done! I have stopped all active downloads, cleared the queue, and paused the 24/7 Auto-Crawler.</b>"}

    if any(w in low for w in ["how are u", "how are you", "hows it going", "how is it going", "good morning", "good evening", "sup", "whats up"]):
        return {
            "action": "CHAT_ONLY", "risk_level": "LOW",
            "reply": (
                f"I'm doing great and running smoothly! 🚀\n\n"
                f"• <b>Current Task:</b> <code>{ACTIVE_CLEAN_TITLE or 'Idle & Ready'}</code>\n"
                f"• <b>Auto-Crawler:</b> <code>{'ON 🟢' if AUTO_DISCOVERY_ENABLED else 'OFF 🔴'}</code> ({c_yr} • {c_kind.upper()})\n"
                f"• <b>Queue:</b> <code>{len(TITLE_QUEUE)}</code> item(s) ({q_preview})\n\n"
                f"What would you like me to do next? You can ask me to search movies, manage channels, check your website, or teach me a new rule!"
            )
        }

    if any(w in low for w in ["what are you downloading", "downloading right now", "working on", "status", "queue", "progress"]):
        return {
            "action": "CHAT_ONLY", "risk_level": "LOW",
            "reply": (
                f"<b>Here is my live operational status:</b>\n"
                f"• <b>Active Download:</b> <code>{ACTIVE_CLEAN_TITLE or 'Idle'}</code> ({ACTIVE_RES_TAG or '-'})\n"
                f"• <b>Waiting in Queue ({len(TITLE_QUEUE)}):</b> <code>{q_preview}</code>\n"
                f"• <b>24/7 Auto-Crawler:</b> <code>{'ON 🟢' if AUTO_DISCOVERY_ENABLED else 'OFF 🔴'}</code> (Year {c_yr} • {c_kind.upper()})\n"
                f"• <b>Storage Channels:</b> <code>{count_unique_storage_channels()}/{MAX_STORAGE_CHANNELS}</code>\n"
                f"• <b>AI Engine Status:</b> <code>{LAST_GEMINI_ERROR}</code>"
            )
        }

    if re.search(r"\b(?:hi|hello|hey|who are you|what are you|what can you do)\b", low) or "tell me about u" in low or "tell me about you" in low:
        return {
            "action": "CHAT_ONLY", "risk_level": "LOW",
            "reply": (
                "<b>I am THE BACKROOM AI COMMANDER</b> — your personal AI operating system inside Telegram.\n\n"
                "You can talk to me naturally like ChatGPT:\n"
                "• <i>\"Download Vikings season 1\"</i> or <i>\".search Title\"</i>\n"
                "• <i>\"Delete everything from the korean channel and reupload it\"</i>\n"
                "• <i>\"From now on, always prefer official English titles\"</i>\n"
                "• <i>\"Find the conversation where I discussed Cloudflare\"</i>\n"
                "• <i>\"Stop anything in progress\"</i>"
            )
        }

    if low.startswith(("from now on", "always ", "never ", "remember that")):
        return {"action": "MANAGE_RULE", "risk_level": "LOW", "rule_op": "ADD", "rule_text": user_msg.strip(), "reply": f"Got it! I have saved this as a permanent behavioral rule: <code>{user_msg.strip()}</code>"}

    if low.startswith(("remove ", "delete movie ", "delete series ")):
        t_del = re.sub(r"^(?:remove|delete)\s+(?:the\s+)?(?:movie|series)?\s*", "", user_msg, flags=re.I).strip()
        return {"action": "DELETE_MEDIA", "risk_level": "MEDIUM", "target_title": t_del, "reply": f"Removing all files and cards for <code>{t_del}</code>..."}

    if low.startswith(("reupload ", "re-upload ", "resync ")):
        t_re = re.sub(r"^(?:reupload|re-upload|resync)\s+", "", user_msg, flags=re.I).strip()
        return {"action": "REUPLOAD_MEDIA", "risk_level": "MEDIUM", "target_title": t_re, "reply": f"Re-uploading <code>{t_re}</code> as a brand-new post with its Official Public Name..."}

    return {
        "action": "CHAT_ONLY", "risk_level": "LOW",
        "reply": (
            f"I heard you: <i>\"{user_msg}\"</i>\n\n"
            f"Right now my Gemini key is cooling down (<code>{LAST_GEMINI_ERROR}</code> — tip: add a 2nd/3rd free key with <code>.addgemini AIzaSy...</code> so I never hit rate limits!).\n"
            f"Meanwhile, you can still tell me to <b>stop</b>, ask <b>what are you downloading</b>, teach rules (<b>From now on...</b>), or use <code>.search Title</code> / <code>.repopulate channel</code>!"
        )
    }

async def gemini_interactive_agent(user_msg: str) -> dict:
    ch_map = load_all_channels_from_db()
    c_yr, c_pg, c_kind = get_crawler_progress()
    active_rules = get_active_behavior_rules()
    rules_str = "\n".join(f"  [Rule #{r[0]} v{r[3]}]: {r[1]}" for r in active_rules) or "  (No custom rules yet)"
    memories = retrieve_relevant_memories(user_msg, limit=10)
    mem_str = "\n".join(f"  [{m[0]}] {m[1]}: {m[2]}" for m in memories) or "  (No stored memories yet)"
    history_str = "\n".join(AI_CHAT_HISTORY)

    prompt = f"""
    You are THE BACKROOM AI COMMANDER — a persistent personal AI operating system inside the owner's Telegram account.
    Current State:
    - Active Download: {ACTIVE_CLEAN_TITLE or 'Idle'} ({ACTIVE_RES_TAG or '-'}) | Queue: {len(TITLE_QUEUE)}
    - Auto-Crawler: {'ON' if AUTO_DISCOVERY_ENABLED else 'OFF'} (Year: {c_yr}, Category: {c_kind})
    - Gemini Keys Pool: {len(GEMINI_KEYS)} | Delivery Bots: {len(BOT_POOL)}
    - Channels (cat_key: channel_id): {json.dumps(ch_map)}

    ACTIVE PERMANENT BEHAVIORAL RULES (MUST OBEY):
    {rules_str}

    RELEVANT LONG-TERM MEMORIES:
    {mem_str}

    RECENT CONVERSATION:
    {history_str}

    OWNER MESSAGE: "{user_msg}"

    CRITICAL EXECUTION RULES (DO NOT ROLEPLAY WITHOUT AN ACTION!):
    1. NEVER say "I am checking right now" or "Let me search for X" with action="CHAT_ONLY"! You are a single-turn tool executor: if an action is needed, you MUST set the matching "action" field NOW!
    2. If the owner sends a movie or series title (e.g. "Prison break", "Vikings", "Solo Leveling") or asks to find/download/pull up a title, you MUST set action="QUEUE_TITLES" and populate "tasks": [{{"raw": "Prison Break", "title": "Prison Break", "year": null, "mode": "normal", "season": null, "episodes": null}}]!
    3. If the owner asks to check/scan/verify what movies or files are inside their storage channel(s) (or says "Have u checked it?"), you MUST set action="INSPECT_STORAGE" and target_channel_key="<cat_key or null for all>"!
    4. If the owner says "stop anything on progress" or "stop everything", set action="STOP_ALL" and crawler_on=false.
    5. If the owner asks to delete everything from one storage channel and re-upload it again by itself, set action="WIPE_AND_REUPLOAD_CHANNEL" and target_channel_key="<cat_key_or_id>".
    6. If the owner gives a permanent rule ("From now on...", "Always...", "Never..."), set action="MANAGE_RULE", rule_op="ADD", and rule_text="<clean rule>".
    7. Other actions: "SEARCH_ACCOUNT", "SEARCH_CONTACTS", "INSPECT_WEBSITE", "SELF_EVOLVE", "SCHEDULE_JOB", "DELETE_MEDIA", "REUPLOAD_MEDIA", "RENAME_ALL_PUBLIC", "REBUILD_SERIES_INDEX", "RENAME_CHANNEL", "DELETE_CHANNEL", "CLEAR_CHANNEL_MESSAGES", "CREATE_CHANNEL", "CRAWLER_CONTROL", "SKIP", "CLEAR_QUEUE".

    Return ONLY valid JSON:
    {{
      "reply": "Warm, direct HTML-formatted response",
      "action": "CHAT_ONLY",
      "risk_level": "LOW",
      "rule_op": null, "rule_id": null, "rule_text": null, "memory_to_save": null,
      "search_query": null, "cap_name": null, "cap_description": null, "cap_code": null,
      "schedule_prompt": null, "interval_minutes": null, "target_channel_key": null,
      "new_name": null, "target_title": null, "crawler_year": null, "crawler_mode": null,
      "crawler_on": null, "tasks": []
    }}
    """
    res = await ask_gemini_json(prompt)
    if isinstance(res, dict) and res.get("action"):
        # Safety Net: If Gemini's reply says it is searching/pulling up a title but forgot to set action="QUEUE_TITLES"
        act = (res.get("action") or "CHAT_ONLY").upper()
        rep_low = (res.get("reply") or "").lower()
        if act == "CHAT_ONLY":
            if any(p in rep_low for p in ["looking into your storage", "checking it right now", "pull up the list from your storage"]):
                res["action"] = "INSPECT_STORAGE"
            elif any(p in rep_low for p in ["let me search for", "pull up the series index", "fetch its details"]) and len(user_msg.split()) <= 6:
                cmd = parse_smart_user_command(user_msg)
                res["action"] = "QUEUE_TITLES"
                res["tasks"] = [cmd]
        return res

    top_tasks = await tmdb_smart_list_fallback(user_msg)
    if top_tasks:
        return {"action": "QUEUE_TITLES", "risk_level": "LOW", "reply": f"Queued <b>{len(top_tasks)}</b> titles from TMDB Discover!", "tasks": top_tasks}
    return local_commander_fallback(user_msg)

async def gemini_resolve_movie_meta(raw_title: str, target_year: int = None) -> dict:
    clean_k = normalize_title_key(raw_title)
    prompt = f"""Identify movie/series from: "{raw_title}" (Year hint: {target_year or 'Any'}).
    Return JSON object: {{"public_title": "Official Popular Published English Title", "year": 2026, "genres": "Action, Sci-Fi", "language": "en", "rating": "7.8/10", "is_series": false}}"""
    return await ask_gemini_json(prompt, cache_key=f"meta:{clean_k}:{target_year or 'any'}")

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

    public_title, resolved_year, genres_list, lang, runtime, rating = clean.title(), target_year, [], "", "", ""
    has_ep_pattern = bool(re.search(r"\b(S\d{1,2}|Season|E\d{1,3}|EP\.?\s*\d+|Episode)\b", title, re.I))
    is_series = has_ep_pattern

    today_str = current_utc_date_str()
    if OMDB_CALL_DAY != today_str:
        OMDB_CALL_DAY, OMDB_CALL_COUNT = today_str, 0

    async with aiohttp.ClientSession() as session:
        if TMDB_API_KEY and clean != "unknown":
            async with TMDB_SEMAPHORE:
                try:
                    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&language=en-US&query={quote_plus(clean)}"
                    async with session.get(url, timeout=4) as r:
                        if r.status == 200:
                            results = [x for x in ((await r.json()).get("results") or []) if x.get("media_type") in ("movie", "tv")]
                            chosen = None
                            for item in results:
                                m_type = item.get("media_type")
                                if has_ep_pattern and m_type != "tv":
                                    continue
                                rel = item.get("release_date") or item.get("first_air_date") or ""
                                iy = int(rel[:4]) if len(rel) >= 4 and rel[:4].isdigit() else None
                                if target_year and iy and iy != target_year:
                                    continue
                                chosen, resolved_year = item, iy or resolved_year
                                break
                            if not chosen and results and not target_year:
                                chosen = results[0]
                                rel = chosen.get("release_date") or chosen.get("first_air_date") or ""
                                if len(rel) >= 4 and rel[:4].isdigit():
                                    resolved_year = int(rel[:4])
                            if chosen:
                                public_title = chosen.get("title") or chosen.get("name") or public_title
                                lang = chosen.get("original_language", "")
                                if chosen.get("media_type") == "tv":
                                    is_series = True
                                vote = chosen.get("vote_average")
                                if vote and float(vote) > 0:
                                    rating = f"{float(vote):.1f}/10"
                                for gid in (chosen.get("genre_ids") or []):
                                    if gid in TMDB_GENRE_MAP and TMDB_GENRE_MAP[gid] not in genres_list:
                                        genres_list.append(TMDB_GENRE_MAP[gid])
                    await asyncio.sleep(0.2)
                except Exception:
                    pass

        if OMDB_API_KEY and OMDB_CALL_COUNT < OMDB_DAILY_SAFE_LIMIT and clean != "unknown" and (not genres_list or not runtime):
            try:
                OMDB_CALL_COUNT += 1
                y_param = f"&y={target_year}" if target_year else ""
                async with session.get(f"https://www.omdbapi.com/?apikey={OMDB_API_KEY}&t={quote_plus(clean)}{y_param}", timeout=4) as r:
                    if r.status == 200:
                        data = await r.json()
                        if data.get("Response") == "True":
                            if data.get("Title"):
                                public_title = data["Title"]
                            y_str = re.search(r"\b(19\d\d|20\d\d)\b", data.get("Year", ""))
                            if y_str:
                                resolved_year = resolved_year or int(y_str.group(1))
                            for og in [g.strip() for g in (data.get("Genre") or "").split(",") if g.strip() and g.strip() != "N/A"]:
                                if og not in genres_list:
                                    genres_list.append(og)
                            if data.get("Runtime") and data.get("Runtime") != "N/A":
                                runtime = data["Runtime"]
                            if data.get("imdbRating") and data.get("imdbRating") != "N/A":
                                rating = f"{data['imdbRating']}/10"
                            if data.get("Type") == "series":
                                is_series = True
            except Exception:
                pass

        if not genres_list and clean != "unknown":
            try:
                async with session.get(f"https://itunes.apple.com/search?term={quote_plus(clean)}&limit=5", timeout=4) as ir:
                    if ir.status == 200:
                        idata = await ir.json(content_type=None)
                        for item in (idata.get("results") or []):
                            t_name = item.get("trackName") or item.get("collectionName") or ""
                            rel = item.get("releaseDate") or ""
                            iy = int(rel[:4]) if len(rel) >= 4 and rel[:4].isdigit() else None
                            if target_year and iy and abs(iy - target_year) > 1:
                                continue
                            if is_strict_title_and_year_match(t_name, clean, target_year):
                                if t_name:
                                    public_title = re.sub(r"\s*\(?\bSeason\s*\d+.*$", "", t_name, flags=re.I).strip()
                                resolved_year = resolved_year or iy
                                pg = item.get("primaryGenreName")
                                if pg and pg not in genres_list:
                                    genres_list.append(pg)
                                break
            except Exception:
                pass

    # Save Gemini RPM quota for user chat! Only call Gemini on background files if ALL other APIs missed the title completely.
    if GEMINI_KEYS and not genres_list and public_title.lower() == clean and not resolved_year:
        ai_meta = await gemini_resolve_movie_meta(title, target_year)
        if isinstance(ai_meta, dict):
            public_title = ai_meta.get("public_title") or public_title
            resolved_year = resolved_year or ai_meta.get("year")
            if ai_meta.get("genres"):
                genres_list = [g.strip() for g in str(ai_meta["genres"]).split(",") if g.strip()]
            lang = lang or ai_meta.get("language", "")
            rating = rating or ai_meta.get("rating", "")
            if ai_meta.get("is_series") is not None:
                is_series = bool(ai_meta["is_series"])

    result = {
        "title": clean, "public_title": public_title, "year": resolved_year,
        "genres": ", ".join(genres_list), "language": lang, "runtime": runtime,
        "rating": rating, "is_series": is_series
    }
    META_CACHE[cache_key] = result
    save_db_cached_meta(cache_key, result)
    return result

async def fetch_official_catalog(title: str) -> dict:
    clean = normalize_title_key(title)
    seasons_map, franchise_movies = {}, []
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"https://api.tvmaze.com/singlesearch/shows?q={quote_plus(clean)}&embed=episodes", timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    for ep in data.get("_embedded", {}).get("episodes", []):
                        if ep.get("season") and ep.get("number"):
                            seasons_map.setdefault(int(ep["season"]), []).append(int(ep["number"]))
        except Exception:
            pass
        if TMDB_API_KEY and not franchise_movies:
            async with TMDB_SEMAPHORE:
                try:
                    async with session.get(f"https://api.themoviedb.org/3/search/collection?api_key={TMDB_API_KEY}&language=en-US&query={quote_plus(clean)}", timeout=4) as cr:
                        if cr.status == 200:
                            c_res = (await cr.json()).get("results") or []
                            if c_res:
                                col_id = c_res[0]["id"]
                                async with session.get(f"https://api.themoviedb.org/3/collection/{col_id}?api_key={TMDB_API_KEY}&language=en-US", timeout=4) as dr:
                                    if dr.status == 200:
                                        franchise_movies = [p["title"] for p in (await dr.json()).get("parts") or [] if p.get("title")]
                except Exception:
                    pass
    return {"seasons": seasons_map, "franchise_movies": franchise_movies[:12]}

# ============================================================================
# 5. THEBACKROOM.LOL CAPTION BUILDER & IN-PLACE GREEN BUTTON PUBLISHER
# ============================================================================
def strip_subtitle_sections(caption: str) -> str:
    if not caption:
        return ""
    no_bubble = caption.split("💬")[0]
    cleaned = []
    for line in no_bubble.splitlines():
        if re.match(r"^(?:📝|🔤|🍿|╰)?\s*(?:subtitles?|subs?|esub|mmsub|join:)\b", line.strip().lower()):
            break
        cleaned.append(line)
    return "\n".join(cleaned)

def format_size_human(size_bytes: int) -> str:
    if not size_bytes:
        return ""
    mb = size_bytes / (1024 * 1024)
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"

def extract_real_filename(filename="", caption=""):
    cap = str(caption or "")
    m = re.search(r"File\s*Name\s*:\s*\r?\n?([^\r\n]+)", cap, re.I)
    fn = m.group(1).strip() if (m and len(m.group(1).strip()) >= 4) else str(filename or "Media.mkv").strip()
    return strip_uploader_and_junk(fn)

def extract_tech_specs(filename="", caption="", file_size_bytes=0, meta=None):
    meta = meta or {}
    real_fn = extract_real_filename(filename, caption)
    audio_safe = strip_subtitle_sections(caption or "")
    full_combined = f"{real_fn}\n{caption or ''}"
    qual = extract_resolution_tag(full_combined, ACTIVE_RES_TAG or "1080P")

    source = ""
    for pat, label in [(r"\b(?:bluray|bdrip|brrip|bd)\b", "BluRay"), (r"\bweb-?dl\b", "WEB-DL"), (r"\bwebrip\b", "WEBRip"), (r"\bhdrip\b", "HDRip"), (r"\bamzn\b", "AMZN"), (r"\bnf|netflix\b", "NF")]:
        if re.search(pat, full_combined, re.I):
            source = label
            break

    codec = "HEVC x265" if re.search(r"\b(x265|h265|hevc)\b", full_combined, re.I) else ("AV1/VP9" if re.search(r"\b(av1|vp9)\b", full_combined, re.I) else "x264")
    if re.search(r"\b10\s*-?bit\b", full_combined, re.I) and "10-Bit" not in codec:
        codec += " 10-Bit"

    aud_codec = "Dolby Atmos 5.1" if re.search(r"\batmos\b", full_combined, re.I) else ("DDP 5.1 (6CH)" if re.search(r"\b6ch\b|\bddp\s*5\.1\b|\baac\s*5[\.\s]1\b|\b5[\.\s]1\b", full_combined, re.I) else "AAC 2.0")
    br_m = re.search(r"Video\s*Bitrate\s*:\s*([^\r\n]+)", full_combined, re.I) or re.search(r"\b(\d{3,5}\s*kbps)\b", full_combined, re.I)
    dur_m = re.search(r"Duration\s*:\s*([^\r\n]+)", full_combined, re.I) or re.search(r"⏳\s*(\d{1,2}:\d{2}(?::\d{2})?)", full_combined)

    speaker_m = re.search(r"🔊\s*([^\r\n💬╰]+)", audio_safe, re.DOTALL)
    audio_only_text = f"{real_fn}\n{speaker_m.group(1) if speaker_m else ''}"

    detected_langs = []
    for pat, name in [
        (r"\b(?:hindi|hin)\b", "Hindi"), (r"\b(?:japanese|jap|jpn)\b", "Japanese"),
        (r"\b(?:english|eng)\b", "English"), (r"\b(?:korean|kor)\b", "Korean"),
        (r"\b(?:tamil|tam)\b", "Tamil"), (r"\b(?:telugu|tel)\b", "Telugu"),
        (r"\b(?:malayalam|mal)\b", "Malayalam"), (r"\b(?:kannada|kan)\b", "Kannada"),
        (r"\b(?:turkish|tur)\b", "Turkish"), (r"\b(?:amharic|amh|ትርጉም)\b", "Amharic")
    ]:
        if re.search(pat, audio_only_text, re.I):
            detected_langs.append(name)

    if not detected_langs:
        detected_langs.append(ORIG_LANG_MAP.get((meta.get("language") or "").lower(), "English"))

    audio_tier = extract_audio_tier(audio_only_text, len(detected_langs))
    audio_mode = f"Multi Audio / Dubbed ({', '.join(detected_langs[:5])})" if audio_tier == 3 else (f"Dual Audio ({' + '.join(detected_langs)})" if audio_tier == 2 else f"{detected_langs[0]} ({aud_codec})")

    return {
        "real_filename": real_fn, "quality": qual, "source": source, "codec": codec,
        "audio_mode": audio_mode, "audio_tier": audio_tier,
        "size_str": format_size_human(file_size_bytes),
        "duration": dur_m.group(1).strip() if dur_m else "",
        "bitrate": br_m.group(1).strip() if br_m else "",
        "for_tv": bool("x264" in codec), "has_sub": True,
        "audio_lines": [f"* {aud_codec} — {l}" for l in detected_langs]
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
    official_public_filename = f"{pub_title}{year_str}{ep_str}{dub_badge} [{spec_tag}].mkv"
    orig_lang_name = ORIG_LANG_MAP.get((meta.get("language") or "").lower(), "")

    lines = [f"🎬 <b>{pub_title}{year_str}{ep_str}</b>"]
    if meta.get("genres"):
        lines.append(f"🎭 <b>Genre:</b> {meta['genres']}")
    if meta.get("rating"):
        lines.append(f"⭐ <b>Rating:</b> {meta['rating']}")
    if orig_lang_name:
        lines.append(f"🗣 <b>Language:</b> {orig_lang_name}")

    lines.extend(["", "<b>File Name:</b>", f"<code>{official_public_filename}</code>", ""])
    if dur:
        lines.append(f"<b>Duration:</b> {dur}")
    spec_parts = [p for p in [sp["quality"], sp["source"], sp["codec"], sp["size_str"]] if p]
    lines.append(f"<b>Specs:</b> {' • '.join(spec_parts)}")
    if sp["audio_mode"]:
        lines.append(f"<b>Audio Track:</b> {sp['audio_mode']}")
    lines.append("")
    if sp["for_tv"]:
        lines.append("✅ Can be played on TV")
    lines.append("💬 Subtitles: English")
    if sp["audio_lines"]:
        lines.extend(["", "<b>Audio List:</b>"] + sp["audio_lines"][:8])
    return "\n".join(lines)

def detect_category(filename, caption, meta):
    meta = meta or {}
    genres_low = (meta.get("genres") or "").lower()
    lang = (meta.get("language") or "").lower()
    fn_low = (filename or "").lower()
    yr = meta.get("year") or extract_year_from_text(filename)
    speaker_m = re.search(r"🔊\s*([^\r\n💬╰]+)", strip_subtitle_sections(caption or ""))
    audio_line = (speaker_m.group(1) if speaker_m else "").lower()
    ch_map = load_all_channels_from_db()

    if re.search(r"\b(anime|demon slayer|solo leveling|jujutsu|naruto|one piece|bleach|attack on titan|kimetsu|blue lock)\b", f"{fn_low} {meta.get('public_title', '').lower()}") or ("animation" in genres_low and lang == "ja"):
        return "anime"
    if lang == "zh" and "chinese_donghua" in ch_map:
        return "chinese_donghua"
    if "animation" in genres_low:
        return "animation"
    if meta.get("is_series") or re.search(r"\b(S\d{1,2}\s*E\d{1,3}|Season\s*\d+|Episode\s*\d+|EP\.?\s*\d+)\b", fn_low, re.I):
        return "series"
    if lang == "am" or re.search(r"[\u1200-\u137F]+|amharic|ትርጉም", f"{fn_low} {audio_line}"):
        return "amharic"

    if lang in ("ta", "te", "ml", "kn") and "south_indian" in ch_map:
        return "south_indian"
    if lang in ("es", "fr", "de", "it") and "european" in ch_map:
        return "european"
    if yr and int(yr) < 2000 and "classic_90s" in ch_map:
        return "classic_90s"

    if "documentary" in genres_low and "documentary" in ch_map:
        return "documentary"
    if ("war" in genres_low or "history" in genres_low) and "war_history" in ch_map:
        return "war_history"
    if "family" in genres_low and "family" in ch_map:
        return "family"
    if ("sci-fi" in genres_low or "science fiction" in genres_low) and "scifi" in ch_map:
        return "scifi"
    if "fantasy" in genres_low and "fantasy" in ch_map:
        return "fantasy"
    if "horror" in genres_low:
        return "horror"
    if "mystery" in genres_low and "mystery" in ch_map:
        return "mystery"
    if "thriller" in genres_low:
        return "thriller" if "thriller" in ch_map else "horror"
    if "crime" in genres_low and "crime" in ch_map:
        return "crime"
    if "adventure" in genres_low and "adventure" in ch_map:
        return "adventure"
    if "action" in genres_low:
        return "action"
    if "comedy" in genres_low and "comedy" in ch_map:
        return "comedy"
    if ("romance" in genres_low or "drama" in genres_low) and "romance" in ch_map:
        return "romance"

    if lang in INDIAN_LANG_CODES or re.search(r"\b(hindi|bollywood|telugu|tamil|malayalam|kannada)\b", f"{fn_low} {audio_line}"):
        return "hindi"
    if lang == "ko" or re.search(r"\b(korean|k-drama)\b", f"{fn_low} {audio_line}"):
        return "korean"
    if lang == "tr" or re.search(r"\bturkish\b", f"{fn_low} {audio_line}"):
        return "turkish"
    return "english"

async def publish_file_with_green_buttons(dest_channel: int, media, final_caption: str, is_series: bool = False):
    sent_msg = await client.send_file(dest_channel, file=media, caption=final_caption, buttons=INLINE_BUTTONS, parse_mode="HTML")
    token = WEBSITE_BOT_TOKEN or pick_bot_for_role("series" if is_series else "movies").get("token")
    if not token or not sent_msg:
        return sent_msg
    try:
        api_url = f"https://api.telegram.org/bot{token}/editMessageReplyMarkup"
        payload = {"chat_id": dest_channel, "message_id": sent_msg.id, "reply_markup": json.dumps(GREEN_BOT_API_MARKUP)}
        async with aiohttp.ClientSession() as session:
            await session.post(api_url, data=payload, timeout=8)
    except Exception:
        pass
    return sent_msg

async def update_series_index_card(meta: dict, ep_tag: str, force_resend: bool = False):
    if not BOT_POOL or not ep_tag or ep_tag == "MOVIE":
        return
    index_ch = SERIES_INDEX_CHANNEL or load_all_channels_from_db().get("series_index", 0)
    if not index_ch or index_ch == DEFAULT_CHANNELS["other"]:
        return

    pub_title = meta.get("public_title") or "Series"
    clean_t = normalize_title_key(pub_title)
    slug = make_slug_key(clean_t)
    s_m = re.match(r"^(S\d{2})", ep_tag.upper())
    season_code = s_m.group(1) if s_m else "S01"

    conn = sqlite3.connect("storage_index.db")
    row = conn.execute("SELECT index_message_id, seasons_json FROM series_index_cards WHERE slug_key=?", (slug,)).fetchone()
    existing_msg_id = row[0] if row else 0
    seasons_set = set(json.loads(row[1])) if row else set()

    db_ep_rows = conn.execute("SELECT DISTINCT substr(ep_tag, 1, 3) FROM indexed_files_v4 WHERE slug_key=? AND ep_tag LIKE 'S%'", (slug,)).fetchall()
    for r_ep in db_ep_rows:
        if r_ep[0]:
            seasons_set.add(r_ep[0].upper())
    seasons_set.add(season_code)
    sorted_seasons = sorted(list(seasons_set))

    # Obeys Permanent Rule #1: Inline buttons show ONLY the Season name (no resolution tags!)
    season_rows = []
    active_bots = [b for b in BOT_POOL[:2] if b.get("username")]
    for s_code in sorted_seasons:
        s_num = int(s_code[1:]) if s_code[1:].isdigit() else 1
        if len(active_bots) >= 2:
            b1_u, b2_u = active_bots[0]["username"], active_bots[1]["username"]
            season_rows.append([
                {"text": f"📺 Season {s_num:02d} • Bot 1 ↗", "url": f"https://t.me/{b1_u}?start=season_{slug}_{s_code}", "style": "primary"},
                {"text": f"📺 Season {s_num:02d} • Bot 2 ↗", "url": f"https://t.me/{b2_u}?start=season_{slug}_{s_code}", "style": "primary"}
            ])
        elif active_bots:
            b1_u = active_bots[0]["username"]
            season_rows.append([{"text": f"📺 Season {s_num:02d} ↗", "url": f"https://t.me/{b1_u}?start=season_{slug}_{s_code}", "style": "primary"}])

    season_rows.append(GREEN_BRAND_ROW)
    markup = {"inline_keyboard": season_rows}
    yr_str = f" ({meta['year']})" if meta.get("year") else ""
    card_text = (
        f"📺 <b>{pub_title}{yr_str}</b>\n"
        f"🎭 <b>Genre:</b> {meta.get('genres') or 'Series'} • ⭐ <b>{meta.get('rating') or 'HD'}</b>\n"
        f"📦 <b>Available Seasons:</b> <code>{', '.join(sorted_seasons)}</code>\n\n"
        f"👇 <i>Tap a Season button below to choose your Quality and receive all episodes!</i>"
    )

    token = BOT_POOL[0]["token"]
    try:
        if existing_msg_id and force_resend:
            try:
                await client.delete_messages(index_ch, [existing_msg_id])
            except Exception:
                pass
            existing_msg_id = 0

        async with aiohttp.ClientSession() as session:
            if existing_msg_id:
                edit_url = f"https://api.telegram.org/bot{token}/editMessageText"
                payload = {"chat_id": index_ch, "message_id": existing_msg_id, "text": card_text, "parse_mode": "HTML", "reply_markup": json.dumps(markup)}
                async with session.post(edit_url, data=payload, timeout=8) as r:
                    res_d = await r.json()
                    if res_d.get("ok") or "not modified" in (res_d.get("description") or "").lower():
                        conn.execute("INSERT OR REPLACE INTO series_index_cards (slug_key, clean_title, public_title, index_channel_id, index_message_id, seasons_json) VALUES (?, ?, ?, ?, ?, ?)", (slug, clean_t, pub_title, index_ch, existing_msg_id, json.dumps(sorted_seasons)))
                        conn.commit()
                        conn.close()
                        return

            send_url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {"chat_id": index_ch, "text": card_text, "parse_mode": "HTML", "reply_markup": json.dumps(markup)}
            async with session.post(send_url, data=payload, timeout=8) as r:
                d = await r.json()
                if d.get("ok"):
                    msg_id = d["result"]["message_id"]
                    conn.execute("INSERT OR REPLACE INTO series_index_cards (slug_key, clean_title, public_title, index_channel_id, index_message_id, seasons_json) VALUES (?, ?, ?, ?, ?, ?)", (slug, clean_t, pub_title, index_ch, msg_id, json.dumps(sorted_seasons)))
                    conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

async def rebuild_series_index_channel() -> int:
    await sync_created_channels_from_dialogs()
    ch_map = load_all_channels_from_db()
    index_ch = SERIES_INDEX_CHANNEL or ch_map.get("series_index", 0)
    if not index_ch or index_ch == DEFAULT_CHANNELS["other"]:
        return 0

    old_ids = []
    try:
        async for m in client.iter_messages(index_ch, limit=500):
            old_ids.append(m.id)
            if len(old_ids) >= 100:
                await client.delete_messages(index_ch, old_ids)
                old_ids.clear()
                await asyncio.sleep(0.5)
        if old_ids:
            await client.delete_messages(index_ch, old_ids)
    except Exception:
        pass

    conn = sqlite3.connect("storage_index.db")
    conn.execute("DELETE FROM series_index_cards")
    conn.commit()
    conn.close()

    series_map = {}
    for src_key in ("series", "anime"):
        cid = ch_map.get(src_key, DEFAULT_CHANNELS[src_key])
        try:
            async for m in client.iter_messages(cid, limit=300):
                if not (m.video or m.document):
                    continue
                fn = (m.file.name if m.file else "") or ""
                txt = f"{fn}\n{m.text or ''}"
                real_fn = extract_real_filename(fn, m.text or "")
                ep_tag = extract_season_ep_tag(txt, "S01")
                if ep_tag == "MOVIE":
                    continue
                res_tag = extract_resolution_tag(txt, "1080P")
                clean_t = normalize_title_key(real_fn)
                if clean_t == "unknown":
                    continue
                record_indexed_file(cid, m.id, clean_t, ep_tag, res_tag, res_tag, 2, real_fn)
                slug = make_slug_key(clean_t)
                if slug not in series_map:
                    series_map[slug] = (clean_t, set())
                series_map[slug][1].add(ep_tag)
        except Exception:
            pass

    sent_cards = 0
    for slug, (clean_t, ep_tags) in series_map.items():
        meta = await fetch_meta(clean_t)
        sample_ep = sorted(list(ep_tags))[0]
        await update_series_index_card(meta, sample_ep, force_resend=True)
        sent_cards += 1
        await asyncio.sleep(1.2)
    return sent_cards

# ============================================================================
# 6. 3-BOT INTERACTIVE QUALITY PICKER (360P / 480P / 720P / 1080P) & DELIVERY
# ============================================================================
async def recover_season_messages_from_channels(slug: str, s_code: str) -> list:
    conn = sqlite3.connect("storage_index.db")
    row = conn.execute("SELECT public_title, clean_title FROM series_index_cards WHERE slug_key=?", (slug,)).fetchone()
    conn.close()
    search_term = row[0] if row else slug
    ch_map = load_all_channels_from_db()
    target_chs = list({ch_map.get("series", DEFAULT_CHANNELS["series"]), ch_map.get("anime", DEFAULT_CHANNELS["anime"])})
    found = []
    for cid in target_chs:
        try:
            async for m in client.iter_messages(cid, search=search_term, limit=60):
                if not (m.video or m.document):
                    continue
                txt = f"{m.file.name if m.file else ''}\n{m.text or ''}"
                ep_tag = extract_season_ep_tag(txt, s_code)
                if ep_tag.startswith(s_code.upper()):
                    res_tag = extract_resolution_tag(txt, "1080P")
                    record_indexed_file(cid, m.id, search_term, ep_tag, res_tag, res_tag, 2, "recovered.mkv")
                    found.append((cid, m.id, ep_tag, res_tag))
        except Exception:
            pass
    found.sort(key=lambda x: x[2])
    return found

async def poll_single_delivery_bot(bot_obj: dict):
    token = bot_obj["token"]
    offset = 0
    async with aiohttp.ClientSession() as session:
        try:
            await session.get(f"https://api.telegram.org/bot{token}/deleteWebhook?drop_pending_updates=false", timeout=8)
        except Exception:
            pass
        while True:
            try:
                url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=20"
                async with session.get(url, timeout=25) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(4)
                        continue
                    data = await resp.json()
                    for upd in (data.get("result") or []):
                        offset = upd["update_id"] + 1
                        cb = upd.get("callback_query")
                        if cb:
                            cb_id = cb.get("id")
                            cb_data = cb.get("data") or ""
                            chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
                            await session.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery", data={"callback_query_id": cb_id, "text": "📥 Sending episodes..."})
                            m_cb = re.match(r"^dlq_([a-z0-9]+)_(S\d{2})_([A-Z0-9]+)$", cb_data, re.I)
                            if m_cb and chat_id:
                                slug, s_code, q_choice = m_cb.group(1).lower(), m_cb.group(2).upper(), m_cb.group(3).upper()
                                rows = get_season_messages_for_slug(slug, s_code, res_filter=q_choice) or get_season_messages_for_slug(slug, s_code, res_filter="ALL")
                                await session.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": f"📺 <b>Delivering {len(rows)} episode(s) for {s_code} ({q_choice})...</b>", "parse_mode": "HTML"})
                                for src_ch, src_msg_id, _, _ in rows:
                                    await session.post(f"https://api.telegram.org/bot{token}/copyMessage", data={"chat_id": chat_id, "from_chat_id": src_ch, "message_id": src_msg_id, "reply_markup": json.dumps(GREEN_BOT_API_MARKUP)})
                                    await asyncio.sleep(0.5)
                            continue

                        msg = upd.get("message") or {}
                        text = (msg.get("text") or "").strip()
                        chat_id = (msg.get("chat") or {}).get("id")
                        if not chat_id or not text.startswith("/start"):
                            continue

                        m = re.match(r"^/start\s+season_([a-z0-9]+)_(S\d{2})", text, re.I)
                        if not m:
                            await session.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": "🎬 <b>Welcome!</b> Tap any Season button in our Series Index Channel to choose your quality and receive episodes.", "parse_mode": "HTML"})
                            continue

                        slug, s_code = m.group(1).lower(), m.group(2).upper()
                        rows = get_season_messages_for_slug(slug, s_code, res_filter="ALL") or await recover_season_messages_from_channels(slug, s_code)
                        if not rows:
                            await session.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": f"⏳ Indexing episodes for <b>{s_code}</b> — please tap again in 1 minute!", "parse_mode": "HTML"})
                            continue

                        avail_quals = get_available_season_qualities(slug, s_code) or ["1080P", "720P", "480P", "360P"]
                        q_buttons, q_row = [], []
                        for q_tag in ["360P", "480P", "720P", "1080P"]:
                            badge = "✅" if q_tag in avail_quals else "🎞️"
                            q_row.append({"text": f"{badge} {q_tag}", "callback_data": f"dlq_{slug}_{s_code}_{q_tag}", "style": "primary"})
                            if len(q_row) == 2:
                                q_buttons.append(q_row)
                                q_row = []
                        if q_row:
                            q_buttons.append(q_row)
                        q_buttons.append([{"text": f"📦 Send All Available ({len(rows)} Files)", "callback_data": f"dlq_{slug}_{s_code}_ALL", "style": "success"}])

                        await session.post(
                            f"https://api.telegram.org/bot{token}/sendMessage",
                            data={
                                "chat_id": chat_id,
                                "text": f"📺 <b>Season {s_code[1:]} Quality Selector</b>\n✨ <b>Available Qualities:</b> <code>{', '.join(avail_quals)}</code>\n\n👇 <i>Select your preferred video resolution below:</i>",
                                "parse_mode": "HTML",
                                "reply_markup": json.dumps({"inline_keyboard": q_buttons})
                            }
                        )
            except Exception:
                await asyncio.sleep(4)

async def init_all_delivery_bots():
    global BOT_POOL
    roles = ["series", "movies", "backup"]
    async with aiohttp.ClientSession() as session:
        for idx, tok in enumerate(RAW_BOT_TOKENS):
            try:
                async with session.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=8) as r:
                    d = await r.json()
                    if d.get("ok"):
                        uname = d["result"].get("username", "")
                        role = roles[idx] if idx < len(roles) else "backup"
                        b_obj = {"token": tok, "username": uname, "role": role}
                        BOT_POOL.append(b_obj)
                        asyncio.create_task(poll_single_delivery_bot(b_obj))
            except Exception:
                pass
    for cid in set(load_all_channels_from_db().values()):
        await promote_all_bots_in_channel(cid)

# ============================================================================
# 7. WIPE & SELF-REUPLOAD CHANNEL ENGINE + WEBSITE RESYNC
# ============================================================================
async def wipe_and_reupload_storage_channel(target_channel_str: str, redownload_from_bots: bool = False) -> str:
    await sync_created_channels_from_dialogs()
    matches = await resolve_account_channel_target(target_channel_str)
    if not matches:
        return f"⚠️ Could not find any storage channel matching <code>{target_channel_str}</code>."
    _, cid, full_name = matches[0]

    collected_msgs, titles_to_redownload = [], set()
    conn = sqlite3.connect("storage_index.db")
    for r in conn.execute("SELECT DISTINCT clean_title FROM indexed_files_v4 WHERE channel_id=?", (cid,)).fetchall():
        if r[0] and r[0] != "unknown":
            titles_to_redownload.add(r[0])

    try:
        async for m in client.iter_messages(cid, limit=500):
            if m.video or m.document:
                fn = (m.file.name if m.file else "") or ""
                txt = m.text or ""
                t_key = normalize_title_key(extract_real_filename(fn, txt))
                if t_key != "unknown":
                    titles_to_redownload.add(t_key)
                collected_msgs.append(m)
            else:
                try:
                    await client.delete_messages(cid, [m.id])
                except Exception:
                    pass
    except Exception as e:
        conn.close()
        return f"⚠️ Error scanning channel <code>{full_name}</code>: <code>{e}</code>"

    conn.execute("DELETE FROM indexed_files_v4 WHERE channel_id=?", (cid,))
    conn.commit()
    conn.close()

    if redownload_from_bots or not collected_msgs:
        del_ids = [m.id for m in collected_msgs]
        for i in range(0, len(del_ids), 100):
            try:
                await client.delete_messages(cid, del_ids[i:i+100])
                await asyncio.sleep(0.5)
            except Exception:
                pass
        for t_name in sorted(list(titles_to_redownload)):
            TITLE_QUEUE.append(({"raw": t_name.title(), "title": t_name, "year": None, "mode": "normal", "season": None, "episodes": None}, True))
        if titles_to_redownload:
            asyncio.create_task(process_queue())
        return f"🗑️ <b>Wiped <code>{full_name}</code> ({len(del_ids)} posts) & Auto-Queued <code>{len(titles_to_redownload)}</code> Title(s)!</b>"

    collected_msgs.reverse()
    reuploaded = 0
    for m in collected_msgs:
        try:
            fn = (m.file.name if m.file else "") or ""
            txt = m.text or ""
            real_fn = extract_real_filename(fn, txt)
            yr = extract_year_from_text(real_fn)
            meta = await fetch_meta(real_fn, yr)
            if meta.get("title") == "unknown":
                continue
            ep_tag = extract_season_ep_tag(f"{real_fn}\n{txt}", "S01")
            res_tag = extract_resolution_tag(f"{real_fn}\n{txt}", "1080P")
            aud_tier = extract_audio_tier(f"{real_fn}\n{txt}")
            v_key = extract_movie_variant_key(f"{real_fn}\n{txt}", res_tag, aud_tier) if ep_tag == "MOVIE" else res_tag
            sz = m.file.size if m.file else 0
            new_cap = build_final_caption(fn or real_fn, txt, meta, sz, ep_tag)

            old_id = m.id
            new_sent = await publish_file_with_green_buttons(cid, m.media, new_cap, is_series=(ep_tag != "MOVIE"))
            if new_sent:
                await client.delete_messages(cid, [old_id])
                record_indexed_file(cid, new_sent.id, meta["public_title"], ep_tag, res_tag, v_key, aud_tier, real_fn)
                if ep_tag != "MOVIE":
                    await update_series_index_card(meta, ep_tag)
                reuploaded += 1
                await asyncio.sleep(2.5)
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds + 2)
        except Exception:
            pass
    return f"♻️ <b>Channel Wipe & Re-Upload Complete for <code>{full_name}</code>:</b> Re-uploaded <code>{reuploaded}</code> file(s) cleanly!"

async def resync_files_for_website(target_query: str = None, max_items: int = 8) -> int:
    await sync_created_channels_from_dialogs()
    ch_map = load_all_channels_from_db()
    clean_q = normalize_title_key(target_query) if target_query else None
    seen_groups, resynced = set(), 0

    scan_channels = list(dict.fromkeys([ch_map["series"], ch_map["anime"], ch_map["english"], ch_map["action"], ch_map["horror"], ch_map["korean"], ch_map["hindi"]] + list(ch_map.values())))
    for cid in scan_channels:
        if resynced >= max_items:
            break
        try:
            iter_kwargs = {"limit": 50}
            if clean_q and clean_q != "unknown":
                iter_kwargs["search"] = target_query
            async for m in client.iter_messages(cid, **iter_kwargs):
                if resynced >= max_items:
                    break
                if not (m.video or m.document) or (m.file and (m.file.size or 0) < MIN_VIDEO_SIZE_BYTES):
                    continue
                fn = (m.file.name if m.file else "") or ""
                txt = m.text or ""
                real_fn = extract_real_filename(fn, txt)
                t_key = normalize_title_key(real_fn)
                if t_key == "unknown" or (clean_q and clean_q not in t_key and t_key not in clean_q):
                    continue

                ep_tag = extract_season_ep_tag(f"{real_fn}\n{txt}", "S01")
                grp_key = f"{t_key}:{ep_tag[:3] if ep_tag != 'MOVIE' else 'MOVIE'}"
                if not target_query and grp_key in seen_groups:
                    continue
                seen_groups.add(grp_key)

                yr = extract_year_from_text(real_fn)
                meta = await fetch_meta(real_fn, yr)
                if meta.get("title") == "unknown":
                    continue
                correct_cat = detect_category(real_fn, txt, meta)
                dest_cid = await get_or_create_genre_channel(correct_cat)
                res_tag = extract_resolution_tag(f"{real_fn}\n{txt}", "1080P")
                aud_tier = extract_audio_tier(f"{real_fn}\n{txt}")
                v_key = extract_movie_variant_key(f"{real_fn}\n{txt}", res_tag, aud_tier) if ep_tag == "MOVIE" else res_tag
                sz = m.file.size if m.file else 0
                new_cap = build_final_caption(fn or real_fn, txt, meta, sz, ep_tag)

                new_sent = await publish_file_with_green_buttons(dest_cid, m.media, new_cap, is_series=(ep_tag != "MOVIE"))
                if new_sent:
                    await client.delete_messages(cid, [m.id])
                    record_indexed_file(dest_cid, new_sent.id, meta["public_title"], ep_tag, res_tag, v_key, aud_tier, real_fn)
                    if ep_tag != "MOVIE":
                        await update_series_index_card(meta, ep_tag, force_resend=True)
                    resynced += 1
                    await asyncio.sleep(2.5)
        except Exception:
            pass
    return resynced

async def rename_and_readjust_all_channels() -> int:
    await sync_created_channels_from_dialogs()
    fixed_count = 0
    all_chs = sorted(list(set(load_all_channels_from_db().values())))
    index_ch = SERIES_INDEX_CHANNEL or load_all_channels_from_db().get("series_index", 0)

    for ch_id in all_chs:
        if ch_id == index_ch:
            continue
        try:
            async for msg in client.iter_messages(ch_id, limit=45):
                if not (msg.video or msg.document) or (msg.file and (msg.file.size or 0) < MIN_VIDEO_SIZE_BYTES):
                    continue
                fn = (msg.file.name if msg.file else "") or ""
                txt = msg.text or ""
                real_fn = extract_real_filename(fn, txt)
                if normalize_title_key(real_fn) == "unknown":
                    continue
                yr = extract_year_from_text(real_fn)
                meta = await fetch_meta(real_fn, yr)
                pub_title = meta.get("public_title") or ""
                correct_cat = detect_category(real_fn, txt, meta)
                correct_ch = await get_or_create_genre_channel(correct_cat)

                if ch_id != correct_ch or "🎬" not in txt or (pub_title and pub_title.lower() not in txt.lower()):
                    ep_tag = extract_season_ep_tag(f"{real_fn}\n{txt}", "S01")
                    res_tag = extract_resolution_tag(f"{real_fn}\n{txt}", "1080P")
                    aud_tier = extract_audio_tier(f"{real_fn}\n{txt}")
                    v_key = extract_movie_variant_key(f"{real_fn}\n{txt}", res_tag, aud_tier) if ep_tag == "MOVIE" else res_tag
                    sz = msg.file.size if msg.file else 0
                    new_cap = build_final_caption(fn or real_fn, txt, meta, sz, ep_tag)
                    sent = await publish_file_with_green_buttons(correct_ch, msg.media, new_cap, is_series=(ep_tag != "MOVIE"))
                    if sent:
                        await client.delete_messages(ch_id, [msg.id])
                        record_indexed_file(correct_ch, sent.id, meta["public_title"], ep_tag, res_tag, v_key, aud_tier, real_fn)
                        fixed_count += 1
                        await asyncio.sleep(2.5)
        except Exception:
            pass
    return fixed_count

async def web_pulse_background_worker():
    await asyncio.sleep(90)
    while True:
        try:
            if WEB_PULSE_ENABLED and not CURRENT_PIPELINE_ACTIVE:
                await resync_files_for_website(max_items=1)
        except Exception:
            pass
        await asyncio.sleep(2700)

# ============================================================================
# 8. BUTTON SCORING & PAGINATION HELPERS
# ============================================================================
def score_quality_button(text: str) -> int:
    t = text.upper()
    score = 1200 if ("2160P" in t or "4K" in t) else (1000 if "1080P" in t else (720 if "720P" in t else (400 if "480P" in t else (200 if "360P" in t else 0))))
    if any(k in t for k in ["MULTI", "DUAL", "DUB", "HIN", "ENG", "ATMOS", "DDP5.1"]):
        score += 450
    if "BLURAY" in t or "BD" in t:
        score += 200
    elif "WEB-DL" in t:
        score += 150
    if "HEVC" in t or "X265" in t or "10BIT" in t or "AV1" in t:
        score += 60
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
            if btn_key in visited_keys or txt in visited_keys or not is_strict_title_and_year_match(txt, query, target_year):
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
            if any(skip in txt.lower() for skip in SKIP_NAV_WORDS) or txt in visited_qualities:
                continue
            s = score_quality_button(txt)
            if s > 0:
                res_list.append((s, extract_resolution_tag(txt, "OTHER"), txt, btn))
    res_list.sort(key=lambda x: x[0], reverse=True)
    return [(r, txt, btn) for _, r, txt, btn in res_list]

def parse_size_mb(text: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(GB|MB|KB)", text or "", re.I)
    if not m:
        return 0.0
    val, unit = float(m.group(1)), m.group(2).upper()
    return val * 1024.0 if unit == "GB" else (val / 1024.0 if unit == "KB" else val)

async def auto_join_channel(url: str):
    try:
        if "+" in url or "joinchat" in url:
            await client(ImportChatInviteRequest(url.split("+")[-1].split("/")[-1].strip()))
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
        return await trigger_start_url(btn.url, bot_entity) if "start=" in btn.url else False
    try:
        res = await asyncio.wait_for(btn.click(), timeout=2.5)
        res_url = getattr(res, "url", None)
        if res_url and "start=" in res_url:
            await trigger_start_url(res_url, bot_entity)
    except asyncio.TimeoutError:
        pass
    except FloodWaitError as fw:
        await asyncio.sleep(fw.seconds + 2)
    except Exception:
        pass
    return True

def get_next_page_button(msg):
    if not msg or not msg.buttons:
        return None
    for row in msg.buttons:
        for btn in row:
            if any(k in (btn.text or "").strip().lower() for k in ["next", "⏩", "➡", "⏭", "»"]):
                return btn
    return None

def get_menu_signature(msg) -> str:
    if not msg or not msg.buttons:
        return ""
    return f"{msg.id}:{msg.text or ''}:{'|'.join(b.text or '' for r in msg.buttons for b in r)}"

async def find_latest_menu_message(bot_entity, tracked_msg_id=None):
    if tracked_msg_id:
        msg = await client.get_messages(bot_entity, ids=tracked_msg_id)
        if msg and msg.buttons:
            return msg
    for m in await client.get_messages(bot_entity, limit=10):
        if not m or not m.buttons:
            continue
        btn_texts = [b.text or "" for r in m.buttons for b in r]
        if len(btn_texts) == 1 and any(x in btn_texts[0].lower() for x in ["auto-delete", "close", "subtitles"]):
            continue
        return m
    return None

async def wait_for_menu_update(bot_entity, tracked_msg_id, old_sig: str, max_wait: float = 4.0):
    for _ in range(int(max_wait / 0.4)):
        await asyncio.sleep(0.4)
        msg = await find_latest_menu_message(bot_entity, tracked_msg_id)
        if msg and get_menu_signature(msg) != old_sig:
            return msg
    return await find_latest_menu_message(bot_entity, tracked_msg_id)

def is_season_menu(msg) -> bool:
    if not msg or not msg.buttons:
        return False
    if "choose the season" in (msg.text or "").lower():
        return True
    return any(re.search(r"^(?:📺\s*)?(?:Season\s*\d+|S\d{1,2}\s*[·•\-]\s*\d+\s*eps?)", (b.text or "").strip(), re.I) for r in msg.buttons for b in r)

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
            if re.search(r"\b(?:S\d{1,2})?E\d{1,3}\b", bt, re.I) or (re.search(r"(?:\[|\·|\•)\s*\d+(?:\.\d+)?\s*(?:MB|GB)\b", bt, re.I) and "quality" not in bt.lower()):
                return True
    return False

# ============================================================================
# 9. INCOMING MEDIA EVENT (VERIFIED OFFICIAL PUBLIC TITLES & ZERO BROKEN IDS)
# ============================================================================
@client.on(events.NewMessage(incoming=True))
async def on_media_arrival(event):
    if not event.is_private:
        return
    sender = await event.get_sender()
    if not getattr(sender, "bot", False) or not (event.video or event.document):
        return

    msg = event.message
    file_size = msg.file.size if msg.file else 0
    raw_fn = (msg.file.name if msg.file else None) or (msg.text or "Media.mkv").split("\n")[0]
    if file_size < MIN_VIDEO_SIZE_BYTES or raw_fn.lower().endswith((".srt", ".sub", ".ass", ".zip", ".rar", ".txt")):
        return

    file_id = f"{msg.id}_{file_size}"
    if file_id in CAPTURED_FILES:
        return
    CAPTURED_FILES.add(file_id)

    caption = msg.text or ""
    real_fn = extract_real_filename(raw_fn, caption)
    combined_text = f"{real_fn}\n{caption}"

    if ACTIVE_CLEAN_TITLE and not is_strict_title_and_year_match(real_fn, ACTIVE_CLEAN_TITLE, ACTIVE_TARGET_YEAR):
        FILE_COMPLETED_EVENT.set()
        return

    file_year = ACTIVE_TARGET_YEAR or extract_year_from_text(real_fn)
    title_for_meta = ACTIVE_CLEAN_TITLE or normalize_title_key(real_fn)
    if not title_for_meta or title_for_meta == "unknown":
        FILE_COMPLETED_EVENT.set()
        return

    meta = await fetch_meta(title_for_meta, file_year)
    pub_key = normalize_title_key(meta.get("public_title") or title_for_meta)
    if pub_key == "unknown":
        FILE_COMPLETED_EVENT.set()
        return

    sp = extract_tech_specs(raw_fn, caption, file_size, meta)
    new_audio_tier = sp["audio_tier"]
    cat = detect_category(real_fn, caption, meta)
    dest_channel = await get_or_create_genre_channel(cat)
    cat_emoji = CATEGORY_EMOJIS.get(cat, "📁")

    ep_tag = ACTIVE_EP_TAG or extract_season_ep_tag(combined_text, ACTIVE_SEASON_TAG)
    res_tag = extract_resolution_tag(combined_text, ACTIVE_RES_TAG or "1080P")
    title_key = ACTIVE_CLEAN_TITLE or pub_key
    variant_key = extract_movie_variant_key(combined_text, res_tag, new_audio_tier) if ep_tag == "MOVIE" else res_tag

    existing = get_stored_file_record(pub_key, ep_tag, variant_key) or get_stored_file_record(title_key, ep_tag, variant_key)
    if existing:
        old_ch, old_msg_id, old_tier = existing
        if new_audio_tier <= (old_tier or 1) and old_ch == dest_channel:
            FILE_COMPLETED_EVENT.set()
            return
        elif old_ch and old_msg_id:
            try:
                await client.delete_messages(old_ch, [old_msg_id])
            except Exception:
                pass

    final_caption = build_final_caption(raw_fn, caption, meta, file_size, ep_tag)
    try:
        sent_msg = await publish_file_with_green_buttons(dest_channel, msg.media, final_caption, is_series=(ep_tag != "MOVIE"))
        sent_id = sent_msg.id if sent_msg else 0
        record_indexed_file(dest_channel, sent_id, title_key, ep_tag, res_tag, variant_key, new_audio_tier, real_fn)
        record_indexed_file(dest_channel, sent_id, pub_key, ep_tag, res_tag, variant_key, new_audio_tier, real_fn)

        if ep_tag != "MOVIE":
            await update_series_index_card(meta, ep_tag)

        await client.send_message("me", f"✅ <b>Published Official Name:</b> <code>{meta['public_title']}</code> [{ep_tag} • {variant_key}] ➔ {cat_emoji} <code>{cat.upper()}</code>", parse_mode="HTML")
    except FloodWaitError as fw:
        await asyncio.sleep(fw.seconds + 2)
    except Exception:
        pass
    finally:
        FILE_COMPLETED_EVENT.set()

# ============================================================================
# 10. DUAL-BOT DEEP MULTI-PAGE NAVIGATOR & DOWNLOADER (GENTLE 2.5S PACING)
# ============================================================================
async def navigate_to_grid(
    query: str, target_season_num: int = 1, target_res: str = None, target_year: int = None,
    send_fresh: bool = True, visited_title_btns: set = None, visited_quality_btns: set = None, bot_entity: str = None
):
    bot_entity = bot_entity or SOURCE_BOT
    visited_title_btns = visited_title_btns if visited_title_btns is not None else set()
    visited_quality_btns = visited_quality_btns if visited_quality_btns is not None else set()

    search_str = f"{query} {target_year}".strip() if target_year else query
    if send_fresh:
        await client.send_message(bot_entity, search_str)
        await asyncio.sleep(2.0)

    tracked_menu_id, available_seasons, discovered_resolutions = None, [], []
    retried_without_year, title_page_tries, quality_page_tries, empty_checks = False, 0, 0, 0

    for _ in range(36):
        if SKIP_CURRENT_TITLE:
            break
        msg = await find_latest_menu_message(bot_entity, tracked_menu_id)
        if not msg or not msg.buttons:
            if target_year and not retried_without_year:
                retried_without_year = True
                await client.send_message(bot_entity, query)
                await asyncio.sleep(2.0)
                continue
            empty_checks += 1
            if empty_checks >= 4:
                break
            await asyncio.sleep(0.8)
            continue
        empty_checks = 0
        tracked_menu_id = msg.id
        text = (msg.text or "").lower()
        old_sig = get_menu_signature(msg)

        joined = False
        for row in msg.buttons:
            for btn in row:
                if getattr(btn, "url", None) and "t.me/" in btn.url and any(w in (btn.text or "").lower() for w in ["join", "channel", "subscribe", "backup"]):
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

        if is_season_menu(msg):
            season_btns, fallback_btn = {}, None
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
            await smart_click_button(chosen_btn, bot_entity)
            await wait_for_menu_update(bot_entity, tracked_menu_id, old_sig)
            continue

        if is_file_or_episode_grid(msg):
            return msg, available_seasons, (discovered_resolutions or TARGET_RESOLUTIONS)

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
                    FILE_COMPLETED_EVENT.clear()
                    await smart_click_button(chosen_btn, bot_entity)
                    updated_msg = await wait_for_menu_update(bot_entity, tracked_menu_id, old_sig, max_wait=3.5)
                    if not updated_msg or get_menu_signature(updated_msg) == old_sig:
                        try:
                            await asyncio.wait_for(FILE_COMPLETED_EVENT.wait(), timeout=45)
                        except asyncio.TimeoutError:
                            pass
                        await asyncio.sleep(2.5)
                    continue
                else:
                    nxt_btn = get_next_page_button(msg)
                    if nxt_btn and title_page_tries < 6:
                        title_page_tries += 1
                        await smart_click_button(nxt_btn, bot_entity)
                        await wait_for_menu_update(bot_entity, tracked_menu_id, old_sig)
                        continue
                    return None, [], []

        if "select the available quality" in text or "quality" in text:
            res_options = collect_resolution_quality_buttons(msg.buttons, visited_quality_btns)
            for r_tag, _, _ in res_options:
                if r_tag not in discovered_resolutions:
                    discovered_resolutions.append(r_tag)
            chosen_qual_btn, chosen_qual_txt = None, ""
            if target_res:
                for r_tag, r_txt, r_btn in res_options:
                    if r_tag == target_res:
                        chosen_qual_btn, chosen_qual_txt = r_btn, r_txt
                        break
                if not chosen_qual_btn:
                    nxt_qual_btn = get_next_page_button(msg)
                    if nxt_qual_btn and quality_page_tries < 6:
                        quality_page_tries += 1
                        await smart_click_button(nxt_qual_btn, bot_entity)
                        await wait_for_menu_update(bot_entity, tracked_menu_id, old_sig)
                        continue
                    return None, available_seasons, discovered_resolutions
            else:
                for r_tag, r_txt, r_btn in res_options:
                    q_tier = extract_audio_tier(r_txt)
                    v_key = extract_movie_variant_key(r_txt, r_tag, q_tier)
                    if not is_already_in_any_channel(query, "MOVIE", v_key, min_audio_tier=q_tier):
                        chosen_qual_btn, chosen_qual_txt = r_btn, r_txt
                        break
                if not chosen_qual_btn and res_options:
                    chosen_qual_txt, chosen_qual_btn = res_options[0][1], res_options[0][2]
                if not chosen_qual_btn:
                    nxt_qual_btn = get_next_page_button(msg)
                    if nxt_qual_btn and quality_page_tries < 6:
                        quality_page_tries += 1
                        await smart_click_button(nxt_qual_btn, bot_entity)
                        await wait_for_menu_update(bot_entity, tracked_menu_id, old_sig)
                        continue
            if chosen_qual_btn:
                visited_quality_btns.add(chosen_qual_txt)
                await smart_click_button(chosen_qual_btn, bot_entity)
                await wait_for_menu_update(bot_entity, tracked_menu_id, old_sig)
                continue
            break
        await asyncio.sleep(0.8)
    return None, available_seasons, discovered_resolutions

async def download_from_grid(
    initial_msg, query: str, season_num: int, active_res: str,
    target_episodes: set = None, only_missing: bool = False, target_year: int = None, bot_entity: str = None
):
    global ACTIVE_SEASON_TAG, ACTIVE_EP_TAG, ACTIVE_RES_TAG
    bot_entity = bot_entity or SOURCE_BOT
    ACTIVE_SEASON_TAG, ACTIVE_RES_TAG = f"S{season_num:02d}", active_res or "1080P"
    menu_msg_id, current_msg, current_page = initial_msg.id, initial_msg, 1
    remaining_targets = set(target_episodes) if target_episodes else None

    while not SKIP_CURRENT_TITLE:
        best_item_map, item_order = {}, []
        next_button = get_next_page_button(current_msg)
        for row in current_msg.buttons:
            for btn in row:
                txt = (btn.text or "").strip()
                if any(nav in txt.lower() for nav in SKIP_NAV_WORDS):
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
                v_key = extract_movie_variant_key(txt, btn_res, btn_tier) if ep_tag == "MOVIE" else btn_res
                size_mb = parse_size_mb(txt) + (btn_tier * 250.0)
                item_key = (ep_tag, btn_res, v_key, ep_num)
                if item_key not in best_item_map:
                    item_order.append(item_key)
                    best_item_map[item_key] = (size_mb, btn, btn_tier)
                elif size_mb > best_item_map[item_key][0]:
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

            ACTIVE_EP_TAG, ACTIVE_RES_TAG = ep_tag, btn_res
            FILE_COMPLETED_EVENT.clear()
            await smart_click_button(btn, bot_entity)
            try:
                await asyncio.wait_for(FILE_COMPLETED_EVENT.wait(), timeout=85)
                if remaining_targets is not None and ep_num in remaining_targets:
                    remaining_targets.discard(ep_num)
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(2.5)

            check_msg = await client.get_messages(bot_entity, ids=menu_msg_id)
            if not check_msg or not check_msg.buttons:
                recovered_msg, _, _ = await navigate_to_grid(query, season_num, active_res, target_year, send_fresh=True, bot_entity=bot_entity)
                if recovered_msg:
                    menu_msg_id, current_msg = recovered_msg.id, recovered_msg
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
            old_sig = get_menu_signature(current_msg)
            await smart_click_button(next_button, bot_entity)
            current_msg = await wait_for_menu_update(bot_entity, menu_msg_id, old_sig)
            if not current_msg or not current_msg.buttons:
                break
        else:
            break

# ============================================================================
# 11. REAL-TIME RADAR, MULTI-INDUSTRY CRAWLER & DYNAMIC SCHEDULER
# ============================================================================
CRAWLER_MODES = ["movie", "anime", "animation", "bollywood", "korean", "turkish", "amharic", "horror", "action", "scifi", "crime", "chinese_donghua", "tv"]

async def fetch_realtime_live_releases() -> list:
    live_items, today_str, now_yr = [], current_utc_date_str(), current_utc_year()
    async with aiohttp.ClientSession() as session:
        if TMDB_API_KEY:
            for ep_path in [f"trending/all/day?api_key={TMDB_API_KEY}&language=en-US", f"movie/now_playing?api_key={TMDB_API_KEY}&language=en-US&page=1", f"tv/airing_today?api_key={TMDB_API_KEY}&language=en-US&page=1"]:
                async with TMDB_SEMAPHORE:
                    try:
                        async with session.get(f"https://api.themoviedb.org/3/{ep_path}", timeout=4) as r:
                            if r.status == 200:
                                for it in (await r.json()).get("results", []):
                                    name = it.get("title") or it.get("name")
                                    rel = it.get("release_date") or it.get("first_air_date") or ""
                                    yr = int(rel[:4]) if len(rel) >= 4 and rel[:4].isdigit() else now_yr
                                    if name:
                                        live_items.append((name, None if (it.get("media_type") == "tv" or "tv/" in ep_path) else yr))
                        await asyncio.sleep(0.2)
                    except Exception:
                        pass
        try:
            async with session.get("https://itunes.apple.com/us/rss/topmovies/limit=25/json", timeout=4) as r:
                if r.status == 200:
                    for ent in ((await r.json(content_type=None)).get("feed", {}).get("entry") or []):
                        name = (ent.get("im:name") or {}).get("label")
                        rel = ((ent.get("im:releaseDate") or {}).get("label") or "")[:4]
                        if name:
                            live_items.append((name, int(rel) if rel.isdigit() else now_yr))
        except Exception:
            pass
    return live_items

async def fetch_discover_batch(year: int, page: int, media_kind: str) -> list:
    discovered, today_str, now_yr = [], current_utc_date_str(), current_utc_year()
    async with aiohttp.ClientSession() as session:
        if TMDB_API_KEY and media_kind != "amharic":
            async with TMDB_SEMAPHORE:
                try:
                    endpoint = "tv" if media_kind in ("tv", "anime") else "movie"
                    yr_key = "first_air_date_year" if endpoint == "tv" else "primary_release_year"
                    date_lte = "first_air_date.lte" if endpoint == "tv" else "primary_release_date.lte"
                    date_filter = f"&{yr_key}={year}" + (f"&{date_lte}={today_str}" if year >= now_yr else "")
                    sort_p = "&sort_by=popularity.desc" if page == 1 else ("&sort_by=vote_average.desc&vote_count.gte=80" if page == 2 else "&sort_by=vote_average.desc&vote_count.gte=15")
                    extra_map = {
                        "anime": "&with_genres=16&with_original_language=ja", "animation": "&with_genres=16",
                        "bollywood": "&with_original_language=hi|ta|te|ml|kn", "korean": "&with_original_language=ko",
                        "turkish": "&with_original_language=tr", "chinese_donghua": "&with_original_language=zh",
                        "horror": "&with_genres=27|53", "action": "&with_genres=28|12",
                        "scifi": "&with_genres=878|14", "crime": "&with_genres=80|9648"
                    }
                    d_url = f"https://api.themoviedb.org/3/discover/{endpoint}?api_key={TMDB_API_KEY}&language=en-US{date_filter}{sort_p}{extra_map.get(media_kind, '')}&page={page}"
                    async with session.get(d_url, timeout=5) as dr:
                        if dr.status == 200:
                            for it in (await dr.json()).get("results", []):
                                name = it.get("title") or it.get("name")
                                if name:
                                    discovered.append((name, None if endpoint == "tv" else year))
                    await asyncio.sleep(0.2)
                except Exception:
                    pass
        if not discovered:
            try:
                ent = "tvSeason" if media_kind in ("tv", "anime") else "movie"
                async with session.get(f"https://itunes.apple.com/search?term={quote_plus(f'{media_kind} {year}')}&entity={ent}&limit=25", timeout=5) as ir:
                    if ir.status == 200:
                        for it in ((await ir.json(content_type=None)).get("results") or []):
                            name = it.get("trackName") or it.get("collectionName")
                            if name:
                                clean_n = re.sub(r"\s*\(?\bSeason\s*\d+.*$", "", name, flags=re.I).strip()
                                discovered.append((clean_n, None if ent == "tvSeason" else year))
            except Exception:
                pass
    return discovered

async def auto_discovery_crawler_worker():
    global LAST_REALTIME_RADAR_TS
    await asyncio.sleep(8.0)
    while True:
        try:
            if not AUTO_DISCOVERY_ENABLED or CURRENT_PIPELINE_ACTIVE or len(TITLE_QUEUE) > 0:
                await asyncio.sleep(5.0)
                continue
            now_ts = asyncio.get_event_loop().time()
            queued_one = False
            if (now_ts - LAST_REALTIME_RADAR_TS) >= 900:
                for title_name, yr in await fetch_realtime_live_releases():
                    clean_t = normalize_title_key(title_name)
                    if not clean_t or clean_t == "unknown" or is_title_crawled_recently(clean_t, hours=6):
                        continue
                    mark_title_crawled(clean_t, yr)
                    if len(get_existing_resolutions(clean_t, "MOVIE")) >= 4:
                        continue
                    TITLE_QUEUE.append(({"raw": f"{title_name} ({yr})" if yr else title_name, "title": title_name, "year": yr, "mode": "normal", "season": None, "episodes": None}, True))
                    asyncio.create_task(process_queue())
                    queued_one = True
                    break
                if not queued_one:
                    LAST_REALTIME_RADAR_TS = now_ts
            if queued_one:
                await asyncio.sleep(6.0)
                continue

            year, page, kind = get_crawler_progress()
            if kind not in CRAWLER_MODES:
                kind = "movie"
            for title_name, yr in await fetch_discover_batch(year, page, kind):
                clean_t = normalize_title_key(title_name)
                if not clean_t or clean_t == "unknown" or is_title_crawled_recently(clean_t, hours=12):
                    continue
                mark_title_crawled(clean_t, yr)
                if len(get_existing_resolutions(clean_t, "MOVIE")) >= 4:
                    continue
                TITLE_QUEUE.append(({"raw": f"{title_name} ({yr})" if yr else title_name, "title": title_name, "year": yr, "mode": "normal", "season": None, "episodes": None}, True))
                asyncio.create_task(process_queue())
                queued_one = True
                break
            if not queued_one:
                next_idx = (CRAWLER_MODES.index(kind) + 1) % len(CRAWLER_MODES)
                if next_idx != 0:
                    save_crawler_progress(year, page, CRAWLER_MODES[next_idx])
                elif page < 3:
                    save_crawler_progress(year, page + 1, "movie")
                else:
                    save_crawler_progress(year - 1 if year > 1990 else current_utc_year(), 1, "movie")
            await asyncio.sleep(6.0)
        except Exception:
            await asyncio.sleep(10.0)

async def dynamic_scheduler_worker():
    await asyncio.sleep(30)
    while True:
        try:
            conn = sqlite3.connect("storage_index.db")
            rows = conn.execute(
                "SELECT job_id, task_prompt, interval_minutes FROM scheduled_jobs WHERE active=1 AND (last_run IS NULL OR last_run <= datetime('now', '-' || interval_minutes || ' minutes'))"
            ).fetchall()
            for jid, t_prompt, iv in rows:
                conn.execute("UPDATE scheduled_jobs SET last_run=CURRENT_TIMESTAMP WHERE job_id=?", (jid,))
                conn.commit()
                if "website" in t_prompt.lower() or "sync" in t_prompt.lower():
                    rep = await inspect_website_intelligence()
                    await client.send_message("me", f"⏰ <b>Scheduled Task ({jid}):</b>\n{rep}", parse_mode="HTML")
                elif "duplicate" in t_prompt.lower() or "index" in t_prompt.lower():
                    cnt = await rebuild_series_index_channel()
                    await client.send_message("me", f"⏰ <b>Scheduled Index Check ({jid}):</b> Verified <code>{cnt}</code> Series Cards.", parse_mode="HTML")
            conn.close()
        except Exception:
            pass
        await asyncio.sleep(60)

# ============================================================================
# 12. DUAL-BOT FAILOVER QUEUE WORKER & REFILLER
# ============================================================================
async def sync_title_across_all_channels(query: str, target_year: int = None):
    clean_q = normalize_title_key(query)
    if not clean_q or clean_q == "unknown" or clean_q in SYNCED_TITLES:
        return
    SYNCED_TITLES.add(clean_q)
    meta = await fetch_meta(query, target_year)
    pub_clean = normalize_title_key(meta.get("public_title") or query)
    SYNCED_TITLES.add(pub_clean)

    correct_cat = detect_category(query, "", meta)
    correct_ch = await get_or_create_genre_channel(correct_cat)
    all_chs = sorted(list(set(load_all_channels_from_db().values())))
    channels_to_scan = [correct_ch] + [c for c in all_chs if c != correct_ch]

    for ch_id in channels_to_scan:
        try:
            async for msg in client.iter_messages(ch_id, search=meta.get("public_title") or clean_q, limit=30):
                if not (msg.video or msg.document) or (msg.file and (msg.file.size or 0) < MIN_VIDEO_SIZE_BYTES):
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
            await asyncio.sleep(0.15)
        except Exception:
            pass

async def run_extraction_on_bot(
    bot_entity: str, popular_title: str, raw_query: str, start_season: int,
    target_year: int = None, target_eps: set = None, only_missing: bool = False, send_initial: bool = True
) -> bool:
    global ACTIVE_CLEAN_TITLE
    visited_title_btns, visited_quality_btns = set(), set()
    before_res = get_existing_resolutions(ACTIVE_CLEAN_TITLE, "MOVIE")

    grid_msg, all_seasons, _ = await navigate_to_grid(
        popular_title, start_season, target_res=None, target_year=target_year,
        send_fresh=send_initial, visited_title_btns=visited_title_btns, visited_quality_btns=visited_quality_btns, bot_entity=bot_entity
    )
    if not grid_msg and not visited_title_btns and normalize_title_key(raw_query) != ACTIVE_CLEAN_TITLE:
        grid_msg, all_seasons, _ = await navigate_to_grid(
            raw_query, start_season, target_res=None, target_year=target_year,
            send_fresh=True, visited_title_btns=visited_title_btns, visited_quality_btns=visited_quality_btns, bot_entity=bot_entity
        )

    after_res = get_existing_resolutions(ACTIVE_CLEAN_TITLE, "MOVIE")
    if not grid_msg:
        return len(after_res) > len(before_res) or bool(visited_title_btns)

    if target_eps:
        await download_from_grid(grid_msg, popular_title, start_season, ACTIVE_RES_TAG or "1080P", target_episodes=target_eps, only_missing=only_missing, target_year=target_year, bot_entity=bot_entity)
        return True

    seasons_to_run = [start_season] if only_missing else (all_seasons if all_seasons else [start_season])
    first_season = seasons_to_run[0]
    await download_from_grid(grid_msg, popular_title, first_season, ACTIVE_RES_TAG or "1080P", only_missing=only_missing, target_year=target_year, bot_entity=bot_entity)

    for res_tag in TARGET_RESOLUTIONS:
        if SKIP_CURRENT_TITLE:
            break
        await asyncio.sleep(1.5)
        res_grid, _, _ = await navigate_to_grid(popular_title, first_season, target_res=res_tag, target_year=target_year, send_fresh=True, visited_quality_btns=visited_quality_btns, bot_entity=bot_entity)
        if res_grid and not SKIP_CURRENT_TITLE:
            await download_from_grid(res_grid, popular_title, first_season, res_tag, only_missing=only_missing, target_year=target_year, bot_entity=bot_entity)

    for next_s in seasons_to_run[1:]:
        if SKIP_CURRENT_TITLE or next_s <= first_season:
            continue
        s_visited_qual = set()
        for res_tag in TARGET_RESOLUTIONS:
            if SKIP_CURRENT_TITLE:
                break
            await asyncio.sleep(1.5)
            r_grid, _, _ = await navigate_to_grid(popular_title, next_s, target_res=res_tag, target_year=target_year, send_fresh=True, visited_quality_btns=s_visited_qual, bot_entity=bot_entity)
            if r_grid and not SKIP_CURRENT_TITLE:
                await download_from_grid(r_grid, popular_title, next_s, res_tag, only_missing=only_missing, target_year=target_year, bot_entity=bot_entity)
    return True

async def execute_missing_audit_and_fill(cmd: dict):
    title, target_year = cmd["title"], cmd.get("year")
    clean_t = normalize_title_key(title)
    await sync_title_across_all_channels(title, target_year)
    catalog = await fetch_official_catalog(title)
    existing_eps = get_indexed_episodes_for_title(clean_t)

    if not catalog["seasons"] and len(catalog["franchise_movies"]) > 1:
        missing_movies = [m for m in catalog["franchise_movies"] if not is_already_in_any_channel(m, "MOVIE", "ANY")]
        for m_title in reversed(missing_movies):
            TITLE_QUEUE.appendleft(({"raw": m_title, "title": m_title, "year": None, "mode": "normal", "season": None, "episodes": None}, True))
        return

    missing_by_season = {}
    for s_num, ep_list in sorted(catalog["seasons"].items()):
        if cmd["season"] and s_num != cmd["season"]:
            continue
        for e_num in ep_list:
            if f"S{s_num:02d}E{e_num:02d}" not in existing_eps:
                missing_by_season.setdefault(s_num, set()).add(e_num)

    if missing_by_season:
        for s_num, miss_eps in sorted(missing_by_season.items()):
            if SKIP_CURRENT_TITLE:
                break
            found = await run_extraction_on_bot(SOURCE_BOT, title, title, s_num, target_year, miss_eps, only_missing=True)
            if not found and BACKUP_BOT:
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
                query, target_year = cmd["title"], cmd.get("year")
                meta_info = await fetch_meta(query, target_year)
                popular_title = meta_info.get("public_title") or query

                ACTIVE_CLEAN_TITLE, ACTIVE_TARGET_YEAR, ACTIVE_EP_TAG, ACTIVE_RES_TAG = normalize_title_key(popular_title), target_year, "", ""
                if ACTIVE_CLEAN_TITLE == "unknown":
                    continue
                if cmd["mode"] == "missing":
                    await execute_missing_audit_and_fill(cmd)
                    continue

                await sync_title_across_all_channels(popular_title, target_year)
                if SKIP_CURRENT_TITLE:
                    continue

                catalog = await fetch_official_catalog(popular_title)
                if not catalog["seasons"] and len(catalog["franchise_movies"]) > 1:
                    for f_movie in catalog["franchise_movies"]:
                        if normalize_title_key(f_movie) != ACTIVE_CLEAN_TITLE and not is_already_in_any_channel(f_movie, "MOVIE", "ANY"):
                            if not is_title_crawled_recently(f_movie, hours=24):
                                mark_title_crawled(f_movie)
                                TITLE_QUEUE.append(({"raw": f_movie, "title": f_movie, "year": None, "mode": "normal", "season": None, "episodes": None}, True))

                start_season = cmd["season"] or 1
                target_eps = set(cmd["episodes"]) if cmd["episodes"] else None
                primary_bot = cmd.get("bot_entity") or SOURCE_BOT

                if cmd.get("manual"):
                    await client.send_message(
                        "me",
                        f"🔎 <b>Searching @{primary_bot} for:</b> <code>{popular_title}</code>...",
                        parse_mode="HTML"
                    )

                found_primary = await run_extraction_on_bot(
                    primary_bot, popular_title, query, start_season,
                    target_year=target_year, target_eps=target_eps, send_initial=send_initial
                )
                stored_res = get_existing_resolutions(ACTIVE_CLEAN_TITLE, "MOVIE")

                if BACKUP_BOT and primary_bot != BACKUP_BOT and not SKIP_CURRENT_TITLE:
                    if not found_primary or (not meta_info.get("is_series") and len(stored_res) < 3):
                        if cmd.get("manual"):
                            await client.send_message(
                                "me",
                                f"🔄 <b>Checking Backup Bot @{BACKUP_BOT} for:</b> <code>{popular_title}</code>...",
                                parse_mode="HTML"
                            )
                        await asyncio.sleep(2.0)
                        found_backup = await run_extraction_on_bot(
                            BACKUP_BOT, popular_title, query, start_season,
                            target_year=target_year, target_eps=target_eps, send_initial=True
                        )
                        if not found_primary and not found_backup and cmd.get("manual"):
                            await client.send_message(
                                "me",
                                f"⚠️ <b>Not Found:</b> <code>{popular_title}</code> returned no matching files on @{primary_bot} or @{BACKUP_BOT}.",
                                parse_mode="HTML"
                            )

                await client.send_message(
                    "me",
                    f"🏁 <b>Finished:</b> <code>{popular_title}</code>\n📋 <b>Remaining in Queue:</b> <code>{len(TITLE_QUEUE)}</code>",
                    parse_mode="HTML"
                )
                await asyncio.sleep(2.5)
            except Exception as item_err:
                print(f"⚠️ Queue item error: {item_err}")
                await asyncio.sleep(2.0)
    finally:
        CURRENT_PIPELINE_ACTIVE = False
        ACTIVE_TARGET_YEAR = None
        ACTIVE_EP_TAG = ""
        ACTIVE_RES_TAG = ""

def enqueue_commands_bulk(raw_text: str, send_initial: bool = True, bot_entity: str = None) -> list:
    global SKIP_CURRENT_TITLE
    raw_items = split_bulk_commands(raw_text)
    parsed_list = []
    for item in reversed(raw_items):
        cmd = parse_smart_user_command(item)
        cmd["manual"] = True
        if bot_entity:
            cmd["bot_entity"] = bot_entity
        if cmd["title"] and len(cmd["title"]) >= 2 and normalize_title_key(cmd["title"]) != "unknown":
            TITLE_QUEUE.appendleft((cmd, send_initial))
            parsed_list.append(cmd)
    if parsed_list:
        if CURRENT_PIPELINE_ACTIVE:
            SKIP_CURRENT_TITLE = True
            FILE_COMPLETED_EVENT.set()
        asyncio.create_task(process_queue())
    return list(reversed(parsed_list))

# ============================================================================
# 13. THE BACKROOM AI COMMANDER ORCHESTRATOR & SAVED MESSAGES
# ============================================================================
async def execute_ai_controller_action(ai_res: dict, event, raw_user_req: str = ""):
    global SKIP_CURRENT_TITLE, AUTO_DISCOVERY_ENABLED
    act = (ai_res.get("action") or "CHAT_ONLY").upper()
    risk = (ai_res.get("risk_level") or "LOW").upper()
    reply_msg = ai_res.get("reply") or "🤖 Done!"

    mem_obj = ai_res.get("memory_to_save")
    if isinstance(mem_obj, dict) and mem_obj.get("key") and mem_obj.get("value"):
        save_long_term_memory(mem_obj.get("category", "USER_PREFERENCE"), mem_obj["key"], mem_obj["value"])

    AI_CHAT_HISTORY.append(f"AI: {reply_msg}")
    log_audit_event(raw_user_req, act, risk, "EXECUTED")
    await event.reply(f"🧠 {reply_msg}", parse_mode="HTML")

    if act == "STOP_ALL":
        AUTO_DISCOVERY_ENABLED = False
        TITLE_QUEUE.clear()
        SKIP_CURRENT_TITLE = True
        FILE_COMPLETED_EVENT.set()

    elif act == "WIPE_AND_REUPLOAD_CHANNEL":
        ch_key = (ai_res.get("target_channel_key") or "").strip()
        if ch_key:
            res_txt = await wipe_and_reupload_storage_channel(ch_key, redownload_from_bots=False)
            await event.reply(res_txt, parse_mode="HTML")

    elif act == "MANAGE_RULE":
        r_status = manage_behavior_rule(ai_res.get("rule_op", "ADD"), ai_res.get("rule_text", ""), ai_res.get("rule_id"))
        await event.reply(f"📜 <b>Behavioral Rule Engine:</b> {r_status}", parse_mode="HTML")

    elif act == "SEARCH_ACCOUNT":
        s_q = ai_res.get("search_query") or ai_res.get("target_title") or ""
        if s_q:
            res_txt = await search_account_intelligence(s_q)
            await event.reply(f"🔍 <b>Account Intelligence Search ('{s_q}'):</b>\n{res_txt}", parse_mode="HTML")

    elif act == "SEARCH_CONTACTS":
        s_q = ai_res.get("search_query") or ""
        res_txt = await search_telegram_contacts(s_q)
        await event.reply(f"👤 <b>Contacts Search:</b>\n{res_txt}", parse_mode="HTML")

    elif act == "INSPECT_WEBSITE":
        rep = await inspect_website_intelligence(ai_res.get("target_title"))
        await event.reply(rep, parse_mode="HTML")

    elif act == "SELF_EVOLVE":
        c_name = ai_res.get("cap_name") or "custom_skill"
        c_desc = ai_res.get("cap_description") or "AI-evolved skill"
        c_code = ai_res.get("cap_code") or "def run():\n    return True\n"
        ev_msg = stage_or_register_capability(c_name, c_desc, c_code, auto_activate=False)
        await event.reply(ev_msg, parse_mode="HTML")

    elif act == "SCHEDULE_JOB":
        s_p = ai_res.get("schedule_prompt") or "website_sync"
        iv = int(ai_res.get("interval_minutes") or 30)
        jid = make_slug_key(s_p)
        conn = sqlite3.connect("storage_index.db")
        conn.execute("INSERT OR REPLACE INTO scheduled_jobs (job_id, task_prompt, interval_minutes, active) VALUES (?, ?, ?, 1)", (jid, s_p, iv))
        conn.commit()
        conn.close()
        await event.reply(f"⏰ <b>Scheduled Recurring Job:</b> <code>{s_p}</code> (Every <code>{iv}</code> mins)", parse_mode="HTML")

    elif act == "QUEUE_TITLES":
        tasks = ai_res.get("tasks") or []
        for t_obj in tasks:
            if isinstance(t_obj, dict) and t_obj.get("title"):
                TITLE_QUEUE.append((t_obj, True))
        if tasks:
            asyncio.create_task(process_queue())

    elif act == "DELETE_MEDIA":
        t_title = ai_res.get("target_title")
        if t_title:
            cnt = await delete_movie_or_series_everywhere(t_title)
            await event.reply(f"🗑️ <b>Deleted <code>{cnt}</code> file(s)/card(s) for:</b> <code>{t_title}</code>", parse_mode="HTML")

    elif act == "REUPLOAD_MEDIA":
        t_title = ai_res.get("target_title")
        cnt = await resync_files_for_website(t_title, max_items=8)
        await event.reply(f"♻️ <b>Re-uploaded <code>{cnt}</code> file(s) as brand-new with Official Public Name!</b>", parse_mode="HTML")

    elif act == "RENAME_ALL_PUBLIC":
        cnt = await rename_and_readjust_all_channels()
        await event.reply(f"✏️ <b>Renamed & Readjusted <code>{cnt}</code> file(s) to Official Public Names!</b>", parse_mode="HTML")

    elif act == "REBUILD_SERIES_INDEX":
        cnt = await rebuild_series_index_channel()
        await event.reply(f"✅ <b>Series Index Rebuilt:</b> Sent <code>{cnt}</code> fresh Series Master Cards!", parse_mode="HTML")

    elif act == "RENAME_CHANNEL":
        ch_key = ai_res.get("target_channel_key") or ""
        new_n = ai_res.get("new_name") or ""
        if ch_key and new_n:
            res_msg = await rename_telegram_channel_or_chat(ch_key, new_n)
            await event.reply(res_msg, parse_mode="HTML")

    elif act == "DELETE_CHANNEL":
        ch_key = (ai_res.get("target_channel_key") or "").strip()
        matches = await resolve_account_channel_target(ch_key)
        if len(matches) == 1:
            k_lbl, cid, _ = matches[0]
            try:
                await client(DeleteChannelRequest(channel=cid))
                conn = sqlite3.connect("storage_index.db")
                conn.execute("DELETE FROM dynamic_channels WHERE channel_id=?", (cid,))
                conn.commit()
                conn.close()
                await event.reply(f"🗑️ <b>Deleted Channel:</b> <code>{k_lbl}</code> (<code>{cid}</code>)", parse_mode="HTML")
            except Exception as e:
                await event.reply(f"⚠️ Delete error: <code>{e}</code>", parse_mode="HTML")

    elif act == "CLEAR_CHANNEL_MESSAGES":
        ch_key = (ai_res.get("target_channel_key") or "").strip()
        matches = await resolve_account_channel_target(ch_key)
        if matches:
            _, cid, c_name = matches[0]
            ids = [m.id async for m in client.iter_messages(cid, limit=300)]
            if ids:
                await client.delete_messages(cid, ids)
            await event.reply(f"🧹 <b>Cleared {len(ids)} messages in <code>{c_name}</code>!</b>", parse_mode="HTML")

    elif act == "CREATE_CHANNEL":
        ch_key = (ai_res.get("target_channel_key") or "custom").lower().strip()
        cid = await get_or_create_genre_channel(ch_key)
        await event.reply(f"🆕 <b>Channel Ready for <code>{ch_key.upper()}</code>:</b> <code>{cid}</code>", parse_mode="HTML")

    elif act == "CRAWLER_CONTROL":
        if ai_res.get("crawler_on") is not None:
            AUTO_DISCOVERY_ENABLED = bool(ai_res["crawler_on"])
        c_yr, _, c_kind = get_crawler_progress()
        if ai_res.get("crawler_year"):
            c_yr = int(ai_res["crawler_year"])
        if ai_res.get("crawler_mode"):
            c_kind = str(ai_res["crawler_mode"]).lower()
        save_crawler_progress(c_yr, 1, c_kind)

    elif act == "SKIP":
        SKIP_CURRENT_TITLE = True
        FILE_COMPLETED_EVENT.set()

    elif act == "CLEAR_QUEUE":
        TITLE_QUEUE.clear()

@client.on(events.NewMessage(outgoing=True))
async def on_outgoing_trigger(event):
    global SKIP_CURRENT_TITLE, AUTO_DISCOVERY_ENABLED, AUTO_CREATE_CHANNELS, WEB_PULSE_ENABLED, ME_USER_ID
    text = (event.message.text or "").strip()
    if not text or text.startswith(("/", "✅", "⏳", "🏁", "⚠️", "🔗", "📋", "🛑", "🔎", "🎬", "🤖", "⚡", "♻️", "🆕", "📖", "🌐", "🧠", "🗑️", "🧹", "✏️", "🔑", "📜", "🧬", "🔍", "⏰", "👤")):
        return

    if ME_USER_ID is None:
        ME_USER_ID = (await client.get_me()).id

    if event.chat_id == ME_USER_ID:
        low = text.lower()

        if low in (".help", "help", ".admin", "admin"):
            await event.reply(
                "📖 <b>THE BACKROOM AI COMMANDER — Full Guide</b>\n\n"
                "• <b>Natural Chat:</b> Talk to me like ChatGPT! Ask questions, teach rules (<i>\"From now on...\"</i>), stop tasks, or control channels.\n"
                "• <code>.repopulate channel_key</code> — Delete all posts in one storage channel & re-upload them fresh by itself\n"
                "• <code>.redownloadchannel channel_key</code> — Wipe channel & re-download all its titles from source bots\n"
                "• <code>.search Title</code> — Direct search movie/series/range/missing\n"
                "• <code>.auto on</code> / <code>.auto off</code> / <code>.auto 2025</code> / <code>.auto bollywood</code>\n"
                "• <code>.reupload Title</code> | <code>.delete Title</code> | <code>.renameall</code> | <code>.rebuildindex</code>\n"
                "• <code>.rules</code> | <code>.memory</code> | <code>.evolve</code> | <code>.approve</code> | <code>.webcheck</code>\n"
                "• <code>.delchannel name</code> | <code>.renamechat old | new</code> | <code>.createchannel all</code>\n"
                "• <code>.addgemini AIzaSy...</code> | <code>.status</code> | <code>.channels</code> | <code>.bots</code> | <code>.skip</code> | <code>.clear</code>",
                parse_mode="HTML"
            )
            return

        if low in (".approve", "approve"):
            conn = sqlite3.connect("storage_index.db")
            conn.execute("UPDATE custom_capabilities SET active=1 WHERE active=0")
            conn.execute("UPDATE evolution_history SET status='APPROVED_ACTIVE' WHERE status='WAITING_FOR_APPROVAL'")
            conn.commit()
            conn.close()
            await event.reply("✅ <b>Approved & Activated pending AI Commander skill update(s)!</b>", parse_mode="HTML")
            return

        if low.startswith((".repopulate ", ".wipeandreupload ")):
            target_ch = text.split(maxsplit=1)[1].strip()
            await event.reply(f"♻️ <b>Wiping old posts in <code>{target_ch}</code> & re-uploading every file fresh with thebackroom.lol captions...</b>", parse_mode="HTML")
            res_msg = await wipe_and_reupload_storage_channel(target_ch, redownload_from_bots=False)
            await event.reply(res_msg, parse_mode="HTML")
            return

        if low.startswith(".redownloadchannel "):
            target_ch = text.split(maxsplit=1)[1].strip()
            await event.reply(f"🗑️ <b>Wiping <code>{target_ch}</code> completely & queueing all its titles for fresh bot extraction...</b>", parse_mode="HTML")
            res_msg = await wipe_and_reupload_storage_channel(target_ch, redownload_from_bots=True)
            await event.reply(res_msg, parse_mode="HTML")
            return

        if low in (".rules", "rules"):
            rules = get_active_behavior_rules()
            r_txt = "\n".join(f"• <b>#{r[0]} (v{r[3]}):</b> {r[1]}" for r in rules) or "<i>(No permanent rules yet — tell me 'From now on...' to add one!)</i>"
            await event.reply(f"📜 <b>Active Permanent Behavioral Rules:</b>\n{r_txt}", parse_mode="HTML")
            return

        if low in (".memory", "memory"):
            mems = retrieve_relevant_memories(limit=15)
            m_txt = "\n".join(f"• <b>[{m[0]}] {m[1]}:</b> <code>{m[2]}</code>" for m in mems) or "<i>(No stored memories yet)</i>"
            await event.reply(f"🧠 <b>Long-Term Memory Store:</b>\n{m_txt}", parse_mode="HTML")
            return

        if low in (".evolve", "evolve", ".skills", "skills"):
            conn = sqlite3.connect("storage_index.db")
            rows = conn.execute("SELECT change_type, summary, backup_path, status FROM evolution_history ORDER BY id DESC LIMIT 10").fetchall()
            conn.close()
            e_txt = "\n".join(f"• <b>[{r[3]}]</b> <code>{r[1]}</code> (Backup: <code>{r[2]}</code>)" for r in rows) or "<i>(No self-evolution updates yet)</i>"
            await event.reply(f"🧬 <b>Self-Evolution & Skill History:</b>\n{e_txt}", parse_mode="HTML")
            return

        if low.startswith(".webcheck"):
            parts = text.split(maxsplit=1)
            q_w = parts[1].strip() if len(parts) > 1 else None
            rep = await inspect_website_intelligence(q_w)
            await event.reply(rep, parse_mode="HTML")
            return

        if low.startswith((".search ", ".get ", ".s ")):
            added = enqueue_commands_bulk(text, send_initial=True)
            if added:
                summary_lines = [f"• 🎬 <code>{c['title']}</code> ({c['mode']})" for c in added[:10]]
                await event.reply(f"⏳ <b>Queued {len(added)} Search Task(s):</b>\n" + "\n".join(summary_lines), parse_mode="HTML")
            return

        if low.startswith(".auto") or low.startswith("auto "):
            if "off" in low:
                AUTO_DISCOVERY_ENABLED = False
                await event.reply("🤖 <b>24/7 Auto-Crawler Paused (OFF 🔴).</b> Send <code>.auto on</code> to resume.", parse_mode="HTML")
                return
            elif "on" in low:
                AUTO_DISCOVERY_ENABLED = True
                await event.reply("🤖 <b>24/7 Auto-Crawler Resumed (ON 🟢)!</b>", parse_mode="HTML")
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

        if low.startswith((".delete ", ".rm ")):
            q_del = text.split(maxsplit=1)[1].strip()
            await event.reply(f"🗑️ <b>Deleting all files & index cards for:</b> <code>{q_del}</code>...", parse_mode="HTML")
            cnt = await delete_movie_or_series_everywhere(q_del)
            await event.reply(f"✅ <b>Deleted <code>{cnt}</code> post(s) for:</b> <code>{q_del}</code>", parse_mode="HTML")
            return

        if low.startswith((".reupload", ".resync")):
            parts = text.split(maxsplit=1)
            q_str = parts[1].strip() if len(parts) > 1 else None
            await event.reply(f"♻️ <b>Deleting old copy & re-uploading as brand-new with Official Public Name ({q_str or 'Batch'})...</b>", parse_mode="HTML")
            cnt = await resync_files_for_website(q_str, max_items=8)
            await event.reply(f"✅ <b>Re-Upload Complete:</b> Published <code>{cnt}</code> fresh item(s)!", parse_mode="HTML")
            return

        if low in (".renameall", "renameall", ".readjust", "readjust"):
            await event.reply("✏️ <b>Renaming all channel files to Official Public Names & moving to Genre Vaults...</b>", parse_mode="HTML")
            fixed = await rename_and_readjust_all_channels()
            await event.reply(f"✅ <b>Rename & Readjust Complete:</b> Updated <code>{fixed}</code> file(s)!", parse_mode="HTML")
            return

        if low.startswith(".delchannel"):
            parts = text.split(maxsplit=1)
            target_ch = parts[1].strip() if len(parts) > 1 else ""
            matches = await resolve_account_channel_target(target_ch)
            if not matches:
                await event.reply(f"⚠️ <b>No channel found matching:</b> <code>{target_ch}</code>", parse_mode="HTML")
            elif len(matches) > 1:
                opts = "\n".join(f"• <code>.delchannel {cid}</code> ({name})" for _, cid, name in matches[:8])
                await event.reply(f"⚠️ <b>Multiple channels match '{target_ch}'. Which one should I delete?</b>\n{opts}", parse_mode="HTML")
            else:
                _, cid, c_name = matches[0]
                try:
                    await client(DeleteChannelRequest(channel=cid))
                    conn = sqlite3.connect("storage_index.db")
                    conn.execute("DELETE FROM dynamic_channels WHERE channel_id=?", (cid,))
                    conn.commit()
                    conn.close()
                    await event.reply(f"🗑️ <b>Deleted Channel:</b> <code>{c_name}</code> (<code>{cid}</code>)", parse_mode="HTML")
                except Exception as e:
                    await event.reply(f"⚠️ Delete channel error: <code>{e}</code>", parse_mode="HTML")
            return

        if low.startswith(".renamechat"):
            body = text.split(maxsplit=1)[1] if len(text.split()) > 1 else ""
            if "|" in body:
                old_t, new_t = [x.strip() for x in body.split("|", 1)]
                res_msg = await rename_telegram_channel_or_chat(old_t, new_t)
                await event.reply(res_msg, parse_mode="HTML")
            else:
                await event.reply("⚠️ <b>Format:</b> <code>.renamechat scifi | Vault • Sci-Fi Movies</code>", parse_mode="HTML")
            return

        if low in (".rebuildindex", "rebuildindex", ".fixindex", "fixindex"):
            await event.reply("🧹 <b>Clearing Series Index Channel & sending 1 fresh Master Card per Series...</b>", parse_mode="HTML")
            cnt = await rebuild_series_index_channel()
            await event.reply(f"✅ <b>Series Index Rebuilt:</b> Sent <code>{cnt}</code> clean Series Master Card(s)!", parse_mode="HTML")
            return

        if low.startswith(".addgemini"):
            k_str = text.split(maxsplit=1)[1] if len(text.split()) > 1 else ""
            total_k = add_backup_gemini_key(k_str)
            DISCOVERED_GEMINI_MODELS.clear()
            await event.reply(f"🔑 <b>Gemini Key Pool Updated!</b> Total active keys: <code>{total_k}</code>", parse_mode="HTML")
            return

        if low.startswith(".createchannel"):
            parts = low.split(maxsplit=1)
            cat_k = parts[1].strip() if len(parts) > 1 else "series_index"
            if cat_k == "all":
                await event.reply("🆕 <b>Creating all detailed genre channels...</b>", parse_mode="HTML")
                created = []
                for gk in DETAILED_GENRE_TITLES.keys():
                    if count_unique_storage_channels() >= MAX_STORAGE_CHANNELS:
                        break
                    cid = await get_or_create_genre_channel(gk)
                    created.append(f"{gk.upper()}: {cid}")
                    await asyncio.sleep(3.0)
                await event.reply("✅ <b>Channels Ready:</b>\n" + "\n".join(created), parse_mode="HTML")
                return
            cid = await get_or_create_genre_channel(cat_k)
            await event.reply(f"🆕 <b>Channel Ready for <code>{cat_k.upper()}</code>:</b> <code>{cid}</code>", parse_mode="HTML")
            return

        if low in (".bots", "bots"):
            b_lines = [f"• <b>Bot #{i+1} ({b['role'].upper()}):</b> @{b['username']}" for i, b in enumerate(BOT_POOL)] or ["• <i>No BOT_TOKEN configured</i>"]
            if WEBSITE_BOT_USERNAME:
                b_lines.append(f"• <b>Protected Website Bot:</b> @{WEBSITE_BOT_USERNAME} (Webhook Safe ✅)")
            await event.reply("🤖 <b>Bot Pool Status:</b>\n" + "\n".join(b_lines), parse_mode="HTML")
            return

        if low in (".channels", "channels"):
            await sync_created_channels_from_dialogs()
            ch_map = load_all_channels_from_db()
            lines = [f"• <b>{k.upper()}:</b> <code>{v}</code>" for k, v in sorted(ch_map.items())]
            await event.reply(f"📋 <b>Linked Storage Channels ({count_unique_storage_channels()}/{MAX_STORAGE_CHANNELS}):</b>\n" + "\n".join(lines), parse_mode="HTML")
            return

        if low in (".queue", ".status", "queue", "status", ".stats", "stats"):
            c_yr, c_pg, c_kind = get_crawler_progress()
            q_list = "\n".join(f"  {i+1}. <code>{t[0]['raw'] if isinstance(t[0], dict) else t[0]}</code>" for i, t in enumerate(TITLE_QUEUE)) or "  <i>(Empty — 24/7 Crawler Active)</i>"
            await event.reply(
                f"📋 <b>THE BACKROOM AI COMMANDER Status</b>\n"
                f"• <b>Source Bots:</b> <code>@{SOURCE_BOT}</code> ➔ <code>@{BACKUP_BOT}</code>\n"
                f"• <b>Delivery Bots:</b> <code>{len(BOT_POOL)}</code> | <b>Gemini Keys:</b> <code>{len(GEMINI_KEYS)}</code> (<code>{LAST_GEMINI_ERROR}</code>)\n"
                f"• <b>Active Rules:</b> <code>{len(get_active_behavior_rules())}</code> | <b>Memories:</b> <code>{len(retrieve_relevant_memories())}</code>\n"
                f"• <b>Active Title:</b> <code>{ACTIVE_CLEAN_TITLE or 'Idle'}</code> ({ACTIVE_RES_TAG or '-'})\n"
                f"• <b>Auto-Crawler:</b> <code>{'ON 🟢' if AUTO_DISCOVERY_ENABLED else 'OFF 🔴'}</code> ({c_yr} • {c_kind.upper()})\n"
                f"• <b>Storage Vaults:</b> <code>{count_unique_storage_channels()}/{MAX_STORAGE_CHANNELS}</code>\n"
                f"• <b>Queue ({len(TITLE_QUEUE)}):</b>\n{q_list}",
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

        # DEFAULT FOR ALL CONVERSATIONAL MESSAGES: THE BACKROOM AI COMMANDER!
        clean_prompt = re.sub(r"^\.ai\s+", "", text, flags=re.I).strip()
        AI_CHAT_HISTORY.append(f"Owner: {clean_prompt}")
        ai_res = await gemini_interactive_agent(clean_prompt)
        if ai_res:
            await execute_ai_controller_action(ai_res, event, clean_prompt)
        return

    chat = await event.get_chat()
    chat_username = (getattr(chat, "username", "") or "").lower()
    if chat_username in (SOURCE_BOT.lower(), BACKUP_BOT.lower()) and not CURRENT_PIPELINE_ACTIVE:
        enqueue_commands_bulk(text, send_initial=False, bot_entity=chat_username)

# ============================================================================
# 14. 24/7 KEEP-ALIVE, 3-BOT POOL INIT & MAIN ENTRYPOINT
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
        "system": "THE BACKROOM AI COMMANDER",
        "primary_bot": SOURCE_BOT,
        "backup_bot": BACKUP_BOT,
        "delivery_bots_active": len(BOT_POOL),
        "gemini_keys_loaded": len(GEMINI_KEYS),
        "gemini_status": LAST_GEMINI_ERROR,
        "active_rules": len(get_active_behavior_rules()),
        "pipeline_active": CURRENT_PIPELINE_ACTIVE,
        "crawler_progress": f"{c_yr} ({c_kind} page {c_pg})",
        "storage_channels": f"{count_unique_storage_channels()}/{MAX_STORAGE_CHANNELS}",
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
    await sync_created_channels_from_dialogs()
    await init_all_delivery_bots()

    asyncio.create_task(keep_alive_worker())
    asyncio.create_task(web_pulse_background_worker())
    asyncio.create_task(dynamic_scheduler_worker())
    asyncio.create_task(auto_discovery_crawler_worker())

    print(f"🚀 THE BACKROOM AI COMMANDER LIVE! Gemini Keys: {len(GEMINI_KEYS)} | Delivery Bots: {len(BOT_POOL)}")
    await client.run_until_disconnected()

if __name__ == "__main__":
    loop.run_until_complete(main())
