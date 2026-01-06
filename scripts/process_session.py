#!/usr/bin/env python3
"""
Master pipeline orchestrator
Processes a complete session from video ID to final catalog update
"""

import sys
import os
import argparse
import json
from pathlib import Path
import subprocess

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.append(str(PROJECT_ROOT))


def run_script(script_path, args=None, description=""):
    """Run a pipeline script and return success status"""
    cmd = [sys.executable, str(script_path)]
    if args:
        cmd.extend(args)

    print(f"\n{'='*60}")
    print(f"Running: {description}")
    print(f"{'='*60}")

    try:
        result = subprocess.run(cmd, check=True, cwd=PROJECT_ROOT)
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        print(f"Error running {script_path}: {e}")
        return False


def main():
    """Main orchestrator"""
    parser = argparse.ArgumentParser(description='Process a complete Asamblea Nacional session')
    parser.add_argument('--video-id', required=True, help='YouTube video ID to process')
    parser.add_argument('--skip-download', action='store_true', help='Skip audio download if already exists')
    parser.add_argument('--skip-stats', action='store_true', help='Skip statistics generation')

    args = parser.parse_args()

    video_id = args.video_id
    print("="*60)
    print(f"PROCESSING SESSION: {video_id}")
    print("="*60)

    # Define paths
    audio_path = PROJECT_ROOT / "temp" / "audio" / f"{video_id}.m4a"
    transcript_path = PROJECT_ROOT / "temp" / "transcripts" / f"{video_id}.json"
    identified_path = PROJECT_ROOT / "temp" / "identified" / f"{video_id}_identified.json"
    classified_path = PROJECT_ROOT / "temp" / "classified" / f"{video_id}_classified.json"
    session_path = PROJECT_ROOT / "data" / "sessions" / f"{video_id}.json"

    # Step 1: Download audio (unless skip flag is set)
    if args.skip_download and audio_path.exists():
        print(f"\nSkipping download, using existing: {audio_path}")
    else:
        success = run_script(
            PROJECT_ROOT / "scripts" / "pipeline" / "02_download_audio.py",
            ["--video-id", video_id],
            "Step 1: Download Audio"
        )
        if not success:
            print("\n❌ Failed at Step 1: Download Audio")
            return 1

    # Step 2: Transcribe
    success = run_script(
        PROJECT_ROOT / "scripts" / "pipeline" / "03_transcribe.py",
        ["--audio-path", str(audio_path), "--output-path", str(transcript_path)],
        "Step 2: Transcribe Audio"
    )
    if not success:
        print("\n❌ Failed at Step 2: Transcription")
        return 1

    # Step 3: Identify speakers
    success = run_script(
        PROJECT_ROOT / "scripts" / "pipeline" / "04_identify_speakers.py",
        ["--transcript-path", str(transcript_path), "--output-path", str(identified_path)],
        "Step 3: Identify Speakers"
    )
    if not success:
        print("\n❌ Failed at Step 3: Speaker Identification")
        return 1

    # Step 4: Classify topics
    success = run_script(
        PROJECT_ROOT / "scripts" / "pipeline" / "05_classify_topics.py",
        ["--transcript-path", str(identified_path), "--output-path", str(classified_path)],
        "Step 4: Classify Topics"
    )
    if not success:
        print("\n❌ Failed at Step 4: Topic Classification")
        return 1

    # Step 5: Save session to data directory
    print(f"\n{'='*60}")
    print("Step 5: Save Session Data")
    print(f"{'='*60}")

    try:
        # Load classified data
        with open(classified_path, 'r', encoding='utf-8') as f:
            session_data = json.load(f)

        # Add video metadata
        session_data['id'] = video_id
        session_data['video_id'] = video_id
        session_data['source_url'] = f"https://www.youtube.com/watch?v={video_id}"

        # Save to data/sessions
        session_path.parent.mkdir(parents=True, exist_ok=True)
        with open(session_path, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, indent=2, ensure_ascii=False)

        print(f"✓ Session saved to: {session_path}")

    except Exception as e:
        print(f"❌ Error saving session: {e}")
        return 1

    # Step 6: Generate statistics (unless skip flag is set)
    if not args.skip_stats:
        success = run_script(
            PROJECT_ROOT / "scripts" / "pipeline" / "06_generate_stats.py",
            [],
            "Step 6: Generate Statistics"
        )
        if not success:
            print("\n⚠️  Warning: Statistics generation failed")

    # Step 7: Build search index
    success = run_script(
        PROJECT_ROOT / "scripts" / "pipeline" / "07_build_search_index.py",
        [],
        "Step 7: Build Search Index"
    )
    if not success:
        print("\n⚠️  Warning: Search index build failed")

    # Step 8: Update catalog
    success = run_script(
        PROJECT_ROOT / "scripts" / "pipeline" / "08_update_catalog.py",
        [],
        "Step 8: Update Catalog"
    )
    if not success:
        print("\n⚠️  Warning: Catalog update failed")

    # Success!
    print("\n" + "="*60)
    print("✅ SESSION PROCESSING COMPLETE!")
    print("="*60)
    print(f"\nSession data saved to: {session_path}")
    print(f"Temporary files in: temp/")
    print(f"\nTo clean up temp files, run:")
    print(f"  rm -rf temp/")

    return 0


if __name__ == "__main__":
    sys.exit(main())
