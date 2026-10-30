"""Fresh headlines from news sites' RSS/Atom feeds: the model's candidate list.

Web search alone rarely shows when a page was published; a feed does, so the
freshness window is enforced here, before the model sees a story.
"""

import email.utils
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from poster import config

FEEDS = {
    # tech and AI
    "The Verge": "https://www.theverge.com/rss/index.xml",
    "TechCrunch": "https://techcrunch.com/feed/",
    "Engadget": "https://www.engadget.com/rss.xml",
    "Ars Technica": "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "Wired": "https://www.wired.com/feed/rss",
    "The Register": "https://www.theregister.com/headlines.atom",
    "404 Media": "https://www.404media.co/rss/",
    "Hacker News 300+": "https://hnrss.org/frontpage?points=300",
    "OpenAI": "https://openai.com/news/rss.xml",
    "Google": "https://blog.google/rss/",
    "Nvidia": "https://blogs.nvidia.com/feed/",
    "Apple Newsroom": "https://www.apple.com/newsroom/rss-feed.rss",
    "Microsoft": "https://blogs.microsoft.com/feed/",
    "Meta": "https://about.fb.com/news/feed/",
    "Samsung": "https://news.samsung.com/global/feed",
    # gadgets and hardware
    "Tom's Hardware": "https://www.tomshardware.com/feeds.xml",
    "GSMArena": "https://www.gsmarena.com/rss-news-reviews.php3",
    "9to5Mac": "https://9to5mac.com/feed/",
    "9to5Google": "https://9to5google.com/feed/",
    # security
    "BleepingComputer": "https://www.bleepingcomputer.com/feed/",
    # games
    "IGN": "https://feeds.feedburner.com/ign/news",
    "Eurogamer": "https://www.eurogamer.net/feed",
    "VGC": "https://www.videogameschronicle.com/feed/",
    "PC Gamer": "https://www.pcgamer.com/rss/",
    "GameSpot": "https://www.gamespot.com/feeds/news/",
    "Kotaku": "https://kotaku.com/rss",
    "PlayStation Blog": "https://blog.playstation.com/feed/",
    "Xbox Wire": "https://news.xbox.com/en-us/feed/",
    # internet culture
    "Know Your Meme": "https://knowyourmeme.com/newsfeed.rss",
    # Russian
    "3DNews": "https://3dnews.ru/news/rss/",
    "iXBT": "https://www.ixbt.com/export/news/rss.xml",
    "Habr news": "https://habr.com/ru/rss/news/",
    "DTF": "https://dtf.ru/rss/all",
    "Wylsacom": "https://wylsa.com/feed/",
    "CNews": "https://www.cnews.ru/inc/rss/news.xml",
}
MAX_ITEMS = 90
PER_SOURCE = 6  # busy feeds (CNews, The Register) would crowd out the rest


def _when(text: str | None) -> float | None:
    if not text:
        return None
    text = text.strip()
    try:
        return email.utils.parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return moment.timestamp() if moment.tzinfo else None
    except ValueError:
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse(xml: bytes, source: str) -> list[dict]:
    root = ET.fromstring(xml)
    items = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        fields = {}
        for child in node:
            name = _local(child.tag)
            if name == "link":
                href = child.get("href")
                if href and child.get("rel", "alternate") == "alternate":
                    fields.setdefault("link", href)
                elif child.text and child.text.strip():
                    fields.setdefault("link", child.text.strip())
            elif name in ("title", "pubDate", "published", "updated", "date") and child.text:
                fields.setdefault(name, child.text.strip())
        ts = _when(fields.get("pubDate") or fields.get("published") or fields.get("date") or fields.get("updated"))
        if fields.get("title") and fields.get("link") and ts:
            title = re.sub(r"<[^>]+>", "", fields["title"])
            items.append({"source": source, "title": title, "url": fields["link"], "ts": ts})
    return items


def _fetch(source: str, url: str) -> list[dict]:
    request = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return parse(response.read(5 * 1024 * 1024), source)
    except Exception:  # one dead feed must not cost the run
        return []


def fresh(seen, ts: float) -> list[dict]:
    """Items published within FRESH_HOURS that the channel has not used, newest first."""
    with ThreadPoolExecutor(max_workers=12) as pool:
        batches = pool.map(lambda kv: _fetch(*kv), FEEDS.items())
    cutoff = ts - config.FRESH_HOURS * 3600
    items = [i for batch in batches for i in batch if cutoff <= i["ts"] <= ts + 600]
    items = [i for i in items if not seen([i["url"]])]
    items.sort(key=lambda i: -i["ts"])
    per_source: dict[str, int] = {}
    kept = []
    for item in items:
        per_source[item["source"]] = per_source.get(item["source"], 0) + 1
        if per_source[item["source"]] <= PER_SOURCE:
            kept.append(item)
    return kept[:MAX_ITEMS]


def digest(items: list[dict], ts: float) -> str:
    if not items:
        return "- лент со свежим нет, ищи сам"
    return "\n".join(
        f"- {(ts - i['ts']) / 3600:.1f} ч назад · {i['source']} · {i['title']} · {i['url']}" for i in items
    )


def check_all() -> dict:
    """Items per feed, for checking the list by hand."""
    with ThreadPoolExecutor(max_workers=12) as pool:
        return dict(zip(FEEDS, pool.map(lambda kv: len(_fetch(*kv)), FEEDS.items())))


if __name__ == "__main__":
    for name, count in check_all().items():
        print(f"{count:4d}  {name}")
    print(time.strftime("%H:%M"), len(fresh(lambda urls: False, time.time())), "fresh")
