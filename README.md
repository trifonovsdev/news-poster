# news-poster

An autonomous news editor for a Russian-language Telegram channel about tech,
AI, games, gadgets and internet culture. Claude Sonnet (headless Claude Code)
picks one fresh story per run, writes the post in the style of @techmedia,
@lolzteam and @technomotel, and picks photos or a video. Code checks the result and posts it.

- Each run, the model gets fresh headlines (≤ 6 h, from ~35 RSS feeds in
  `poster/feeds.py`), the competitors' latest posts (public `t.me/s/`
  preview, no Telegram account needed) and the channel's own last 3 days. It
  may use web search and fetch only: `prompts/news.md`.
- Code checks the answer (`poster/main.py`): source not used before,
  published at most 6 h ago, caption ≤ 1024 (shortened if needed), media
  present. The caption is built by `poster/caption.py`; only `<b> <i> <u> <s>
  <code> <a href=http…>` survive.
- Media (`poster/media.py`): one video (yt-dlp, ≤ 5 min, ≤ 50 MB, H.264 or
  converted), or photos: every candidate that downloads (at least 640×360,
  saved as JPEG) plus the source pages' og:image goes to a second, short
  model run (`prompts/pick.md`, Read tool only) that looks at them and keeps
  one or two, rejecting watermarks, blur and off-topic shots. No media, no post.
- Breaking (`poster/breaking.py`): every 5 min code reads the source Telegram
  channels; a story two of them posted within 3 hours (two shared names) and
  the channel has not covered starts a run before the usual gap ends (20 min
  after the last post at the earliest, 8 such runs a day).
- Schedule: 09:00–23:00 MSK, up to 10 posts a day, 60–100 min after a post;
  after runs that posted nothing 20, 40, then 60 min. Settings:
  `poster/config.py`; the channel itself: `.env`.
- No wasted runs: before each model run the poster checks that the channel can
  take a post; a prepared post that failed to send is retried up to 3 times
  without a new run, unless it goes stale.

## Install

Needs Linux, Python 3.10+ with Pillow, ffmpeg, node,
[uv](https://docs.astral.sh/uv/) and Claude Code logged in (`claude` on
PATH; the model runs on that account).

    git clone <this repo> /opt/news-poster && cd /opt/news-poster
    uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python 'yt-dlp[default]'
    python3 -c 'import PIL' || sudo apt install python3-pil
    cp .env.example .env && chmod 600 .env      # fill in, see below
    python3 -m unittest discover -s tests -t .
    python3 -m poster.main once --dry           # one run: prints the post, sends nothing

`.env`: create a bot with @BotFather, add it to the channel as an admin
with "Post messages", put its token in `BOT_TOKEN` and the channel in
`CHANNEL_ID` (`@username` or `-100…`), plus `CHANNEL_NAME` and `CHANNEL_URL`
for the prompt and the footer.

Run as a service: copy `deploy/news-poster.service` to `/etc/systemd/system/`,
fix the paths and the user in it, then `systemctl enable --now news-poster`.
yt-dlp in `.venv` updates itself once a day (YouTube breaks old versions).

## Operate

    journalctl -u news-poster -f
    python3 -m poster.main once        # one story now, published
    touch PAUSE                        # stop posting; rm PAUSE to resume

State: `data/poster.db` (posted and rejected stories). The model's last run is
in `data/run/news-last.jsonl`.

## Posting as a user account

Without `BOT_TOKEN`, posts go to the unix socket `CHANNEL_SOCKET`, served by a
process that holds a user account's Telegram connection (a user account may
have only one). One JSON line per request: `{"tool": "status"}` answers `ok`
or `held <seconds>` without calling Telegram; `{"tool": "send_file", "args":
{"file_path": path or [paths], "caption": html}}` sends to the channel and
answers telegram-mcp's text ("File sent…" / "Album sent…"), or `{"error": …}`.

## Test

    python3 -m unittest discover -s tests -t .
