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
import subprocess
import sys
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
VOICE_CLI = Path("/home/luis/proyectos/voice-detect.cpp/build/examples/cli/voicedetect-cli")
VOICE_MODEL = Path("/home/luis/proyectos/voice-detect.cpp/models/wespeaker-resnet34-voxceleb.gguf")
DEFAULT_DB_PATH = PROJECT_ROOT / "internal" / "speakers" / "voiceprints.json"

MIN_ENROLL_SECONDS = 20.0
MIN_MATCH_SECONDS = 5.0
MAX_CLIP_SECONDS = 60.0
ENROLL_CONFIDENCE_FLOOR = 0.9


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--video-file", required=True, help="Path to the video/audio file (still on disk, pre-cleanup)")
    parser.add_argument("--session-file", required=True, help="Session JSON to read/update in place")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--threshold", type=float, default=0.50, help="Min cosine similarity to accept a voiceprint match")
    args = parser.parse_args()

    if not VOICE_CLI.exists() or not VOICE_MODEL.exists():
        print(f"  ! voice-detect.cpp CLI or model not found, skipping voiceprint stage", file=sys.stderr)
        return 0  # non-fatal — OCR-only result stands

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

    enrolled = 0
    matched = 0

    # Pass 1: enroll every confidently-OCR-identified cluster FIRST, so a
    # same-session voice match (pass 2) can benefit even if that speaker's
    # confident cluster appears later in the transcript than an unidentified
    # one earlier on.
    unidentified: list[tuple[str, list[dict]]] = []
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

        if confidence >= ENROLL_CONFIDENCE_FLOOR and total_dur >= MIN_ENROLL_SECONDS:
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
        total_dur = sum(e - s for s, e in spans)
        clip_path = tmp_dir / f"{args.video_id}_{sid}.wav"
        if total_dur >= MIN_MATCH_SECONDS and db:
            written = build_cluster_clip(video_file, spans, clip_path)
            if written >= MIN_MATCH_SECONDS:
                vec = embed_clip(clip_path)
                if vec is not None:
                    sims = [(e["name"], cosine(vec, e["embedding"])) for e in db.values()]
                    sims.sort(key=lambda x: -x[1])
                    best_name, best_sim = sims[0]
                    if best_sim >= args.threshold:
                        for seg in segs:
                            seg["speaker"] = {
                                "id": sid,
                                "name": best_name,
                                "confidence": round(best_sim, 4),
                                "source": "voiceprint",
                            }
                        matched += 1
            clip_path.unlink(missing_ok=True)

    save_db(db_path, db)
    session_file.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  voiceprint: enrolled {enrolled} cluster(s), matched {matched} previously-unidentified cluster(s) (db size={len(db)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
