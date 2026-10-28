"""03_transcribe_local: a failed whisper call is retried, and a segment that
keeps failing aborts the run instead of vanishing from the transcript.

Run: python -m unittest discover tests
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "transcribe_local", ROOT / "scripts" / "pipeline" / "03_transcribe_local.py")
assert _spec and _spec.loader
tl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tl)

WAV = Path("x.wav")


class RetryTest(unittest.TestCase):
    def test_returns_after_transient_failures(self):
        calls = iter([ConnectionError("down"), ConnectionError("down"), ("hola", [])])

        def fake(*_):
            r = next(calls)
            if isinstance(r, Exception):
                raise r
            return r

        sleeps = []
        with mock.patch.object(tl, "transcribe_segment", side_effect=fake):
            out = tl.transcribe_segment_with_retry(WAV, 0, 5, delays=(1, 2, 3), sleep=sleeps.append)
        self.assertEqual(out, ("hola", []))
        self.assertEqual(sleeps, [1, 2])

    def test_raises_when_every_attempt_fails(self):
        sleeps = []
        with mock.patch.object(tl, "transcribe_segment", side_effect=ConnectionError("down")) as t:
            with self.assertRaises(tl.SegmentTranscriptionError):
                tl.transcribe_segment_with_retry(WAV, 0, 5, delays=(1, 2), sleep=sleeps.append)
        self.assertEqual(t.call_count, 3)
        self.assertEqual(sleeps, [1, 2])

    def test_empty_text_is_silence_not_retried(self):
        with mock.patch.object(tl, "transcribe_segment", return_value=("", [])) as t:
            out = tl.transcribe_segment_with_retry(WAV, 0, 5, delays=(1,), sleep=lambda _: None)
        self.assertEqual(out, ("", []))
        self.assertEqual(t.call_count, 1)


class MainTest(unittest.TestCase):
    def _run_main(self, transcribe):
        segments = [{"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00"},
                    {"start": 4.0, "end": 9.0, "speaker": "SPEAKER_01"}]
        with tempfile.TemporaryDirectory() as d:
            audio = Path(d) / "a.wav"
            audio.write_bytes(b"")
            out = Path(d) / "s.json"
            argv = ["03", "--video-id", "vid", "--audio-file", str(audio), "--output", str(out)]
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(tl, "PROJECT_ROOT", Path(d)), \
                 mock.patch.object(tl, "to_wav_16k_mono", lambda _i, o: o.write_bytes(b"")), \
                 mock.patch.object(tl, "get_duration", return_value=9.0), \
                 mock.patch.object(tl, "diarize", return_value={"segments": segments}), \
                 mock.patch.object(tl, "transcribe_segment_with_retry", side_effect=transcribe):
                rc = tl.main()
            return rc, (json.loads(out.read_text()) if out.exists() else None)

    def test_failed_segment_aborts_without_writing_session(self):
        def transcribe(_w, start, _e):
            if start == 4.0:
                raise tl.SegmentTranscriptionError("down")
            return "texto", []

        rc, session = self._run_main(transcribe)
        self.assertEqual(rc, 1)
        self.assertIsNone(session)

    def test_all_segments_ok_writes_session(self):
        rc, session = self._run_main(lambda _w, s, _e: (f"t{s}", []))
        self.assertEqual(rc, 0)
        assert session is not None
        self.assertEqual([s["text"] for s in session["segments"]], ["t0.0", "t4.0"])


if __name__ == "__main__":
    unittest.main()
