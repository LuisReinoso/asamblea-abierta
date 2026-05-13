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
CHANNEL_URL = "https://www.youtube.com/@AsambleaNacionalEC/videos"


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


def list_channel_videos() -> list[tuple[str, str]]:
    """Return all (video_id, title) in the channel, newest first.

    Uses flat-playlist mode: fast, no per-video calls, no YouTube bot
    challenges. Returns title from the playlist but no upload_date — date
    is fetched lazily later via fetch_metadata().
    """
    print(f"Listing videos from {CHANNEL_URL} …")
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--print", "%(id)s\t%(title)s",
        CHANNEL_URL,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print(f"  yt-dlp failed: {r.stderr[-300:]}", file=sys.stderr)
        sys.exit(1)
    videos: list[tuple[str, str]] = []
    for line in r.stdout.splitlines():
        if "\t" not in line:
            continue
        vid, title = line.split("\t", 1)
        vid, title = vid.strip(), title.strip()
        if vid:
            videos.append((vid, title))
    print(f"  channel has {len(videos)} videos")
    return videos


def fetch_metadata(video_id: str) -> dict | None:
    """Fetch title + upload_date for a single video. Returns None on failure."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        "yt-dlp",
        "--extractor-args", "youtube:player_client=tv_embedded,android_vr,ios",
        "--no-warnings", "--skip-download",
        "--print", "%(id)s\t%(title)s\t%(upload_date)s\t%(channel)s",
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
        "url": url,
        "discovered_at": datetime.now().isoformat(),
    }


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

    channel_videos = list_channel_videos()

    # Candidate = in channel, not in index (or --rebuild)
    candidates: list[tuple[str, str]] = []
    for vid, title in channel_videos:
        if not args.rebuild and vid in known:
            continue
        candidates.append((vid, title))
        if len(candidates) >= args.limit:
            break

    print(f"Candidates to inspect: {len(candidates)} (limit={args.limit})")
    if not candidates:
        print("Nothing new to add.")
        return 0

    since_dt = None
    if args.since:
        since_dt = datetime.strptime(args.since, "%Y-%m-%d")

    # Try fetching per-video metadata (title + date). YouTube sometimes
    # rate-limits this with "Sign in to confirm you're not a bot"; in that
    # case we fall back to the flat-playlist title and leave published_at
    # empty so the entry can still be queued for processing.
    added: list[dict] = []
    skipped_old = 0
    fallback_used = 0
    for i, (vid, flat_title) in enumerate(candidates, 1):
        meta = fetch_metadata(vid)
        if not meta:
            fallback_used += 1
            meta = {
                "video_id": vid,
                "title": flat_title,
                "description": "",
                "published_at": None,
                "channel_title": "Asamblea Nacional del Ecuador",
                "url": f"https://www.youtube.com/watch?v={vid}",
                "discovered_at": datetime.now().isoformat(),
            }
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
        print(f"  [{i}/{len(candidates)}] {vid}  {pub_label}  {meta['title'][:70]}{flag}")

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
