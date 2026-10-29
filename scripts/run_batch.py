#!/usr/bin/env python3
"""
run_batch.py

Process every pending video listed in data/sessions/index.json end-to-end:
  download → transcribe (diarize+ASR) → map speakers → cleanup the video file.

Skips videos that already have a data/sessions/<id>.json. Writes a status
file to temp/batch_status.json so progress is visible while running.

Usage:
    python scripts/run_batch.py [--keep-video] [--limit N]
"""

import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path


SESSION_NUM_RE = re.compile(r"[Ss]esi[oó]n\s+(\d+)|[Ss]ession\s+(\d+)")

# Being a polite YouTube client. A random pause between downloads keeps a
# night's batch from reading as a burst, and when YouTube answers with its
# anti-bot check every download stops until the pause file expires: retrying
# only extends the block (2026-10-11 hit 20/20 clips; it lifted by itself).
PAUSE_BETWEEN_DOWNLOADS = (45, 120)  # seconds, uniform
BOT_PAUSE_HOURS = 12


def session_number(title: str) -> int:
    """Extract the plenary session number from a title, e.g. 'Sesión 094' -> 94.

    Used as a recency proxy when published_at is missing — discovery's
    per-video metadata fetch gets bot-rate-limited often, but the session
    number embedded in the title is a reliable, always-present ordering
    signal for this channel.
    """
    m = SESSION_NUM_RE.search(title or "")
    if not m:
        return -1
    return int(m.group(1) or m.group(2))

PROJECT_ROOT = Path(__file__).parent.parent
DATA_SESSIONS = PROJECT_ROOT / "data" / "sessions"
DATA_VIDEO = PROJECT_ROOT / "data" / "video"
STATUS_FILE = PROJECT_ROOT / "temp" / "batch_status.json"
LOG_FILE = PROJECT_ROOT / "temp" / "batch.log"
QA_SAMPLES_DIR = PROJECT_ROOT / "temp" / "qa_samples"  # local-only, temp/ is gitignored
QA_FRAGMENT_SECONDS = 20
BOT_PAUSE_FILE = PROJECT_ROOT / "temp" / "youtube_pause.json"
# A clip whose loudest sample is below this is digital silence (hKcIpbZoAsg
# peaks at -91 dB): an empty transcript is the right answer, not a failure.
SILENCE_MAX_DB = -60.0


def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def write_status(status: dict):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2, default=str)


def downloader_module():
    """internal/downloader.py (gitignored, machine-local) when present,
    otherwise scripts/default_downloader.py (plain yt-dlp). Both expose
    download_video(video_id, data_video, log_fn, audio_only) and BotBlocked."""
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    sys.path.insert(0, str(PROJECT_ROOT / "internal"))
    try:
        import downloader
    except ImportError:
        import default_downloader as downloader
    return downloader


def download_video(video_id: str, audio_only: bool = False) -> Path | None:
    """Fetch the source into data/video/<id>.mp4 (or .m4a with audio_only).
    Raises the downloader's BotBlocked when YouTube asks for a bot check."""
    return downloader_module().download_video(video_id, DATA_VIDEO, log, audio_only=audio_only)


def youtube_paused_until() -> float | None:
    """Epoch until which downloads are paused after a bot check, if any."""
    try:
        until = json.loads(BOT_PAUSE_FILE.read_text())["until"]
    except (OSError, ValueError, KeyError):
        return None
    return until if until > time.time() else None


def pause_youtube(reason: str) -> None:
    BOT_PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    until = time.time() + BOT_PAUSE_HOURS * 3600
    BOT_PAUSE_FILE.write_text(json.dumps({"until": until, "reason": reason}))
    log(f"  YouTube pidió verificación anti-bot: descargas en pausa hasta "
        f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(until))}")


