"""04b_voiceprint_match: --voice-only names full-session clusters by voice
against roster voiceprints only, and overlay (ocr-oov) names never enroll.

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
    "voiceprint_match", ROOT / "scripts" / "pipeline" / "04b_voiceprint_match.py")
assert _spec and _spec.loader
vm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vm)

MEMBER = [1.0, 0.0, 0.0]
HOST = [0.0, 1.0, 0.0]


def seg(sid, start, end, name="No identificado", confidence=0.0):
    return {"speaker_id": sid, "start": start, "end": end, "text": "x",
            "speaker": {"id": sid, "name": name, "confidence": confidence}}


class VoiceOnlyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.roster = d / "asambleistas.json"
        self.roster.write_text(json.dumps({"asambleistas": [{"name": "Ana Pérez"}]}))
        self.db = {
            "ana perez": {"name": "Ana Pérez", "embedding": MEMBER, "n_samples": 3},
            # a non-member read off some clip overlay, e.g. the TV host
            "maritza flores": {"name": "Maritza Flores", "embedding": HOST, "n_samples": 1},
        }
        self.dir = d

    def tearDown(self):
        self.tmp.cleanup()

    def run_voice_only(self, embeddings, threshold=0.75):
        session = {"segments": [seg("s0", 0, 30, "Pleno De La", 1.0), seg("s1", 30, 60), seg("s2", 60, 62)],
                   "speaker_mapping": {"s0": {"name": "Pleno De La", "source": "ocr-oov"}}}
        clusters = {}
        for s in session["segments"]:
            clusters.setdefault(s["speaker_id"], []).append(s)
        clip_ids = iter(embeddings)
        with mock.patch.object(vm, "ROSTER_FILE", self.roster), \
             mock.patch.object(vm, "build_cluster_clip", side_effect=lambda _v, spans, _o: sum(e - s for s, e in spans)), \
             mock.patch.object(vm, "embed_clip", side_effect=lambda _c: next(clip_ids)):
            matched = vm.identify_by_voice("vid", Path("v.mp4"), session, clusters, dict(self.db),
                                           self.dir, threshold)
        return matched, session

    def test_member_named_host_and_short_cluster_unidentified(self):
        # s0 sounds like the host (only in DB as a non-member), s1 like the member;
        # s2 has 2 s of audio, under MIN_MATCH_SECONDS, so it is never embedded.
        matched, session = self.run_voice_only([HOST, MEMBER])
        m = session["speaker_mapping"]
        self.assertEqual(matched, 1)
        self.assertEqual(m["s1"], {"name": "Ana Pérez", "confidence": 1.0, "source": "voiceprint"})
        self.assertEqual(m["s0"]["name"], "No identificado")  # OCR name dropped, host not matched
        self.assertEqual(m["s2"]["name"], "No identificado")
        self.assertEqual([s["speaker"]["name"] for s in session["segments"]],
                         ["No identificado", "Ana Pérez", "No identificado"])

    def test_below_threshold_is_unidentified(self):
        _, session = self.run_voice_only([HOST, [0.7, 0.71, 0.0]])  # cosine ~0.70 to the member
        self.assertEqual(session["speaker_mapping"]["s1"]["name"], "No identificado")


class EnrollTest(unittest.TestCase):
    def test_overlay_names_never_enroll(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            session = {
                "segments": [seg("s0", 0, 40, "Pleno De La", 1.0), seg("s1", 40, 80, "Ana Pérez", 1.0)],
                "speaker_mapping": {"s0": {"name": "Pleno De La", "source": "ocr-oov"},
                                    "s1": {"name": "Ana Pérez", "source": "db"}},
            }
            sf, db_path = d / "s.json", d / "db.json"
            sf.write_text(json.dumps(session))
            argv = ["04b", "--video-id", "vid", "--video-file", str(d / "v.mp4"),
                    "--session-file", str(sf), "--db-path", str(db_path)]
            exe = d / "cli"
            exe.write_text("")
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(vm, "VOICE_CLI", exe), mock.patch.object(vm, "VOICE_MODEL", exe), \
                 mock.patch.object(vm, "PROJECT_ROOT", d), \
                 mock.patch.object(vm, "build_cluster_clip", return_value=40.0), \
                 mock.patch.object(vm, "embed_clip", return_value=MEMBER):
                self.assertEqual(vm.main(), 0)
            self.assertEqual(sorted(json.loads(db_path.read_text())), ["ana perez"])


if __name__ == "__main__":
    unittest.main()
