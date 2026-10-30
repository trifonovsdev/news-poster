"""Schedule, limits and fixed texts of the news poster.

The channel and how posts reach it come from `.env` in the project folder (see
`.env.example`); everything else is fixed here.
"""

import os
import shutil
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> None:
    """KEY=value lines; the process environment wins over the file."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_env(ROOT / ".env")
DATA = ROOT / "data"
DB_PATH = DATA / "poster.db"
MEDIA = DATA / "media"  # downloaded photos and videos, kept two days
RUN_DIR = DATA / "run"  # empty cwd for the model; keeps the last run of each prompt
PROMPTS = ROOT / "prompts"
PAUSE_FILE = ROOT / "PAUSE"  # touch it to stop posting without stopping the service
MSK = ZoneInfo("Europe/Moscow")

CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "")  # how the prompt and the footer call it
CHANNEL_URL = os.environ.get("CHANNEL_URL", "")  # https://t.me/<username>
FOOTER = os.environ.get("FOOTER") or f'🐾 <a href="{CHANNEL_URL}"><b>{CHANNEL_NAME}. подписаться</b></a>'
# Posts go out either through a bot that is an admin of the channel (Bot API)...
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")  # @username or -100… id
# ...or, without BOT_TOKEN, through a local socket that speaks for a user account
# (see README, "Posting as a user account").
CHANNEL_SOCKET = Path(os.environ.get("CHANNEL_SOCKET", "/run/news-poster/channel.sock"))

MODEL = os.environ.get("MODEL", "claude-sonnet-5-5")
NEWS_TIMEOUT_SECONDS = 20 * 60
PICK_TIMEOUT_SECONDS = 5 * 60

ACTIVE_HOURS = (9, 23)  # MSK, [start, end)
POSTS_PER_DAY = 10
GAP_MINUTES = (60, 100)  # random pause after a post
RETRY_MINUTES = (20, 40, 60)  # after 1, 2, 3+ runs in a row that posted nothing
# A story two or more source channels picked up within the hour starts a run before
# the gap ends (poster/breaking.py); the check itself costs no model run.
BREAKING_CHECK_MINUTES = 5
BREAKING_MIN_GAP_MINUTES = 20
BREAKING_RUNS_PER_DAY = 8
FRESH_HOURS = 6  # older stories are refused whatever the model says
RECENT_DAYS = 3  # own posts the model sees, so it does not repeat a story

# Telegram channels read from their public web preview (t.me/s/<name>): the
# competitors set the style; all of them show what is breaking right now, since a
# lot of news (payments, bans, outages) surfaces in Telegram before any site.
COMPETITORS = ("techmedia", "lolzteam", "technomotel")
SOURCE_CHANNELS = (*COMPETITORS, "bugnotfeature", "exploitex", "data_secrets", "ai_newz")
COMPETITOR_POSTS = 10  # latest posts per channel, within FRESH_HOURS

CAPTION_LIMIT = 1024  # Telegram's caption limit for an account without Premium
PHOTO_MIN_SIZE = (640, 360)  # a typical news picture is 800x450
PHOTO_MAX_BYTES = 10 * 1024 * 1024
VIDEO_MAX_BYTES = 50 * 1024 * 1024
VIDEO_MAX_SECONDS = 300
VIDEO_MAX_HEIGHT = 1080
# Our own yt-dlp, kept current (the host-wide one is pinned and YouTube refuses it),
# with node to solve YouTube's JS challenges.
VENV = ROOT / ".venv"
YTDLP = VENV / "bin" / "yt-dlp"
NODE = os.environ.get("NODE") or shutil.which("node") or "node"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)
