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
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

SESSION_NUM_RE = re.compile(r"[Ss]esi[oó]n\s+(\d+)|[Ss]ession\s+(\d+)")


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


def download_video(video_id: str) -> Path | None:
    DATA_VIDEO.mkdir(parents=True, exist_ok=True)
    out_path = DATA_VIDEO / f"{video_id}.mp4"
    if out_path.exists() and out_path.stat().st_size > 100_000:
        return out_path
    # Wipe any partial leftovers (e.g. .f136.mp4 + .f140.m4a from a killed run)
    for stale in DATA_VIDEO.glob(f"{video_id}.*"):
        try:
            stale.unlink()
        except OSError:
            pass

    url = f"https://www.youtube.com/watch?v={video_id}"
    cookies_file = PROJECT_ROOT / "temp" / "cookies" / "youtube.txt"
    cmd = [
        "yt-dlp",
        # 720p mp4 + medium m4a. android_vr/ios alone trip "Sign in to confirm
        # you're not a bot" without cookies; web needs cookies too for SABR
        # formats. The cookies file is seeded via a stealth browser session
        # (see docs/runbooks or ask — cloakbrowser) and yt-dlp keeps it fresh
        # across runs.
        "--extractor-args", "youtube:player_client=android_vr,ios,web",
        # Space out requests — a burst of back-to-back downloads (esp. right
        # after a discovery run's per-video metadata fetches) tripped a
        # YouTube "rate-limited for up to an hour" block on 2026-07-09.
        "--sleep-requests", "2", "--sleep-interval", "3", "--max-sleep-interval", "8",
        "-f", "136+140/135+140/134+140/18/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "-o", str(out_path),
        url,
    ]
    if cookies_file.exists():
        cmd[1:1] = ["--cookies", str(cookies_file)]
    yt_log = PROJECT_ROOT / "temp" / f"yt-dlp-{video_id}.log"
    yt_log.parent.mkdir(parents=True, exist_ok=True)
    # Stream stdout/stderr directly to a log file — capture_output buffers in RAM
    # and can deadlock when yt-dlp + ffmpeg merge produce a lot of progress output.
    with open(yt_log, "w") as logf:
        try:
            # 1h ceiling — file size (not video duration) drives download
            # time, and multi-GB full-session downloads need real headroom.
            rc = subprocess.call(cmd, stdout=logf, stderr=subprocess.STDOUT, timeout=3600)
        except subprocess.TimeoutExpired:
            log("  yt-dlp timed out (3600s)")
            return None
    if rc != 0 or not out_path.exists() or out_path.stat().st_size < 100_000:
        tail = yt_log.read_text(errors="ignore").splitlines()[-3:]
        log(f"  yt-dlp failed (rc={rc}): {' | '.join(tail)}")
        return None
    # Remove the per-video yt-dlp log on success
    try:
        yt_log.unlink()
    except OSError:
        pass
    return out_path


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

    # Stage 1: download
    t0 = time.time()
    video_path = download_video(vid)
    result["stages"]["download"] = round(time.time() - t0, 1)
    if not video_path:
        result["error"] = "download failed"
        return result

    # Stage 2: transcribe (diarize + ASR). Full plenary sessions run hours
    # long — give those runs a lot more headroom than the 2h default (which
    # is already generous for a short per-speaker clip).
    is_full_session = session_meta.get("video_type") == "full_session"
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
        result["error"] = "transcription produced 0 segments"
        out_session.unlink(missing_ok=True)
        return result

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
    run_script(
        "04b_voiceprint_match.py",
        "--video-id", vid,
        "--video-file", str(video_path),
        "--session-file", str(out_session),
    )
    result["stages"]["voiceprint"] = round(time.time() - t0, 1)

    # Stage 4: merge metadata from index into the session file
    try:
        with open(out_session, encoding="utf-8") as f:
            session = json.load(f)
        for key in ["title", "url", "published_at", "channel_title"]:
            if key in session_meta and key not in session:
                session[key] = session_meta[key]
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

    # Stage 5: cleanup video to save disk (unless --keep-video)
    if not keep_video:
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
    args = parser.parse_args()

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("")  # clear old log

    index = json.loads((DATA_SESSIONS / "index.json").read_text())
    sessions = index.get("sessions", [])
    existing = {f.stem for f in DATA_SESSIONS.iterdir() if f.suffix == ".json" and f.stem != "index"}
    pending = [s for s in sessions if s["video_id"] not in existing]

    if args.since:
        pending = [s for s in pending if not s.get("published_at") or s["published_at"][:10] >= args.since]

    # Newest sessions first. Primary key is the session number parsed from
    # the title (reliable, always present, and a better recency proxy than
    # published_at — discovery's per-video date fetch is frequently bot-
    # rate-limited and leaves published_at null for genuinely recent videos).
    # published_at is the tiebreaker for same/unknown session numbers.
    if not args.oldest_first:
        pending.sort(key=lambda s: (session_number(s.get("title", "")), s.get("published_at") or ""), reverse=True)

    if args.limit:
        pending = pending[: args.limit]

    log(f"Pending: {len(pending)} videos to process")
    if not pending:
        log("Nothing to do.")
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
        for script in ("06_generate_stats.py", "07_build_search_index.py", "08_update_catalog.py"):
            rc = subprocess.call(
                [sys.executable, "-u", str(PROJECT_ROOT / "scripts" / "pipeline" / script)],
                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
            )
            if rc != 0:
                log(f"  ⚠ {script} exited with code {rc}")
            else:
                log(f"  ✓ {script}")

    return 0 if successes == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
