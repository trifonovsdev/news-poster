"""Premium (custom) emoji: the channel's plain emoji go out as animated ones.

`custom_emoji.json` maps a plain emoji to a custom emoji id, taken from the
posts of @technomotel, @techmedia and @lolzteam, with what the animation shows:
a custom emoji often is a meme (behind 💀 there is a walking cat), so the model
picks by what the reader will see, not by the plain character.

Telegram shows a custom emoji only when the sender has Premium; anyone else's
post keeps the plain character, so the markup is always safe to send.
"""

import json
import re
from functools import cache

from poster import config

_PATH = config.ROOT / "poster" / "custom_emoji.json"
_VS16 = "️"


@cache
def table() -> dict[str, dict]:
    """Plain emoji without U+FE0F -> {"id", "shows", "mood"}."""
    if not _PATH.exists():
        return {}
    return {key.replace(_VS16, ""): value for key, value in json.loads(_PATH.read_text()).items()}


@cache
def _pattern() -> re.Pattern | None:
    keys = sorted(table(), key=len, reverse=True)  # 🤦‍♂ before 🤦
    if not keys:
        return None
    # Each character may carry an optional U+FE0F, as models and keyboards differ.
    return re.compile("|".join("".join(re.escape(ch) + f"{_VS16}?" for ch in key) for key in keys))


def catalog() -> str:
    """One line per emoji for the model: the character and what its animation shows."""
    return "\n".join(f"{key} — {value['shows']} ({value['mood']})" for key, value in table().items())


def premium(markup: str) -> str:
    """Wraps known emoji in text (never inside tags) as <tg-emoji>."""
    pattern = _pattern()
    if not config.CUSTOM_EMOJI or pattern is None:
        return markup

    def wrap(m: re.Match) -> str:
        return f'<tg-emoji emoji-id="{table()[m.group().replace(_VS16, "")]["id"]}">{m.group()}</tg-emoji>'

    return "".join(
        part if part.startswith("<") else pattern.sub(wrap, part) for part in re.split(r"(<[^>]*>)", markup)
    )
