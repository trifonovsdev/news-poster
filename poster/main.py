"""The news loop: pick a fresh story with the model, fetch its media, post it.

    python3 -m poster.main               # the service loop
    python3 -m poster.main once          # one story now, published
    python3 -m poster.main once --dry    # one story now, printed, not published
"""

import asyncio
import json
import logging
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from poster import caption, channel, competitors, config, feeds, llm, media
from poster.store import Store

log = logging.getLogger("news-poster")
SEND_ATTEMPTS = 3

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "skip", "skip_reason", "topic", "source_urls", "published_at",
        "emoji", "headline", "paragraphs", "kicker", "media",
    ],
    "properties": {
        "skip": {"type": "boolean"},
        "skip_reason": {"type": "string"},
        "topic": {"type": "string"},
        "source_urls": {"type": "array", "items": {"type": "string"}},
        "published_at": {"type": "string"},
        "emoji": {"type": "string"},
        "headline": {"type": "string"},
        "paragraphs": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "kicker": {"type": "string"},
        "media": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "url", "about"],
                "properties": {
                    "kind": {"enum": ["photo", "video"]},
                    "url": {"type": "string"},
                    "about": {"type": "string"},
                },
            },
        },
    },
}


def now() -> float:
    return time.time()


def msk(ts: float | None = None) -> datetime:
    return datetime.fromtimestamp(now() if ts is None else ts, config.MSK)


def day_start(ts: float | None = None) -> float:
    return msk(ts).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def parse_time(value: str) -> float | None:
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.timestamp()


def check(story: dict, store: Store, ts: float) -> str | None:
    """Why the story may not go out, or None."""
    if not story["source_urls"]:
        return "no source"
    if store.seen(story["source_urls"]):
        return "source already used"
    published = parse_time(story["published_at"])
    if published is None:
        return f"bad published_at {story['published_at']!r}"
    if published > ts + 3600:
        return f"published_at in the future: {story['published_at']}"
    if ts - published > config.FRESH_HOURS * 3600:
        return f"stale: published {story['published_at']}"
    if not story["headline"].strip() or not any(p.strip() for p in story["paragraphs"]):
        return "empty text"
    return None


def task_text(store: Store, ts: float) -> str:
    recent = store.recent(ts - config.RECENT_DAYS * 86400)
    own = "\n".join(
        f"- {msk(r['ts']):%d.%m %H:%M} [{r['status']}] {r['topic'] or ''} — {r['headline'] or ''} ({r['url']})"
        for r in recent
    ) or "- пока ничего"
    fresh = feeds.fresh(store.seen, ts)
    return (
        f"Сейчас {msk(ts):%Y-%m-%d %H:%M} по Москве.\n\n"
        f"# Свежие заголовки из лент за {config.FRESH_HOURS} ч (кандидаты, новые сверху)\n"
        f"{feeds.digest(fresh, ts)}\n\n"
        f"# Что канал уже публиковал или отклонил (не повторяй эти события)\n{own}\n\n"
        f"# Последние посты конкурентов (стиль и что уже разобрали)\n{competitors.digest()}\n\n"
        "Найди одну свежую историю и подготовь пост."
    )


async def make_post(store: Store, *, dry: bool) -> bool:
    """One run of the model; True if a post went out (or would have, when dry)."""
    ts = now()
    story = await llm.run("news", task_text(store, ts), SCHEMA, timeout=config.NEWS_TIMEOUT_SECONDS)
    if story["skip"]:
        log.info("model skipped: %s", story["skip_reason"])
        return False
    reason = check(story, store, ts)
    text = caption.fit(story, config.CAPTION_LIMIT)
    if not reason and caption.visible_length(text) > config.CAPTION_LIMIT:
        reason = f"caption too long ({caption.visible_length(text)})"
    files = []
    if not reason:
        config.MEDIA.mkdir(parents=True, exist_ok=True)
        stamp = msk(ts).strftime("%Y%m%d-%H%M%S")
        files = await asyncio.to_thread(media.collect, story["media"], story["source_urls"], stamp, log)
        if not files:
            reason = "no usable media"
    record = dict(
        topic=story["topic"],
        headline=story["headline"],
        caption=text,
        media=json.dumps([m["url"] for m in story["media"]]),
    )
    if reason:
        log.info("rejected %s: %s", story["source_urls"][:1], reason)
        if not dry and story["source_urls"]:
            store.add("rejected", story["source_urls"][0], reason=reason, **record)
        return False
    if dry:
        print(text, "\n", *files, sep="\n")
        return True
    # Kept until it is out: a refused send is retried without paying for a new model run.
    store.put(
        "pending",
        {
            "url": story["source_urls"][0],
            "published": parse_time(story["published_at"]),
            "files": [str(f) for f in files],
            "record": record,
        },
    )
    return await publish_pending(store)