def extract_qa_fragment(video_path: Path, video_id: str, duration_hint: float | None) -> None:
    """Save a short clip (temp/qa_samples/<id>.mp4, local-only — temp/ is
    gitignored) before the full video gets deleted, so a person can spot-
    check "does the transcript actually match what's on screen" without
    re-downloading multi-GB files. Stream-copied (no re-encode), so it's
    fast and near-free in CPU. Sampled from ~40% into the video — long
    enough past the intro/procedural opening to likely show real content,
    short of the ending where sessions sometimes trail into silence."""
    QA_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = QA_SAMPLES_DIR / f"{video_id}.mp4"
    if out_path.exists():
        return
    duration = duration_hint or 60.0
    start = max(0.0, duration * 0.4)
    cmd = [
        "ffmpeg", "-y", "-ss", f"{start:.1f}", "-i", str(video_path),
        "-t", str(QA_FRAGMENT_SECONDS), "-c", "copy", str(out_path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        pass
    if not out_path.exists() or out_path.stat().st_size < 1000:
        out_path.unlink(missing_ok=True)


def run_script(script: str, *args: str, timeout: int = 7200) -> bool:
    # Join --flag and value with '=' so video_ids that start with '-' do not
    # confuse argparse into reading them as flags.
    joined: list[str] = []
    skip = False
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        if a.startswith("--") and i + 1 < len(args) and not args[i + 1].startswith("--"):
            joined.append(f"{a}={args[i + 1]}")
            skip = True
        else:
            joined.append(a)
    cmd = [sys.executable, "-u", str(PROJECT_ROOT / "scripts" / "pipeline" / script), *joined]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        log(f"  {script} failed (exit {r.returncode})")
        log(f"  stderr tail: {r.stderr[-500:]}")
        return False
    return True


def max_volume_db(path: Path) -> float | None:
    """Loudest sample of the file's audio in dBFS, or None if ffmpeg can't tell."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"max_volume: (-?[\d.]+|-inf) dB", r.stderr)
    if not m:
        return None
    return float("-inf") if m.group(1) == "-inf" else float(m.group(1))


def mark_silent(video_id: str) -> None:
    """Flag an index entry as silent so later runs stop picking it up."""
    index_path = DATA_SESSIONS / "index.json"
    index = json.loads(index_path.read_text())
    for s in index.get("sessions", []):
        if s["video_id"] == video_id:
            s["skip_reason"] = "silent audio"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2))


def process_video(session_meta: dict, keep_video: bool) -> dict:
    vid = session_meta["video_id"]
    started = time.time()
    result = {"video_id": vid, "title": session_meta.get("title", ""), "ok": False, "stages": {}}

    log(f"▶ {vid}  {session_meta.get('title','')[:70]}")

    out_session = DATA_SESSIONS / f"{vid}.json"
    if out_session.exists():
        # A session file with zero segments means a prior transcription
        # attempt silently produced nothing (server hiccup, not real
        # silence — plenary clips are never actually empty audio). Treat
        # it as unprocessed so it gets retried instead of skipped forever.
        try:
            has_segments = bool(json.loads(out_session.read_text()).get("segments"))
        except (json.JSONDecodeError, OSError):
            has_segments = False
        if has_segments:
            log("  already processed — skip")
            result.update({"ok": True, "skipped": True})
            return result
        log("  existing session has 0 segments — retrying")
        out_session.unlink()

    # Stage 1: download. Full sessions are named by voice, so audio is enough.
    is_full_session = session_meta.get("video_type") == "full_session"
    t0 = time.time()
    try:
        video_path = download_video(vid, audio_only=is_full_session)
    except downloader_module().BotBlocked as e:
        pause_youtube(str(e))
        result.update({"error": "youtube bot-check", "bot_blocked": True})
        return result
    result["stages"]["download"] = round(time.time() - t0, 1)
    if not video_path:
        result["error"] = "download failed"
        return result

    # Stage 2: transcribe (diarize + ASR). Full plenary sessions run hours
    # long — give those runs a lot more headroom than the 2h default (which
    # is already generous for a short per-speaker clip).
    stage_timeout = 21600 if is_full_session else 7200  # 6h vs 2h
    t0 = time.time()
    ok = run_script(
        "03_transcribe_local.py",
        "--video-id", vid,
        "--audio-file", str(video_path),
        "--output", str(out_session),
        timeout=stage_timeout,
    )
    result["stages"]["transcribe"] = round(time.time() - t0, 1)
    if not ok:
        result["error"] = "transcription failed"
        return result
    try:
        has_segments = bool(json.loads(out_session.read_text()).get("segments"))
    except (json.JSONDecodeError, OSError):
        has_segments = False
    if not has_segments:
        out_session.unlink(missing_ok=True)
        peak = max_volume_db(video_path)
        if peak is not None and peak < SILENCE_MAX_DB:
            mark_silent(vid)
            if not keep_video:
                video_path.unlink(missing_ok=True)
            log(f"  audio is silent (peak {peak:.0f} dB): marked in the index, not retried")
            result.update({"ok": True, "silent": True, "total_time": round(time.time() - started, 1)})
            return result
        result["error"] = "transcription produced 0 segments"
        return result

    voiceprint_args = [
        "04b_voiceprint_match.py",
        "--video-id", vid,
        "--video-file", str(video_path),
        "--session-file", str(out_session),
    ]
    if is_full_session:
        # Full sessions are named by voice alone: the clips already built the
        # voiceprint DB, and a plenary's overlay bands are institutional text
        # that OCR would turn into fake speakers. Required, unlike for clips.
        t0 = time.time()
        ok = run_script(*voiceprint_args, "--voice-only", timeout=stage_timeout)
        result["stages"]["voiceprint"] = round(time.time() - t0, 1)
        if not ok:
            result["error"] = "voiceprint identification failed (transcript saved without names)"
            return result
    else:
        # Stage 3: map speakers
        t0 = time.time()
        map_args = [
            "04_map_speakers_local.py",
            "--video-id", vid,
            "--video-file", str(video_path),
            "--session-file", str(out_session),
        ]
        if session_meta.get("title"):
            map_args += ["--title", session_meta["title"]]
        ok = run_script(*map_args, timeout=stage_timeout)
        result["stages"]["map"] = round(time.time() - t0, 1)
        if not ok:
            result["error"] = "speaker mapping failed (transcript saved without names)"
            return result

        # Stage 3b: voiceprint assist for clusters OCR couldn't name (best-effort,
        # never fails the pipeline — must run before video cleanup below)
        t0 = time.time()
        run_script(*voiceprint_args)
        result["stages"]["voiceprint"] = round(time.time() - t0, 1)

    # Stage 4: merge metadata from index into the session file
    try:
        with open(out_session, encoding="utf-8") as f:
            session = json.load(f)
        for key in ["title", "url", "published_at", "channel_title", "video_type"]:
            if key in session_meta and key not in session:
                session[key] = session_meta[key]
        session.setdefault("video_type", "clip")
        session["id"] = vid
        if not session.get("date") and session_meta.get("published_at"):
            session["date"] = session_meta["published_at"]
        if not session.get("source_url"):
            session["source_url"] = session_meta.get("url") or f"https://www.youtube.com/watch?v={vid}"

        # Convert speaker_stats from dict (new schema) to list (legacy schema expected by 06/07/08)
        if isinstance(session.get("speaker_stats"), dict) or not session.get("speaker_stats"):
            from collections import defaultdict
            agg = defaultdict(lambda: {"duration": 0.0, "segments": 0, "word_count": 0})
            for seg in session.get("segments", []):
                spk = seg.get("speaker") or {}
                name = spk.get("name") or "No identificado"
                sid = "UNIDENTIFIED" if name == "No identificado" else (spk.get("id") or "UNIDENTIFIED")
                agg[(sid, name)]["duration"] += seg.get("end", 0) - seg.get("start", 0)
                agg[(sid, name)]["segments"] += 1
                agg[(sid, name)]["word_count"] += len(seg.get("text", "").split())
            stats_list = [
                {
                    "id": sid, "name": name,
                    "party": None, "province": None,
                    "total_time": round(v["duration"], 2),
                    "interventions": v["segments"],
                    "word_count": v["word_count"],
                }
                for (sid, name), v in agg.items()
            ]
            stats_list.sort(key=lambda s: -s["total_time"])
            session["speaker_stats"] = stats_list

        with open(out_session, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"  metadata merge failed: {e}")
        traceback.print_exc()

    # Stage 5: keep a short spot-check fragment, then drop the full video —
    # full-session recordings run multi-GB and storage isn't infinite.
    if not keep_video:
        extract_qa_fragment(video_path, vid, session_meta.get("duration"))
        try:
            video_path.unlink()
        except OSError:
            pass

    result["ok"] = True
    result["total_time"] = round(time.time() - started, 1)
    log(f"  ✓ done in {result['total_time']}s  stages={result['stages']}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-video", action="store_true", help="Keep downloaded videos after processing")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N pending videos (0=all)")
    parser.add_argument("--since", help="Only process videos with published_at >= YYYY-MM-DD (entries with null date are kept)")
    parser.add_argument("--oldest-first", action="store_true", help="Process in index order instead of newest-published-first (the default)")
    parser.add_argument("--video-type", choices=["clip", "full_session"], help="Only process this video_type (default: both)")
    args = parser.parse_args()

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("")  # clear old log

    index = json.loads((DATA_SESSIONS / "index.json").read_text())
    sessions = index.get("sessions", [])
    existing = {f.stem for f in DATA_SESSIONS.iterdir() if f.suffix == ".json" and f.stem != "index"}
    pending = [s for s in sessions if s["video_id"] not in existing and not s.get("skip_reason")]

    if args.video_type:
        pending = [s for s in pending if s.get("video_type", "clip") == args.video_type]

    if args.since:
        pending = [s for s in pending if not s.get("published_at") or s["published_at"][:10] >= args.since]

    # Newest sessions first. Primary key is the session number parsed from
    # the title (reliable, always present, and a better recency proxy than
    # published_at — discovery's per-video date fetch is frequently bot-
    # rate-limited and leaves published_at null for genuinely recent videos).
    # published_at is the tiebreaker for same/unknown session numbers.
    if not args.oldest_first:
        if args.video_type == "full_session":
            # Plenary titles are not a reliable number source ("Sesión 1024"
            # was streamed in 2025-06, ahead of "Sesión No. 134-AN-2025-2029"),
            # and every full session has its date, so date leads here.
            pending.sort(key=lambda s: (s.get("published_at") or "", session_number(s.get("title", ""))), reverse=True)
        else:
            pending.sort(key=lambda s: (session_number(s.get("title", "")), s.get("published_at") or ""), reverse=True)

    if args.limit:
        pending = pending[: args.limit]

    log(f"Pending: {len(pending)} videos to process")
    if not pending:
        log("Nothing to do.")
        return 0
    paused = youtube_paused_until()
    if paused:
        log(f"YouTube pausado hasta {time.strftime('%Y-%m-%d %H:%M', time.localtime(paused))} "
            "(verificación anti-bot): no se descarga nada en esta pasada.")
        return 0

    results = []
    overall_start = time.time()

    for i, meta in enumerate(pending, 1):
        write_status({
            "current": i,
            "total": len(pending),
            "current_video": meta["video_id"],
            "current_title": meta.get("title", ""),
            "elapsed": round(time.time() - overall_start, 1),
            "results_so_far": results,
        })
        try:
            res = process_video(meta, args.keep_video)
        except Exception as e:
            log(f"  unhandled exception: {e}")
            traceback.print_exc()
            res = {"video_id": meta["video_id"], "ok": False, "error": str(e)}
        results.append(res)
        if res.get("bot_blocked"):
            log("Stopping: YouTube asked for a bot check; the rest waits for the next run.")
            break
        if i < len(pending) and not res.get("skipped"):
            pause = random.uniform(*PAUSE_BETWEEN_DOWNLOADS)
            log(f"  pausa de {pause:.0f}s antes del siguiente video")
            time.sleep(pause)

    write_status({
        "current": len(pending),
        "total": len(pending),
        "done": True,
        "elapsed": round(time.time() - overall_start, 1),
        "results_so_far": results,
    })

    successes = sum(1 for r in results if r.get("ok"))
    log("")
    log("=" * 60)
    log(f"BATCH DONE — {successes}/{len(results)} succeeded in {round(time.time()-overall_start)}s")
    log("=" * 60)
    for r in results:
        status = "✓" if r.get("ok") else "✗"
        extra = f" [{r['error']}]" if r.get("error") else ""
        log(f"  {status} {r['video_id']}  {r.get('total_time','?')}s{extra}")

    # Rebuild site data + mirror to docs/data so GitHub Pages picks up the
    # changes. Skipped if nothing succeeded — no point rebuilding an unchanged
    # catalog and risking masking an upstream failure.
    if successes > 0:
        log("")
        log("Rebuilding site data (stats, search index, catalog)…")
        # The per-session pages (what session-detail.html redirects to) come
        # last: they read the catalog that 08 just mirrored into docs/data.
        # Leaving them out made every video processed after 2026-08-19 a 404.
        for script in ("pipeline/06_generate_stats.py", "pipeline/07_build_search_index.py",
                       "pipeline/08_update_catalog.py", "generate_static_sessions.py"):
            rc = subprocess.call(
                [sys.executable, "-u", str(PROJECT_ROOT / "scripts" / script)],
                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
            )
            if rc != 0:
                log(f"  ⚠ {script} exited with code {rc}")
            else:
                log(f"  ✓ {script}")

    return 0 if successes == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
