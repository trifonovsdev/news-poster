import asyncio
import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from poster import caption, channel, competitors, config, feeds, main, media
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
    def test_render_keeps_only_safe_markup(self):
        text = caption.render(story())
        self.assertTrue(text.startswith("🤖 <b>Вышла новая нейронка</b>\n\n"))
        self.assertIn("<b>главным</b>", text)
        self.assertIn("&lt;", "".join(caption.clean("a < b")))
        self.assertNotIn("<script>", text)
        self.assertIn('<i>Пробуем <a href="https://x.com/t">тут</a></i>', text)
        self.assertTrue(text.endswith(config.FOOTER))

    def test_unsafe_links_and_unclosed_tags(self):
        self.assertEqual(caption.clean('<a href="javascript:x">y</a>'), "y")
        self.assertEqual(caption.clean("<b>bold <i>both</b> tail"), "<b>bold <i>both</i></b> tail")
        self.assertEqual(caption.clean("<b>open"), "<b>open</b>")

    def test_visible_length_counts_utf16_without_markup(self):
        self.assertEqual(caption.visible_length("<b>ab</b> &amp; 🐾"), 7)


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
            '<time datetime="2026-10-30T06:30:02+00:00"></time></div>'
        )
        self.assertEqual(
            competitors.parse(page, 5),
            [{"time": "2026-10-30T06:30:02+00:00", "media": "photo", "text": "Заголовок\nтекст & ещё"}],
        )


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


if __name__ == "__main__":
    unittest.main()
