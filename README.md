# news-poster

An autonomous news editor for a Russian-language Telegram channel about tech,
AI, games, gadgets and internet culture. Claude Sonnet (headless Claude Code)
picks one post per run, writes it and picks a photo album or a video. Code
checks the result and posts it. Two kinds of posts: news in the style of
@technomotel (a CAPS hook, bold key facts, a 2–6 word punchline with an
emoji) and «находки», things to try today, in the style of @lolzteam and
@ulta_games (what it does, a «➖» list, «Забираем — тут» with the link on the
word); about one post in three is a find.

- Each run, the model gets fresh headlines (≤ 6 h, from ~35 RSS feeds in
  `poster/feeds.py`), finds (≤ 7 days: GitHub repos created lately with the
  most stars, Show HN 100+, PC game giveaways worth $15+; `poster/finds.py`),
  the competitors' latest posts with their views (public `t.me/s/` preview,
  no Telegram account needed) and the channel's own last 3 days with their
  kickers. It scores candidates (try / money / wow / drama / fandom / Russia /
  proof) and may use web search and fetch only: `prompts/news.md`.
- Code checks the answer (`poster/main.py`): source not used before,
  published at most 6 h ago (a find: 7 days), every link opens
  (`poster/links.py`: a dead one loses its link, a find without a working
  link is dropped), caption ≤ 1024 (shortened if needed), media present. The caption is built by `poster/caption.py`; only `<b> <i> <u> <s>
  <code> <a href=http…>` survive.
- Premium emoji (`poster/emoji.py`): the headline's lead emoji and the
  kicker's go out as custom emoji from `poster/custom_emoji.json` (ids taken
  from @technomotel, @techmedia and @lolzteam, each described by what its
  animation shows, since many are memes; the model gets that list). Telegram
  shows them only when the posting account has Premium; `CUSTOM_EMOJI=0` in
  `.env` turns them off (off by default for a bot).
- Media (`poster/media.py`): one video (yt-dlp, ≤ 5 min, ≤ 50 MB, H.264 or
  converted), or photos: every candidate that downloads (at least 640×360,
  saved as JPEG) plus the source pages' og:image, without repeats of one
  picture, goes to a second, short model run (`prompts/pick.md`, Read tool
  only) that looks at them and keeps one to five as an album, rejecting
  watermarks, blur, stock illustrations and off-topic shots. No media, no post.
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

With `CHANNEL_MCP=1` (and `CHANNEL_ID`), each post starts the host's
telegram-mcp server (`TELEGRAM_MCP`, the redacting wrapper by default) for its
default account and calls `send_file` with the caption as HTML; the server may
read only `data/media`. The wrapper takes that session's lock in shared mode,
like the owner's Claude sessions on the same host and IP, so the account keeps
working there. A FloodWait in the answer holds the channel for at least an hour.
This is how the channel posts as рейман, whose Premium shows the custom emoji.

Otherwise, without `BOT_TOKEN`, posts go to the unix socket `CHANNEL_SOCKET`, served by a
process that holds a user account's Telegram connection (a user account may
have only one). One JSON line per request: `{"tool": "status"}` answers `ok`
or `held <seconds>` without calling Telegram; `{"tool": "send_file", "args":
{"file_path": path or [paths], "caption": html}}` sends to the channel and
answers telegram-mcp's text ("File sent…" / "Album sent…"), or `{"error": …}`.

## Test

    python3 -m unittest discover -s tests -t .
