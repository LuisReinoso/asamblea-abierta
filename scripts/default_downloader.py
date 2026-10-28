"""Default video downloader: plain yt-dlp, no cookies or proxies.

run_batch.py uses internal/downloader.py instead when that file exists, so
an operator can plug in their own download setup without touching the repo.
Both must expose the same function and exception:

    download_video(video_id, data_video, log_fn, audio_only=False) -> Path | None
    class BotBlocked(Exception)

download_video writes data_video/<video_id>.mp4 (or .m4a with audio_only,
used for full sessions, which are named by voice and need no picture) and
returns its path, or None on failure. It raises BotBlocked when YouTube
answers with its anti-bot check, so run_batch stops for the night instead
of hammering it.
"""

import subprocess
from pathlib import Path

MIN_VIDEO_BYTES = 100_000
BOT_SIGNATURES = ("Sign in to confirm", "not a bot", "HTTP Error 429", "rate-limited")


class BotBlocked(Exception):
    """YouTube asked for a bot check; stop downloading for now."""


def download_video(video_id: str, data_video: Path, log_fn, audio_only: bool = False) -> Path | None:
    data_video.mkdir(parents=True, exist_ok=True)
    out_path = data_video / (f"{video_id}.m4a" if audio_only else f"{video_id}.mp4")
    if out_path.exists() and out_path.stat().st_size > MIN_VIDEO_BYTES:
        return out_path
    cmd = [
        "yt-dlp",
        # Space out requests; bursts get rate-limited by YouTube.
        "--sleep-requests", "2", "--sleep-interval", "3", "--max-sleep-interval", "8",
        # <=480p is plenty for the overlay-text OCR in 04_map_speakers_local.py.
        # "ba" rather than a fixed format id so auto-dubbed videos still get
        # the original Spanish track.
        *(["-f", "ba[ext=m4a]/ba"] if audio_only else
          ["-f", "bv*[height<=480][ext=mp4]+ba[ext=m4a]/b[height<=480]/b", "--merge-output-format", "mp4"]),
        "--no-playlist",
        "-o", str(out_path),
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired:
        log_fn("  yt-dlp timed out (3600s)")
        return None
    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size < MIN_VIDEO_BYTES:
        output = (result.stderr or "") + (result.stdout or "")
        tail = output.strip().splitlines()[-3:]
        log_fn(f"  yt-dlp failed (rc={result.returncode}): {' | '.join(tail)}")
        if any(sig in output for sig in BOT_SIGNATURES):
            raise BotBlocked(" | ".join(tail))
        return None
    return out_path
