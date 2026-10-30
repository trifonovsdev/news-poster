"""Recent posts of competitor channels, read from their public web preview (t.me/s/<name>).

The web preview needs no Telegram account, so reading it costs the posting
account nothing against Telegram's limits.
"""

import html
import re
import urllib.request
from datetime import datetime

from poster import config

_WRAP = re.compile(r'<div class="tgme_widget_message_wrap')
_TEXT = re.compile(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', re.S)
_TIME = re.compile(r'<time[^>]*datetime="([^"]+)"')


def parse(page: str, limit: int) -> list[dict]:
    posts = []
    for block in _WRAP.split(page)[1:]:
        m = _TEXT.search(block)
        if not m:
            continue
        raw = re.sub(r"<br\s*/?>", "\n", m.group(1))
        text = html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        when = _TIME.search(block)
        media = "video" if "video_player" in block else "photo" if "photo_wrap" in block else "none"
        posts.append({"time": when.group(1) if when else "", "media": media, "text": text})
    return posts[-limit:]


def fetch(name: str, limit: int = config.COMPETITOR_POSTS) -> list[dict]:
    request = urllib.request.Request(f"https://t.me/s/{name}", headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        return parse(response.read().decode("utf-8", "replace"), limit)


def digest(ts: float) -> str:
    """Each channel's latest posts within FRESH_HOURS, with their age."""
    parts = []
    for name in config.SOURCE_CHANNELS:
        role = "конкурент, ориентир по стилю" if name in config.COMPETITORS else "источник"
        try:
            posts = fetch(name)
        except Exception as e:  # a channel's page being down must not stop a post
            parts.append(f"## @{name} ({role}): не загрузился ({e})")
            continue
        lines = [f"## @{name} ({role})"]
        for post in posts:
            when = _age(post["time"], ts)
            if when is None or when > config.FRESH_HOURS:
                continue
            lines.append(f"[{when:.1f} ч назад, медиа: {post['media']}]\n{post['text'][:700]}")
        parts.append("\n\n".join(lines) if len(lines) > 1 else f"{lines[0]}: свежих постов нет")
    return "\n\n".join(parts)


def _age(iso: str, ts: float) -> float | None:
    try:
        return (ts - datetime.fromisoformat(iso).timestamp()) / 3600
    except ValueError:
        return None
