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
# A story competitors posted within HOT_HOURS is hot: proof readers care, still
# worth our own post. Posted earlier (up to COVERED_HOURS) it is old news here.
HOT_HOURS = 3
COVERED_HOURS = 24
_TIME = re.compile(r'<time[^>]*datetime="([^"]+)"')
_VIEWS = re.compile(r'tgme_widget_message_views">([^<]+)<')


def views(text: str) -> int:
    """«12.1K» -> 12100."""
    m = re.fullmatch(r"([\d.]+)([KM]?)", text.strip())
    if not m:
        return 0
    return int(float(m.group(1)) * {"": 1, "K": 1_000, "M": 1_000_000}[m.group(2)])


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
        seen = _VIEWS.search(block)
        posts.append({
            "time": when.group(1) if when else "",
            "media": media,
            "text": text,
            "views": views(seen.group(1)) if seen else 0,
        })
    return posts[-limit:]


def fetch(name: str, limit: int = config.COMPETITOR_POSTS) -> list[dict]:
    request = urllib.request.Request(f"https://t.me/s/{name}", headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        return parse(response.read().decode("utf-8", "replace"), limit)


def digest(ts: float) -> str:
    """Each channel's posts of the last day: fresh ones in full, older ones as a headline."""
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
            if when is None or when > COVERED_HOURS:
                continue
            if when > HOT_HOURS:  # only to know the story is taken
                # Views show what the audience reads (they grow with age, so the
                # model compares posts of similar age).
                lines.append(
                    f"[{when:.0f} ч назад, уже было, {post['views'] / 1000:.1f}K просмотров] "
                    f"{post['text'].splitlines()[0][:150]}"
                )
            else:
                lines.append(f"[{when:.1f} ч назад, медиа: {post['media']}]\n{post['text'][:700]}")
        parts.append("\n\n".join(lines) if len(lines) > 1 else f"{lines[0]}: свежих постов нет")
    return "\n\n".join(parts)


def _age(iso: str, ts: float) -> float | None:
    try:
        return (ts - datetime.fromisoformat(iso).timestamp()) / 3600
    except ValueError:
        return None


def old_news(text: str, ts: float, names) -> str | None:
    """The competitor that posted this story HOT_HOURS to COVERED_HOURS ago, or None.

    Same story = at least three shared names (names() from poster.breaking):
    the model dates a story by the article it read, so a day-old event retold
    by a new site looks fresh to the time check alone.
    """
    ours = names(text)
    for name in config.COMPETITORS:
        try:
            posts = fetch(name, 30)
        except Exception:
            continue
        for post in posts:
            age = _age(post["time"], ts)
            if age is not None and HOT_HOURS < age <= COVERED_HOURS and len(ours & names(post["text"])) >= 3:
                return name
    return None
