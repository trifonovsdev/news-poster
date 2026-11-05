"""Links in the post («Забираем — тут»): each must open before the post goes out.

The model may misremember a URL. A link that is gone (404, no such host) is
taken off its word and the text stays; a page that only refuses robots (403,
429 from Cloudflare and the like) counts as alive.
"""

import re
import urllib.error
import urllib.request

from poster import config

_LINK = re.compile(r"""<a\s+href\s*=\s*["']([^"']+)["']\s*>(.*?)</a>""", re.S | re.I)
GONE = {404, 410}


def alive(url: str) -> bool:
    if not re.match(r"https?://[^\s\"'<>]+$", url):
        return False
    request = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read(1024)
        return True
    except urllib.error.HTTPError as e:
        return e.code not in GONE
    except Exception:  # no such host, refused, timed out
        return False


def _fields(story: dict) -> list[str]:
    return [*story["paragraphs"], story.get("kicker", "")]


def verify(story: dict) -> tuple[dict, list[str]]:
    """The story with dead links unwrapped, and those links."""
    urls = {m.group(1) for text in _fields(story) for m in _LINK.finditer(text)}
    dead = [url for url in sorted(urls) if not alive(url)]

    def unwrap(text: str) -> str:
        return _LINK.sub(lambda m: m.group(2) if m.group(1) in dead else m.group(0), text)

    if not dead:
        return story, []
    return dict(story, paragraphs=[unwrap(p) for p in story["paragraphs"]], kicker=unwrap(story.get("kicker", ""))), dead


def found(story: dict) -> bool:
    return any(_LINK.search(text) for text in _fields(story))
