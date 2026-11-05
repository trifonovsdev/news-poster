import asyncio
import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from poster import breaking, caption, channel, competitors, config, emoji, feeds, finds, links, main, media
from poster.store import Store


def story(**overrides):
    base = {
        "skip": False,
        "skip_reason": "",
        "topic": "релиз",
        "source_urls": ["https://example.com/a"],
        "published_at": "",
        "emoji": "🤖",
        "headline": "Вышла <b>новая</b> нейронка",
        "paragraphs": ["Первый абзац с <b>главным</b> и <script>alert(1)</script>.", "Второй."],
        "kicker": 'Пробуем <a href="https://x.com/t">тут</a>',
        "media": [],
    }
    base.update(overrides)
    return base


class CaptionTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(config, "CUSTOM_EMOJI", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_render_keeps_only_safe_markup(self):
        text = caption.render(story())
        self.assertTrue(text.startswith("🤖 Вышла <b>новая</b> нейронка\n\n"))
        self.assertTrue(caption.render(story(emoji="")).startswith("Вышла"))
        self.assertIn("<b>главным</b>", text)
        self.assertIn("&lt;", "".join(caption.clean("a < b")))
        self.assertNotIn("<script>", text)
        self.assertIn('\n\nПробуем <a href="https://x.com/t">тут</a>\n\n', text)
        self.assertTrue(text.endswith(config.FOOTER))

    def test_unsafe_links_and_unclosed_tags(self):
        self.assertEqual(caption.clean('<a href="javascript:x">y</a>'), "y")
        self.assertEqual(caption.clean("<b>bold <i>both</b> tail"), "<b>bold <i>both</i></b> tail")
        self.assertEqual(caption.clean("<b>open"), "<b>open</b>")

    def test_visible_length_counts_utf16_without_markup(self):
        self.assertEqual(caption.visible_length("<b>ab</b> &amp; 🐾"), 7)


class EmojiTest(unittest.TestCase):
    TABLE = {
        "💀": {"id": "1", "shows": "кот", "mood": "ирония"},
        "⚡": {"id": "2", "shows": "молния", "mood": "срочно"},
        "🤦‍♂": {"id": "3", "shows": "фейспалм", "mood": "стыд"},
    }

    def setUp(self):
        emoji._pattern.cache_clear()
        for patcher in (
            mock.patch.object(emoji, "table", lambda: self.TABLE),
            mock.patch.object(config, "CUSTOM_EMOJI", True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(emoji._pattern.cache_clear)

    def test_known_emoji_in_text_become_custom(self):
        self.assertEqual(
            emoji.premium('<b>Дожили</b> 💀 <a href="https://e.com/💀">x</a> 🙂'),
            '<b>Дожили</b> <tg-emoji emoji-id="1">💀</tg-emoji> <a href="https://e.com/💀">x</a> 🙂',
        )
        self.assertEqual(emoji.premium("⚡️ и ⚡"), '<tg-emoji emoji-id="2">⚡️</tg-emoji> и <tg-emoji emoji-id="2">⚡</tg-emoji>')
        self.assertEqual(emoji.premium("🤦‍♂️"), '<tg-emoji emoji-id="3">🤦‍♂️</tg-emoji>')

    def test_caption_uses_them_but_counts_only_the_character(self):
        text = caption.render(story(emoji="⚡️", kicker="Дожили 💀"))
        self.assertTrue(text.startswith('<tg-emoji emoji-id="2">⚡️</tg-emoji> Вышла'))
        self.assertIn('Дожили <tg-emoji emoji-id="1">💀</tg-emoji>', text)
        self.assertNotIn("tg-emoji", caption.render(story(emoji="", kicker="", paragraphs=["Бан 💀"])))
        with mock.patch.object(config, "CUSTOM_EMOJI", False):
            plain = caption.render(story(emoji="⚡️", kicker="Дожили 💀"))
        self.assertNotIn("tg-emoji", plain)
        self.assertEqual(caption.visible_length(text), caption.visible_length(plain))
        self.assertIn("💀 — кот (ирония)", emoji.catalog())


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.ts = time.time()

    def iso(self, hours_ago):
        return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(self.ts - hours_ago * 3600))

    def test_fresh_story_passes(self):
        self.assertIsNone(main.check(story(published_at=self.iso(1)), self.store, self.ts))

    def test_stale_future_naive_and_repeated_are_refused(self):
        self.assertIn("stale", main.check(story(published_at=self.iso(9)), self.store, self.ts))
        self.assertIn("future", main.check(story(published_at=self.iso(-3)), self.store, self.ts))
        self.assertIn("bad", main.check(story(published_at="2026-10-30T10:00:00"), self.store, self.ts))
        self.store.add("rejected", "https://example.com/a", reason="x")
        self.assertIn("already", main.check(story(published_at=self.iso(1)), self.store, self.ts))

    def test_daily_limit_and_hours(self):
        noon = main.msk(self.ts).replace(hour=12).timestamp()
        night = main.msk(self.ts).replace(hour=3).timestamp()
        self.assertTrue(main.due(self.store, noon))
        self.assertFalse(main.due(self.store, night))
        for n in range(config.POSTS_PER_DAY):
            self.store.db.execute(
                "INSERT INTO stories (ts, status, url) VALUES (?, 'posted', ?)", (noon - 60, f"u{n}")
            )
        self.assertFalse(main.due(self.store, noon))


class MediaTest(unittest.TestCase):
    def png(self, w, h, mode="RGBA"):
        buffer = io.BytesIO()
        Image.new(mode, (w, h)).save(buffer, "PNG")
        return buffer.getvalue()

    def test_photo_is_converted_to_jpeg_and_small_ones_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "x"
            with mock.patch.object(media, "_get", return_value=self.png(1200, 700)):
                path = media.photo("https://e.com/i.png", dest)
            self.assertEqual(path.suffix, ".jpg")
            self.assertEqual(Image.open(path).format, "JPEG")
            with mock.patch.object(media, "_get", return_value=self.png(300, 200)):
                with self.assertRaises(media.MediaError):
                    media.photo("https://e.com/i.png", dest)
            with mock.patch.object(media, "_get", return_value=self.png(3000, 600)):
                with self.assertRaises(media.MediaError):
                    media.photo("https://e.com/i.png", dest)

    def test_repeats_of_one_picture_are_dropped_keeping_the_largest(self):
        def picture(flip):
            image = Image.linear_gradient("L").rotate(90).resize((800, 450)).convert("RGB")  # left to right
            return image.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if flip else image

        with tempfile.TemporaryDirectory() as tmp:
            small, other, big = (Path(tmp) / n for n in ("small.jpg", "other.jpg", "big.jpg"))
            picture(False).resize((640, 360)).save(small, quality=60)
            picture(True).save(other)
            picture(False).resize((1600, 900)).save(big, quality=95)
            self.assertEqual(media.unique([small, other, big]), [big, other])

    def test_only_web_addresses(self):
        with self.assertRaises(media.MediaError):
            media.video("file:///etc/passwd", Path("/tmp/x"))
        with self.assertRaises(media.MediaError):
            media._get("-oProxyCommand=x", 10)


class CompetitorsTest(unittest.TestCase):
    def test_parse_preview_page(self):
        page = (
            '<div class="tgme_widget_message_wrap"><div class="tgme_widget_message_photo_wrap"></div>'
            '<div class="tgme_widget_message_text js-message_text"><b>Заголовок</b><br/>текст &amp; ещё</div>'
            '<span class="tgme_widget_message_views">12.1K</span>'
            '<time datetime="2026-10-30T06:30:02+00:00"></time></div>'
        )
        self.assertEqual(
            competitors.parse(page, 5),
            [{"time": "2026-10-30T06:30:02+00:00", "media": "photo", "text": "Заголовок\nтекст & ещё", "views": 12100}],
        )
        self.assertEqual([competitors.views(v) for v in ("950", "1.3M", "?")], [950, 1_300_000, 0])


class LinksTest(unittest.TestCase):
    def test_dead_links_lose_their_word_and_a_find_needs_one(self):
        good, gone = "https://github.com/a/b", "https://e.com/nope"
        post = story(
            paragraphs=[f'Valve <a href="{gone}">обновила</a> правила'],
            kicker=f'Забираем — <a href="{good}">тут</a> 😎',
        )
        with mock.patch.object(links, "alive", lambda url: url == good):
            fixed, dead = links.verify(post)
        self.assertEqual(dead, [gone])
        self.assertEqual(fixed["paragraphs"], ["Valve обновила правила"])
        self.assertEqual(fixed["kicker"], post["kicker"])
        self.assertTrue(links.found(fixed))
        self.assertFalse(links.found(story(kicker="Дожили 💀", paragraphs=["x"])))

    def test_alive(self):
        def answer(code):
            return mock.patch.object(
                links.urllib.request, "urlopen", side_effect=links.urllib.error.HTTPError("u", code, "", {}, None)
            )

        with answer(404):
            self.assertFalse(links.alive("https://e.com/x"))
        with answer(403):  # Cloudflare refusing robots: the page is there
            self.assertTrue(links.alive("https://e.com/x"))
        self.assertFalse(links.alive("javascript:alert(1)"))


class FindsTest(unittest.TestCase):
    def test_window_kind_and_cheap_giveaways(self):
        ts = time.time()
        repo = {"full_name": "a/b", "description": "tool", "stargazers_count": 900, "html_url": "https://github.com/a/b",
                "created_at": "2020-01-01T00:00:00Z"}
        fresh_repo = dict(repo, html_url="https://github.com/a/c", created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts - 86400)))
        gifts = [
            {"title": "Cheap", "worth": "$4.99", "platforms": "PC", "published_date": "2026-11-04 07:40:59",
             "open_giveaway_url": "https://g/1"},
            {"title": "Big", "worth": "$24.99", "platforms": "PC", "published_date": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts - 3600)),
             "open_giveaway_url": "https://g/2", "gamerpower_url": "https://g/2i", "end_date": "N/A"},
        ]

        def fake(url):
            return {"items": [repo, fresh_repo]} if "github" in url else gifts

        with mock.patch.object(finds, "_json", fake), mock.patch.object(finds.feeds, "_fetch", return_value=[]):
            items = finds.fresh(lambda urls: urls == ["https://github.com/a/x"], ts)
        self.assertEqual([i["url"] for i in items], ["https://github.com/a/c", "https://g/2i"])
        self.assertIn("★ 900", items[0]["title"])

    def test_a_find_may_be_days_old_news_may_not(self):
        store = Store(":memory:")
        old = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 3 * 86400))
        self.assertIsNone(main.check(story(kind="find", published_at=old), store, time.time()))
        self.assertIn("stale", main.check(story(kind="news", published_at=old), store, time.time()))


