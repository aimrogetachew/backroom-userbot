

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
from telethon.errors import FloodWaitError, ChatForwardsRestrictedError
from telethon.tl.functions.channels import (
    JoinChannelRequest, CreateChannelRequest, EditAdminRequest,
    DeleteChannelRequest, EditTitleRequest
)
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, ToggleNoForwardsRequest
from telethon.tl.types import ChatAdminRights

# ============================================================================
# 1. CONFIGURATION, WEBSITE BOT PROTECTION & 3-BOT POOL
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
CAPTURED_FILES, SYNCED_TITLES, META_CACHE = set(), set(), {}
DIALOG_CACHE_READY = False
TITLE_QUEUE, AI_CHAT_HISTORY = deque(), deque(maxlen=18)
BOT_POOL, BOT_RR_INDEX, GEMINI_KEY_INDEX = [], 0, 0
OMDB_CALL_DAY, OMDB_CALL_COUNT, OMDB_DAILY_SAFE_LIMIT = "", 0, 900
GEMINI_CALL_COUNT = 0
TMDB_SEMAPHORE, GEMINI_SEMAPHORE = asyncio.Semaphore(3), asyncio.Semaphore(1)

CURRENT_PIPELINE_ACTIVE = False
SKIP_CURRENT_TITLE = False
AUTO_DISCOVERY_ENABLED = True
AUTO_CREATE_CHANNELS = True
WEB_PULSE_ENABLED = False
SYNCBOT_RUNNING = False
SYNCBOT_STOP = False
LAST_REALTIME_RADAR_TS = 0.0
ME_USER_ID = None
ACTIVE_CLEAN_TITLE, ACTIVE_TARGET_YEAR, ACTIVE_SEASON_TAG, ACTIVE_EP_TAG, ACTIVE_RES_TAG = "", None, "S01", "", ""

def current_utc_year() -> int: return datetime.now(timezone.utc).year
def current_utc_date_str() -> str: return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def pick_bot_for_role(role: str = "any") -> dict:
    global BOT_RR_INDEX
    if not BOT_POOL: return {"token": BOT_TOKEN, "username": ""}
    if role == "series": return BOT_POOL[0]
    if role == "movies" and len(BOT_POOL) >= 2: return BOT_POOL[1]
    chosen = BOT_POOL[BOT_RR_INDEX % len(BOT_POOL)]
    BOT_RR_INDEX += 1
    return chosen

# ============================================================================
# 2. STRICT OFFICIAL PUBLIC TITLE CLEANER & COMMAND PARSER
# ============================================================================
JUNK_PREFIX_RE = re.compile(
    r"^(?:\s*(?:\[(?:CF|MFA|CC|SO|AE|PSA|YTS|RARBG|PAHE|DFBC|TOONWORLD4ALL|[^\]]{1,16})\]|"
    r"\b(?:DFBC|Toonworld4all|Toonworld|KatmovieHD|Vegamovies|MoviesMod|Filmyzilla|HDHub4u|18\+)\b|"
    r"@[A-Za-z0-9_]+|S\d{1,2}E\d{1,3}\s*[\-–:]\s*))+", re.I
)
JUNK_TOKENS_RE = re.compile(
    r"\b(?:Linkz|Multi\s*Aud(?:io)?|Dual\s*Aud(?:io)?|ESub|MSub|UNCUT|EXTENDED|REMASTERED|"
    r"BluRay|BDRip|BRRip|WEB-?DL|WEBRip|HDRip|DVDRip|CAMRip|HDTS|NF|AMZN|DSNP|Pahe|PSA|YTS|RARBG|"
    r"DFBC|Toonworld4all|Toonworld|KatmovieHD|Vegamovies|MoviesMod|Filmyzilla|HDHub4u|"
    r"MKV|MP4|AVI|HEVC|x265|x264|h265|h264|AV1|VP9|10Bit|8Bit|6CH|8CH|AAC\d*(?:[\.\s]\d)?|"
    r"DDP\d*(?:[\.\s]\d)?|AC3|Atmos|Dubbed|Dub)\b.*$", re.I
)

def extract_year_from_text(text: str):
    m = re.search(r"\b(19[5-9]\d|20[0-3]\d)\b", text or "")
    return int(m.group(1)) if m else None

def strip_uploader_and_junk(raw_str: str) -> str:
    s = re.sub(r"https?://\S+|t\.me/\S+|@[A-Za-z0-9_]+", " ", str(raw_str or ""))
    s = JUNK_PREFIX_RE.sub(" ", s).strip()
    return re.sub(r"\[(?:CF|MFA|CC|SO|AE|PSA|YTS|RARBG|PAHE|DFBC|TOONWORLD4ALL)\]", " ", s, flags=re.I).strip()

def normalize_title_key(title: str) -> str:
    s = strip_uploader_and_junk(title)
    s = re.sub(r"['’`]", "", s)
    s = re.sub(r"[._\-·•]+", " ", s).strip()
    s = re.sub(r"^[^\w]+", "", s).strip()
    s = re.sub(r"^(?:S\d{1,2}E\d{1,3}|E\d{1,3})\s+", "", s, flags=re.I).strip()
    s = re.sub(r"\b\d{1,2}\s*Seasons?\b.*$", "", s, flags=re.I).strip()
    cut_m = re.search(
        r"^(.{2,}?)\s+\b(?:19[5-9]\d|20[0-3]\d|S\d{1,2}E\d{1,3}|S\d{1,2}|Season\s*\d+|E\d{1,3}|EP\.?\s*\d+|"
        r"360p|480p|540p|720p|1080p|1440p|2160p|4K|BluRay|WEB-?DL|WEBRip|HDRip|DVDRip|CAMRip|HDTS)\b", s, flags=re.I
    )
    if cut_m and not re.fullmatch(r"\d{4}", cut_m.group(1).strip()):
        s = cut_m.group(1)
    s = JUNK_TOKENS_RE.sub("", s)
    out_s = re.sub(r"[^a-zA-Z0-9&]+", " ", s).strip().lower()
    out_s = re.sub(r"\s+\b(?:108|72|48|36)\b$", "", out_s).strip()
    if out_s in FRANCHISE_ALIASES: out_s = FRANCHISE_ALIASES[out_s].lower()
    return "unknown" if (not out_s or re.fullmatch(r"\d{1,4}", out_s)) else out_s

def make_slug_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_title_key(title))[:22] or "show"

def is_strict_title_and_year_match(candidate_text: str, target_title: str, target_year: int = None) -> bool:
    cand_years = [int(y) for y in re.findall(r"\b(19[5-9]\d|20[0-3]\d)\b", candidate_text or "")]
    if target_year and cand_years and target_year not in cand_years: return False
    norm_target, norm_cand = normalize_title_key(target_title), normalize_title_key(candidate_text)
    if not norm_target or not norm_cand or norm_cand == "unknown": return False
    compact_target, compact_cand = norm_target.replace(" ", ""), norm_cand.replace(" ", "")
    if compact_target == compact_cand or compact_target in compact_cand: return True
    stop_words = {"the", "a", "an", "of", "and", "in", "to", "for", "part", "movie", "seasons", "season"}
    target_words = [w for w in norm_target.split() if w not in stop_words and len(w) > 1] or norm_target.split()
    cand_words = set(norm_cand.split())
    return all(tw in cand_words or tw in compact_cand for tw in target_words)

def parse_smart_user_command(raw_text: str) -> dict:
    text = re.sub(r"^\.(?:search|get|ai|s)\s+", "", (raw_text or "").strip(), flags=re.I).strip()
    text = re.sub(r"^[\d\-\*\•\.]+\s*", "", text).strip(" ,.;:")
    mode, season_num, target_episodes = "normal", None, set()
    missing_m = re.match(r"^(?:find\s+|check\s+|fill\s+|download\s+)?missing(?:\s+files?|\s+episodes?|\s+ep|\s+parts?|\s+movies?)?(?:\s+from|\s+in|\s+for|\s+of)?\s+(.+)$", text, flags=re.I)
    if missing_m: mode, text = "missing", missing_m.group(1).strip()
    elif re.search(r"\bmissing(?:\s+files?|\s+episodes?|\s+ep|\s+parts?)?\b$", text, flags=re.I):
        mode = "missing"
        text = re.sub(r"\b(?:find|check|fill)?\s*missing(?:\s+files?|\s+episodes?|\s+ep|\s+parts?)?\b$", "", text, flags=re.I).strip()
    tail_match = re.search(r"\b(?:from\s+)?(?:(s(?:eason)?\s*0*\d{1,2})\b.*|(?:ep?(?:isode)?\.?\s*0*\d{1,3})\b.*)$", text, flags=re.I)
    if tail_match:
        tail_str, title_part = tail_match.group(0), text[:tail_match.start()].strip(" ,-:")
        if len(title_part) >= 2:
            s_m = re.search(r"\bs(?:eason)?\s*0*(\d{1,2})\b", tail_str, re.I)
            if s_m: season_num = int(s_m.group(1))
            ep_str = re.sub(r"\bs(?:eason)?\s*0*\d{1,2}\b", " ", tail_str, flags=re.I)
            for r_m in re.finditer(r"(?:ep?(?:isode)?\.?\s*)?0*(\d{1,3})\s*(?:\-|to|–|~)\s*(?:ep?(?:isode)?\.?\s*)?0*(\d{1,3})", ep_str, re.I):
                s_e, e_e = int(r_m.group(1)), int(r_m.group(2))
                if s_e <= e_e and (e_e - s_e) <= 200: target_episodes.update(range(s_e, e_e + 1))
            for single_m in re.finditer(r"(?:ep?(?:isode)?\.?\s*)0*(\d{1,3})\b", ep_str, re.I):
                target_episodes.add(int(single_m.group(1)))
            if season_num is not None or target_episodes:
                text = title_part
                if target_episodes and mode != "missing": mode = "range"
    target_year = extract_year_from_text(text)
    clean_search = re.sub(r"\(\d{4}\)|\[\d{4}\]|\b(19[5-9]\d|20[0-3]\d)\b", "", text).strip(" ,.-:")
    if clean_search.lower() in FRANCHISE_ALIASES: clean_search = FRANCHISE_ALIASES[clean_search.lower()]
    return {"raw": raw_text.strip(), "title": clean_search or text.strip(" ,."), "year": target_year, "mode": mode, "season": season_num, "episodes": sorted(list(target_episodes)) if target_episodes else None}

def split_bulk_commands(raw_message: str) -> list:
    msg = re.sub(r"^\.(?:search|get|s)\s+", "", (raw_message or "").strip(), flags=re.I).strip()
    cmds = []
    for line in msg.splitlines():
        line = line.strip()
        if not line or re.match(r"^[\.\s,]+$", line): continue
        if "," in line and not re.search(r"\b(?:ep?|episode|s\d{1,2})\b", line, re.I):
            cmds.extend([p.strip(" .,-") for p in line.split(",") if len(p.strip(" .,-")) >= 2])
        elif len(line.strip(" .,-")) >= 2:
            cmds.append(line.strip(" .,-"))
    return cmds

# ============================================================================
# 3. SQLITE DATABASE, RULES, MEMORY, SYNCBOT TRACKING & CHANNELS
# ============================================================================
def extract_resolution_tag(text: str, default_res: str = "HD") -> str:
    m = re.search(r"\b(360p|480p|540p|720p|1080p|1440p|2160p|4K)\b", text or "", re.I)
    return ("2160P" if m.group(1).upper() == "4K" else m.group(1).upper()) if m else default_res.upper()

def extract_audio_tier(text: str, num_langs: int = 1) -> int:
    t = (text or "").lower()
    if num_langs >= 3 or re.search(r"\b(multi\s*audio|multi\s*aud|multi\s*dub|multi|dubbed|hin.*eng.*jap|tam.*tel.*hin)\b", t): return 3
    if num_langs == 2 or re.search(r"\b(dual\s*audio|dual\s*aud|dual\s*dub|dual|hin[\s\-\+]+eng|eng[\s\-\+]+jap)\b", t): return 2
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
    conn.execute("CREATE TABLE IF NOT EXISTS indexed_files_v4 (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER NOT NULL, message_id INTEGER DEFAULT 0, clean_title TEXT NOT NULL, slug_key TEXT NOT NULL, ep_tag TEXT NOT NULL, res_tag TEXT NOT NULL, variant_key TEXT NOT NULL, audio_tier INTEGER DEFAULT 1, file_name TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(clean_title, ep_tag, variant_key))")
    conn.execute("CREATE TABLE IF NOT EXISTS dynamic_channels (cat_key TEXT PRIMARY KEY, channel_id INTEGER NOT NULL, title TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS series_index_cards (slug_key TEXT PRIMARY KEY, clean_title TEXT NOT NULL, public_title TEXT NOT NULL, index_channel_id INTEGER NOT NULL, index_message_id INTEGER NOT NULL, seasons_json TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS persistent_meta_cache (cache_key TEXT PRIMARY KEY, json_data TEXT NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS extra_gemini_keys (api_key TEXT PRIMARY KEY)")
    conn.execute(f"CREATE TABLE IF NOT EXISTS crawler_state (id INTEGER PRIMARY KEY CHECK (id = 1), current_year INTEGER DEFAULT {now_yr}, current_page INTEGER DEFAULT 1, media_kind TEXT DEFAULT 'movie')")
    conn.execute("CREATE TABLE IF NOT EXISTS crawler_history (clean_title TEXT PRIMARY KEY, year INTEGER, checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS synced_to_web_bot (channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL, synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (channel_id, message_id))")
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
        if r[0] and r[0] not in GEMINI_KEYS: GEMINI_KEYS.append(r[0])
    conn.commit()
    conn.close()

def is_msg_synced_to_web(channel_id: int, message_id: int) -> bool:
    conn = sqlite3.connect("storage_index.db")
    row = conn.execute("SELECT 1 FROM synced_to_web_bot WHERE channel_id=? AND message_id=?", (int(channel_id), int(message_id))).fetchone()
    conn.close()
    return bool(row)

def mark_msg_synced_to_web(channel_id: int, message_id: int):
    conn = sqlite3.connect("storage_index.db")
    conn.execute("INSERT OR IGNORE INTO synced_to_web_bot (channel_id, message_id) VALUES (?, ?)", (int(channel_id), int(message_id)))
    conn.commit()
    conn.close()

