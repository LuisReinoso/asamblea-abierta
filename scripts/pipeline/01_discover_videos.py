#!/usr/bin/env python3
"""
01_discover_videos.py

Discover new Asamblea Nacional session videos using yt-dlp (no YouTube API key).

Flow:
  1. List every video on the channel (flat playlist — fast, no per-video calls).
  2. Filter to videos that don't have a data/sessions/<id>.json yet.
  3. For each candidate, fetch metadata (title, upload_date) via yt-dlp.
  4. Optionally filter by --since YYYY-MM-DD.
  5. Append new videos to data/sessions/index.json.

Usage:
    python scripts/pipeline/01_discover_videos.py [--since 2026-01-01] [--limit 100]

Default: discovers every channel video newer than the latest entry already in
the index — useful for "catch up" runs after the local pipeline has been idle.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
INDEX_PATH = PROJECT_ROOT / "data" / "sessions" / "index.json"
DATA_SESSIONS = PROJECT_ROOT / "data" / "sessions"
# /videos = short per-speaker intervention clips (minutes). /streams = the
# real un-cut plenary session recordings (hours) — a completely separate
# YouTube tab, not reachable by scraping /videos. Discovered 2026-07-10:
# short clips are training/enrollment data for voice ID; full sessions are
# the primary content the site should feature.
CHANNEL_TABS = {
    "clip": "https://www.youtube.com/@AsambleaNacionalEC/videos",
    "full_session": "https://www.youtube.com/@AsambleaNacionalEC/streams",
}
# Belt-and-suspenders: even within a tab, classify by duration too (a few
# short "trailer"-style videos on the channel share near-identical titles
# with the real multi-hour streams, e.g. "Sesión 104 del Pleno..." at 63s vs
# "Sesión No. 104-AN-2025-2029 del pleno..." at 17087s — title alone lies).
FULL_SESSION_MIN_SECONDS = 1800  # 30 min
COOKIES_FILE = PROJECT_ROOT / "temp" / "cookies" / "youtube.txt"


def _cookie_args() -> list[str]:
    return ["--cookies", str(COOKIES_FILE)] if COOKIES_FILE.exists() else []


def load_index() -> dict:
    if INDEX_PATH.exists():
        with open(INDEX_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"sessions": [], "last_updated": None}


def save_index(data: dict) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✓ Wrote {INDEX_PATH} ({len(data['sessions'])} sessions total)")


def list_channel_videos(channel_url: str) -> list[tuple[str, str, float | None]]:
    """Return all (video_id, title, duration_seconds) in a channel tab, newest first.

    Uses flat-playlist mode: fast, no per-video calls, no YouTube bot
    challenges. Duration comes for free from the flat listing; upload_date
    does not — that's fetched lazily later via fetch_metadata().
    """
    print(f"Listing videos from {channel_url} …")
    cmd = [
        "yt-dlp",
        *_cookie_args(),
        "--flat-playlist",
        "--print", "%(id)s\t%(title)s\t%(duration)s",
        channel_url,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print(f"  yt-dlp failed: {r.stderr[-300:]}", file=sys.stderr)
        sys.exit(1)
    videos: list[tuple[str, str, float | None]] = []
    for line in r.stdout.splitlines():
        if "\t" not in line:
            continue
        parts = line.split("\t")
        vid, title = parts[0].strip(), parts[1].strip()
        duration = None
        if len(parts) > 2:
            try:
                duration = float(parts[2])
            except ValueError:
                duration = None
        if vid:
            videos.append((vid, title, duration))
    print(f"  tab has {len(videos)} videos")
    return videos


def fetch_metadata(video_id: str) -> dict | None:
    """Fetch title + upload_date + duration for a single video. None on failure."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        "yt-dlp",
        *_cookie_args(),
        "--extractor-args", "youtube:player_client=android_vr,ios,web",
        "--no-warnings", "--skip-download",
        "--print", "%(id)s\t%(title)s\t%(upload_date)s\t%(channel)s\t%(duration)s",
        url,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    line = r.stdout.strip().splitlines()
    if not line:
        return None
    parts = line[0].split("\t")
    if len(parts) < 3:
        return None
    vid, title, upload_date, *rest = parts
    channel = rest[0] if rest else "Asamblea Ecuador"
    duration = None
    if len(rest) > 1:
        try:
            duration = float(rest[1])
        except ValueError:
            duration = None
    # YYYYMMDD → RFC3339-ish
    try:
        published_at = datetime.strptime(upload_date, "%Y%m%d").strftime("%Y-%m-%dT00:00:00Z")
    except ValueError:
        published_at = None
    return {
        "video_id": vid,
        "title": title,
        "description": "",
        "published_at": published_at,
        "channel_title": channel,
        "duration": duration,
        "url": url,
        "discovered_at": datetime.now().isoformat(),
    }


