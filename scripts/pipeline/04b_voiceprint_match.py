#!/usr/bin/env python3
"""
04b_voiceprint_match.py

Audio-based speaker identification, complementary to the OCR overlay reader
in 04_map_speakers_local.py. Runs AFTER it, on the same session JSON:

  1. For diarization clusters (speaker_id) that OCR identified with high
     confidence and enough audio, compute a voice embedding and merge it
     into a persistent voiceprint DB (running average per person).
  2. For clusters OCR could NOT name ("No identificado"), compute a voice
     embedding and match it against the DB via cosine similarity. If the
     best match clears --threshold, assign that name with source="voiceprint"
     and confidence set to the similarity score (kept clearly distinguishable
     from OCR-sourced, 1.0-style confidences).

With --voice-only (full plenary sessions) OCR is skipped altogether: every
cluster is named by voice against the roster voiceprints, and nothing is
enrolled. The clips build the DB; the sessions only consume it.

Uses wespeaker-resnet34-voxceleb.gguf via voice-detect.cpp's CLI — NOT
ecapa-tdnn-voxceleb.gguf, which pilot testing (2026-07-09) showed fails to
separate speakers in this domain (shared-room/broadcast channel dominates
the embedding). See project memory "voiceprint-pilot-result" for the pilot
that established this.

The voiceprint DB lives OUTSIDE data/ (in internal/speakers/voiceprints.json)
so it never gets mirrored to docs/data for GitHub Pages — it's an internal
matching artifact, not published site content.
"""

import argparse
import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
# voice-detect.cpp CLI + WeSpeaker model; machine-local paths come from the
# environment (set in internal/local.sh, which the cron script sources).
VOICE_CLI = Path(os.environ["VOICE_CLI"]) if os.environ.get("VOICE_CLI") else None
VOICE_MODEL = Path(os.environ["VOICE_MODEL"]) if os.environ.get("VOICE_MODEL") else None
DEFAULT_DB_PATH = PROJECT_ROOT / "internal" / "speakers" / "voiceprints.json"

MIN_ENROLL_SECONDS = 20.0
MIN_MATCH_SECONDS = 5.0
MAX_CLIP_SECONDS = 60.0
ENROLL_CONFIDENCE_FLOOR = 0.9
# Title-fallback names ("Asambleísta X - Sesión N", confidence 0.75) also
# enroll when that cluster clearly owns the clip. Checked 2026-10-26 on 15
# random such clusters against OCR-enrolled voiceprints: 13 matched the
# titled person at 0.85-0.96 cosine, and the other 2 were one person whose
# DB entry was the one at fault (OCR had enrolled it from vote clips).
TITLE_ENROLL_MIN_SECONDS = 60.0
TITLE_ENROLL_MIN_SHARE = 0.8
# --voice-only names every cluster of a plenary, TV hosts included, so it needs
# a stricter bar than the clip-mode 0.50. Checked 2026-10-28 on h0-KqqCsxWU
# against the host's "el asambleísta X dijo…" recaps: matches at 0.78+ were
# 12/12 right, and every one at 0.52-0.68 was the wrong person.
VOICE_ONLY_THRESHOLD = 0.75
ROSTER_FILE = PROJECT_ROOT / "data" / "speakers" / "asambleistas.json"


