"""Publishing to the channel: through a bot (Bot API) or a user account's local socket.

With BOT_TOKEN set, posts go through the Bot API. Without it, they go through
CHANNEL_SOCKET, a unix socket served by a process that holds a user account's
Telegram connection (one JSON line per request: {"tool", "args"} -> {"text"} or
{"error"}; tools "status" and "send_file" as in telegram-mcp).
"""

import asyncio
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from poster import config


class ChannelError(RuntimeError):
    pass


# --- user account through a local socket ----------------------------------------


async def _call(tool: str, **args) -> str:
    reader, writer = await asyncio.open_unix_connection(str(config.CHANNEL_SOCKET))
    try:
        writer.write((json.dumps({"tool": tool, "args": args}, ensure_ascii=False) + "\n").encode())
        await writer.drain()
        # Uploading a 50 MB video takes a while.
        reply = json.loads(await asyncio.wait_for(reader.readline(), 300))
    finally:
        writer.close()
    if "error" in reply:
        raise ChannelError(reply["error"])
    return reply["text"]


async def _socket_publish(caption: str, files: list[Path]) -> None:
    paths = [str(f) for f in files]
    text = await _call("send_file", file_path=paths if len(paths) > 1 else paths[0], caption=caption)
    # telegram-mcp reports Telegram-side failures as text, not as errors.
    if not text.startswith(("File sent", "Album sent")):
        raise ChannelError(text[:300])


async def _socket_status() -> int:
    try:
        text = await _call("status")
    except (OSError, ChannelError):  # the account's process is down or restarting
        return 600
    if text == "ok":
        return 0
    return int(text.split()[1])


# --- bot through the Bot API -----------------------------------------------------

_bot_held_until = 0.0  # from Telegram's retry_after on the last 429


def _multipart(fields: dict, files: dict[str, Path]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    for name, path in files.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode()
            + path.read_bytes()
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _bot_request(caption: str, files: list[Path]) -> tuple[str, bytes, str]:
    base = {"chat_id": config.CHANNEL_ID}
    if len(files) == 1:
        kind = "video" if files[0].suffix == ".mp4" else "photo"
        fields = dict(base, caption=caption, parse_mode="HTML")
        if kind == "video":
            fields["supports_streaming"] = "true"
        body, ctype = _multipart(fields, {kind: files[0]})
        return ("sendVideo" if kind == "video" else "sendPhoto"), body, ctype
    media = [{"type": "photo", "media": f"attach://f{n}"} for n in range(len(files))]
    media[0].update(caption=caption, parse_mode="HTML")
    body, ctype = _multipart(
        dict(base, media=json.dumps(media, ensure_ascii=False)),
        {f"f{n}": f for n, f in enumerate(files)},
    )
    return "sendMediaGroup", body, ctype


def _bot_send(caption: str, files: list[Path]) -> None:
    global _bot_held_until
    method, body, ctype = _bot_request(caption, files)
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}",
        data=body,
        headers={"Content-Type": ctype},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            reply = json.load(response)
    except urllib.error.HTTPError as e:
        reply = json.load(e)
    if not reply.get("ok"):
        wait = (reply.get("parameters") or {}).get("retry_after")
        if wait:
            _bot_held_until = time.time() + wait
            raise ChannelError(f"flood: retry after {wait} s")
        raise ChannelError(f"{method}: {reply.get('description', reply)}"[:300])


# --- what the loop uses ------------------------------------------------------------


async def status() -> int:
    """0 if a post can go out now, else seconds to wait. Touches no Telegram method."""
    if config.BOT_TOKEN:
        return max(0, int(_bot_held_until - time.time()))
    return await _socket_status()


async def publish(caption: str, files: list[Path]) -> None:
    if config.BOT_TOKEN:
        await asyncio.to_thread(_bot_send, caption, files)
    else:
        await _socket_publish(caption, files)