async def publish_pending(store: Store) -> bool:
    """Sends the prepared post, if any; True if it went out."""
    pending = store.get("pending")
    if not pending:
        return False
    files = [Path(f) for f in pending["files"]]
    if now() - pending["published"] > config.FRESH_HOURS * 3600 or not all(f.exists() for f in files):
        store.add("rejected", pending["url"], reason="went stale before it could be sent", **pending["record"])
        store.put("pending", None)
        return False
    try:
        await channel.publish(pending["record"]["caption"], files)
    except channel.ChannelError as e:
        # A flood or the agent restarting passes; a post Telegram keeps refusing
        # (bad file, markup) is given up after a few tries.
        pending["attempts"] = pending.get("attempts", 0) + 1
        if pending["attempts"] >= SEND_ATTEMPTS:
            store.add("rejected", pending["url"], reason=f"telegram: {e}"[:300], **pending["record"])
            pending = None
        store.put("pending", pending)
        raise
    store.add("posted", pending["url"], **pending["record"])
    store.put("pending", None)
    log.info("posted: %s", pending["record"]["headline"])
    return True


def due(store: Store, ts: float) -> bool:
    hour = msk(ts).hour
    if config.PAUSE_FILE.exists() or not config.ACTIVE_HOURS[0] <= hour < config.ACTIVE_HOURS[1]:
        return False
    if store.posted_since(day_start(ts)) >= config.POSTS_PER_DAY:
        return False
    return ts >= store.get("next_run_at", 0)


def later(store: Store, minutes: float) -> None:
    store.put("next_run_at", now() + minutes * 60)
    log.info("next run at %s", msk(now() + minutes * 60).strftime("%H:%M"))


async def tick(store: Store) -> None:
    # Checked before the model: a post that cannot go out must not cost a run.
    wait = await channel.status()
    if wait:
        log.info("channel unavailable for %d s", wait)
        later(store, max(wait, 600) / 60)
        return
    try:
        posted = await publish_pending(store) or await make_post(store, dry=False)
    except Exception as e:  # one bad run must not stop the channel
        log.error("run failed: %s", e)
        posted = False
    if store.get("pending"):  # the send failed; retried without a new model run
        later(store, config.RETRY_MINUTES[0])
        return
    misses = 0 if posted else store.get("misses", 0) + 1
    store.put("misses", misses)
    if posted:
        later(store, random.uniform(*config.GAP_MINUTES))
    else:
        later(store, config.RETRY_MINUTES[min(misses, len(config.RETRY_MINUTES)) - 1])
    media.prune()


async def loop(store: Store) -> None:
    log.info("started")
    while True:
        ts = now()
        if due(store, ts):
            await tick(store)
        if store.get("ytdlp_updated_on") != msk(ts).date().isoformat():
            await asyncio.to_thread(media.update_ytdlp)
            store.put("ytdlp_updated_on", msk(ts).date().isoformat())
        await asyncio.sleep(60)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config.DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    store = Store(config.DB_PATH)
    if sys.argv[1:2] == ["once"]:
        asyncio.run(make_post(store, dry="--dry" in sys.argv))
    else:
        asyncio.run(loop(store))


if __name__ == "__main__":
    main()