class FindTurnTest(unittest.TestCase):
    def task(self, topics):
        store = Store(":memory:")
        for n, topic in enumerate(topics):
            store.add("posted", f"https://e.com/{n}", topic=topic, headline="h", caption="")
        with mock.patch.object(main.feeds, "fresh", return_value=[]), \
                mock.patch.object(main.finds, "fresh", return_value=[]), \
                mock.patch.object(main.competitors, "digest", return_value=""):
            return main.task_text(store, time.time())

    def test_a_find_after_three_news_in_a_row(self):
        self.assertIn("сделай находку", self.task(["a", "b", "c"]))
        self.assertNotIn("сделай находку", self.task(["a", "b"]))
        self.assertNotIn("сделай находку", self.task(["a", "b", "c", main.FIND_TAG + "d", "e"]))


class KickerTest(unittest.TestCase):
    def test_closing_line_of_a_sent_caption(self):
        sent = 'Заголовок\n\nТекст\n\nЗабираем — <a href="https://x">тут</a> 😎\n\n🐾 <a href="https://t.me/c">c</a>'
        self.assertEqual(main.kicker(sent), "Забираем — тут 😎")
        self.assertEqual(main.kicker("один абзац"), "")


class FitTest(unittest.TestCase):
    def test_long_caption_loses_last_paragraph_then_kicker(self):
        long = story(paragraphs=["а" * 300, "б" * 300, "в" * 400])
        text = caption.fit(long, 1024)
        self.assertLessEqual(caption.visible_length(text), 1024)
        self.assertIn("б" * 300, text)
        self.assertNotIn("в" * 400, text)
        self.assertIn("Пробуем", text)
        self.assertNotIn("Пробуем", caption.fit(story(paragraphs=["а" * 980]), 1024))


class TickTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "a.jpg"
        self.file.write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def pend(self, age_hours=1):
        self.store.put(
            "pending",
            {
                "url": "https://e.com/a",
                "published": time.time() - age_hours * 3600,
                "files": [str(self.file)],
                "record": {"topic": "t", "headline": "h", "caption": "c", "media": "[]"},
            },
        )

    def test_held_channel_costs_no_model_run(self):
        with mock.patch.object(main.channel, "status", mock.AsyncMock(return_value=5000)), \
                mock.patch.object(main, "make_post", mock.AsyncMock()) as make:
            asyncio.run(main.tick(self.store))
        make.assert_not_called()
        self.assertGreater(self.store.get("next_run_at"), time.time() + 4900)

    def test_flood_keeps_the_post_and_next_tick_sends_it_without_the_model(self):
        self.pend()
        refuse = mock.AsyncMock(side_effect=main.channel.ChannelError("Refused: Telegram flood limit"))
        with mock.patch.object(main.channel, "status", mock.AsyncMock(return_value=0)), \
                mock.patch.object(main.channel, "publish", refuse), \
                mock.patch.object(main, "make_post", mock.AsyncMock()) as make:
            asyncio.run(main.tick(self.store))
        self.assertIsNotNone(self.store.get("pending"))
        with mock.patch.object(main.channel, "status", mock.AsyncMock(return_value=0)), \
                mock.patch.object(main.channel, "publish", mock.AsyncMock()), \
                mock.patch.object(main, "make_post", mock.AsyncMock()) as make:
            asyncio.run(main.tick(self.store))
        make.assert_not_called()
        self.assertIsNone(self.store.get("pending"))
        self.assertEqual(self.store.posted_since(0), 1)

    def test_a_post_refused_three_times_is_given_up(self):
        self.pend()
        refuse = mock.AsyncMock(side_effect=main.channel.ChannelError("Bad Request"))
        with mock.patch.object(main.channel, "publish", refuse):
            for _ in range(main.SEND_ATTEMPTS):
                self.assertIsNotNone(self.store.get("pending"))
                with self.assertRaises(main.channel.ChannelError):
                    asyncio.run(main.publish_pending(self.store))
        self.assertIsNone(self.store.get("pending"))
        self.assertTrue(self.store.seen(["https://e.com/a"]))

    def test_stale_pending_post_is_dropped(self):
        self.pend(age_hours=config.FRESH_HOURS + 1)
        self.assertFalse(asyncio.run(main.publish_pending(self.store)))
        self.assertIsNone(self.store.get("pending"))
        self.assertTrue(self.store.seen(["https://e.com/a"]))

    def test_empty_runs_back_off(self):
        waits = []
        with mock.patch.object(main.channel, "status", mock.AsyncMock(return_value=0)), \
                mock.patch.object(main, "make_post", mock.AsyncMock(return_value=False)), \
                mock.patch.object(main.media, "prune"):
            for _ in range(4):
                asyncio.run(main.tick(self.store))
                waits.append(round((self.store.get("next_run_at") - time.time()) / 60))
        self.assertEqual(waits, [20, 40, 60, 60])