def normalize_name(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(no_accents.lower().split())


def load_db(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_db(path: Path, db: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb + 1e-9)


def build_cluster_clip(video_file: Path, segments: list[tuple[float, float]], out_path: Path) -> float:
    """Concat up to MAX_CLIP_SECONDS of the given segments into out_path. Returns seconds written."""
    parts = []
    total = 0.0
    for start, end in segments:
        if total >= MAX_CLIP_SECONDS:
            break
        dur = min(end - start, MAX_CLIP_SECONDS - total)
        if dur <= 0:
            continue
        parts.append((start, dur))
        total += dur
    if total < 1.0:
        return 0.0

    inputs = []
    filter_parts = []
    for i, (start, dur) in enumerate(parts):
        inputs += ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(video_file)]
        filter_parts.append(f"[{i}:a]")
    filter_complex = "".join(filter_parts) + f"concat=n={len(parts)}:v=0:a=1[out]"
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", "[out]", "-ar", "16000", "-ac", "1",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    if r.returncode != 0 or not out_path.exists():
        return 0.0
    return total


def embed_clip(clip_path: Path) -> list[float] | None:
    r = subprocess.run(
        [str(VOICE_CLI), "embed", "--model", str(VOICE_MODEL), "--input", str(clip_path), "--json"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    return data.get("embedding")


def match_cluster(clip_path: Path, video_file: Path, spans: list[tuple[float, float]],
                  db: dict, threshold: float) -> tuple[str, float] | None:
    """Best DB match for one cluster's voice, or None below threshold / too little audio."""
    if sum(e - s for s, e in spans) < MIN_MATCH_SECONDS or not db:
        return None
    try:
        if build_cluster_clip(video_file, spans, clip_path) < MIN_MATCH_SECONDS:
            return None
        vec = embed_clip(clip_path)
    finally:
        clip_path.unlink(missing_ok=True)
    if vec is None:
        return None
    best_name, best_sim = max(((e["name"], cosine(vec, e["embedding"])) for e in db.values()),
                              key=lambda x: x[1])
    return (best_name, best_sim) if best_sim >= threshold else None


def identify_by_voice(video_id: str, video_file: Path, session: dict, clusters: dict[str, list[dict]],
                      db: dict, tmp_dir: Path, threshold: float) -> int:
    """Full sessions: name every cluster by voice alone and enroll nothing. The
    overlay was how clips taught the DB who is who; in a plenary its bands carry
    institutional text ("Pleno de la Asamblea", "Sesión No.") that OCR turns
    into fake speakers, and the TV host would get enrolled as one of them.
    Only roster voiceprints are candidates: the DB also holds non-members read
    off clip overlays, and the plenary's own host is one of them."""
    roster = {normalize_name(a["name"])
              for a in json.loads(ROSTER_FILE.read_text(encoding="utf-8"))["asambleistas"]}
    db = {k: v for k, v in db.items() if k in roster}
    mapping = {}
    matched = 0
    for sid, segs in clusters.items():
        spans = [(s["start"], s["end"]) for s in segs]
        hit = match_cluster(tmp_dir / f"{video_id}_{sid}.wav", video_file, spans, db, threshold)
        if hit:
            name, sim = hit
            info = {"name": name, "confidence": round(sim, 4), "source": "voiceprint"}
            matched += 1
        else:
            info = {"name": "No identificado", "confidence": 0.0, "source": None}
        mapping[sid] = info
        for seg in segs:
            seg["speaker"] = {"id": sid or "UNKNOWN", **info}
    session["speaker_mapping"] = mapping
    return matched


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--video-file", required=True, help="Path to the video/audio file (still on disk, pre-cleanup)")
    parser.add_argument("--session-file", required=True, help="Session JSON to read/update in place")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--threshold", type=float, default=None,
                        help=f"Min cosine similarity to accept a voiceprint match "
                             f"(default 0.50, or {VOICE_ONLY_THRESHOLD} with --voice-only)")
    parser.add_argument("--voice-only", action="store_true",
                        help="Full sessions: ignore OCR names, identify every cluster by voice, enroll nothing")
    args = parser.parse_args()
    if args.threshold is None:
        args.threshold = VOICE_ONLY_THRESHOLD if args.voice_only else 0.50

    if not (VOICE_CLI and VOICE_MODEL and VOICE_CLI.exists() and VOICE_MODEL.exists()):
        print(f"  ! voice-detect.cpp CLI or model not found, skipping voiceprint stage", file=sys.stderr)
        # Voice is the only identification for full sessions, so its absence
        # is an error there; for clips the OCR-only result stands.
        return 1 if args.voice_only else 0

    video_file = Path(args.video_file)
    session_file = Path(args.session_file)
    db_path = Path(args.db_path)

    session = json.loads(session_file.read_text(encoding="utf-8"))
    segments = session.get("segments", [])
    if not segments:
        return 0

    db = load_db(db_path)

    # group by diarization cluster
    clusters: dict[str, list[dict]] = {}
    for seg in segments:
        sid = seg.get("speaker_id")
        clusters.setdefault(sid, []).append(seg)

    tmp_dir = PROJECT_ROOT / "temp" / "voiceprint_clips"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if args.voice_only:
        matched = identify_by_voice(args.video_id, video_file, session, clusters, db, tmp_dir, args.threshold)
        session_file.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  voiceprint (voice-only): named {matched}/{len(clusters)} cluster(s) (db size={len(db)})")
        return 0

    enrolled = 0
    matched = 0

    # Pass 1: enroll every confidently-OCR-identified cluster FIRST, so a
    # same-session voice match (pass 2) can benefit even if that speaker's
    # confident cluster appears later in the transcript than an unidentified
    # one earlier on.
    unidentified: list[tuple[str, list[dict]]] = []
    mapping = session.get("speaker_mapping") or {}
    session_dur = sum(s["end"] - s["start"] for s in segments)
    for sid, segs in clusters.items():
        spans = [(s["start"], s["end"]) for s in segs]
        total_dur = sum(e - s for s, e in spans)
        speaker = segs[0].get("speaker") or {}
        name = speaker.get("name")
        confidence = speaker.get("confidence") or 0.0
        is_identified = bool(name) and name != "No identificado"

        if not is_identified:
            if total_dur >= MIN_MATCH_SECONDS:
                unidentified.append((sid, segs))
            continue

        source = (mapping.get(sid) or {}).get("source")
        # Only roster-validated OCR names ("db") enroll. Out-of-vocabulary reads
        # ("ocr-oov") are often overlay text, not a person: "Pleno De La",
        # "Sesión Extraordinaria" and the like had entered the DB that way.
        ocr_ok = (source == "db" and confidence >= ENROLL_CONFIDENCE_FLOOR
                  and total_dur >= MIN_ENROLL_SECONDS)
        title_ok = (
            source == "title"
            and total_dur >= TITLE_ENROLL_MIN_SECONDS
            and session_dur > 0
            and total_dur / session_dur >= TITLE_ENROLL_MIN_SHARE
        )
        if ocr_ok or title_ok:
            clip_path = tmp_dir / f"{args.video_id}_{sid}.wav"
            written = build_cluster_clip(video_file, spans, clip_path)
            if written >= MIN_MATCH_SECONDS:
                vec = embed_clip(clip_path)
                if vec is not None:
                    key = normalize_name(str(name))
                    entry = db.get(key)
                    if entry is None:
                        db[key] = {"name": name, "embedding": vec, "n_samples": 1}
                    else:
                        n = entry["n_samples"]
                        entry["embedding"] = [
                            (e * n + v) / (n + 1) for e, v in zip(entry["embedding"], vec)
                        ]
                        entry["n_samples"] = n + 1
                    enrolled += 1
            clip_path.unlink(missing_ok=True)

    # Pass 2: try to match every unidentified cluster against the DB.
    for sid, segs in unidentified:
        spans = [(s["start"], s["end"]) for s in segs]
        hit = match_cluster(tmp_dir / f"{args.video_id}_{sid}.wav", video_file, spans, db, args.threshold)
        if hit:
            best_name, best_sim = hit
            for seg in segs:
                seg["speaker"] = {
                    "id": sid,
                    "name": best_name,
                    "confidence": round(best_sim, 4),
                    "source": "voiceprint",
                }
            matched += 1

    save_db(db_path, db)
    session_file.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  voiceprint: enrolled {enrolled} cluster(s), matched {matched} previously-unidentified cluster(s) (db size={len(db)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
