#!/usr/bin/env python3
"""
03_transcribe_local.py

Local transcription pipeline:
  1. POST audio to diarization-server (:8001) → speaker segments
  2. For each segment, slice audio with ffmpeg and POST to whisper-server (:8000)
  3. Write session JSON matching the existing schema in data/sessions/<video_id>.json

Replaces 03_transcribe_elevenlabs.py. No external APIs, fully local.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).parent.parent.parent
DIARIZE_URL = "http://localhost:8001/diarize"
WHISPER_URL = "http://localhost:8000/transcribe"
LANGUAGE = "es"


def to_wav_16k_mono(input_path: Path, output_path: Path) -> None:
    """Convert any audio/video file to 16kHz mono WAV."""
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-ac", "1", "-ar", "16000", "-vn",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def get_duration(wav_path: Path) -> float:
    """Get audio duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(wav_path),
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return float(out.stdout.strip())


def diarize(wav_path: Path) -> dict:
    """Call diarization server, return parsed response."""
    print(f"→ Diarizing {wav_path.name} via {DIARIZE_URL} …")
    t0 = time.time()
    with open(wav_path, "rb") as f:
        r = requests.post(DIARIZE_URL, files={"file": (wav_path.name, f, "audio/wav")}, timeout=3600)
    r.raise_for_status()
    data = r.json()
    print(f"  ✓ {data['num_speakers']} speakers, {len(data['segments'])} segments ({time.time()-t0:.1f}s)")
    return data


def transcribe_segment(wav_path: Path, start: float, end: float) -> tuple[str, list]:
    """Slice audio with ffmpeg and POST to whisper-server. Returns (text, internal_segments)."""
    duration = max(0.05, end - start)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        cmd = [
            "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(wav_path),
            "-t", f"{duration:.3f}", "-ac", "1", "-ar", "16000", "-vn",
            str(tmp_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        with open(tmp_path, "rb") as f:
            r = requests.post(
                WHISPER_URL,
                files={"file": (tmp_path.name, f, "audio/wav")},
                params={"language": LANGUAGE},
                timeout=600,
            )
        r.raise_for_status()
        data = r.json()
        return data.get("text", "").strip(), data.get("segments", [])
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def merge_short_segments(segments: list[dict], min_duration: float = 0.5) -> list[dict]:
    """Merge consecutive segments from the same speaker; drop tiny ones."""
    merged = []
    for seg in segments:
        dur = seg["end"] - seg["start"]
        if merged and merged[-1]["speaker"] == seg["speaker"]:
            merged[-1]["end"] = seg["end"]
        elif dur < min_duration:
            # too short to transcribe meaningfully; drop
            continue
        else:
            merged.append(dict(seg))
    return merged


def normalize_speaker_id(label: str) -> str:
    """Convert 'SPEAKER_00' → 'speaker_0' to match the existing schema."""
    if label.startswith("SPEAKER_"):
        try:
            return f"speaker_{int(label.split('_')[1])}"
        except (IndexError, ValueError):
            return label.lower()
    return label.lower()


def main():
    parser = argparse.ArgumentParser(description="Local transcription via pyannote + whisper-server")
    parser.add_argument("--video-id", required=True, help="YouTube video ID (used for filenames)")
    parser.add_argument("--audio-file", help="Path to audio file (default: temp/audio/<id>.m4a or data/video/<id>.mp4)")
    parser.add_argument("--output", help="Output JSON path (default: data/sessions/<id>.json)")
    parser.add_argument("--keep-wav", action="store_true", help="Keep the intermediate 16kHz wav")
    args = parser.parse_args()

    # Find input audio
    if args.audio_file:
        input_audio = Path(args.audio_file)
    else:
        for candidate in [
            PROJECT_ROOT / "temp" / "audio" / f"{args.video_id}.m4a",
            PROJECT_ROOT / "temp" / "audio" / f"{args.video_id}.wav",
            PROJECT_ROOT / "data" / "video" / f"{args.video_id}.mp4",
        ]:
            if candidate.exists():
                input_audio = candidate
                break
        else:
            print(f"ERROR: no audio/video found for {args.video_id}", file=sys.stderr)
            return 1

    print(f"Input: {input_audio}")

    # Convert to 16kHz mono wav
    wav_path = PROJECT_ROOT / "temp" / "audio" / f"{args.video_id}.wav"
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    if not wav_path.exists() or wav_path.stat().st_mtime < input_audio.stat().st_mtime:
        print(f"→ Converting to 16kHz mono wav …")
        to_wav_16k_mono(input_audio, wav_path)
    duration = get_duration(wav_path)
    print(f"  Duration: {duration:.1f}s ({duration/60:.1f} min)")

    # Diarize
    diar = diarize(wav_path)
    raw_segments = merge_short_segments(diar["segments"], min_duration=0.5)
    print(f"After merge/filter: {len(raw_segments)} segments")

    # Transcribe each segment
    print(f"→ Transcribing {len(raw_segments)} segments via whisper-server …")
    final_segments = []
    full_text_parts = []
    t0 = time.time()

    for idx, seg in enumerate(raw_segments):
        try:
            text, _internal = transcribe_segment(wav_path, seg["start"], seg["end"])
        except Exception as e:
            print(f"  ! segment {idx} failed: {e}")
            text = ""
        if not text:
            continue
        speaker_id = normalize_speaker_id(seg["speaker"])
        final_segments.append({
            "id": len(final_segments),
            "start": round(seg["start"], 2),
            "end": round(seg["end"], 2),
            "text": text,
            "speaker_id": speaker_id,
        })
        full_text_parts.append(text)

        if (idx + 1) % 10 == 0 or idx == len(raw_segments) - 1:
            elapsed = time.time() - t0
            print(f"  {idx+1}/{len(raw_segments)}  elapsed={elapsed:.1f}s  rtf={elapsed/seg['end']:.2f}x")

    print(f"✓ Transcribed {len(final_segments)} segments in {time.time()-t0:.1f}s")

    # Build session JSON in the existing schema
    speakers_detected = sorted({s["speaker_id"] for s in final_segments})
    session = {
        "text": " ".join(full_text_parts),
        "language": LANGUAGE,
        "duration": round(duration, 2),
        "speakers_detected": len(speakers_detected),
        "segments": final_segments,
        "video_id": args.video_id,
        "source": "local-pyannote-community-1+whisper-large-v3-turbo",
    }

    output_path = Path(args.output) if args.output else (PROJECT_ROOT / "temp" / "sessions" / f"{args.video_id}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)
    print(f"✓ Wrote {output_path}")

    if not args.keep_wav:
        try:
            wav_path.unlink()
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
