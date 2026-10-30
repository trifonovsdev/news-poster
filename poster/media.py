"""Downloads the photos and videos the model picked and checks them before they go out.

Everything runs without a shell: URLs come from web pages and are untrusted.
"""

import io
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from PIL import Image

from poster import config


class MediaError(RuntimeError):
    pass


def _get(url: str, limit: int) -> bytes:
    if not re.match(r"https?://", url):
        raise MediaError(f"not a web address: {url[:100]}")
    request = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise MediaError("file too large")
    return data


def photo(url: str, dest: Path) -> Path:
    try:
        image = Image.open(io.BytesIO(_get(url, config.PHOTO_MAX_BYTES)))
        image.load()
    except MediaError:
        raise
    except Exception as e:
        raise MediaError(f"not an image: {e}") from e
    w, h = image.size
    if max(w, h) < config.PHOTO_MIN_SIZE[0] or min(w, h) < config.PHOTO_MIN_SIZE[1]:
        raise MediaError(f"too small: {w}x{h}")
    if not 0.4 <= w / h <= 2.6:
        raise MediaError(f"banner-shaped: {w}x{h}")
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.getchannel("A"))
        image = background
    image = image.convert("RGB")
    image.thumbnail((2560, 2560))
    path = dest.with_suffix(".jpg")
    image.save(path, "JPEG", quality=90)
    return path


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        timeout=60,
    )
    if out.returncode:
        raise MediaError("ffprobe could not read the video")
    info = json.loads(out.stdout)
    video = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    if not video:
        raise MediaError("no video stream")
    return {
        "codec": video.get("codec_name"),
        "height": int(video.get("height") or 0),
        "seconds": float(info["format"].get("duration") or 0),
        "bytes": int(info["format"].get("size") or 0),
        "audio": [s.get("codec_name") for s in info["streams"] if s.get("codec_type") == "audio"],
    }


def video(url: str, dest: Path) -> Path:
    if not re.match(r"https?://", url):
        raise MediaError(f"not a web address: {url[:100]}")
    raw = dest.with_suffix(".src.mp4")
    h = config.VIDEO_MAX_HEIGHT
    result = subprocess.run(
        [
            str(config.YTDLP),
            "--js-runtimes", f"node:{config.NODE}",
            "--no-playlist",
            "--no-progress",
            "--quiet",
            "--no-warnings",
            "--max-filesize", str(config.VIDEO_MAX_BYTES),
            "--match-filter", f"duration<={config.VIDEO_MAX_SECONDS}",
            "-f",
            f"bv*[vcodec^=avc1][height<={h}]+ba[ext=m4a]/b[vcodec^=avc1][height<={h}]"
            f"/bv*[height<={h}]+ba/b[height<={h}]",
            "--merge-output-format", "mp4",
            "-o", str(raw),
            "--",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode or not raw.exists():
        raise MediaError(f"yt-dlp: {(result.stderr or 'nothing downloaded').strip()[-300:]}")
    info = probe(raw)
    if info["seconds"] > config.VIDEO_MAX_SECONDS + 1:
        raise MediaError(f"too long: {info['seconds']:.0f}s")
    path = dest.with_suffix(".mp4")
    if info["codec"] == "h264" and set(info["audio"]) <= {"aac"}:
        raw.rename(path)
    else:
        # VP9/AV1 do not play inline in every Telegram client.
        done = subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-y", "-i", str(raw),
                "-vf", "scale=-2:'min(720,ih)'",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(path),
            ],
            capture_output=True,
            timeout=900,
        )
        raw.unlink(missing_ok=True)
        if done.returncode:
            raise MediaError("ffmpeg could not convert the video")
    if path.stat().st_size > config.VIDEO_MAX_BYTES:
        path.unlink()
        raise MediaError("too large after conversion")
    return path


def page_image(url: str) -> str | None:
    """The page's own preview image (og:image / twitter:image), if it has one."""
    try:
        page = _get(url, 3 * 1024 * 1024).decode("utf-8", "replace")
    except Exception:
        return None
    for name in ("og:image", "twitter:image"):
        m = re.search(
            rf'<meta[^>]+(?:property|name)=["\']{name}["\'][^>]+content=["\']([^"\']+)', page
        ) or re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{name}["\']', page)
        if m:
            return urllib.request.urljoin(url, m.group(1).replace("&amp;", "&"))
    return None


def collect(candidates: list[dict], sources: list[str], stamp: str, log) -> list[Path]:
    """One video, or up to three photos, in the model's order; the source's own image last."""
    files: list[Path] = []
    for n, item in enumerate(candidates):
        dest = config.MEDIA / f"{stamp}-{n}"
        try:
            if item["kind"] == "video":
                if files:
                    continue  # a video goes alone: albums mute it
                return [video(item["url"], dest)]
            files.append(photo(item["url"], dest))
        except Exception as e:
            log.info("media skipped %s: %s", item.get("url", "")[:120], e)
        if len(files) == 3:
            break
    if not files:
        for url in sources[:2]:
            image = page_image(url)
            if not image:
                continue
            try:
                return [photo(image, config.MEDIA / f"{stamp}-og")]
            except Exception as e:
                log.info("og image skipped %s: %s", image[:120], e)
    return files


def update_ytdlp() -> None:
    """Sites change often and yt-dlp follows within days; stale versions just fail."""
    subprocess.run(
        ["uv", "pip", "install", "-q", "-U", "--python", str(config.VENV / "bin" / "python"), "yt-dlp[default]"],
        capture_output=True,
        timeout=300,
    )


def prune(days: float = 2) -> None:
    cutoff = time.time() - days * 86400
    for path in config.MEDIA.glob("*"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