def classify_video_type(tab: str, duration: float | None) -> str:
    """Duration wins over title/tab — some short trailer clips share
    near-identical titles with the real multi-hour streams."""
    if duration is not None:
        return "full_session" if duration >= FULL_SESSION_MIN_SECONDS else "clip"
    return tab


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", help="Only discover videos published on/after YYYY-MM-DD (default: include all)")
    parser.add_argument("--limit", type=int, default=200, help="Max new candidates to inspect this run (default: 200)")
    parser.add_argument("--rebuild", action="store_true", help="Reinspect even videos already in the index")
    args = parser.parse_args()

    print("=" * 60)
    print("01_DISCOVER_VIDEOS — yt-dlp based")
    print("=" * 60)

    index = load_index()
    known: dict[str, dict] = {s["video_id"]: s for s in index.get("sessions", [])}
    processed = {f.stem for f in DATA_SESSIONS.iterdir() if f.suffix == ".json" and f.stem != "index"}

    # Candidate = in channel, not in index (or --rebuild). Collected from
    # BOTH tabs; each candidate carries which tab it came from so we can
    # classify video_type even if the later per-video metadata fetch (which
    # also returns duration) gets bot-rate-limited.
    candidates: list[tuple[str, str, float | None, str]] = []
    for tab, channel_url in CHANNEL_TABS.items():
        for vid, title, duration in list_channel_videos(channel_url):
            if not args.rebuild and vid in known:
                continue
            if any(c[0] == vid for c in candidates):
                continue
            candidates.append((vid, title, duration, tab))
            if len(candidates) >= args.limit:
                break

    print(f"Candidates to inspect: {len(candidates)} (limit={args.limit})")
    if not candidates:
        print("Nothing new to add.")
        return 0

    since_dt = None
    if args.since:
        since_dt = datetime.strptime(args.since, "%Y-%m-%d")

    # Try fetching per-video metadata (title + date + duration). YouTube
    # sometimes rate-limits this with "Sign in to confirm you're not a bot";
    # in that case we fall back to the flat-playlist title/duration and
    # leave published_at empty so the entry can still be queued.
    added: list[dict] = []
    skipped_old = 0
    fallback_used = 0
    for i, (vid, flat_title, flat_duration, tab) in enumerate(candidates, 1):
        meta = fetch_metadata(vid)
        if not meta:
            fallback_used += 1
            meta = {
                "video_id": vid,
                "title": flat_title,
                "description": "",
                "published_at": None,
                "channel_title": "Asamblea Nacional del Ecuador",
                "duration": flat_duration,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "discovered_at": datetime.now().isoformat(),
            }
        meta["video_type"] = classify_video_type(tab, meta.get("duration") or flat_duration)
        if since_dt and meta.get("published_at"):
            try:
                pub = datetime.strptime(meta["published_at"], "%Y-%m-%dT00:00:00Z")
                if pub < since_dt:
                    skipped_old += 1
                    continue
            except ValueError:
                pass
        added.append(meta)
        flag = " (already processed)" if vid in processed else ""
        pub_label = meta["published_at"] or "(date unknown)"
        dur_label = f"{meta.get('duration'):.0f}s" if meta.get("duration") else "?s"
        print(f"  [{i}/{len(candidates)}] {vid}  {pub_label}  [{meta['video_type']:12s} {dur_label:>8s}]  {meta['title'][:60]}{flag}")

    if not added:
        print("\nNo new sessions matching the filters.")
        return 0

    # Merge into index — newest first
    by_id = {s["video_id"]: s for s in index.get("sessions", [])}
    for m in added:
        by_id[m["video_id"]] = m
    merged = sorted(by_id.values(), key=lambda s: s.get("published_at") or "", reverse=True)
    index["sessions"] = merged
    index["last_updated"] = datetime.now().isoformat() + "Z"
    save_index(index)

    print()
    print(f"✓ Added/updated {len(added)} videos")
    print(f"  skipped (older than --since): {skipped_old}")
    print(f"  added without published_at (metadata fetch rate-limited): {fallback_used}")
    print(f"  total in index now: {len(merged)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
