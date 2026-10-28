"""run_batch: a YouTube bot check stops downloading for the night (and for
the next pass), full sessions ask for audio only.

Run: python -m unittest discover tests
"""

import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import run_batch as rb  # noqa: E402


class FakeBotBlocked(Exception):
    pass


def fake_downloader(download):
    return types.SimpleNamespace(BotBlocked=FakeBotBlocked, download_video=download)


class YouTubePoliteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(rb, "BOT_PAUSE_FILE", d / "pause.json"),
            mock.patch.object(rb, "LOG_FILE", d / "batch.log"),
            mock.patch.object(rb, "DATA_SESSIONS", d / "sessions"),
            mock.patch.object(rb, "STATUS_FILE", d / "status.json"),
        ]
        for p in self.patches:
            p.start()
        (d / "sessions").mkdir()
        self.dir = d

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_bot_check_pauses_without_retrying(self):
        calls = []

        def download(video_id, data_video, log_fn, audio_only=False):
            calls.append(video_id)
            raise FakeBotBlocked("Sign in to confirm you're not a bot")

        with mock.patch.object(rb, "downloader_module", return_value=fake_downloader(download)):
            res = rb.process_video({"video_id": "abc", "video_type": "clip"}, keep_video=False)
        self.assertTrue(res["bot_blocked"])
        self.assertEqual(calls, ["abc"])
        self.assertIsNotNone(rb.youtube_paused_until())

    def test_active_pause_skips_the_next_pass(self):
        rb.BOT_PAUSE_FILE.write_text(json.dumps({"until": time.time() + 3600}))
        index = {"sessions": [{"video_id": "s1", "video_type": "full_session", "title": "Sesión 1"}]}
        (rb.DATA_SESSIONS / "index.json").write_text(json.dumps(index))
        with mock.patch.object(sys, "argv", ["run_batch", "--video-type", "full_session"]), \
             mock.patch.object(rb, "process_video") as process:
            self.assertEqual(rb.main(), 0)
        process.assert_not_called()

    def test_expired_pause_is_ignored(self):
        rb.BOT_PAUSE_FILE.write_text(json.dumps({"until": time.time() - 1}))
        self.assertIsNone(rb.youtube_paused_until())

    def test_night_stops_at_first_bot_check(self):
        index = {"sessions": [{"video_id": f"c{i}", "video_type": "clip", "title": f"Sesión {i}"}
                              for i in range(3)]}
        (rb.DATA_SESSIONS / "index.json").write_text(json.dumps(index))
        results = iter([{"video_id": "c2", "ok": False, "bot_blocked": True, "error": "youtube bot-check"}])
        with mock.patch.object(sys, "argv", ["run_batch"]), \
             mock.patch.object(rb, "process_video", side_effect=lambda *_: next(results)) as process, \
             mock.patch.object(rb.time, "sleep") as sleep:
            rb.main()
        self.assertEqual(process.call_count, 1)
        sleep.assert_not_called()

    def test_full_sessions_download_audio_only(self):
        seen = {}

        def download(video_id, data_video, log_fn, audio_only=False):
            seen[video_id] = audio_only
            return None  # stop right after the download stage

        with mock.patch.object(rb, "downloader_module", return_value=fake_downloader(download)):
            rb.process_video({"video_id": "full", "video_type": "full_session"}, keep_video=False)
            rb.process_video({"video_id": "clip", "video_type": "clip"}, keep_video=False)
        self.assertEqual(seen, {"full": True, "clip": False})


if __name__ == "__main__":
    unittest.main()