class BotTest(unittest.TestCase):
    def test_request_per_media_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = [Path(tmp) / n for n in ("a.jpg", "b.jpg", "c.mp4")]
            for f in files:
                f.write_bytes(b"data")
            with mock.patch.object(config, "CHANNEL_ID", "@chan"):
                self.assertEqual(channel._bot_request("c", files[:1])[0], "sendPhoto")
                method, body, _ = channel._bot_request("c", files[2:])
                self.assertEqual(method, "sendVideo")
                self.assertIn(b'name="supports_streaming"', body)
                method, body, _ = channel._bot_request("<b>c</b>", files[:2])
                self.assertEqual(method, "sendMediaGroup")
                self.assertIn(b"attach://f1", body)
                self.assertIn(b'"caption": "<b>c</b>"', body)

    def test_429_holds_the_channel(self):
        class Reply(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        answer = Reply(b'{"ok": false, "error_code": 429, "parameters": {"retry_after": 120}}')
        with tempfile.TemporaryDirectory() as tmp:
            photo = Path(tmp) / "a.jpg"
            photo.write_bytes(b"x")
            with mock.patch.object(config, "BOT_TOKEN", "t"), \
                    mock.patch.object(channel.urllib.request, "urlopen", return_value=answer):
                with self.assertRaises(channel.ChannelError):
                    channel._bot_send("c", [photo])
                self.assertGreater(asyncio.run(channel.status()), 100)
        channel._bot_held_until = 0.0


FAKE_MCP = """#!/usr/bin/env python3
import json, os, sys
for line in sys.stdin:
    m = json.loads(line)
    if m.get("method") == "initialize":
        result = {"serverInfo": {"name": "fake"}}
    elif m.get("method") == "tools/call":
        a = m["params"]["arguments"]
        text = os.environ["FAKE_REPLY"] if "FAKE_REPLY" in os.environ else (
            "Album sent to %s" if isinstance(a["file_path"], list) else "File sent to %s") % a["chat_id"]
        text += " | " + os.environ["TELEGRAM_EXPOSED_TOOLS"] + " | " + a["parse_mode"]
        result = {"content": [{"type": "text", "text": text}]}
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": result}), flush=True)
"""


class McpChannelTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        server = Path(tmp.name) / "fake-mcp"
        server.write_text(FAKE_MCP)
        server.chmod(0o755)
        for patcher in (
            mock.patch.object(config, "TELEGRAM_MCP", server),
            mock.patch.object(config, "CHANNEL_MCP", True),
            mock.patch.object(config, "BOT_TOKEN", ""),
            mock.patch.object(config, "CHANNEL_ID", "@chan"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(setattr, channel, "_mcp_held_until", 0.0)

    def test_album_goes_out_through_send_file_only(self):
        text = asyncio.run(channel._mcp_send_file(["/m/a.jpg", "/m/b.jpg"], "<b>c</b>"))
        self.assertEqual(text, "Album sent to @chan | read-only+send_file | html")
        asyncio.run(channel.publish("c", [Path("/m/a.jpg")]))
        self.assertEqual(asyncio.run(channel.status()), 0)

    def test_flood_holds_the_channel(self):
        with mock.patch.dict(channel.os.environ, {"FAKE_REPLY": "Error: FloodWaitError, wait 120 seconds"}):
            with self.assertRaises(channel.ChannelError):
                asyncio.run(channel.publish("c", [Path("/m/a.jpg")]))
        self.assertGreater(asyncio.run(channel.status()), 3000)


class BreakingTest(unittest.TestCase):
    TS = 1_800_000_000.0

    def post(self, minutes_ago, text):
        when = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(self.TS - minutes_ago * 60))
        return {"time": when, "media": "photo", "text": text}

    def test_two_channels_with_two_shared_names_are_hot(self):
        posts = {
            "a": [self.post(5, "Китайскую Kimi можно оплатить картами Мир.")],
            "b": [self.post(10, "Разработчики Kimi добавили оплату картой «Мир».")],
            "c": [self.post(3, "Claude запретили копировать игры.")],
        }
        key, texts = breaking.hot(posts, self.TS, set(), set())
        self.assertEqual(key, "kimi+мир")
        self.assertEqual(len(texts), 2)
        self.assertIsNone(breaking.hot(posts, self.TS, {"kimi", "мир"}, set()))
        self.assertIsNone(breaking.hot(posts, self.TS, set(), {"kimi+мир"}))

    def test_one_shared_name_old_posts_and_one_channel_are_not(self):
        posts = {
            "a": [self.post(5, "Claude запретили копировать игры."),
                  self.post(6, "Kimi можно оплатить картами Мир.")],
            "b": [self.post(9, "Kimi обогнала Claude в тестах."),
                  self.post(240, "Kimi принимает карты Мир.")],
        }
        self.assertIsNone(breaking.hot(posts, self.TS, set(), set()))

    def test_sentence_starts_are_not_names(self):
        self.assertEqual(breaking.names("Теперь можно платить. Новая функция у Kimi и «Мир»"), {"kimi", "мир"})


class PickTest(unittest.TestCase):
    def test_keeps_only_known_files_and_falls_back_on_error(self):
        files = [Path("/m/a.jpg"), Path("/m/b.jpg")]
        answer = {"chosen": ["b.jpg", "../etc/passwd"], "reason": "r"}
        with mock.patch.object(main.llm, "run", mock.AsyncMock(return_value=answer)):
            self.assertEqual(asyncio.run(main.pick_photos("t", files)), [files[1]])
        with mock.patch.object(main.llm, "run", mock.AsyncMock(side_effect=main.llm.LLMError("x"))):
            self.assertEqual(asyncio.run(main.pick_photos("t", files)), files[:1])


class FeedsTest(unittest.TestCase):
    def test_rss_and_atom(self):
        rss = (
            b"<rss><channel><item><title>A &lt;b&gt;x&lt;/b&gt;</title><link>https://e.com/a</link>"
            b"<pubDate>Thu, 30 Oct 2026 12:00:00 +0000</pubDate></item>"
            b"<item><title>no date</title><link>https://e.com/b</link></item></channel></rss>"
        )
        atom = (
            b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>B</title>'
            b'<link rel="alternate" href="https://e.com/c"/><updated>2026-10-30T12:00:00Z</updated>'
            b"</entry></feed>"
        )
        self.assertEqual([i["title"] for i in feeds.parse(rss, "s")], ["A x"])
        self.assertEqual(feeds.parse(atom, "s")[0]["url"], "https://e.com/c")

    def test_fresh_window_seen_and_per_source_cap(self):
        ts = 1_800_000_000.0
        batch = [{"source": "s", "title": str(n), "url": f"u{n}", "ts": ts - n * 600} for n in range(60)]
        with mock.patch.object(feeds, "FEEDS", {"s": "x"}), mock.patch.object(feeds, "_fetch", return_value=batch):
            items = feeds.fresh(lambda urls: urls == ["u0"], ts)
        self.assertEqual([i["url"] for i in items], [f"u{n}" for n in range(1, feeds.PER_SOURCE + 1)])


class OldNewsTest(unittest.TestCase):
    def test_story_competitors_posted_hours_ago_is_old(self):
        ts = 1_800_000_000.0
        today = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts - 20 * 3600))
        old = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts - 30 * 3600))
        posts = {
            "lolzteam": [{"time": today, "text": "В Claude Code для Cursor нашли бэкдор через SSH"}],
            "techmedia": [{"time": old, "text": "Apple выпустила iPhone с чипом и Vision Pro"}],
        }
        with mock.patch.object(competitors, "fetch", lambda name, limit: posts.get(name, [])):
            ours = "Поддельный Claude Code в магазине Cursor ставил SSH-бэкдор"
            self.assertEqual(competitors.old_news(ours, ts, breaking.names), "lolzteam")
            # posted within HOT_HOURS it is hot, not old
            self.assertIsNone(competitors.old_news(ours, ts + 3600 - 19 * 3600, breaking.names))
            # older than a day, and one shared name, are not the same story
            self.assertIsNone(competitors.old_news("Apple показала iPhone и Vision Pro", ts, breaking.names))
            self.assertIsNone(competitors.old_news("Claude научился рисовать", ts, breaking.names))


class StoreTest(unittest.TestCase):
    def test_story_dropped_for_media_may_come_back(self):
        store = Store(":memory:")
        store.add("rejected", "u1", reason="no usable media")
        store.add("rejected", "u2", reason="stale")
        self.assertFalse(store.seen(["u1"]))
        self.assertTrue(store.seen(["u2"]))


if __name__ == "__main__":
    unittest.main()