def manage_behavior_rule(op: str, rule_text: str = "", rule_id: int = None, category: str = "PERMANENT_INSTRUCTION") -> str:
    conn = sqlite3.connect("storage_index.db")
    op = (op or "ADD").upper()
    if op == "ADD" and rule_text:
        row = conn.execute("SELECT id FROM behavior_rules WHERE lower(rule_text)=?", (rule_text.lower().strip(),)).fetchone()
        if row: conn.close(); return f"Rule #{row[0]} already active."
        rid = conn.execute("INSERT INTO behavior_rules (rule_text, category) VALUES (?, ?)", (rule_text.strip(), category)).lastrowid
        conn.commit(); conn.close()
        return f"✅ Permanent Rule #{rid} saved: \"{rule_text.strip()}\""
    elif op == "UPDATE" and rule_id and rule_text:
        old = conn.execute("SELECT rule_text, version FROM behavior_rules WHERE id=?", (rule_id,)).fetchone()
        if old:
            conn.execute("INSERT INTO rule_versions (rule_id, version, old_text) VALUES (?, ?, ?)", (rule_id, old[1], old[0]))
            conn.execute("UPDATE behavior_rules SET rule_text=?, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (rule_text.strip(), rule_id))
            conn.commit()
        conn.close(); return f"♻️ Updated Rule #{rule_id}."
    elif op in ("REMOVE", "DISABLE") and rule_id:
        conn.execute("UPDATE behavior_rules SET active=0, updated_at=CURRENT_TIMESTAMP WHERE id=?", (rule_id,))
        conn.commit(); conn.close(); return f"🛑 Disabled Rule #{rule_id}."
    conn.close()
    return "ℹ️ Done."

def get_active_behavior_rules() -> list:
    conn = sqlite3.connect("storage_index.db")
    rows = conn.execute("SELECT id, rule_text, category, version FROM behavior_rules WHERE active=1 ORDER BY priority DESC, id ASC").fetchall()
    conn.close()
    return rows

def save_long_term_memory(category: str, mem_key: str, mem_value: str):
    conn = sqlite3.connect("storage_index.db")
    conn.execute("INSERT OR REPLACE INTO long_term_memory (category, mem_key, mem_value, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)", (category.upper().strip(), mem_key.lower().strip(), str(mem_value).strip()))
    conn.commit(); conn.close()

def retrieve_relevant_memories(query: str = "", limit: int = 12) -> list:
    conn = sqlite3.connect("storage_index.db")
    rows = conn.execute("SELECT category, mem_key, mem_value FROM long_term_memory ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return rows

def log_audit_event(user_req: str, action_type: str, risk_level: str, status: str):
    conn = sqlite3.connect("storage_index.db")
    conn.execute("INSERT INTO audit_logs (user_request, action_type, risk_level, status) VALUES (?, ?, ?, ?)", (user_req[:300], action_type, risk_level, status))
    conn.commit(); conn.close()

def stage_or_register_capability(cap_name: str, description: str, python_code: str, auto_activate: bool = False) -> str:
    try: ast.parse(python_code)
    except SyntaxError as se: return f"❌ Syntax error: {se}"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_file = f"backups/userbot_backup_{ts}.py"
    try:
        if os.path.exists(__file__): shutil.copy2(__file__, backup_file)
    except Exception: pass
    with open(f"development/{cap_name}_{ts}.py", "w", encoding="utf-8") as f: f.write(python_code)
    conn = sqlite3.connect("storage_index.db")
    conn.execute("INSERT OR REPLACE INTO custom_capabilities (cap_name, description, python_code, active, version, updated_at) VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)", (cap_name, description, python_code, 1 if auto_activate else 0))
    conn.execute("INSERT INTO evolution_history (change_type, summary, backup_path, status) VALUES (?, ?, ?, ?)", ("SKILL_PROPOSAL", f"{cap_name}: {description}", backup_file, "ACTIVE" if auto_activate else "WAITING_FOR_APPROVAL"))
    conn.commit(); conn.close()
    return f"🧬 <b>Skill Staged:</b> <code>{cap_name}</code> (Backup: <code>{backup_file}</code>) — send <code>.approve</code> to activate."

def add_backup_gemini_key(new_key: str) -> int:
    for part in (new_key or "").split(","):
        k = part.strip()
        if len(k) >= 20 and k not in GEMINI_KEYS:
            GEMINI_KEYS.append(k)
            conn = sqlite3.connect("storage_index.db")
            conn.execute("INSERT OR IGNORE INTO extra_gemini_keys (api_key) VALUES (?)", (k,))
            conn.commit(); conn.close()
    return len(GEMINI_KEYS)

def load_all_channels_from_db() -> dict:
    conn = sqlite3.connect("storage_index.db")
    rows = conn.execute("SELECT cat_key, channel_id FROM dynamic_channels").fetchall()
    conn.close()
    ch_map = dict(DEFAULT_CHANNELS)
    for k, cid in rows:
        if cid: ch_map[k] = int(cid)
    return ch_map

def count_unique_storage_channels() -> int: return len(set(load_all_channels_from_db().values()))

async def promote_all_bots_in_channel(channel_id: int):
    rights = ChatAdminRights(post_messages=True, edit_messages=True, delete_messages=True, invite_users=True, pin_messages=True)
    usernames = [b.get("username") for b in BOT_POOL if b.get("username")]
    if WEBSITE_BOT_USERNAME and WEBSITE_BOT_USERNAME not in usernames: usernames.append(WEBSITE_BOT_USERNAME)
    for uname in usernames:
        try: await client(EditAdminRequest(channel=channel_id, user_id=uname, admin_rights=rights, rank="Bot"))
        except Exception: pass

async def sync_created_channels_from_dialogs():
    global DIALOG_CACHE_READY, SERIES_INDEX_CHANNEL
    title_to_key = {v.lower(): k for k, v in DETAILED_GENRE_TITLES.items()}
    conn = sqlite3.connect("storage_index.db")
    try:
        async for dlg in client.iter_dialogs(limit=250):
            if not dlg.is_channel: continue
            d_title = (dlg.name or "").strip().lower()
            for target_t, cat_k in title_to_key.items():
                if d_title == target_t or target_t in d_title:
                    cid = int(dlg.id)
                    conn.execute("INSERT OR REPLACE INTO dynamic_channels (cat_key, channel_id, title) VALUES (?, ?, ?)", (cat_k, cid, dlg.name))
                    if cat_k == "series_index" and not SERIES_INDEX_CHANNEL: SERIES_INDEX_CHANNEL = cid
                    break
        conn.commit(); DIALOG_CACHE_READY = True
    except Exception: pass
    finally: conn.close()

async def resolve_account_channel_target(target_str: str):
    t = (target_str or "").strip().lower()
    if not t: return []
    ch_map = load_all_channels_from_db()
    if t in ch_map: return [(t, ch_map[t], DETAILED_GENRE_TITLES.get(t, t.upper()))]
    matches = []
    async for dlg in client.iter_dialogs(limit=250):
        if not (dlg.is_channel or dlg.is_group): continue
        d_name = (dlg.name or "").strip()
        if t == str(dlg.id) or t in d_name.lower(): matches.append((d_name, int(dlg.id), d_name))
    return matches

async def rename_telegram_channel_or_chat(target_str: str, new_title: str) -> str:
    matches = await resolve_account_channel_target(target_str)
    if not matches: return f"⚠️ No channel matching <code>{target_str}</code>."
    _, cid, old_name = matches[0]
    try:
        await client(EditTitleRequest(channel=cid, title=new_title.strip()))
        conn = sqlite3.connect("storage_index.db")
        conn.execute("UPDATE dynamic_channels SET title=? WHERE channel_id=?", (new_title.strip(), cid))
        conn.commit(); conn.close()
        return f"✏️ <b>Renamed:</b> <code>{old_name}</code> ➔ <code>{new_title.strip()}</code>"
    except Exception as e: return f"⚠️ Rename error: <code>{e}</code>"

async def delete_movie_or_series_everywhere(query: str) -> int:
    await sync_created_channels_from_dialogs()
    clean_q = normalize_title_key(query)
    if not clean_q or clean_q == "unknown": return 0
    deleted = 0
    for cid in sorted(list(set(load_all_channels_from_db().values()))):
        try:
            to_del = [m.id async for m in client.iter_messages(cid, search=query, limit=60) if is_strict_title_and_year_match(f"{m.file.name if m.file else ''}\n{m.text or ''}", clean_q)]
            if to_del: await client.delete_messages(cid, to_del); deleted += len(to_del)
        except Exception: pass
    slug = make_slug_key(clean_q)
    conn = sqlite3.connect("storage_index.db")
    conn.execute("DELETE FROM indexed_files_v4 WHERE clean_title=? OR slug_key=?", (clean_q, slug))
    conn.execute("DELETE FROM series_index_cards WHERE slug_key=?", (slug,))
    conn.execute("DELETE FROM crawler_history WHERE clean_title=?", (clean_q,))
    conn.commit(); conn.close()
    return deleted

async def get_or_create_genre_channel(cat_key: str) -> int:
    ch_map = load_all_channels_from_db()
    if cat_key in ch_map and ch_map[cat_key]: return ch_map[cat_key]
    if not AUTO_CREATE_CHANNELS or count_unique_storage_channels() >= MAX_STORAGE_CHANNELS:
        fb = {"scifi": "action", "adventure": "action", "war_history": "action", "thriller": "horror", "mystery": "horror", "crime": "action", "south_indian": "hindi", "chinese_donghua": "anime", "family": "animation", "comedy": "english", "romance": "english", "fantasy": "action", "documentary": "english", "european": "english", "classic_90s": "english", "franchise_vault": "english"}.get(cat_key, "other")
        return ch_map.get(fb, DEFAULT_CHANNELS["other"])
    ch_title = DETAILED_GENRE_TITLES.get(cat_key, f"Vault • {cat_key.replace('_', ' ').title()}")
    try:
        res = await client(CreateChannelRequest(title=ch_title, about=f"Protected Vault ({cat_key.upper()})", megagroup=False, broadcast=True))
        cid = int(f"-100{res.chats[0].id}")
        try: await client(ToggleNoForwardsRequest(peer=cid, enabled=True))
        except Exception: pass
        await promote_all_bots_in_channel(cid)
        conn = sqlite3.connect("storage_index.db")
        conn.execute("INSERT OR REPLACE INTO dynamic_channels (cat_key, channel_id, title) VALUES (?, ?, ?)", (cat_key, cid, ch_title))
        conn.commit(); conn.close()
        return cid
    except Exception: return DEFAULT_CHANNELS["other"]

def get_stored_file_record(clean_title: str, ep_tag: str, variant_or_res: str = "ANY"):
    t_key, e_key, v_key = normalize_title_key(clean_title), ep_tag.upper().strip(), (variant_or_res or "ANY").upper().strip()
    conn = sqlite3.connect("storage_index.db")
    if v_key == "ANY": row = conn.execute("SELECT channel_id, message_id, audio_tier FROM indexed_files_v4 WHERE clean_title=? AND ep_tag=?", (t_key, e_key)).fetchone()
    elif "_" in v_key: row = conn.execute("SELECT channel_id, message_id, audio_tier FROM indexed_files_v4 WHERE clean_title=? AND ep_tag=? AND variant_key=?", (t_key, e_key, v_key)).fetchone()
    else: row = conn.execute("SELECT channel_id, message_id, audio_tier FROM indexed_files_v4 WHERE clean_title=? AND ep_tag=? AND res_tag=?", (t_key, e_key, v_key)).fetchone()
    conn.close()
    return row

def is_already_in_any_channel(clean_title: str, ep_tag: str, variant_or_res: str = "ANY", min_audio_tier: int = 1) -> bool:
    row = get_stored_file_record(clean_title, ep_tag, variant_or_res)
    return bool(row and (row[2] or 1) >= min_audio_tier)

def record_indexed_file(channel_id: int, message_id: int, clean_title: str, ep_tag: str, res_tag: str, variant_key: str, audio_tier: int, file_name: str):
    norm_t = normalize_title_key(clean_title)
    if norm_t == "unknown": return
    conn = sqlite3.connect("storage_index.db")
    conn.execute("INSERT OR REPLACE INTO indexed_files_v4 (channel_id, message_id, clean_title, slug_key, ep_tag, res_tag, variant_key, audio_tier, file_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (channel_id, message_id or 0, norm_t, make_slug_key(norm_t), ep_tag.upper().strip(), (res_tag or "HD").upper().strip(), (variant_key or res_tag or "HD").upper().strip(), int(audio_tier or 1), file_name))
    conn.commit(); conn.close()

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
        rows = conn.execute("SELECT channel_id, message_id, ep_tag, res_tag FROM indexed_files_v4 WHERE slug_key=? AND ep_tag LIKE ? AND res_tag=? AND message_id > 0 GROUP BY ep_tag ORDER BY ep_tag ASC", (slug_key, f"{season_prefix.upper()}%", res_filter.upper())).fetchall()
    else:
        rows = conn.execute("SELECT channel_id, message_id, ep_tag, res_tag FROM indexed_files_v4 WHERE slug_key=? AND ep_tag LIKE ? AND message_id > 0 GROUP BY ep_tag, res_tag ORDER BY ep_tag ASC", (slug_key, f"{season_prefix.upper()}%")).fetchall()
    conn.close()
    return rows

def get_available_season_qualities(slug_key: str, season_prefix: str) -> list:
    conn = sqlite3.connect("storage_index.db")
    rows = conn.execute("SELECT DISTINCT res_tag FROM indexed_files_v4 WHERE slug_key=? AND ep_tag LIKE ? AND message_id > 0", (slug_key, f"{season_prefix.upper()}%")).fetchall()
    conn.close()
    return sorted([r[0].upper() for r in rows if r[0]], key=lambda x: TARGET_RESOLUTIONS.index(x) if x in TARGET_RESOLUTIONS else 99)

def get_crawler_progress():
    conn = sqlite3.connect("storage_index.db")
    row = conn.execute("SELECT current_year, current_page, media_kind FROM crawler_state WHERE id=1").fetchone()
    conn.close()
    return row if row else (current_utc_year(), 1, "movie")

def save_crawler_progress(year: int, page: int, media_kind: str):
    conn = sqlite3.connect("storage_index.db")
    conn.execute("UPDATE crawler_state SET current_year=?, current_page=?, media_kind=? WHERE id=1", (year, page, media_kind))
    conn.commit(); conn.close()

def is_title_crawled_recently(clean_title: str, hours: int = 12) -> bool:
    conn = sqlite3.connect("storage_index.db")
    row = conn.execute(f"SELECT 1 FROM crawler_history WHERE clean_title=? AND checked_at >= datetime('now', '-{int(hours)} hours')", (normalize_title_key(clean_title),)).fetchone()
    conn.close()
    return bool(row)

def mark_title_crawled(clean_title: str, year: int = None):
    conn = sqlite3.connect("storage_index.db")
    conn.execute("INSERT OR REPLACE INTO crawler_history (clean_title, year, checked_at) VALUES (?, ?, CURRENT_TIMESTAMP)", (normalize_title_key(clean_title), year))
    conn.commit(); conn.close()

def get_db_cached_meta(cache_key: str):
    conn = sqlite3.connect("storage_index.db")
    row = conn.execute("SELECT json_data FROM persistent_meta_cache WHERE cache_key=?", (cache_key,)).fetchone()
    conn.close()
    if row:
        try: return json.loads(row[0])
        except Exception: pass
    return None

def save_db_cached_meta(cache_key: str, data: dict):
    conn = sqlite3.connect("storage_index.db")
    conn.execute("INSERT OR REPLACE INTO persistent_meta_cache (cache_key, json_data, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)", (cache_key, json.dumps(data)))
    conn.commit(); conn.close()

init_storage_db()

# ============================================================================
# 4. GEMINI AI COMMANDER BRAIN (STABLE MODELS ONLY + PROMISE ENFORCER)
# ============================================================================
LAST_GEMINI_ERROR = "None"
DISCOVERED_GEMINI_MODELS = []

async def get_available_gemini_models(session: aiohttp.ClientSession, api_key: str) -> list:
    global DISCOVERED_GEMINI_MODELS
    if DISCOVERED_GEMINI_MODELS: return DISCOVERED_GEMINI_MODELS
    preferred = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash"]
    try:
        async with session.get(f"https://generativelanguage.googleapis.com/v1beta/models?pageSize=100&key={api_key}", timeout=8) as resp:
            if resp.status == 200:
                avail, extra = set(), []
                for m in ((await resp.json()).get("models") or []):
                    if "generateContent" not in (m.get("supportedGenerationMethods") or []): continue
                    mn = (m.get("name") or "").replace("models/", "").strip()
                    if any(b in mn.lower() for b in ["preview", "exp", "omni", "live", "tts", "image", "audio", "embedding", "vision", "pro", "thinking", "learnlm", "robotics"]): continue
                    avail.add(mn)
                    if "flash" in mn and mn not in preferred: extra.append(mn)
                ordered = [m for m in preferred if m in avail] + sorted(extra, reverse=True)
                if ordered:
                    DISCOVERED_GEMINI_MODELS = ordered[:6]
                    return DISCOVERED_GEMINI_MODELS
    except Exception: pass
    return preferred

async def ask_gemini_json(prompt: str, cache_key: str = None):
    global GEMINI_KEY_INDEX, GEMINI_CALL_COUNT, LAST_GEMINI_ERROR
    if not GEMINI_KEYS:
        LAST_GEMINI_ERROR = "No GEMINI_API_KEY loaded (.addgemini AIzaSy...)"
        return None
    if cache_key:
        cached = get_db_cached_meta(f"gemini:{cache_key}")
        if cached: return cached
    async with GEMINI_SEMAPHORE:
        payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
        async with aiohttp.ClientSession() as session:
            for _ in range(len(GEMINI_KEYS)):
                api_key = GEMINI_KEYS[GEMINI_KEY_INDEX % len(GEMINI_KEYS)]
                GEMINI_KEY_INDEX += 1
                for model in await get_available_gemini_models(session, api_key):
                    try:
                        async with session.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}", json=payload, timeout=14) as resp:
                            if resp.status == 200:
                                GEMINI_CALL_COUNT += 1
                                parts = (await resp.json()).get("candidates", [{}])[0].get("content", {}).get("parts", [])
                                raw_txt = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                                m_json = re.search(r"(\{.*\}|\[.*\])", raw_txt.strip(), re.DOTALL)
                                if m_json:
                                    parsed = json.loads(m_json.group(1))
                                    if cache_key and parsed: save_db_cached_meta(f"gemini:{cache_key}", parsed)
                                    LAST_GEMINI_ERROR = f"OK ({model})"
                                    await asyncio.sleep(1.0)
                                    return parsed
                            elif resp.status in (404, 429, 503):
                                LAST_GEMINI_ERROR = f"HTTP {resp.status} ({model})"
                                continue
                            else:
                                LAST_GEMINI_ERROR = f"HTTP {resp.status} ({model})"
                                break
                    except Exception as e:
                        LAST_GEMINI_ERROR = str(e)[:80]
    return None

def local_commander_fallback(user_msg: str) -> dict:
    low = (user_msg or "").lower().strip()
    c_yr, _, c_kind = get_crawler_progress()
    if re.search(r"\b(?:stop|pause|cancel|halt|clear)\b", low):
        return {"action": "STOP_ALL", "risk_level": "LOW", "crawler_on": False, "reply": "🛑 <b>Stopped all active downloads, cleared queue, and paused Auto-Crawler!</b>"}
    if any(w in low for w in ["how are u", "how are you", "hows it going", "good morning", "whats up"]):
        return {"action": "CHAT_ONLY", "risk_level": "LOW", "reply": f"I'm doing great, Chief! 🚀 Currently: <code>{ACTIVE_CLEAN_TITLE or 'Idle'}</code> | Queue: <code>{len(TITLE_QUEUE)}</code> | Auto-Crawler: <code>{'ON 🟢' if AUTO_DISCOVERY_ENABLED else 'OFF 🔴'}</code> ({c_yr} {c_kind.upper()}). What do you need?"}
    if any(w in low for w in ["what are you downloading", "status", "queue", "progress"]):
        return {"action": "CHAT_ONLY", "risk_level": "LOW", "reply": f"• <b>Active:</b> <code>{ACTIVE_CLEAN_TITLE or 'Idle'}</code>\n• <b>Queue:</b> <code>{len(TITLE_QUEUE)}</code>\n• <b>Channels:</b> <code>{count_unique_storage_channels()}/{MAX_STORAGE_CHANNELS}</code>\n• <b>Gemini:</b> <code>{LAST_GEMINI_ERROR}</code>"}
    if low.startswith(("from now on", "always ", "never ", "remember that")):
        return {"action": "MANAGE_RULE", "risk_level": "LOW", "rule_op": "ADD", "rule_text": user_msg.strip(), "reply": f"Locked in permanent rule: <code>{user_msg.strip()}</code>"}
    if any(w in low for w in ["duplicate", "dedup"]):
        return {"action": "DEDUP_CHANNELS", "risk_level": "MEDIUM", "reply": "🧹 Scanning all storage channels for duplicate pairs and auto-deleting extras..."}
    return {"action": "CHAT_ONLY", "risk_level": "LOW", "reply": f"I received: <i>\"{user_msg}\"</i>\n<i>(Gemini: <code>{LAST_GEMINI_ERROR}</code> — use <code>.search Title</code>, <code>.syncbot all</code>, <code>.dedup</code>, or <code>.addgemini Key</code>)</i>"}

async def gemini_interactive_agent(user_msg: str) -> dict:
    ch_map = load_all_channels_from_db()
    c_yr, _, c_kind = get_crawler_progress()
    rules_str = "\n".join(f"  [Rule #{r[0]}]: {r[1]}" for r in get_active_behavior_rules()) or "  (None)"
    mem_str = "\n".join(f"  [{m[0]}] {m[1]}: {m[2]}" for m in retrieve_relevant_memories(user_msg, 8)) or "  (None)"
    history_str = "\n".join(AI_CHAT_HISTORY)
    prompt = f"""
    You are THE BACKROOM AI COMMANDER — a warm, ChatGPT-like personal AI operating system inside the owner's Telegram account.
    State: Active={ACTIVE_CLEAN_TITLE or 'Idle'} | Queue={len(TITLE_QUEUE)} | AutoCrawler={'ON' if AUTO_DISCOVERY_ENABLED else 'OFF'} ({c_yr} {c_kind}) | Channels={json.dumps(ch_map)}
    PERMANENT RULES:\n{rules_str}\nMEMORIES:\n{mem_str}\nRECENT CHAT:\n{history_str}
    OWNER MESSAGE: "{user_msg}"

    RULES:
    1. If owner asks to fetch/search/download a movie/series (or confirms a follow-up like "Yes 1 season only"), set action="QUEUE_TITLES", target_title="<Title>", target_season=<int or null>, and populate tasks.
    2. NEVER say "Fetching X" or "Pulling up X" with action="CHAT_ONLY"!
    3. Actions: CHAT_ONLY, QUEUE_TITLES, STOP_ALL, INSPECT_STORAGE, SYNC_WEBSITE_BOT, DEDUP_CHANNELS, WIPE_AND_REUPLOAD_CHANNEL, MANAGE_RULE, SEARCH_ACCOUNT, INSPECT_WEBSITE, SELF_EVOLVE, SCHEDULE_JOB, DELETE_MEDIA, REUPLOAD_MEDIA, RENAME_ALL_PUBLIC, REBUILD_SERIES_INDEX, RENAME_CHANNEL, DELETE_CHANNEL, CREATE_CHANNEL, CRAWLER_CONTROL, SKIP, CLEAR_QUEUE.

    Return ONLY valid JSON:
    {{"reply": "Warm HTML reply", "action": "CHAT_ONLY", "risk_level": "LOW", "target_title": null, "target_season": null, "target_channel_key": null, "new_name": null, "rule_op": null, "rule_id": null, "rule_text": null, "memory_to_save": null, "search_query": null, "cap_name": null, "cap_description": null, "cap_code": null, "schedule_prompt": null, "interval_minutes": null, "crawler_year": null, "crawler_mode": null, "crawler_on": null, "tasks": []}}
    """
    res = await ask_gemini_json(prompt)
    if isinstance(res, dict) and res.get("action"):
        act, rep_txt = (res.get("action") or "CHAT_ONLY").upper(), res.get("reply") or ""
        rep_low = rep_txt.lower()
        if res.get("target_title") and not res.get("tasks") and act in ("QUEUE_TITLES", "CHAT_ONLY"):
            cmd = parse_smart_user_command(str(res["target_title"]))
            if str(res.get("target_season") or "").isdigit(): cmd["season"] = int(res["target_season"])
            res["action"], res["tasks"] = "QUEUE_TITLES", [cmd]
            return res
        if act == "CHAT_ONLY" and any(pw in rep_low for pw in ["fetching", "pulling up", "let me search", "searching for", "downloading", "here it comes", "give me a second to pull"]):
            bm = re.findall(r"<b>([^<]{2,60})</b>", rep_txt)
            cmd = parse_smart_user_command(bm[0] if bm else user_msg)
            sm = re.search(r"\b(?:season\s*|s)0*(\d{1,2})\b|\b(\d{1,2})\s+season\b", f"{user_msg} {bm[0] if bm else ''}", re.I)
            if sm: cmd["season"] = int(sm.group(1) or sm.group(2))
            if cmd.get("title") and normalize_title_key(cmd["title"]) != "unknown":
                res["action"], res["tasks"] = "QUEUE_TITLES", [cmd]
                return res
        if act == "CHAT_ONLY" and any(p in rep_low for p in ["looking into your storage", "checking it right now", "pull up the list from your storage"]):
            res["action"] = "INSPECT_STORAGE"
        return res
    return local_commander_fallback(user_msg)

async def fetch_meta(title: str, target_year: int = None):
    global OMDB_CALL_DAY, OMDB_CALL_COUNT
    clean = normalize_title_key(title)
    if not target_year: target_year = extract_year_from_text(title)
    cache_key = f"{clean}:{target_year or 'any'}"
    if cache_key in META_CACHE: return META_CACHE[cache_key]
    db_cached = get_db_cached_meta(cache_key)
    if db_cached: META_CACHE[cache_key] = db_cached; return db_cached

    public_title, resolved_year, genres_list, lang, runtime, rating = clean.title(), target_year, [], "", "", ""
    has_ep = bool(re.search(r"\b(S\d{1,2}|Season|E\d{1,3}|EP\.?\s*\d+|Episode)\b", title, re.I))
    is_series = has_ep
    today_str = current_utc_date_str()
    if OMDB_CALL_DAY != today_str: OMDB_CALL_DAY, OMDB_CALL_COUNT = today_str, 0

    async with aiohttp.ClientSession() as session:
        if TMDB_API_KEY and clean != "unknown":
            async with TMDB_SEMAPHORE:
                try:
                    async with session.get(f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&language=en-US&query={quote_plus(clean)}", timeout=4) as r:
                        if r.status == 200:
                            results = [x for x in ((await r.json()).get("results") or []) if x.get("media_type") in ("movie", "tv")]
                            chosen = None
                            for item in results:
                                if has_ep and item.get("media_type") != "tv": continue
                                rel = item.get("release_date") or item.get("first_air_date") or ""
                                iy = int(rel[:4]) if len(rel) >= 4 and rel[:4].isdigit() else None
                                if target_year and iy and iy != target_year: continue
                                chosen, resolved_year = item, iy or resolved_year; break
                            if not chosen and results and not target_year:
                                chosen = results[0]
                                rel = chosen.get("release_date") or chosen.get("first_air_date") or ""
                                if len(rel) >= 4 and rel[:4].isdigit(): resolved_year = int(rel[:4])
                            if chosen:
                                public_title = chosen.get("title") or chosen.get("name") or public_title
                                lang = chosen.get("original_language", "")
                                if chosen.get("media_type") == "tv": is_series = True
                                vote = chosen.get("vote_average")
                                if vote and float(vote) > 0: rating = f"{float(vote):.1f}/10"
                                for gid in (chosen.get("genre_ids") or []):
                                    if gid in TMDB_GENRE_MAP and TMDB_GENRE_MAP[gid] not in genres_list: genres_list.append(TMDB_GENRE_MAP[gid])
                    await asyncio.sleep(0.2)
                except Exception: pass
        if OMDB_API_KEY and OMDB_CALL_COUNT < OMDB_DAILY_SAFE_LIMIT and clean != "unknown" and (not genres_list or not runtime):
            try:
                OMDB_CALL_COUNT += 1
                yp = f"&y={target_year}" if target_year else ""
                async with session.get(f"https://www.omdbapi.com/?apikey={OMDB_API_KEY}&t={quote_plus(clean)}{yp}", timeout=4) as r:
                    if r.status == 200:
                        d = await r.json()
                        if d.get("Response") == "True":
                            if d.get("Title"): public_title = d["Title"]
                            ym = re.search(r"\b(19\d\d|20\d\d)\b", d.get("Year", ""))
                            if ym: resolved_year = resolved_year or int(ym.group(1))
                            for og in [g.strip() for g in (d.get("Genre") or "").split(",") if g.strip() and g.strip() != "N/A"]:
                                if og not in genres_list: genres_list.append(og)
                            if d.get("Runtime") and d.get("Runtime") != "N/A": runtime = d["Runtime"]
                            if d.get("imdbRating") and d.get("imdbRating") != "N/A": rating = f"{d['imdbRating']}/10"
                            if d.get("Type") == "series": is_series = True
            except Exception: pass
        if not genres_list and clean != "unknown":
            try:
                async with session.get(f"https://itunes.apple.com/search?term={quote_plus(clean)}&limit=5", timeout=4) as ir:
                    if ir.status == 200:
                        for it in ((await ir.json(content_type=None)).get("results") or []):
                            tn = it.get("trackName") or it.get("collectionName") or ""
                            rel = it.get("releaseDate") or ""
                            iy = int(rel[:4]) if len(rel) >= 4 and rel[:4].isdigit() else None
                            if is_strict_title_and_year_match(tn, clean, target_year):
                                if tn: public_title = re.sub(r"\s*\(?\bSeason\s*\d+.*$", "", tn, flags=re.I).strip()
                                resolved_year = resolved_year or iy
                                pg = it.get("primaryGenreName")
                                if pg and pg not in genres_list: genres_list.append(pg)
                                break
            except Exception: pass

    result = {"title": clean, "public_title": public_title, "year": resolved_year, "genres": ", ".join(genres_list), "language": lang, "runtime": runtime, "rating": rating, "is_series": is_series}
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
                    for ep in (await r.json()).get("_embedded", {}).get("episodes", []):
                        if ep.get("season") and ep.get("number"): seasons_map.setdefault(int(ep["season"]), []).append(int(ep["number"]))
        except Exception: pass
        if TMDB_API_KEY and not franchise_movies:
            async with TMDB_SEMAPHORE:
                try:
                    async with session.get(f"https://api.themoviedb.org/3/search/collection?api_key={TMDB_API_KEY}&language=en-US&query={quote_plus(clean)}", timeout=4) as cr:
                        if cr.status == 200:
                            c_res = (await cr.json()).get("results") or []
                            if c_res:
                                async with session.get(f"https://api.themoviedb.org/3/collection/{c_res[0]['id']}?api_key={TMDB_API_KEY}&language=en-US", timeout=4) as dr:
                                    if dr.status == 200: franchise_movies = [p["title"] for p in (await dr.json()).get("parts") or [] if p.get("title")]
                except Exception: pass
    return {"seasons": seasons_map, "franchise_movies": franchise_movies[:12]}

# ============================================================================
# 5. THEBACKROOM.LOL CAPTION BUILDER, IN-PLACE GREEN BUTTONS & SERIES INDEX
# ============================================================================
def strip_subtitle_sections(caption: str) -> str:
    if not caption: return ""
    cleaned = []
    for line in caption.split("💬")[0].splitlines():
        if re.match(r"^(?:📝|🔤|🍿|╰)?\s*(?:subtitles?|subs?|esub|mmsub|join:)\b", line.strip().lower()): break
        cleaned.append(line)
    return "\n".join(cleaned)

def format_size_human(size_bytes: int) -> str:
    if not size_bytes: return ""
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
    full = f"{real_fn}\n{caption or ''}"
    qual = extract_resolution_tag(full, ACTIVE_RES_TAG or "1080P")
    source = next((lbl for pat, lbl in [(r"\b(?:bluray|bdrip|brrip|bd)\b", "BluRay"), (r"\bweb-?dl\b", "WEB-DL"), (r"\bwebrip\b", "WEBRip"), (r"\bhdrip\b", "HDRip"), (r"\bamzn\b", "AMZN"), (r"\bnf|netflix\b", "NF")] if re.search(pat, full, re.I)), "")
    codec = "HEVC x265" if re.search(r"\b(x265|h265|hevc)\b", full, re.I) else ("AV1/VP9" if re.search(r"\b(av1|vp9)\b", full, re.I) else "x264")
    if re.search(r"\b10\s*-?bit\b", full, re.I) and "10-Bit" not in codec: codec += " 10-Bit"
    aud_codec = "Dolby Atmos 5.1" if re.search(r"\batmos\b", full, re.I) else ("DDP 5.1 (6CH)" if re.search(r"\b6ch\b|\bddp\s*5\.1\b|\baac\s*5[\.\s]1\b|\b5[\.\s]1\b", full, re.I) else "AAC 2.0")
    dur_m = re.search(r"Duration\s*:\s*([^\r\n]+)", full, re.I) or re.search(r"⏳\s*(\d{1,2}:\d{2}(?::\d{2})?)", full)
    speaker_m = re.search(r"🔊\s*([^\r\n💬╰]+)", audio_safe, re.DOTALL)
    aud_txt = f"{real_fn}\n{speaker_m.group(1) if speaker_m else ''}"
    langs = [n for p, n in [(r"\b(?:hindi|hin)\b", "Hindi"), (r"\b(?:japanese|jap|jpn)\b", "Japanese"), (r"\b(?:english|eng)\b", "English"), (r"\b(?:korean|kor)\b", "Korean"), (r"\b(?:tamil|tam)\b", "Tamil"), (r"\b(?:telugu|tel)\b", "Telugu"), (r"\b(?:malayalam|mal)\b", "Malayalam"), (r"\b(?:kannada|kan)\b", "Kannada"), (r"\b(?:turkish|tur)\b", "Turkish"), (r"\b(?:amharic|amh|ትርጉም)\b", "Amharic")] if re.search(p, aud_txt, re.I)]
    if not langs: langs.append(ORIG_LANG_MAP.get((meta.get("language") or "").lower(), "English"))
    tier = extract_audio_tier(aud_txt, len(langs))
    mode = f"Multi Audio / Dubbed ({', '.join(langs[:5])})" if tier == 3 else (f"Dual Audio ({' + '.join(langs)})" if tier == 2 else f"{langs[0]} ({aud_codec})")
    return {"real_filename": real_fn, "quality": qual, "source": source, "codec": codec, "audio_mode": mode, "audio_tier": tier, "size_str": format_size_human(file_size_bytes), "duration": dur_m.group(1).strip() if dur_m else "", "for_tv": bool("x264" in codec), "audio_lines": [f"* {aud_codec} — {l}" for l in langs]}

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
    official_fn = f"{pub_title}{year_str}{ep_str}{dub_badge} [{spec_tag}].mkv"
    orig_lang = ORIG_LANG_MAP.get((meta.get("language") or "").lower(), "")

    lines = [f"🎬 <b>{pub_title}{year_str}{ep_str}</b>"]
    if meta.get("genres"): lines.append(f"🎭 <b>Genre:</b> {meta['genres']}")
    if meta.get("rating"): lines.append(f"⭐ <b>Rating:</b> {meta['rating']}")
    if orig_lang: lines.append(f"🗣 <b>Language:</b> {orig_lang}")
    lines.extend(["", "<b>File Name:</b>", f"<code>{official_fn}</code>", ""])
    if dur: lines.append(f"<b>Duration:</b> {dur}")
    lines.append(f"<b>Specs:</b> {' • '.join(p for p in [sp['quality'], sp['source'], sp['codec'], sp['size_str']] if p)}")
    if sp["audio_mode"]: lines.append(f"<b>Audio Track:</b> {sp['audio_mode']}")
    lines.append("")
    if sp["for_tv"]: lines.append("✅ Can be played on TV")
    lines.append("💬 Subtitles: English")
    if sp["audio_lines"]: lines.extend(["", "<b>Audio List:</b>"] + sp["audio_lines"][:8])
    return "\n".join(lines)

def detect_category(filename, caption, meta):
    meta = meta or {}
    g_low, lang, fn_low = (meta.get("genres") or "").lower(), (meta.get("language") or "").lower(), (filename or "").lower()
    yr = meta.get("year") or extract_year_from_text(filename)
    ch_map = load_all_channels_from_db()
    if re.search(r"\b(anime|demon slayer|solo leveling|jujutsu|naruto|one piece|bleach|attack on titan|kimetsu|blue lock|tokyo ghoul)\b", f"{fn_low} {meta.get('public_title', '').lower()}") or ("animation" in g_low and lang == "ja"): return "anime"
    if lang == "zh" and "chinese_donghua" in ch_map: return "chinese_donghua"
    if "animation" in g_low: return "animation"
    if meta.get("is_series") or re.search(r"\b(S\d{1,2}\s*E\d{1,3}|Season\s*\d+|Episode\s*\d+|EP\.?\s*\d+)\b", fn_low, re.I): return "series"
    if lang == "am" or re.search(r"[\u1200-\u137F]+|amharic|ትርጉም", fn_low): return "amharic"
    if lang in ("ta", "te", "ml", "kn") and "south_indian" in ch_map: return "south_indian"
    if lang in ("es", "fr", "de", "it") and "european" in ch_map: return "european"
    if yr and int(yr) < 2000 and "classic_90s" in ch_map: return "classic_90s"
    for g_kw, ch_k in [("documentary", "documentary"), ("war", "war_history"), ("history", "war_history"), ("family", "family"), ("sci-fi", "scifi"), ("science fiction", "scifi"), ("fantasy", "fantasy"), ("horror", "horror"), ("mystery", "mystery"), ("thriller", "thriller"), ("crime", "crime"), ("adventure", "adventure"), ("action", "action"), ("comedy", "comedy"), ("romance", "romance"), ("drama", "romance")]:
        if g_kw in g_low and ch_k in ch_map: return ch_k
    if lang in INDIAN_LANG_CODES: return "hindi"
    if lang == "ko": return "korean"
    if lang == "tr": return "turkish"
    return "english"

async def publish_file_with_green_buttons(dest_channel: int, media, final_caption: str, is_series: bool = False):
    sent_msg = await client.send_file(dest_channel, file=media, caption=final_caption, buttons=INLINE_BUTTONS, parse_mode="HTML")
    token = WEBSITE_BOT_TOKEN or pick_bot_for_role("series" if is_series else "movies").get("token")
    if not token or not sent_msg: return sent_msg
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(f"https://api.telegram.org/bot{token}/editMessageReplyMarkup", data={"chat_id": dest_channel, "message_id": sent_msg.id, "reply_markup": json.dumps(GREEN_BOT_API_MARKUP)}, timeout=8)
    except Exception: pass
    return sent_msg

async def update_series_index_card(meta: dict, ep_tag: str, force_resend: bool = False):
    """Obeys Rule #1: Inline buttons display ONLY the Season name (no resolution tags!)."""
    if not BOT_POOL or not ep_tag or ep_tag == "MOVIE": return
    index_ch = SERIES_INDEX_CHANNEL or load_all_channels_from_db().get("series_index", 0)
    if not index_ch or index_ch == DEFAULT_CHANNELS["other"]: return
    pub_title = meta.get("public_title") or "Series"
    clean_t, slug = normalize_title_key(pub_title), make_slug_key(pub_title)
    s_m = re.match(r"^(S\d{2})", ep_tag.upper())
    season_code = s_m.group(1) if s_m else "S01"

    conn = sqlite3.connect("storage_index.db")
    row = conn.execute("SELECT index_message_id, seasons_json FROM series_index_cards WHERE slug_key=?", (slug,)).fetchone()
    existing_msg_id = row[0] if row else 0
    seasons_set = set(json.loads(row[1])) if row else set()
    for r_ep in conn.execute("SELECT DISTINCT substr(ep_tag, 1, 3) FROM indexed_files_v4 WHERE slug_key=? AND ep_tag LIKE 'S%'", (slug,)).fetchall():
        if r_ep[0]: seasons_set.add(r_ep[0].upper())
    seasons_set.add(season_code)
    sorted_seasons = sorted(list(seasons_set))

    season_rows, active_bots = [], [b for b in BOT_POOL[:2] if b.get("username")]
    for s_code in sorted_seasons:
        s_num = int(s_code[1:]) if s_code[1:].isdigit() else 1
        if len(active_bots) >= 2:
            season_rows.append([
                {"text": f"📺 Season {s_num:02d} • Bot 1 ↗", "url": f"https://t.me/{active_bots[0]['username']}?start=season_{slug}_{s_code}", "style": "primary"},
                {"text": f"📺 Season {s_num:02d} • Bot 2 ↗", "url": f"https://t.me/{active_bots[1]['username']}?start=season_{slug}_{s_code}", "style": "primary"}
            ])
        elif active_bots:
            season_rows.append([{"text": f"📺 Season {s_num:02d} ↗", "url": f"https://t.me/{active_bots[0]['username']}?start=season_{slug}_{s_code}", "style": "primary"}])
    season_rows.append(GREEN_BRAND_ROW)
    markup = {"inline_keyboard": season_rows}
    yr_str = f" ({meta['year']})" if meta.get("year") else ""
    card_text = f"📺 <b>{pub_title}{yr_str}</b>\n🎭 <b>Genre:</b> {meta.get('genres') or 'Series'} • ⭐ <b>{meta.get('rating') or 'HD'}</b>\n📦 <b>Seasons:</b> <code>{', '.join(sorted_seasons)}</code>\n\n👇 <i>Tap a Season button below to choose your Quality and receive all episodes!</i>"

    token = BOT_POOL[0]["token"]
    try:
        if existing_msg_id and force_resend:
            try: await client.delete_messages(index_ch, [existing_msg_id])
            except Exception: pass
            existing_msg_id = 0
        async with aiohttp.ClientSession() as session:
            if existing_msg_id:
                async with session.post(f"https://api.telegram.org/bot{token}/editMessageText", data={"chat_id": index_ch, "message_id": existing_msg_id, "text": card_text, "parse_mode": "HTML", "reply_markup": json.dumps(markup)}, timeout=8) as r:
                    rd = await r.json()
                    if rd.get("ok") or "not modified" in (rd.get("description") or "").lower():
                        conn.execute("INSERT OR REPLACE INTO series_index_cards VALUES (?, ?, ?, ?, ?, ?)", (slug, clean_t, pub_title, index_ch, existing_msg_id, json.dumps(sorted_seasons)))
                        conn.commit(); conn.close(); return
            async with session.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": index_ch, "text": card_text, "parse_mode": "HTML", "reply_markup": json.dumps(markup)}, timeout=8) as r:
                d = await r.json()
                if d.get("ok"):
                    conn.execute("INSERT OR REPLACE INTO series_index_cards VALUES (?, ?, ?, ?, ?, ?)", (slug, clean_t, pub_title, index_ch, d["result"]["message_id"], json.dumps(sorted_seasons)))
                    conn.commit()
    except Exception: pass
    finally: conn.close()

async def rebuild_series_index_channel() -> int:
    await sync_created_channels_from_dialogs()
    ch_map = load_all_channels_from_db()
    index_ch = SERIES_INDEX_CHANNEL or ch_map.get("series_index", 0)
    if not index_ch or index_ch == DEFAULT_CHANNELS["other"]: return 0
    old_ids = [m.id async for m in client.iter_messages(index_ch, limit=500)]
    for i in range(0, len(old_ids), 100):
        try: await client.delete_messages(index_ch, old_ids[i:i+100]); await asyncio.sleep(0.4)
        except Exception: pass
    conn = sqlite3.connect("storage_index.db"); conn.execute("DELETE FROM series_index_cards"); conn.commit(); conn.close()
    series_map = {}
    for src_key in ("series", "anime"):
        cid = ch_map.get(src_key, DEFAULT_CHANNELS[src_key])
        async for m in client.iter_messages(cid, limit=300):
            if not (m.video or m.document): continue
            fn = (m.file.name if m.file else "") or ""
            txt = f"{fn}\n{m.text or ''}"
            ep_tag = extract_season_ep_tag(txt, "S01")
            if ep_tag == "MOVIE": continue
            clean_t = normalize_title_key(extract_real_filename(fn, m.text or ""))
            if clean_t == "unknown": continue
            record_indexed_file(cid, m.id, clean_t, ep_tag, extract_resolution_tag(txt, "1080P"), extract_resolution_tag(txt, "1080P"), 2, fn)
            series_map.setdefault(make_slug_key(clean_t), (clean_t, set()))[1].add(ep_tag)
    sent_cards = 0
    for _, (clean_t, ep_tags) in series_map.items():
        await update_series_index_card(await fetch_meta(clean_t), sorted(list(ep_tags))[0], force_resend=True)
        sent_cards += 1; await asyncio.sleep(1.2)
    return sent_cards

# ============================================================================
# 6. 3-BOT INTERACTIVE QUALITY PICKER (360P / 480P / 720P / 1080P) & DELIVERY
# ============================================================================
async def poll_single_delivery_bot(bot_obj: dict):
    token, offset = bot_obj["token"], 0
    async with aiohttp.ClientSession() as session:
        try: await session.get(f"https://api.telegram.org/bot{token}/deleteWebhook?drop_pending_updates=false", timeout=8)
        except Exception: pass
        while True:
            try:
                async with session.get(f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=20", timeout=25) as resp:
                    if resp.status != 200: await asyncio.sleep(4); continue
                    for upd in ((await resp.json()).get("result") or []):
                        offset = upd["update_id"] + 1
                        cb = upd.get("callback_query")
                        if cb:
                            chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
                            await session.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery", data={"callback_query_id": cb.get("id"), "text": "📥 Sending episodes..."})
                            m_cb = re.match(r"^dlq_([a-z0-9]+)_(S\d{2})_([A-Z0-9]+)$", cb.get("data") or "", re.I)
                            if m_cb and chat_id:
                                slug, s_code, q_ch = m_cb.group(1).lower(), m_cb.group(2).upper(), m_cb.group(3).upper()
                                rows = get_season_messages_for_slug(slug, s_code, res_filter=q_ch) or get_season_messages_for_slug(slug, s_code, res_filter="ALL")
                                await session.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": f"📺 <b>Delivering {len(rows)} episode(s) for {s_code} ({q_ch})...</b>", "parse_mode": "HTML"})
                                for src_ch, src_mid, _, _ in rows:
                                    await session.post(f"https://api.telegram.org/bot{token}/copyMessage", data={"chat_id": chat_id, "from_chat_id": src_ch, "message_id": src_mid, "reply_markup": json.dumps(GREEN_BOT_API_MARKUP)})
                                    await asyncio.sleep(0.5)
                            continue
                        msg = upd.get("message") or {}
                        text, chat_id = (msg.get("text") or "").strip(), (msg.get("chat") or {}).get("id")
                        if not chat_id or not text.startswith("/start"): continue
                        m = re.match(r"^/start\s+season_([a-z0-9]+)_(S\d{2})", text, re.I)
                        if not m:
                            await session.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": "🎬 Tap any Season button in our Series Index Channel to choose your quality!", "parse_mode": "HTML"})
                            continue
                        slug, s_code = m.group(1).lower(), m.group(2).upper()
                        rows = get_season_messages_for_slug(slug, s_code, res_filter="ALL")
                        avail = get_available_season_qualities(slug, s_code) or ["1080P", "720P", "480P", "360P"]
                        q_btns = [
                            [{"text": f"{'✅' if q in avail else '🎞️'} {q}", "callback_data": f"dlq_{slug}_{s_code}_{q}", "style": "primary"} for q in ["360P", "480P"]],
                            [{"text": f"{'✅' if q in avail else '🎞️'} {q}", "callback_data": f"dlq_{slug}_{s_code}_{q}", "style": "primary"} for q in ["720P", "1080P"]],
                            [{"text": f"📦 Send All ({len(rows)} Files)", "callback_data": f"dlq_{slug}_{s_code}_ALL", "style": "success"}]
                        ]
                        await session.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": f"📺 <b>Season {s_code[1:]} Quality Selector</b>\n👇 <i>Choose your video quality:</i>", "parse_mode": "HTML", "reply_markup": json.dumps({"inline_keyboard": q_btns})})
            except Exception: await asyncio.sleep(4)

async def init_all_delivery_bots():
    global BOT_POOL
    roles = ["series", "movies", "backup"]
    async with aiohttp.ClientSession() as session:
        for idx, tok in enumerate(RAW_BOT_TOKENS):
            try:
                async with session.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=8) as r:
                    d = await r.json()
                    if d.get("ok"):
                        b_obj = {"token": tok, "username": d["result"].get("username", ""), "role": roles[idx] if idx < 3 else "backup"}
                        BOT_POOL.append(b_obj)
                        asyncio.create_task(poll_single_delivery_bot(b_obj))
            except Exception: pass
    for cid in set(load_all_channels_from_db().values()):
        await promote_all_bots_in_channel(cid)

# ============================================================================
# 7. .SYNCBOT WORKER, AUTO-DUPLICATE PAIR DELETER (.DEDUP) & .REPOPULATE
# ============================================================================
async def deduplicate_storage_channels(target_filter: str = None) -> dict:
    """
    Advanced Multi-Signal Duplicate Detector:
    Detects duplicate pairs by (file_size + clean_title + ep_tag) OR (clean_title + ep_tag + variant_key)
    and automatically deletes 1 duplicate from every pair while keeping the best copy!
    """
    await sync_created_channels_from_dialogs()
    ch_map = load_all_channels_from_db()
    index_ch = SERIES_INDEX_CHANNEL or ch_map.get("series_index", 0)
    if target_filter and target_filter.lower() not in ("all", "*"):
        target_cids = list(dict.fromkeys([m[1] for m in await resolve_account_channel_target(target_filter)]))
    else:
        target_cids = list(dict.fromkeys([cid for cid in ch_map.values() if cid and cid != index_ch]))

    seen_signatures = {}  # sig -> (cid, msg_id, audio_tier)
    deleted_pairs, scanned = 0, 0
    for cid in target_cids:
        try:
            async for m in client.iter_messages(cid, limit=400):
                if not (m.video or m.document): continue
                sz = (m.file.size if m.file else 0) or 0
                if sz < (1 * 1024 * 1024): continue
                scanned += 1
                fn = (m.file.name if m.file else "") or ""
                txt = f"{fn}\n{m.text or ''}"
                real_fn = extract_real_filename(fn, m.text or "")
                t_key = normalize_title_key(real_fn)
                if t_key == "unknown": continue
                ep_tag = extract_season_ep_tag(txt, "S01")
                res_tag = extract_resolution_tag(txt, "1080P")
                aud_tier = extract_audio_tier(txt)
                v_key = extract_movie_variant_key(txt, res_tag, aud_tier) if ep_tag == "MOVIE" else res_tag

                # Check both exact-size duplicate AND same (title, episode, variant) duplicate
                sig_size = f"SZ:{t_key}:{ep_tag}:{sz}"
                sig_var = f"VR:{t_key}:{ep_tag}:{v_key}"
                dup_found = seen_signatures.get(sig_size) or seen_signatures.get(sig_var)
                if dup_found:
                    prev_cid, prev_mid, prev_tier = dup_found
                    # Delete the older/lower-tier duplicate from the pair
                    if aud_tier > prev_tier:
                        try: await client.delete_messages(prev_cid, [prev_mid]); deleted_pairs += 1
                        except Exception: pass
                        seen_signatures[sig_size] = seen_signatures[sig_var] = (cid, m.id, aud_tier)
                    else:
                        try: await client.delete_messages(cid, [m.id]); deleted_pairs += 1
                        except Exception: pass
                    await asyncio.sleep(0.35)
                else:
                    seen_signatures[sig_size] = seen_signatures[sig_var] = (cid, m.id, aud_tier)
        except Exception: pass
    return {"scanned": scanned, "deleted": deleted_pairs, "channels": len(target_cids)}

async def sync_channels_to_website_bot(target_filter: str = None, limit_per_channel: int = 500):
    """
    Scans real media messages in storage channels:
    1. Auto-detects & deletes duplicate pairs on the fly.
    2. Adds Series into Series Index Channel & records all files in SQLite.
    3. Forwards every unique video gently (2.8s) to WEBSITE_BOT_USERNAME with origin intact.
    4. Returns a complete report of all titles found across channels!
    """
    global SYNCBOT_RUNNING, SYNCBOT_STOP
    if SYNCBOT_RUNNING: return {"ok": False, "msg": "A .syncbot scan is already running! Send .syncbot stop to stop it."}
    if not WEBSITE_BOT_USERNAME: return {"ok": False, "msg": "Set WEBSITE_BOT_USERNAME (without @) in your Render environment variables first."}

    SYNCBOT_RUNNING, SYNCBOT_STOP = True, False
    forwarded, skipped, failed, dups_deleted = 0, 0, 0, 0
    found_titles_report = {}  # {ch_name: set_of_titles}
    seen_in_sync = set()
    try:
        await sync_created_channels_from_dialogs()
        ch_map = load_all_channels_from_db()
        id_to_name = {cid: k.upper() for k, cid in ch_map.items()}
        index_ch = SERIES_INDEX_CHANNEL or ch_map.get("series_index", 0)
        verify_ch = int(os.environ.get("VERIFY_CHANNEL_ID", "0") or "0")

        if target_filter and target_filter.lower() not in ("all", "*"):
            target_cids = list(dict.fromkeys([m[1] for m in await resolve_account_channel_target(target_filter)]))
        else:
            target_cids = list(dict.fromkeys([cid for cid in ch_map.values() if cid and cid != index_ch and cid != verify_ch]))
        if not target_cids: return {"ok": False, "msg": f"No storage channels matched '{target_filter}'."}

        for cid in target_cids:
            if SYNCBOT_STOP: break
            unlocked_temp = False
            ch_lbl = id_to_name.get(cid, str(cid))
            found_titles_report.setdefault(ch_lbl, set())
            try:
                async for m in client.iter_messages(cid, limit=limit_per_channel):
                    if SYNCBOT_STOP: break
                    if not (m.video or m.document): continue
                    fn = (m.file.name if m.file else "") or ""
                    mime = (m.file.mime_type if m.file else "") or ""
                    sz = (m.file.size if m.file else 0) or 0
                    if mime.startswith(("image/", "audio/")) or fn.lower().endswith((".srt", ".sub", ".ass", ".txt", ".jpg", ".png", ".webp")): continue
                    if not m.video and sz < (1 * 1024 * 1024): continue

                    txt = f"{fn}\n{m.text or ''}"
                    real_fn = extract_real_filename(fn, m.text or "")
                    t_key = normalize_title_key(real_fn)
                    ep_tag = extract_season_ep_tag(txt, "S01")
                    res_tag = extract_resolution_tag(txt, "1080P")
                    aud_tier = extract_audio_tier(txt)
                    v_key = extract_movie_variant_key(txt, res_tag, aud_tier) if ep_tag == "MOVIE" else res_tag

                    # Auto-detect & delete duplicate pair inside the channel!
                    dup_sig = f"{t_key}:{ep_tag}:{v_key}:{sz}"
                    if t_key != "unknown" and dup_sig in seen_in_sync:
                        try: await client.delete_messages(cid, [m.id]); dups_deleted += 1
                        except Exception: pass
                        continue
                    if t_key != "unknown":
                        seen_in_sync.add(dup_sig)
                        found_titles_report[ch_lbl].add(t_key.title())
                        record_indexed_file(cid, m.id, t_key, ep_tag, res_tag, v_key, aud_tier, real_fn)
                        # Automatically add Series to the Series Index Channel!
                        if ep_tag != "MOVIE":
                            meta_s = await fetch_meta(t_key)
                            await update_series_index_card(meta_s, ep_tag)

                    if is_msg_synced_to_web(cid, m.id):
                        skipped += 1; continue

                    try:
                        await client.forward_messages(WEBSITE_BOT_USERNAME, m.id, cid)
                        mark_msg_synced_to_web(cid, m.id); forwarded += 1
                    except ChatForwardsRestrictedError:
                        try:
                            await client(ToggleNoForwardsRequest(peer=cid, enabled=False))
                            unlocked_temp = True; await asyncio.sleep(1.0)
                            await client.forward_messages(WEBSITE_BOT_USERNAME, m.id, cid)
                            mark_msg_synced_to_web(cid, m.id); forwarded += 1
                        except Exception: failed += 1
                    except FloodWaitError as fw:
                        await asyncio.sleep(fw.seconds + 3)
                        try:
                            await client.forward_messages(WEBSITE_BOT_USERNAME, m.id, cid)
                            mark_msg_synced_to_web(cid, m.id); forwarded += 1
                        except Exception: failed += 1
                    except Exception: failed += 1
                    await asyncio.sleep(2.8)
            finally:
                if unlocked_temp:
                    try: await client(ToggleNoForwardsRequest(peer=cid, enabled=True))
                    except Exception: pass

        summary_lines = [f"• <b>{k} ({len(v)}):</b> {', '.join(sorted(list(v))[:8])}" for k, v in found_titles_report.items() if v]
        return {"ok": True, "forwarded": forwarded, "skipped": skipped, "failed": failed, "dups_deleted": dups_deleted, "channels": len(target_cids), "files_summary": "\n".join(summary_lines[:15])}
    finally:
        SYNCBOT_RUNNING = False

async def wipe_and_reupload_storage_channel(target_channel_str: str, redownload_from_bots: bool = False) -> str:
    await sync_created_channels_from_dialogs()
    matches = await resolve_account_channel_target(target_channel_str)
    if not matches: return f"⚠️ No channel matching <code>{target_channel_str}</code>."
    _, cid, full_name = matches[0]
    collected, titles = [], set()
    conn = sqlite3.connect("storage_index.db")
    for r in conn.execute("SELECT DISTINCT clean_title FROM indexed_files_v4 WHERE channel_id=?", (cid,)).fetchall():
        if r[0] and r[0] != "unknown": titles.add(r[0])
    async for m in client.iter_messages(cid, limit=500):
        if m.video or m.document:
            tk = normalize_title_key(extract_real_filename(m.file.name if m.file else "", m.text or ""))
            if tk != "unknown": titles.add(tk)
            collected.append(m)
    conn.execute("DELETE FROM indexed_files_v4 WHERE channel_id=?", (cid,)); conn.commit(); conn.close()
    if redownload_from_bots or not collected:
        ids = [m.id for m in collected]
        for i in range(0, len(ids), 100):
            try: await client.delete_messages(cid, ids[i:i+100]); await asyncio.sleep(0.5)
            except Exception: pass
        for tn in sorted(list(titles)): TITLE_QUEUE.append(({"raw": tn.title(), "title": tn, "year": None, "mode": "normal", "season": None, "episodes": None}, True))
        if titles: asyncio.create_task(process_queue())
        return f"🗑️ <b>Wiped <code>{full_name}</code> ({len(ids)} posts) & Auto-Queued <code>{len(titles)}</code> Title(s)!</b>"
    collected.reverse(); reup = 0
    for m in collected:
        try:
            fn, txt = (m.file.name if m.file else "") or "", m.text or ""
            rfn = extract_real_filename(fn, txt)
            meta = await fetch_meta(rfn, extract_year_from_text(rfn))
            if meta.get("title") == "unknown": continue
            ep_tag, res_tag, at = extract_season_ep_tag(f"{rfn}\n{txt}", "S01"), extract_resolution_tag(f"{rfn}\n{txt}", "1080P"), extract_audio_tier(f"{rfn}\n{txt}")
            vk = extract_movie_variant_key(f"{rfn}\n{txt}", res_tag, at) if ep_tag == "MOVIE" else res_tag
            sent = await publish_file_with_green_buttons(cid, m.media, build_final_caption(fn or rfn, txt, meta, m.file.size if m.file else 0, ep_tag), is_series=(ep_tag != "MOVIE"))
            if sent:
                await client.delete_messages(cid, [m.id])
                record_indexed_file(cid, sent.id, meta["public_title"], ep_tag, res_tag, vk, at, rfn)
                if ep_tag != "MOVIE": await update_series_index_card(meta, ep_tag)
                reup += 1; await asyncio.sleep(2.5)
        except Exception: pass
    return f"♻️ <b>Wiped & Re-Uploaded <code>{reup}</code> files in <code>{full_name}</code>!</b>"

async def resync_files_for_website(target_query: str = None, max_items: int = 8) -> int:
    await sync_created_channels_from_dialogs()
    ch_map = load_all_channels_from_db()
    clean_q = normalize_title_key(target_query) if target_query else None
    seen, resynced = set(), 0
    for cid in list(dict.fromkeys(ch_map.values())):
        if resynced >= max_items: break
        try:
            kw = {"limit": 45, **({"search": target_query} if clean_q and clean_q != "unknown" else {})}
            async for m in client.iter_messages(cid, **kw):
                if resynced >= max_items: break
                if not (m.video or m.document) or (m.file and (m.file.size or 0) < MIN_VIDEO_SIZE_BYTES): continue
                fn, txt = (m.file.name if m.file else "") or "", m.text or ""
                rfn = extract_real_filename(fn, txt); tk = normalize_title_key(rfn)
                if tk == "unknown" or (clean_q and clean_q not in tk and tk not in clean_q): continue
                ep_tag = extract_season_ep_tag(f"{rfn}\n{txt}", "S01")
                gk = f"{tk}:{ep_tag[:3] if ep_tag != 'MOVIE' else 'MOVIE'}"
                if not target_query and gk in seen: continue
                seen.add(gk)
                meta = await fetch_meta(rfn, extract_year_from_text(rfn))
                dcid = await get_or_create_genre_channel(detect_category(rfn, txt, meta))
                res_tag, at = extract_resolution_tag(f"{rfn}\n{txt}", "1080P"), extract_audio_tier(f"{rfn}\n{txt}")
                vk = extract_movie_variant_key(f"{rfn}\n{txt}", res_tag, at) if ep_tag == "MOVIE" else res_tag
                sent = await publish_file_with_green_buttons(dcid, m.media, build_final_caption(fn or rfn, txt, meta, m.file.size if m.file else 0, ep_tag), is_series=(ep_tag != "MOVIE"))
                if sent:
                    await client.delete_messages(cid, [m.id])
                    record_indexed_file(dcid, sent.id, meta["public_title"], ep_tag, res_tag, vk, at, rfn)
                    if ep_tag != "MOVIE": await update_series_index_card(meta, ep_tag, force_resend=True)
                    resynced += 1; await asyncio.sleep(2.5)
        except Exception: pass
    return resynced

async def rename_and_readjust_all_channels() -> int:
    await sync_created_channels_from_dialogs()
    fixed, index_ch = 0, SERIES_INDEX_CHANNEL or load_all_channels_from_db().get("series_index", 0)
    for ch_id in sorted(list(set(load_all_channels_from_db().values()))):
        if ch_id == index_ch: continue
        try:
            async for msg in client.iter_messages(ch_id, limit=45):
                if not (msg.video or msg.document) or (msg.file and (msg.file.size or 0) < MIN_VIDEO_SIZE_BYTES): continue
                fn, txt = (msg.file.name if msg.file else "") or "", msg.text or ""
                rfn = extract_real_filename(fn, txt)
                if normalize_title_key(rfn) == "unknown": continue
                meta = await fetch_meta(rfn, extract_year_from_text(rfn))
                pub = meta.get("public_title") or ""
                cch = await get_or_create_genre_channel(detect_category(rfn, txt, meta))
                if ch_id != cch or "🎬" not in txt or (pub and pub.lower() not in txt.lower()):
                    ep_tag, res_tag, at = extract_season_ep_tag(f"{rfn}\n{txt}", "S01"), extract_resolution_tag(f"{rfn}\n{txt}", "1080P"), extract_audio_tier(f"{rfn}\n{txt}")
                    vk = extract_movie_variant_key(f"{rfn}\n{txt}", res_tag, at) if ep_tag == "MOVIE" else res_tag
                    sent = await publish_file_with_green_buttons(cch, msg.media, build_final_caption(fn or rfn, txt, meta, msg.file.size if msg.file else 0, ep_tag), is_series=(ep_tag != "MOVIE"))
                    if sent:
                        await client.delete_messages(ch_id, [msg.id])
                        record_indexed_file(cch, sent.id, pub, ep_tag, res_tag, vk, at, rfn)
                        fixed += 1; await asyncio.sleep(2.5)
        except Exception: pass
    return fixed

# ============================================================================
# 8. EXACT-MATCH BUTTON SCORER & PAGINATION HELPERS
# ============================================================================
def score_quality_button(text: str) -> int:
    t = text.upper()
    s = 1200 if ("2160P" in t or "4K" in t) else (1000 if "1080P" in t else (720 if "720P" in t else (400 if "480P" in t else (200 if "360P" in t else 0))))
    if any(k in t for k in ["MULTI", "DUAL", "DUB", "HIN", "ENG", "ATMOS", "DDP5.1"]): s += 450
    if "BLURAY" in t or "BD" in t: s += 200
    elif "WEB-DL" in t: s += 150
    if any(k in t for k in ["HEVC", "X265", "10BIT", "AV1"]): s += 60
    return s

def find_matching_title_buttons_on_page(buttons, query: str, target_year: int = None, visited_keys: set = None):
    visited_keys, clean_q = visited_keys or set(), normalize_title_key(query)
    exact_cands, partial_cands = [], []
    for r_idx, row in enumerate(buttons):
        for c_idx, btn in enumerate(row):
            txt = (btn.text or "").strip(); low = txt.lower()
            if any(skip in low for skip in SKIP_NAV_WORDS): continue
            if f"{txt}_{r_idx}_{c_idx}" in visited_keys or txt in visited_keys or not is_strict_title_and_year_match(txt, query, target_year): continue
            btn_clean, score = normalize_title_key(txt), 100
            if "📺" in txt or "season" in low:
                score += 1200
                sc = re.search(r"\b(\d{1,2})\s*seasons?\b", low)
                if sc: score += int(sc.group(1)) * 50
            if target_year and str(target_year) in txt: score += 500
            score += score_quality_button(txt) // 4
            if btn_clean == clean_q: exact_cands.append((score + 2000, txt, btn))
            elif btn_clean.startswith(clean_q): partial_cands.append((score + 200, txt, btn))
    if exact_cands: exact_cands.sort(key=lambda x: x[0], reverse=True); return exact_cands
    partial_cands.sort(key=lambda x: x[0], reverse=True); return partial_cands

def collect_resolution_quality_buttons(buttons, visited_qualities: set = None):
    visited_qualities, res_list = visited_qualities or set(), []
    for row in buttons:
        for btn in row:
            txt = (btn.text or "").strip()
            if any(skip in txt.lower() for skip in SKIP_NAV_WORDS) or txt in visited_qualities: continue
            s = score_quality_button(txt)
            if s > 0: res_list.append((s, extract_resolution_tag(txt, "OTHER"), txt, btn))
    res_list.sort(key=lambda x: x[0], reverse=True)
    return [(r, txt, btn) for _, r, txt, btn in res_list]

def parse_size_mb(text: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(GB|MB|KB)", text or "", re.I)
    if not m: return 0.0
    v, u = float(m.group(1)), m.group(2).upper()
    return v * 1024.0 if u == "GB" else (v / 1024.0 if u == "KB" else v)

async def auto_join_channel(url: str):
    try:
        if "+" in url or "joinchat" in url: await client(ImportChatInviteRequest(url.split("+")[-1].split("/")[-1].strip()))
        else:
            un = url.split("t.me/")[-1].split("/")[0].split("?")[0].strip()
            if un: await client(JoinChannelRequest(un))
    except Exception: pass

async def smart_click_button(btn, bot_entity: str = None):
    if getattr(btn, "url", None):
        if "start=" in btn.url:
            p = urlparse(btn.url); tb = p.path.strip("/") or bot_entity or SOURCE_BOT
            sp = parse_qs(p.query).get("start", [""])[0]
            if tb and sp: await client.send_message(tb, f"/start {sp}"); return True
        return False
    try:
        res = await asyncio.wait_for(btn.click(), timeout=2.5)
        ru = getattr(res, "url", None)
        if ru and "start=" in ru:
            p = urlparse(ru); tb = p.path.strip("/") or bot_entity or SOURCE_BOT
            sp = parse_qs(p.query).get("start", [""])[0]
            if tb and sp: await client.send_message(tb, f"/start {sp}")
    except asyncio.TimeoutError: pass
    except FloodWaitError as fw: await asyncio.sleep(fw.seconds + 2)
    except Exception: pass
    return True

def get_next_page_button(msg):
    if not msg or not msg.buttons: return None
    for row in msg.buttons:
        for btn in row:
            if any(k in (btn.text or "").strip().lower() for k in ["next", "⏩", "➡", "⏭", "»"]): return btn
    return None

def get_menu_signature(msg) -> str:
    return f"{msg.id}:{msg.text or ''}:{'|'.join(b.text or '' for r in msg.buttons for b in r)}" if (msg and msg.buttons) else ""

async def find_latest_menu_message(bot_entity, tracked_msg_id=None):
    if tracked_msg_id:
        msg = await client.get_messages(bot_entity, ids=tracked_msg_id)
        if msg and msg.buttons: return msg
    for m in await client.get_messages(bot_entity, limit=10):
        if not m or not m.buttons: continue
        bt = [b.text or "" for r in m.buttons for b in r]
        if len(bt) == 1 and any(x in bt[0].lower() for x in ["auto-delete", "close", "subtitles"]): continue
        return m
    return None

async def wait_for_menu_update(bot_entity, tracked_msg_id, old_sig: str, max_wait: float = 4.5):
    for _ in range(int(max_wait / 0.4)):
        await asyncio.sleep(0.4)
        msg = await find_latest_menu_message(bot_entity, tracked_msg_id)
        if msg and get_menu_signature(msg) != old_sig: return msg
    return await find_latest_menu_message(bot_entity, tracked_msg_id)

def is_season_menu(msg) -> bool:
    if not msg or not msg.buttons: return False
    if "choose the season" in (msg.text or "").lower(): return True
    return any(re.search(r"^(?:📺\s*)?(?:Season\s*\d+|S\d{1,2}\s*[·•\-]\s*\d+\s*eps?)", (b.text or "").strip(), re.I) for r in msg.buttons for b in r)

def is_file_or_episode_grid(msg) -> bool:
    if not msg or not msg.buttons: return False
    t = (msg.text or "").lower()
    if "choose the correct title" in t or "search results for" in t or is_season_menu(msg): return False
    if "total episodes" in t or "total files" in t: return True
    return any(re.search(r"\b(?:S\d{1,2})?E\d{1,3}\b", b.text or "", re.I) or (re.search(r"(?:\[|\·|\•)\s*\d+(?:\.\d+)?\s*(?:MB|GB)\b", b.text or "", re.I) and "quality" not in (b.text or "").lower()) for r in msg.buttons for b in r)

# ============================================================================
# 9. INCOMING MEDIA ARRIVAL & 10. DUAL-BOT NAVIGATOR/DOWNLOADER
# ============================================================================
@client.on(events.NewMessage(incoming=True))
async def on_media_arrival(event):
    if not event.is_private: return
    sender = await event.get_sender()
    if not getattr(sender, "bot", False) or not (event.video or event.document): return
    msg = event.message
    file_size = (msg.file.size if msg.file else 0) or 0
    raw_fn = (msg.file.name if msg.file else None) or (msg.text or "Media.mkv").split("\n")[0]
    if file_size < MIN_VIDEO_SIZE_BYTES or raw_fn.lower().endswith((".srt", ".sub", ".ass", ".zip", ".rar", ".txt")): return
    fid = f"{msg.id}_{file_size}"
    if fid in CAPTURED_FILES: return
    CAPTURED_FILES.add(fid)

    caption = msg.text or ""
    real_fn = extract_real_filename(raw_fn, caption)
    combined = f"{real_fn}\n{caption}"
    if ACTIVE_CLEAN_TITLE and not is_strict_title_and_year_match(real_fn, ACTIVE_CLEAN_TITLE, ACTIVE_TARGET_YEAR):
        FILE_COMPLETED_EVENT.set(); return

    t_meta = ACTIVE_CLEAN_TITLE or normalize_title_key(real_fn)
    if not t_meta or t_meta == "unknown": FILE_COMPLETED_EVENT.set(); return
    meta = await fetch_meta(t_meta, ACTIVE_TARGET_YEAR or extract_year_from_text(real_fn))
    pub_key = normalize_title_key(meta.get("public_title") or t_meta)
    if pub_key == "unknown": FILE_COMPLETED_EVENT.set(); return

    sp = extract_tech_specs(raw_fn, caption, file_size, meta)
    cat = detect_category(real_fn, caption, meta)
    dest_ch = await get_or_create_genre_channel(cat)
    ep_tag = ACTIVE_EP_TAG or extract_season_ep_tag(combined, ACTIVE_SEASON_TAG)
    res_tag = extract_resolution_tag(combined, ACTIVE_RES_TAG or "1080P")
    title_key = ACTIVE_CLEAN_TITLE or pub_key
    v_key = extract_movie_variant_key(combined, res_tag, sp["audio_tier"]) if ep_tag == "MOVIE" else res_tag

    existing = get_stored_file_record(pub_key, ep_tag, v_key) or get_stored_file_record(title_key, ep_tag, v_key)
    if existing:
        old_ch, old_mid, old_tier = existing
        if sp["audio_tier"] <= (old_tier or 1) and old_ch == dest_ch:
            FILE_COMPLETED_EVENT.set(); return
        elif old_ch and old_mid:
            try: await client.delete_messages(old_ch, [old_mid])
            except Exception: pass

    try:
        sent_msg = await publish_file_with_green_buttons(dest_ch, msg.media, build_final_caption(raw_fn, caption, meta, file_size, ep_tag), is_series=(ep_tag != "MOVIE"))
        sent_id = sent_msg.id if sent_msg else 0
        record_indexed_file(dest_ch, sent_id, title_key, ep_tag, res_tag, v_key, sp["audio_tier"], real_fn)
        record_indexed_file(dest_ch, sent_id, pub_key, ep_tag, res_tag, v_key, sp["audio_tier"], real_fn)
        if ep_tag != "MOVIE": await update_series_index_card(meta, ep_tag)
        await client.send_message("me", f"✅ <b>Published:</b> <code>{meta['public_title']}</code> [{ep_tag} • {v_key}] ➔ {CATEGORY_EMOJIS.get(cat, '📁')} <code>{cat.upper()}</code>", parse_mode="HTML")
    except FloodWaitError as fw: await asyncio.sleep(fw.seconds + 2)
    except Exception: pass
    finally: FILE_COMPLETED_EVENT.set()

async def navigate_to_grid(query: str, target_season_num: int = 1, target_res: str = None, target_year: int = None, send_fresh: bool = True, visited_title_btns: set = None, visited_quality_btns: set = None, bot_entity: str = None):
    bot_entity = bot_entity or SOURCE_BOT
    visited_title_btns = visited_title_btns if visited_title_btns is not None else set()
    visited_quality_btns = visited_quality_btns if visited_quality_btns is not None else set()
    if send_fresh:
        await client.send_message(bot_entity, f"{query} {target_year}".strip() if target_year else query)
        await asyncio.sleep(2.0)

    tracked_id, avail_seasons, disc_res = None, [], []
    retried_ny, t_pages, q_pages, empties = False, 0, 0, 0
    for _ in range(36):
        if SKIP_CURRENT_TITLE: break
        msg = await find_latest_menu_message(bot_entity, tracked_id)
        if not msg or not msg.buttons:
            if target_year and not retried_ny:
                retried_ny = True; await client.send_message(bot_entity, query); await asyncio.sleep(2.0); continue
            empties += 1
            if empties >= 4: break
            await asyncio.sleep(0.8); continue
        empties, tracked_id, text, old_sig = 0, msg.id, (msg.text or "").lower(), get_menu_signature(msg)

        joined = False
        for row in msg.buttons:
            for btn in row:
                if getattr(btn, "url", None) and "t.me/" in btn.url and any(w in (btn.text or "").lower() for w in ["join", "channel", "subscribe", "backup"]):
                    await auto_join_channel(btn.url); joined = True; await asyncio.sleep(1.0)
        if joined:
            for row in msg.buttons:
                for btn in row:
                    if any(w in (btn.text or "").lower() for w in ["try again", "joined", "refresh", "check"]):
                        await smart_click_button(btn, bot_entity); await wait_for_menu_update(bot_entity, tracked_id, old_sig); break
            continue

        if is_season_menu(msg):
            s_btns, fb = {}, None
            for row in msg.buttons:
                for btn in row:
                    low = (btn.text or "").strip().lower()
                    if any(sk in low for sk in SKIP_NAV_WORDS): continue
                    m = re.search(r"(?:season\s*|s)0*(\d+)", low)
                    if m: s_btns[int(m.group(1))] = btn
                    elif "season all" in low or "episodes" in low: fb = btn
            avail_seasons = sorted(s_btns.keys())
            await smart_click_button(s_btns.get(target_season_num) or fb or msg.buttons[0][0], bot_entity)
            await wait_for_menu_update(bot_entity, tracked_id, old_sig); continue

        if is_file_or_episode_grid(msg): return msg, avail_seasons, (disc_res or TARGET_RESOLUTIONS)

        if "choose the correct title" in text or "matching results" in text or "search results for" in text or (len(msg.buttons) >= 2 and "quality" not in text):
            cands = find_matching_title_buttons_on_page(msg.buttons, query, target_year, visited_title_btns)
            if cands:
                _, btn_txt, chosen_btn = cands[0]
                is_sf = ("📺" in btn_txt or "season" in btn_txt.lower())
                if not is_sf: visited_title_btns.add(btn_txt)
                btn_res, btn_tier = extract_resolution_tag(btn_txt, "HD"), extract_audio_tier(btn_txt)
                if not is_sf and btn_res != "HD" and is_already_in_any_channel(query, "MOVIE", extract_movie_variant_key(btn_txt, btn_res, btn_tier), min_audio_tier=btn_tier): continue
                FILE_COMPLETED_EVENT.clear()
                await smart_click_button(chosen_btn, bot_entity)
                upd = await wait_for_menu_update(bot_entity, tracked_id, old_sig, max_wait=5.0)
                if not is_sf and (not upd or get_menu_signature(upd) == old_sig):
                    try: await asyncio.wait_for(FILE_COMPLETED_EVENT.wait(), timeout=45)
                    except asyncio.TimeoutError: pass
                    await asyncio.sleep(2.5)
                continue
            nxt = get_next_page_button(msg)
            if nxt and t_pages < 6:
                t_pages += 1; await smart_click_button(nxt, bot_entity); await wait_for_menu_update(bot_entity, tracked_id, old_sig); continue
            return None, [], []

        if "select the available quality" in text or "quality" in text:
            r_opts = collect_resolution_quality_buttons(msg.buttons, visited_quality_btns)
            for rt, _, _ in r_opts:
                if rt not in disc_res: disc_res.append(rt)
            c_btn, c_txt = None, ""
            if target_res:
                for rt, rtxt, rb in r_opts:
                    if rt == target_res: c_btn, c_txt = rb, rtxt; break
                if not c_btn:
                    nq = get_next_page_button(msg)
                    if nq and q_pages < 6:
                        q_pages += 1; await smart_click_button(nq, bot_entity); await wait_for_menu_update(bot_entity, tracked_id, old_sig); continue
                    return None, avail_seasons, disc_res
            else:
                for rt, rtxt, rb in r_opts:
                    if not is_already_in_any_channel(query, "MOVIE", extract_movie_variant_key(rtxt, rt, extract_audio_tier(rtxt)), min_audio_tier=extract_audio_tier(rtxt)):
                        c_btn, c_txt = rb, rtxt; break
                if not c_btn and r_opts: c_txt, c_btn = r_opts[0][1], r_opts[0][2]
            if c_btn:
                visited_quality_btns.add(c_txt)
                await smart_click_button(c_btn, bot_entity); await wait_for_menu_update(bot_entity, tracked_id, old_sig); continue
            break
        await asyncio.sleep(0.8)
    return None, avail_seasons, disc_res

async def download_from_grid(initial_msg, query: str, season_num: int, active_res: str, target_episodes: set = None, only_missing: bool = False, target_year: int = None, bot_entity: str = None):
    global ACTIVE_SEASON_TAG, ACTIVE_EP_TAG, ACTIVE_RES_TAG
    bot_entity, ACTIVE_SEASON_TAG, ACTIVE_RES_TAG = bot_entity or SOURCE_BOT, f"S{season_num:02d}", active_res or "1080P"
    menu_id, cur_msg, cur_page = initial_msg.id, initial_msg, 1
    rem = set(target_episodes) if target_episodes else None
    while not SKIP_CURRENT_TITLE:
        best_map, order = {}, []
        for row in cur_msg.buttons:
            for btn in row:
                txt = (btn.text or "").strip()
                if any(n in txt.lower() for n in SKIP_NAV_WORDS): continue
                if target_year:
                    yrs = [int(y) for y in re.findall(r"\b(19[5-9]\d|20[0-3]\d)\b", txt)]
                    if yrs and target_year not in yrs: continue
                em = re.search(r"\b(?:S\d{1,2})?(?:E|EP\.?)0*(\d{1,3})\b", txt, re.I)
                en = int(em.group(1)) if em else None
                if en is not None:
                    if rem is not None and en not in rem: continue
                    ep_tag = f"{ACTIVE_SEASON_TAG}E{en:02d}"
                else:
                    if rem is not None: continue
                    ep_tag = "MOVIE"
                br, bt = extract_resolution_tag(txt, ACTIVE_RES_TAG), extract_audio_tier(txt)
                vk = extract_movie_variant_key(txt, br, bt) if ep_tag == "MOVIE" else br
                sz = parse_size_mb(txt) + (bt * 250.0)
                ik = (ep_tag, br, vk, en)
                if ik not in best_map: order.append(ik); best_map[ik] = (sz, btn, bt)
                elif sz > best_map[ik][0]: best_map[ik] = (sz, btn, bt)

        for ep_tag, br, vk, en in order:
            if SKIP_CURRENT_TITLE: break
            _, btn, bt = best_map[(ep_tag, br, vk, en)]
            if is_already_in_any_channel(ACTIVE_CLEAN_TITLE, ep_tag, "ANY" if only_missing else vk, min_audio_tier=bt):
                if rem is not None and en in rem: rem.discard(en)
                continue
            ACTIVE_EP_TAG, ACTIVE_RES_TAG = ep_tag, br
            FILE_COMPLETED_EVENT.clear()
            await smart_click_button(btn, bot_entity)
            try:
                await asyncio.wait_for(FILE_COMPLETED_EVENT.wait(), timeout=85)
                if rem is not None and en in rem: rem.discard(en)
            except asyncio.TimeoutError: pass
            await asyncio.sleep(2.5)
            chk = await client.get_messages(bot_entity, ids=menu_id)
            if not chk or not chk.buttons:
                rec, _, _ = await navigate_to_grid(query, season_num, active_res, target_year, send_fresh=True, bot_entity=bot_entity)
                if rec:
                    menu_id, cur_msg = rec.id, rec
                    for _ in range(1, cur_page):
                        nx = get_next_page_button(cur_msg)
                        if nx:
                            osig = get_menu_signature(cur_msg)
                            await smart_click_button(nx, bot_entity); cur_msg = await wait_for_menu_update(bot_entity, menu_id, osig)
        if rem is not None and len(rem) == 0: break
        ref = await client.get_messages(bot_entity, ids=menu_id)
        if ref and ref.buttons: cur_msg = ref
        nxt = get_next_page_button(cur_msg)
        if nxt and cur_page < 15:
            cur_page += 1; osig = get_menu_signature(cur_msg)
            await smart_click_button(nxt, bot_entity); cur_msg = await wait_for_menu_update(bot_entity, menu_id, osig)
            if not cur_msg or not cur_msg.buttons: break
        else: break

# ============================================================================
# 11. REAL-TIME RADAR, 24/7 MULTI-INDUSTRY CRAWLER & 12. QUEUE WORKER
# ============================================================================
CRAWLER_MODES = ["movie", "anime", "animation", "bollywood", "korean", "turkish", "amharic", "horror", "action", "scifi", "crime", "chinese_donghua", "tv"]

async def fetch_discover_batch(year: int, page: int, media_kind: str) -> list:
    disc, today_str, now_yr = [], current_utc_date_str(), current_utc_year()
    async with aiohttp.ClientSession() as session:
        if TMDB_API_KEY and media_kind != "amharic":
            async with TMDB_SEMAPHORE:
                try:
                    ep = "tv" if media_kind in ("tv", "anime") else "movie"
                    yk, dl = ("first_air_date_year", "first_air_date.lte") if ep == "tv" else ("primary_release_year", "primary_release_date.lte")
                    df = f"&{yk}={year}" + (f"&{dl}={today_str}" if year >= now_yr else "")
                    sp = "&sort_by=popularity.desc" if page == 1 else ("&sort_by=vote_average.desc&vote_count.gte=80" if page == 2 else "&sort_by=vote_average.desc&vote_count.gte=15")
                    em = {"anime": "&with_genres=16&with_original_language=ja", "animation": "&with_genres=16", "bollywood": "&with_original_language=hi|ta|te|ml|kn", "korean": "&with_original_language=ko", "turkish": "&with_original_language=tr", "chinese_donghua": "&with_original_language=zh", "horror": "&with_genres=27|53", "action": "&with_genres=28|12", "scifi": "&with_genres=878|14", "crime": "&with_genres=80|9648"}
                    async with session.get(f"https://api.themoviedb.org/3/discover/{ep}?api_key={TMDB_API_KEY}&language=en-US{df}{sp}{em.get(media_kind, '')}&page={page}", timeout=5) as dr:
                        if dr.status == 200:
                            for it in (await dr.json()).get("results", []):
                                nm = it.get("title") or it.get("name")
                                if nm: disc.append((nm, None if ep == "tv" else year))
                    await asyncio.sleep(0.2)
                except Exception: pass
    return disc

async def auto_discovery_crawler_worker():
    await asyncio.sleep(10.0)
    while True:
        try:
            if not AUTO_DISCOVERY_ENABLED or CURRENT_PIPELINE_ACTIVE or len(TITLE_QUEUE) > 0:
                await asyncio.sleep(5.0); continue
            year, page, kind = get_crawler_progress()
            if kind not in CRAWLER_MODES: kind = "movie"
            queued = False
            for tn, yr in await fetch_discover_batch(year, page, kind):
                ct = normalize_title_key(tn)
                if ct == "unknown" or is_title_crawled_recently(ct, 12): continue
                mark_title_crawled(ct, yr)
                if len(get_existing_resolutions(ct, "MOVIE")) >= 4: continue
                TITLE_QUEUE.append(({"raw": f"{tn} ({yr})" if yr else tn, "title": tn, "year": yr, "mode": "normal", "season": None, "episodes": None}, True))
                asyncio.create_task(process_queue()); queued = True; break
            if not queued:
                ni = (CRAWLER_MODES.index(kind) + 1) % len(CRAWLER_MODES)
                if ni != 0: save_crawler_progress(year, page, CRAWLER_MODES[ni])
                elif page < 3: save_crawler_progress(year, page + 1, "movie")
                else: save_crawler_progress(year - 1 if year > 1990 else current_utc_year(), 1, "movie")
            await asyncio.sleep(6.0)
        except Exception: await asyncio.sleep(10.0)

async def run_extraction_on_bot(bot_entity: str, popular_title: str, raw_query: str, start_season: int, target_year: int = None, target_eps: set = None, only_missing: bool = False, send_initial: bool = True) -> bool:
    v_t, v_q = set(), set()
    before = get_existing_resolutions(ACTIVE_CLEAN_TITLE, "MOVIE")
    g_msg, all_s, _ = await navigate_to_grid(popular_title, start_season, None, target_year, send_initial, v_t, v_q, bot_entity)
    if not g_msg and not v_t and normalize_title_key(raw_query) != ACTIVE_CLEAN_TITLE:
        g_msg, all_s, _ = await navigate_to_grid(raw_query, start_season, None, target_year, True, v_t, v_q, bot_entity)
    if not g_msg: return len(get_existing_resolutions(ACTIVE_CLEAN_TITLE, "MOVIE")) > len(before) or bool(v_t)
    if target_eps:
        await download_from_grid(g_msg, popular_title, start_season, ACTIVE_RES_TAG or "1080P", target_eps, only_missing, target_year, bot_entity); return True
    seasons = [start_season] if only_missing else (all_s if all_s else [start_season])
    await download_from_grid(g_msg, popular_title, seasons[0], ACTIVE_RES_TAG or "1080P", None, only_missing, target_year, bot_entity)
    for rt in TARGET_RESOLUTIONS:
        if SKIP_CURRENT_TITLE: break
        await asyncio.sleep(1.5)
        rg, _, _ = await navigate_to_grid(popular_title, seasons[0], rt, target_year, True, visited_quality_btns=v_q, bot_entity=bot_entity)
        if rg and not SKIP_CURRENT_TITLE: await download_from_grid(rg, popular_title, seasons[0], rt, None, only_missing, target_year, bot_entity)
    for ns in seasons[1:]:
        if SKIP_CURRENT_TITLE or ns <= seasons[0]: continue
        sq = set()
        for rt in TARGET_RESOLUTIONS:
            if SKIP_CURRENT_TITLE: break
            await asyncio.sleep(1.5)
            rg, _, _ = await navigate_to_grid(popular_title, ns, rt, target_year, True, visited_quality_btns=sq, bot_entity=bot_entity)
            if rg and not SKIP_CURRENT_TITLE: await download_from_grid(rg, popular_title, ns, rt, None, only_missing, target_year, bot_entity)
    return True

async def process_queue():
    global CURRENT_PIPELINE_ACTIVE, ACTIVE_CLEAN_TITLE, ACTIVE_TARGET_YEAR, ACTIVE_EP_TAG, ACTIVE_RES_TAG, SKIP_CURRENT_TITLE
    if CURRENT_PIPELINE_ACTIVE: return
    CURRENT_PIPELINE_ACTIVE = True
    try:
        while TITLE_QUEUE:
            try:
                SKIP_CURRENT_TITLE = False
                cmd, send_init = TITLE_QUEUE.popleft()
                if isinstance(cmd, str): cmd = parse_smart_user_command(cmd)
                q, ty = cmd["title"], cmd.get("year")
                meta = await fetch_meta(q, ty)
                pop = meta.get("public_title") or q
                ACTIVE_CLEAN_TITLE, ACTIVE_TARGET_YEAR, ACTIVE_EP_TAG, ACTIVE_RES_TAG = normalize_title_key(pop), ty, "", ""
                if ACTIVE_CLEAN_TITLE == "unknown": continue

                # Auto-fill Franchise sequels & Missing Series episodes
                cat = await fetch_official_catalog(pop)
                if not cat["seasons"] and len(cat["franchise_movies"]) > 1:
                    for fm in cat["franchise_movies"]:
                        if normalize_title_key(fm) != ACTIVE_CLEAN_TITLE and not is_already_in_any_channel(fm, "MOVIE", "ANY") and not is_title_crawled_recently(fm, 24):
                            mark_title_crawled(fm)
                            TITLE_QUEUE.append(({"raw": fm, "title": fm, "year": None, "mode": "normal", "season": None, "episodes": None}, True))

                ss, teps, pb = cmd.get("season") or 1, (set(cmd["episodes"]) if cmd.get("episodes") else None), cmd.get("bot_entity") or SOURCE_BOT
                if cmd.get("manual"): await client.send_message("me", f"🔎 <b>Searching @{pb} for:</b> <code>{pop}</code> (Season {ss:02d})...", parse_mode="HTML")
                fp = await run_extraction_on_bot(pb, pop, q, ss, ty, teps, cmd.get("mode") == "missing", send_init)
                if BACKUP_BOT and pb != BACKUP_BOT and not SKIP_CURRENT_TITLE:
                    if not fp or (not meta.get("is_series") and len(get_existing_resolutions(ACTIVE_CLEAN_TITLE, "MOVIE")) < 3):
                        if cmd.get("manual"): await client.send_message("me", f"🔄 <b>Checking Backup Bot @{BACKUP_BOT} for:</b> <code>{pop}</code>...", parse_mode="HTML")
                        await asyncio.sleep(2.0)
                        fb = await run_extraction_on_bot(BACKUP_BOT, pop, q, ss, ty, teps, cmd.get("mode") == "missing", True)
                        if not fp and not fb and cmd.get("manual"): await client.send_message("me", f"⚠️ <b>Not Found:</b> <code>{pop}</code> on @{pb} or @{BACKUP_BOT}.", parse_mode="HTML")
                await client.send_message("me", f"🏁 <b>Finished:</b> <code>{pop}</code> | 📋 <b>Remaining:</b> <code>{len(TITLE_QUEUE)}</code>", parse_mode="HTML")
                await asyncio.sleep(2.5)
            except Exception as e: print(f"⚠️ Queue error: {e}"); await asyncio.sleep(2.0)
    finally:
        CURRENT_PIPELINE_ACTIVE, ACTIVE_TARGET_YEAR, ACTIVE_EP_TAG, ACTIVE_RES_TAG = False, None, "", ""

def enqueue_commands_bulk(raw_text: str, send_initial: bool = True, bot_entity: str = None) -> list:
    global SKIP_CURRENT_TITLE
    parsed = []
    for item in reversed(split_bulk_commands(raw_text)):
        cmd = parse_smart_user_command(item); cmd["manual"] = True
        if bot_entity: cmd["bot_entity"] = bot_entity
        if cmd["title"] and len(cmd["title"]) >= 2 and normalize_title_key(cmd["title"]) != "unknown":
            TITLE_QUEUE.appendleft((cmd, send_initial)); parsed.append(cmd)
    if parsed:
        if CURRENT_PIPELINE_ACTIVE: SKIP_CURRENT_TITLE = True; FILE_COMPLETED_EVENT.set()
        asyncio.create_task(process_queue())
    return list(reversed(parsed))

# ============================================================================
# 13. AI COMMANDER EXECUTOR & SAVED MESSAGES HANDLER
# ============================================================================
async def execute_ai_controller_action(ai_res: dict, event, raw_req: str = ""):
    global SKIP_CURRENT_TITLE, AUTO_DISCOVERY_ENABLED
    act = (ai_res.get("action") or "CHAT_ONLY").upper()
    reply_msg = ai_res.get("reply") or "🤖 Done!"
    if isinstance(ai_res.get("memory_to_save"), dict) and ai_res["memory_to_save"].get("key"):
        m = ai_res["memory_to_save"]; save_long_term_memory(m.get("category", "USER_PREFERENCE"), m["key"], m.get("value", ""))
    AI_CHAT_HISTORY.append(f"AI: {reply_msg}")
    log_audit_event(raw_req, act, ai_res.get("risk_level", "LOW"), "EXECUTED")
    await event.reply(f"🧠 {reply_msg}", parse_mode="HTML")

    if act == "STOP_ALL":
        AUTO_DISCOVERY_ENABLED = False; TITLE_QUEUE.clear(); SKIP_CURRENT_TITLE = True; FILE_COMPLETED_EVENT.set()
    elif act == "DEDUP_CHANNELS":
        res = await deduplicate_storage_channels(ai_res.get("target_channel_key"))
        await event.reply(f"🧹 <b>Duplicate Cleanup Done:</b> Scanned <code>{res['scanned']}</code> files, deleted <code>{res['deleted']}</code> duplicate pair(s)!", parse_mode="HTML")
    elif act == "SYNC_WEBSITE_BOT":
        arg = ai_res.get("target_channel_key") or "all"
        res = await sync_channels_to_website_bot(arg, 500)
        await event.reply(f"✅ <b>.syncbot Done:</b> Forwarded <code>{res.get('forwarded', 0)}</code> | Dups Deleted: <code>{res.get('dups_deleted', 0)}</code>\n{res.get('files_summary', '')}", parse_mode="HTML")
    elif act == "INSPECT_STORAGE":
        ch_map = load_all_channels_from_db()
        ck = (ai_res.get("target_channel_key") or "").strip().lower()
        scans = [(ck, ch_map[ck])] if ck in ch_map else list(ch_map.items())[:10]
        lines = ["📂 <b>Files Found Inside Your Storage Channels:</b>"]
        for kn, cid in scans:
            try:
                titles = []
                async for m in client.iter_messages(cid, limit=15):
                    if m.video or m.document:
                        t = normalize_title_key(extract_real_filename(m.file.name if m.file else "", m.text or "")).title()
                        if t != "Unknown" and t not in titles: titles.append(t)
                if titles: lines.append(f"• <b>{kn.upper()}</b>: {', '.join(f'<code>{x}</code>' for x in titles[:8])}")
            except Exception: pass
        await event.reply("\n".join(lines) if len(lines) > 1 else "ℹ️ No video files found.", parse_mode="HTML")
    elif act == "WIPE_AND_REUPLOAD_CHANNEL" and ai_res.get("target_channel_key"):
        await event.reply(await wipe_and_reupload_storage_channel(ai_res["target_channel_key"]), parse_mode="HTML")
    elif act == "MANAGE_RULE":
        await event.reply(f"📜 {manage_behavior_rule(ai_res.get('rule_op', 'ADD'), ai_res.get('rule_text', ''), ai_res.get('rule_id'))}", parse_mode="HTML")
    elif act == "SEARCH_ACCOUNT" and ai_res.get("search_query"):
        await event.reply(f"🔍 <b>Account Search:</b>\n{await search_account_intelligence(ai_res['search_query'])}", parse_mode="HTML")
    elif act == "INSPECT_WEBSITE":
        await event.reply(await inspect_website_intelligence(ai_res.get("target_title")), parse_mode="HTML")
    elif act == "SELF_EVOLVE":
        await event.reply(stage_or_register_capability(ai_res.get("cap_name") or "skill", ai_res.get("cap_description") or "", ai_res.get("cap_code") or "def run(): return True"), parse_mode="HTML")
    elif act == "QUEUE_TITLES":
        tasks = ai_res.get("tasks") or []
        if not tasks and ai_res.get("target_title"):
            c = parse_smart_user_command(str(ai_res["target_title"]))
            if str(ai_res.get("target_season") or "").isdigit(): c["season"] = int(ai_res["target_season"])
            tasks = [c]
        names = []
        for t in reversed(tasks):
            if isinstance(t, dict) and t.get("title"):
                t["manual"] = True; t.setdefault("raw", t["title"]); t.setdefault("mode", "normal")
                TITLE_QUEUE.appendleft((t, True))
                names.append(f"{t['title']} (S{t['season']:02d})" if t.get("season") else t["title"])
        if names:
            await event.reply(f"⏳ <b>Starting Search Now:</b> <code>{', '.join(reversed(names))}</code>", parse_mode="HTML")
            if CURRENT_PIPELINE_ACTIVE: SKIP_CURRENT_TITLE = True; FILE_COMPLETED_EVENT.set(); await asyncio.sleep(2.2)
            asyncio.create_task(process_queue())
    elif act == "DELETE_MEDIA" and ai_res.get("target_title"):
        await event.reply(f"🗑️ Deleted <code>{await delete_movie_or_series_everywhere(ai_res['target_title'])}</code> post(s).", parse_mode="HTML")
    elif act == "REUPLOAD_MEDIA":
        await event.reply(f"♻️ Re-uploaded <code>{await resync_files_for_website(ai_res.get('target_title'), 8)}</code> file(s).", parse_mode="HTML")
    elif act == "RENAME_ALL_PUBLIC":
        await event.reply(f"✏️ Renamed/Readjusted <code>{await rename_and_readjust_all_channels()}</code> file(s).", parse_mode="HTML")
    elif act == "REBUILD_SERIES_INDEX":
        await event.reply(f"✅ Rebuilt Series Index: <code>{await rebuild_series_index_channel()}</code> Master Card(s)!", parse_mode="HTML")
    elif act == "RENAME_CHANNEL" and ai_res.get("target_channel_key") and ai_res.get("new_name"):
        await event.reply(await rename_telegram_channel_or_chat(ai_res["target_channel_key"], ai_res["new_name"]), parse_mode="HTML")
    elif act == "DELETE_CHANNEL" and ai_res.get("target_channel_key"):
        m = await resolve_account_channel_target(ai_res["target_channel_key"])
        if len(m) == 1:
            await client(DeleteChannelRequest(channel=m[0][1]))
            await event.reply(f"🗑️ Deleted Channel: <code>{m[0][2]}</code>", parse_mode="HTML")
    elif act == "CRAWLER_CONTROL":
        if ai_res.get("crawler_on") is not None: AUTO_DISCOVERY_ENABLED = bool(ai_res["crawler_on"])
        cy, _, ck = get_crawler_progress()
        save_crawler_progress(int(ai_res.get("crawler_year") or cy), 1, str(ai_res.get("crawler_mode") or ck).lower())
    elif act == "SKIP": SKIP_CURRENT_TITLE = True; FILE_COMPLETED_EVENT.set()
    elif act == "CLEAR_QUEUE": TITLE_QUEUE.clear()

@client.on(events.NewMessage(outgoing=True))
async def on_outgoing_trigger(event):
    global SKIP_CURRENT_TITLE, AUTO_DISCOVERY_ENABLED, SYNCBOT_STOP, ME_USER_ID
    text = (event.message.text or "").strip()
    if not text or text.startswith(("/", "✅", "⏳", "🏁", "⚠️", "🔗", "📋", "🛑", "🔎", "🎬", "🤖", "⚡", "♻️", "🆕", "📖", "🌐", "🧠", "🗑️", "🧹", "✏️", "🔑", "📜", "🧬", "🔍", "⏰", "📡", "📂")): return
    if ME_USER_ID is None: ME_USER_ID = (await client.get_me()).id
    if event.chat_id == ME_USER_ID:
        low = text.lower()
        if low in (".help", "help", ".admin", "admin"):
            await event.reply(
                "📖 <b>THE BACKROOM AI COMMANDER</b>\n\n"
                "• <code>.syncbot all</code> / <code>.syncbot action</code> / <code>.syncbot stop</code> / <code>.syncbot reset</code> — Forward all storage files to Website Bot + auto-delete duplicates + update Series Index + show all files!\n"
                "• <code>.dedup</code> / <code>.dedup action</code> — Detect & auto-delete 1 copy of every duplicate pair\n"
                "• <code>.listfiles</code> — Show all files found across your storage channels\n"
                "• <code>.repopulate ch</code> / <code>.redownloadchannel ch</code> — Wipe & re-upload a channel\n"
                "• <code>.search Title</code> | <code>.auto on/off/2025/bollywood</code> | <code>.rebuildindex</code> | <code>.renameall</code>\n"
                "• <code>.delete Title</code> | <code>.reupload Title</code> | <code>.addgemini Key</code> | <code>.rules</code> | <code>.status</code> | <code>.skip</code> | <code>.clear</code>",
                parse_mode="HTML"
            ); return

        if low.startswith(".syncbot"):
            arg = text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else "all"
            if arg.lower() == "stop": SYNCBOT_STOP = True; await event.reply("🛑 <b>Stopping .syncbot scan...</b>", parse_mode="HTML"); return
            if arg.lower() == "reset":
                conn = sqlite3.connect("storage_index.db"); conn.execute("DELETE FROM synced_to_web_bot"); conn.commit(); conn.close()
                await event.reply("🧹 <b>Cleared .syncbot history!</b>", parse_mode="HTML"); return
            await event.reply(f"📡 <b>Userbot ➔ Website Bot Scan Started</b> (<code>{arg}</code>)\n<i>Forwarding real videos at 2.8s pace to @{WEBSITE_BOT_USERNAME or 'UNSET'} + auto-dedup + Series Index sync...</i>", parse_mode="HTML")
            async def _run_sync():
                res = await sync_channels_to_website_bot(arg, 500)
                if res.get("ok"):
                    await client.send_message(
                        "me",
                        f"✅ <b>.syncbot Finished!</b>\n• Channels: <code>{res['channels']}</code> | Forwarded: <code>{res['forwarded']}</code>\n• Skipped: <code>{res['skipped']}</code> | Failed: <code>{res['failed']}</code> | Dups Deleted: <code>{res['dups_deleted']}</code>\n\n📂 <b>Files Found:</b>\n{res.get('files_summary') or 'None'}",
                        parse_mode="HTML"
                    )
                else: await client.send_message("me", f"⚠️ <b>.syncbot:</b> {res.get('msg')}", parse_mode="HTML")
            asyncio.create_task(_run_sync()); return

        if low.startswith(".dedup"):
            arg = text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else "all"
            await event.reply(f"🧹 <b>Scanning for duplicate pairs in <code>{arg}</code> & auto-deleting extras...</b>", parse_mode="HTML")
            res = await deduplicate_storage_channels(arg)
            await event.reply(f"✅ <b>Duplicate Cleanup Finished:</b> Scanned <code>{res['scanned']}</code> files across <code>{res['channels']}</code> channels, auto-deleted <code>{res['deleted']}</code> duplicate(s)!", parse_mode="HTML"); return

        if low.startswith(".listfiles"):
            arg = text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else ""
            await execute_ai_controller_action({"action": "INSPECT_STORAGE", "reply": "Scanning your storage channels...", "target_channel_key": arg}, event, text); return

        if low.startswith((".repopulate ", ".wipeandreupload ")):
            tch = text.split(maxsplit=1)[1].strip()
            await event.reply(f"♻️ <b>Wiping & re-uploading <code>{tch}</code>...</b>", parse_mode="HTML")
            await event.reply(await wipe_and_reupload_storage_channel(tch, False), parse_mode="HTML"); return

        if low.startswith(".redownloadchannel "):
            tch = text.split(maxsplit=1)[1].strip()
            await event.reply(await wipe_and_reupload_storage_channel(tch, True), parse_mode="HTML"); return

        if low.startswith((".search ", ".get ", ".s ")):
            added = enqueue_commands_bulk(text, True)
            if added: await event.reply("⏳ <b>Queued Search:</b> " + ", ".join(f"<code>{c['title']}</code>" for c in added[:10]), parse_mode="HTML")
            return

        if low.startswith(".auto") or low.startswith("auto "):
            if "off" in low: AUTO_DISCOVERY_ENABLED = False; await event.reply("🤖 <b>Auto-Crawler OFF 🔴</b>", parse_mode="HTML"); return
            if "on" in low: AUTO_DISCOVERY_ENABLED = True; await event.reply("🤖 <b>Auto-Crawler ON 🟢</b>", parse_mode="HTML"); return
            ym = re.search(r"\b(19[5-9]\d|20[0-3]\d)\b", low); cy, _, ck = get_crawler_progress()
            for mn in CRAWLER_MODES:
                if mn in low: ck = mn; break
            save_crawler_progress(int(ym.group(1)) if ym else cy, 1, ck); AUTO_DISCOVERY_ENABLED = True
            await event.reply(f"🤖 <b>Auto-Crawler set to:</b> <code>{int(ym.group(1)) if ym else cy} • {ck.upper()}</code>", parse_mode="HTML"); return

        if low.startswith((".delete ", ".rm ")):
            q = text.split(maxsplit=1)[1].strip()
            await event.reply(f"🗑️ Deleted <code>{await delete_movie_or_series_everywhere(q)}</code> post(s) for <code>{q}</code>.", parse_mode="HTML"); return

        if low.startswith((".reupload", ".resync")):
            q = text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else None
            await event.reply(f"♻️ Re-uploaded <code>{await resync_files_for_website(q, 8)}</code> item(s) cleanly!", parse_mode="HTML"); return

        if low in (".renameall", ".readjust"):
            await event.reply(f"✏️ Renamed & Readjusted <code>{await rename_and_readjust_all_channels()}</code> file(s)!", parse_mode="HTML"); return

        if low in (".rebuildindex", ".fixindex"):
            await event.reply(f"✅ Rebuilt Series Index: <code>{await rebuild_series_index_channel()}</code> Series Card(s)!", parse_mode="HTML"); return

        if low.startswith(".addgemini"):
            tk = add_backup_gemini_key(text.split(maxsplit=1)[1] if len(text.split()) > 1 else "")
            DISCOVERED_GEMINI_MODELS.clear()
            await event.reply(f"🔑 <b>Gemini Key Pool Updated:</b> <code>{tk}</code> active key(s)!", parse_mode="HTML"); return

        if low in (".rules", ".channels", ".bots", ".status", ".queue", ".skip", ".clear", ".approve"):
            if low == ".skip": SKIP_CURRENT_TITLE = True; FILE_COMPLETED_EVENT.set(); await event.reply("🛑 Skipped current title.", parse_mode="HTML")
            elif low == ".clear": c = len(TITLE_QUEUE); TITLE_QUEUE.clear(); await event.reply(f"🛑 Cleared {c} items.", parse_mode="HTML")
            elif low == ".rules":
                rs = "\n".join(f"• #{r[0]}: {r[1]}" for r in get_active_behavior_rules()) or "<i>None</i>"
                await event.reply(f"📜 <b>Permanent Rules:</b>\n{rs}", parse_mode="HTML")
            elif low == ".channels":
                await sync_created_channels_from_dialogs()
                await event.reply("📋 <b>Channels:</b>\n" + "\n".join(f"• <b>{k.upper()}:</b> <code>{v}</code>" for k, v in sorted(load_all_channels_from_db().items())), parse_mode="HTML")
            elif low == ".bots":
                bl = [f"• Bot #{i+1} ({b['role'].upper()}): @{b['username']}" for i, b in enumerate(BOT_POOL)]
                if WEBSITE_BOT_USERNAME: bl.append(f"• Website Bot: @{WEBSITE_BOT_USERNAME} (Protected ✅)")
                await event.reply("🤖 <b>Bots:</b>\n" + "\n".join(bl), parse_mode="HTML")
            elif low == ".approve":
                conn = sqlite3.connect("storage_index.db"); conn.execute("UPDATE custom_capabilities SET active=1"); conn.commit(); conn.close()
                await event.reply("✅ Approved pending AI skills!", parse_mode="HTML")
            else:
                cy, _, ck = get_crawler_progress()
                await event.reply(f"📋 <b>Status:</b> Active=<code>{ACTIVE_CLEAN_TITLE or 'Idle'}</code> | Queue=<code>{len(TITLE_QUEUE)}</code> | Auto=<code>{'ON' if AUTO_DISCOVERY_ENABLED else 'OFF'}</code> ({cy} {ck}) | Gemini=<code>{len(GEMINI_KEYS)}</code> (<code>{LAST_GEMINI_ERROR}</code>) | SyncBot=<code>{'RUNNING' if SYNCBOT_RUNNING else 'IDLE'}</code>", parse_mode="HTML")
            return

        # Natural Conversational Chat -> THE BACKROOM AI COMMANDER
        clean_p = re.sub(r"^\.ai\s+", "", text, flags=re.I).strip()
        AI_CHAT_HISTORY.append(f"Owner: {clean_p}")
        ai_res = await gemini_interactive_agent(clean_p)
        if ai_res: await execute_ai_controller_action(ai_res, event, clean_p)
        return

    chat = await event.get_chat()
    if (getattr(chat, "username", "") or "").lower() in (SOURCE_BOT.lower(), BACKUP_BOT.lower()) and not CURRENT_PIPELINE_ACTIVE:
        enqueue_commands_bulk(text, False, (getattr(chat, "username", "") or "").lower())

# ============================================================================
# 14. 24/7 KEEP-ALIVE & MAIN ENTRYPOINT
# ============================================================================
async def keep_alive_worker():
    url = RENDER_EXTERNAL_URL or f"http://127.0.0.1:{PORT}/"
    while True:
        await asyncio.sleep(600)
        try:
            async with aiohttp.ClientSession() as s: await s.get(url, timeout=10)
        except Exception: pass

async def handle_ping(request):
    cy, cp, ck = get_crawler_progress()
    return web.json_response({"status": "online", "system": "THE BACKROOM AI COMMANDER", "syncbot_running": SYNCBOT_RUNNING, "gemini_status": LAST_GEMINI_ERROR, "crawler": f"{cy} {ck} P{cp}", "queue": len(TITLE_QUEUE)})

async def main():
    global ME_USER_ID
    app = web.Application(); app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    if not SESSION_STRING: print("❌ Missing SESSION_STRING!"); return
    await client.start()
    ME_USER_ID = (await client.get_me()).id
    await sync_created_channels_from_dialogs()
    await init_all_delivery_bots()
    asyncio.create_task(keep_alive_worker())
    asyncio.create_task(auto_discovery_crawler_worker())
    print(f"🚀 THE BACKROOM AI COMMANDER LIVE! Gemini Keys: {len(GEMINI_KEYS)} | Delivery Bots: {len(BOT_POOL)} | Website Bot: @{WEBSITE_BOT_USERNAME}")
    await client.run_until_disconnected()

if __name__ == "__main__":
    loop.run_until_complete(main())

