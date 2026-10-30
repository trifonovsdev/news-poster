"""Builds the post caption from the model's parts; the model never writes raw HTML."""

import html
import re
from html.parser import HTMLParser

from poster import config

INLINE_TAGS = {"b", "i", "u", "s", "code"}


class _Clean(HTMLParser):
    """Keeps <b>, <i>, <u>, <s>, <code> and <a href="http(s)://..."> and escapes the rest."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in INLINE_TAGS:
            self.out.append(f"<{tag}>")
            self.open.append(tag)
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            if re.match(r"https?://[^\s\"'<>]+$", href):
                self.out.append(f'<a href="{html.escape(href)}">')
                self.open.append("a")

    def handle_endtag(self, tag):
        if tag in self.open:
            # Close everything opened after it too, so the result always nests.
            while self.open:
                last = self.open.pop()
                self.out.append(f"</{last}>")
                if last == tag:
                    break

    def handle_data(self, data):
        self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        while self.open:
            self.out.append(f"</{self.open.pop()}>")
        return "".join(self.out)


def clean(fragment: str) -> str:
    parser = _Clean()
    parser.feed(fragment)
    parser.close()
    return parser.result().strip()


def visible_length(markup: str) -> int:
    """What Telegram counts against the caption limit: UTF-16 units of the parsed text."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", markup)).encode("utf-16-le")) // 2


def render(story: dict) -> str:
    emoji = story.get("emoji", "").strip()
    # The headline is bold and the kicker italic as a whole; nested tags of the same
    # kind inside them are dropped.
    headline = html.escape(re.sub(r"<[^>]+>", "", story["headline"]).strip(), quote=False)
    parts = [f"{emoji} <b>{headline}</b>".strip()]
    parts += [clean(p) for p in story["paragraphs"] if p.strip()]
    if story.get("kicker", "").strip():
        parts.append(f"<i>{re.sub(r'</?i>', '', clean(story['kicker']))}</i>")
    parts.append(config.FOOTER)
    return "\n\n".join(parts)


def fit(story: dict, limit: int) -> str:
    """The caption within Telegram's limit: drops the last paragraph, then the kicker."""
    story = dict(story, paragraphs=[p for p in story["paragraphs"] if p.strip()])
    text = render(story)
    while visible_length(text) > limit:
        if len(story["paragraphs"]) > 1:
            story["paragraphs"] = story["paragraphs"][:-1]
        elif story.get("kicker", "").strip():
            story["kicker"] = ""
        else:
            break
        text = render(story)
    return text
