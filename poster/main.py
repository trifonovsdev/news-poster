"""The news loop: pick a fresh story with the model, fetch its media, post it.

    python3 -m poster.main               # the service loop
    python3 -m poster.main once          # one story now, published
    python3 -m poster.main once --dry    # one story now, printed, not published
    python3 -m poster.main once --topic "оплата Kimi картами Мир"   # this story, if it checks out
"""

import asyncio
import json
import logging
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from poster import breaking, caption, channel, competitors, config, feeds, llm, media
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
        # Hard limits the model must keep: the prompt alone let posts run long.
        "headline": {"type": "string", "maxLength": 130},
        "paragraphs": {"type": "array", "items": {"type": "string", "maxLength": 320}, "maxItems": 2},
        "kicker": {"type": "string", "maxLength": 120},
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


def task_text(store: Store, ts: float, hint: str = "", lead: str = "") -> str:
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
        f"# Свежие посты Telegram-каналов (что уже вышло и как оформлено)\n{competitors.digest(ts)}\n\n"
        + (
            f"Редакция решила взять эту историю: {hint}\nПроверь факты и напиши пост именно о ней, "
            "аккуратно разделяя подтверждённое и то, что сообщают пользователи или каналы. "
            "skip — только если история опровергнута или оказалась старой."
            if hint
            else f"Прямо сейчас в Telegram-каналах расходится история:\n{lead}\n"
            "Если она настоящая, свежая и мы её ещё не давали, сделай пост о ней. Если нет — "
            "возьми другую свежую историю или skip."
            if lead
            else "Найди одну свежую историю и подготовь пост."
        )
    )


async def make_post(store: Store, *, dry: bool, hint: str = "", lead: str = "") -> bool:
    """One run of the model; True if a post went out (or would have, when dry)."""
    ts = now()
    story = await llm.run("news", task_text(store, ts, hint, lead), SCHEMA, timeout=config.NEWS_TIMEOUT_SECONDS)
    if story["skip"]:
        log.info("model skipped: %s", story["skip_reason"])
        return False
    reason = check(story, store, ts)
    text = caption.fit(story, config.CAPTION_LIMIT)
    if not reason and not hint:  # the owner's --topic overrides this
        rival = await asyncio.to_thread(
            competitors.old_news, " ".join([story["headline"], *story["paragraphs"]]), ts, breaking.names
        )
        if rival:
            reason = f"old news: @{rival} posted it hours ago"
    if not reason and caption.visible_length(text) > config.CAPTION_LIMIT:
        reason = f"caption too long ({caption.visible_length(text)})"
    files = []
    if not reason:
        config.MEDIA.mkdir(parents=True, exist_ok=True)
        stamp = msk(ts).strftime("%Y%m%d-%H%M%S")
        files = await asyncio.to_thread(media.collect, story["media"], story["source_urls"], stamp, log)
        if files and files[0].suffix != ".mp4":
            files = await pick_photos(text, files)
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


PICK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["chosen", "reason"],
    "properties": {
        "chosen": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
        "reason": {"type": "string"},
    },
}


async def pick_photos(text: str, files: list[Path]) -> list[Path]:
    """The model looks at the downloaded photos and keeps the ones fit to publish."""
    by_name = {f.name: f for f in files}
    task = f"# Пост\n{text}\n\n# Кандидаты\n" + "\n".join(by_name)
    try:
        answer = await llm.run(
            "pick", task, PICK_SCHEMA, timeout=config.PICK_TIMEOUT_SECONDS, tools=("Read",), cwd=config.MEDIA
        )
    except llm.LLMError as e:
        log.error("photo pick failed, using the first candidate: %s", e)
        return files[:1]
    log.info("photo pick: %s — %s", answer["chosen"], answer["reason"])
    return [by_name[n] for n in answer["chosen"] if n in by_name]


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


def allowed(store: Store, ts: float) -> bool:
    hour = msk(ts).hour
    if config.PAUSE_FILE.exists() or not config.ACTIVE_HOURS[0] <= hour < config.ACTIVE_HOURS[1]:
        return False
    return store.posted_since(day_start(ts)) < config.POSTS_PER_DAY


def due(store: Store, ts: float) -> bool:
    return allowed(store, ts) and ts >= store.get("next_run_at", 0)


async def breaking_lead(store: Store, ts: float) -> str:
    """A story breaking in Telegram that is worth a run before the gap ends, or ""."""
    if ts < store.get("breaking_checked_at", 0) + config.BREAKING_CHECK_MINUTES * 60:
        return ""
    store.put("breaking_checked_at", ts)
    if not allowed(store, ts) or store.get("pending"):
        return ""
    if ts - store.last_posted_at() < config.BREAKING_MIN_GAP_MINUTES * 60:
        return ""
    used = {k: v for k, v in store.get("breaking_used", {}).items() if ts - v < 12 * 3600}
    if sum(1 for v in used.values() if v >= day_start(ts)) >= config.BREAKING_RUNS_PER_DAY:
        return ""
    covered = set()
    for row in store.recent(ts - 12 * 3600):
        covered |= breaking.names(f"{row['topic'] or ''} {row['headline'] or ''}")
    found = breaking.hot(await asyncio.to_thread(breaking.scan), ts, covered, set(used))
    if not found:
        return ""
    name, posts = found
    used[name] = ts
    store.put("breaking_used", used)
    log.info("breaking: %s in %d channels", name, len(posts))
    return "\n".join(posts)


def later(store: Store, minutes: float) -> None:
    store.put("next_run_at", now() + minutes * 60)
    log.info("next run at %s", msk(now() + minutes * 60).strftime("%H:%M"))


async def tick(store: Store, lead: str = "") -> None:
    # Checked before the model: a post that cannot go out must not cost a run.
    wait = await channel.status()
    if wait:
        log.info("channel unavailable for %d s", wait)
        later(store, max(wait, 600) / 60)
        return
    try:
        posted = await publish_pending(store) or await make_post(store, dry=False, lead=lead)
    except Exception as e:  # one bad run must not stop the channel
        log.error("run failed: %s", e)
        posted = False
    if store.get("pending"):  # the send failed; retried without a new model run
        later(store, config.RETRY_MINUTES[0])
        return
    if lead and not posted:
        return  # a breaking run that found nothing leaves the usual schedule alone
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
        elif lead := await breaking_lead(store, ts):
            await tick(store, lead)
        if store.get("ytdlp_updated_on") != msk(ts).date().isoformat():
            await asyncio.to_thread(media.update_ytdlp)
            store.put("ytdlp_updated_on", msk(ts).date().isoformat())
        await asyncio.sleep(60)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config.DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    store = Store(config.DB_PATH)
    if sys.argv[1:2] == ["once"]:
        hint = sys.argv[sys.argv.index("--topic") + 1] if "--topic" in sys.argv else ""
        asyncio.run(make_post(store, dry="--dry" in sys.argv, hint=hint))
    else:
        asyncio.run(loop(store))


if __name__ == "__main__":
    main()
