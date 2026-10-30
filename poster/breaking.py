"""Spots a story breaking in Telegram, so the poster need not wait out its usual gap.

No model here: every few minutes code reads the source channels and calls a story
hot when two of them posted about it within the last hour (sharing two or more
names: capitalised words inside a sentence, or Latin brands) and the channel
has not covered it lately. A hot story starts a normal model run, which
still decides whether to post it.
"""

import re

from poster import competitors, config

WINDOW_HOURS = 1.0
# Latin words that show up in every tech post and say nothing about the story.
COMMON = {
    "the", "and", "for", "with", "you", "new", "app", "pro", "max", "plus", "ultra", "mini",
    "lolzteam", "llm", "api", "gpu", "cpu", "pc", "ios", "usb", "ceo", "beta", "update",
}


def names(text: str) -> set[str]:
    """Names in a post: Latin words of 3+ letters, and capitalised Cyrillic words that
    do not start a sentence (sentence starts are capitalised whatever they are)."""
    found = {w.lower() for w in re.findall(r"\b[A-Za-z][A-Za-z0-9]{2,}\b", text)}
    for m in re.finditer(r"(?<=[^\s.!?…\n«\"])\s+«?([А-ЯЁ][а-яё]{2,})", text):
        found.add(m.group(1).lower())
    return found - COMMON


def hot(posts: dict[str, list[dict]], ts: float, covered: set[str], used: set[str]) -> tuple[str, list[str]] | None:
    """A story two channels posted within the hour, as (key, posts), or None.

    Two posts are one story when they share at least two names: one shared name
    ("Claude", "Apple") links unrelated news. covered: names in the channel's own
    recent posts; a story whose shared names are all covered is old news here.
    used: keys of stories that already started a run.
    """
    recent = []
    for channel, items in posts.items():
        for post in items:
            age = competitors._age(post["time"], ts)
            if age is not None and age <= WINDOW_HOURS:
                recent.append((channel, post["text"], names(post["text"])))
    best = None
    for n, (ch_a, text_a, names_a) in enumerate(recent):
        for ch_b, text_b, names_b in recent[n + 1 :]:
            shared = names_a & names_b
            if ch_a == ch_b or len(shared) < 2 or shared <= covered:
                continue
            key = "+".join(sorted(shared))
            if key in used or any(set(u.split("+")) <= shared for u in used):
                continue
            if best is None or len(shared) > len(best[0].split("+")):
                best = (key, [f"@{ch_a}: {text_a[:400]}", f"@{ch_b}: {text_b[:400]}"])
    return best


def scan() -> dict[str, list[dict]]:
    posts = {}
    for name in config.SOURCE_CHANNELS:
        try:
            posts[name] = competitors.fetch(name)
        except Exception:  # one channel's page being down must not stop the rest
            continue
    return posts
