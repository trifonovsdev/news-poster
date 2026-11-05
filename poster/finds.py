"""Candidates for «находки»: things a reader can try today, in the way of @lolzteam
and @ulta_games (a new tool on GitHub, a Show HN project, a free game).

They are not news, so they get a longer window (FIND_FRESH_DAYS): what matters
is that people are trying them right now. Each source failing costs only its own
lines.
"""

import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from poster import config, feeds

GITHUB_MIN_STARS = 500
GIVEAWAY_MIN_WORTH = 15.0  # dollars; cheaper giveaways are shovelware


def _json(url: str):
    request = urllib.request.Request(
        url, headers={"User-Agent": config.USER_AGENT, "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read(5 * 1024 * 1024))


def _stamp(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def github(ts: float) -> list[dict]:
    """Repositories created within the window, most starred first."""
    since = datetime.fromtimestamp(ts, timezone.utc) - timedelta(days=config.FIND_FRESH_DAYS)
    data = _json(
        "https://api.github.com/search/repositories"
        f"?q=created:%3E{since:%Y-%m-%d}+stars:%3E={GITHUB_MIN_STARS}&sort=stars&order=desc&per_page=20"
    )
    return [
        {
            "source": "GitHub",
            "title": f"{repo['full_name']} — {(repo.get('description') or '').strip()[:160]} "
            f"(★ {repo['stargazers_count']})",
            "url": repo["html_url"],
            "ts": _stamp(repo["created_at"]),
        }
        for repo in data.get("items", [])
        if not repo.get("archived") and not repo.get("fork")
    ]


def show_hn(ts: float) -> list[dict]:
    return feeds._fetch("Show HN", "https://hnrss.org/show?points=100")


def giveaways(ts: float) -> list[dict]:
    items = []
    for g in _json("https://www.gamerpower.com/api/giveaways?platform=pc&type=game&sort-by=date"):
        worth = re.sub(r"[^\d.]", "", g.get("worth") or "")
        if not worth or float(worth) < GIVEAWAY_MIN_WORTH:
            continue
        when = datetime.strptime(g["published_date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        items.append({
            "source": "Раздача",
            "title": f"{g['title']} ({g['worth']}, {g['platforms']}, до {g.get('end_date') or '?'})",
            "url": g.get("gamerpower_url") or g["open_giveaway_url"],
            "ts": when.timestamp(),
        })
    return items


SOURCES = (github, show_hn, giveaways)


def fresh(seen, ts: float) -> list[dict]:
    def safe(source):
        try:
            return source(ts)
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        batches = list(pool.map(safe, SOURCES))
    cutoff = ts - config.FIND_FRESH_DAYS * 86400
    return [i for batch in batches for i in batch if cutoff <= i["ts"] <= ts + 600 and not seen([i["url"]])]


def digest(items: list[dict], ts: float) -> str:
    if not items:
        return "- источники находок не ответили, ищи сам"
    return "\n".join(
        f"- {(ts - i['ts']) / 86400:.1f} дн назад · {i['source']} · {i['title']} · {i['url']}" for i in items
    )
