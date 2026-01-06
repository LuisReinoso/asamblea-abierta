#!/usr/bin/env python3
"""
04c_map_speakers_vision.py

Maps speaker IDs from diarization to real names using Vision API.
Extracts video frames at speaker change points and reads on-screen overlays.
"""

import os
import sys
import json
import yaml
import base64
import argparse
from pathlib import Path
import subprocess
import logging
from collections import defaultdict

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def load_config():
    """Load configuration from config.yml"""
    config_path = PROJECT_ROOT / 'config.yml'
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def find_unique_speakers_first_appearance(session_file):
    """
    Find the FIRST timestamp where each unique speaker appears.
    Since ElevenLabs identifies by voice, speaker_0 is the SAME person everywhere.

    Returns:
        List of (timestamp, speaker_id) tuples - ONE per unique speaker
    """
    logger.info("Finding first appearance of each unique speaker...")

    with open(session_file, 'r', encoding='utf-8') as f:
        session = json.load(f)

    segments = session.get('segments', [])

    speaker_first_seen = {}

    for segment in segments:
        speaker_id = segment.get('speaker_id')
        timestamp = segment.get('start', 0)

        # Record FIRST time we see this speaker
        # Add 10 seconds delay to allow camera to switch and show overlay
        if speaker_id and speaker_id not in speaker_first_seen:
            speaker_first_seen[speaker_id] = timestamp + 10

    # Convert to list of tuples
    unique_speakers = [(timestamp, speaker_id) for speaker_id, timestamp in speaker_first_seen.items()]
    unique_speakers.sort()  # Sort by timestamp

    logger.info(f"Found {len(unique_speakers)} unique speakers (down from {len(segments)} segments)")
    return unique_speakers


def extract_frame_at_timestamp(video_id, timestamp, output_path):
    """Extract a single frame from video at specific timestamp."""
    video_file = PROJECT_ROOT / "data" / "video" / f"{video_id}.mp4"

    if not video_file.exists():
        logger.error(f"Video file not found: {video_file}")
        return False

    cmd = [
        'ffmpeg',
        '-ss', str(timestamp),
        '-i', str(video_file),
        '-vframes', '1',
        '-q:v', '2',
        '-y',
        str(output_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.returncode == 0


def analyze_frame_for_speaker(frame_path, openai_client):
    """Use Vision API to detect speaker name from video frame."""
    try:
        with open(frame_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')

        prompt = """Look at this image from an Asamblea Nacional del Ecuador session.

Find the speaker identification overlay (usually a colored bar at the bottom with text).

If you see a clear speaker name, return ONLY a JSON object:
{"speaker": "Full Name"}

If NO speaker overlay is visible or text is unclear, return:
{"speaker": null}

Return ONLY valid JSON, no other text."""

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}",
                                "detail": "low"
                            }
                        }
                    ]
                }
            ],
            max_tokens=100,
            temperature=0
        )

        result_text = response.choices[0].message.content.strip()

        # Clean markdown code blocks
        if result_text.startswith('```'):
            result_text = result_text.split('```')[1]
            if result_text.startswith('json'):
                result_text = result_text[4:]
            result_text = result_text.strip()

        result = json.loads(result_text)
        return result.get('speaker')

    except Exception as e:
        logger.warning(f"Error analyzing frame: {e}")
        return None


def build_speaker_mapping(video_id, unique_speakers, openai_client, max_duration=None):
    """
    Build a mapping of speaker_ids to real names using Vision API.
    Since we have ONE entry per unique speaker, we only analyze that many frames!

    Args:
        video_id: YouTube video ID
        unique_speakers: List of (timestamp, speaker_id) tuples - ONE per speaker
        openai_client: OpenAI client
        max_duration: Maximum video duration in seconds (optional)

    Returns:
        Dictionary mapping speaker_id -> name
    """
    logger.info(f"Building speaker mapping for {len(unique_speakers)} unique speakers...")

    frames_dir = PROJECT_ROOT / "data" / "frames" / video_id
    frames_dir.mkdir(parents=True, exist_ok=True)

    speaker_map = {}
    analyzed_count = 0

    # Set max duration if not provided (default 20 hours)
    if max_duration is None:
        max_duration = 72000

    for timestamp, speaker_id in unique_speakers:
        # Already mapped (shouldn't happen since unique_speakers has one per speaker)
        if speaker_id in speaker_map:
            continue

        # Try multiple time offsets to catch speaker overlay
        # Sometimes overlay isn't visible at +10s but appears later
        time_offsets = [0, 20, 50, 110, 170]  # Try at +10s, +30s, +60s, +120s, +180s from first_seen
        speaker_name = None

        for offset in time_offsets:
            test_timestamp = timestamp + offset

            # Skip if timestamp would be beyond video duration
            if test_timestamp > max_duration:
                continue

            frame_path = frames_dir / f"{speaker_id}_{int(test_timestamp):05d}.jpg"

            # Extract frame if needed
            if not frame_path.exists():
                success = extract_frame_at_timestamp(video_id, test_timestamp, frame_path)
                if not success:
                    continue

            # Analyze with Vision API
            speaker_name = analyze_frame_for_speaker(frame_path, openai_client)
            analyzed_count += 1

            if speaker_name:
                # Found speaker name! Map it and stop trying more offsets
                speaker_map[speaker_id] = speaker_name
                logger.info(f"  {int(test_timestamp):5d}s: {speaker_id:12s} = {speaker_name} (offset +{offset}s)")
                break

        # If no offsets worked, log as not detected
        if not speaker_name:
            logger.warning(f"  {int(timestamp):5d}s: {speaker_id:12s} = [Not detected on screen after {len(time_offsets)} attempts]")

        if (analyzed_count) % 10 == 0:
            logger.info(f"  Progress: {analyzed_count}/{len(unique_speakers)} speakers analyzed")

    logger.info(f"✓ Mapped {len(speaker_map)} speakers from {analyzed_count} frames")
    logger.info(f"Vision API cost: ${analyzed_count * 0.00255:.4f}")

    return speaker_map


def update_session_with_speaker_names(session_file, speaker_map):
    """Update session segments with real speaker names."""
    logger.info("Updating session with speaker names...")

    with open(session_file, 'r', encoding='utf-8') as f:
        session = json.load(f)

    segments = session.get('segments', [])
    updated_count = 0

    for segment in segments:
        speaker_id = segment.get('speaker_id')

        if speaker_id and speaker_id in speaker_map:
            # Update with real name
            segment['speaker'] = {
                'id': speaker_id,
                'name': speaker_map[speaker_id],
                'confidence': 1.0  # From Vision API
            }
            updated_count += 1
        else:
            # Keep speaker_id but mark as unidentified
            segment['speaker'] = {
                'id': speaker_id or 'UNKNOWN',
                'name': 'No identificado',
                'confidence': 0
            }

    # Save updated session
    with open(session_file, 'w', encoding='utf-8') as f:
        json.dump(session, f, ensure_ascii=False, indent=2)

    logger.info(f"✓ Updated {updated_count}/{len(segments)} segments with speaker names")

    # Print speaker summary
    logger.info("\n=== Speaker Summary ===")
    for speaker_id, name in sorted(speaker_map.items(), key=lambda x: int(x[0].replace('speaker_', ''))):
        segments_count = sum(1 for seg in segments if seg.get('speaker_id') == speaker_id)
        logger.info(f"  {speaker_id}: {name} ({segments_count} segments)")


def main():
    parser = argparse.ArgumentParser(
        description='Map speaker IDs to real names using Vision API'
    )
    parser.add_argument('--video-id', required=True, help='YouTube video ID')
    parser.add_argument('--session-file', required=True, help='Session JSON file with diarization')
    parser.add_argument('--max-frames', type=int, default=50, help='Maximum frames to analyze')

    args = parser.parse_args()

    print("=" * 60)
    print("04c_MAP_SPEAKERS_VISION - Vision-based Speaker Mapping")
    print("=" * 60)

    # Load config
    config = load_config()

    # Initialize OpenAI client
    from openai import OpenAI
    openai_client = OpenAI(api_key=config['openai']['api_key'])

    # Load session to get duration
    with open(args.session_file, 'r', encoding='utf-8') as f:
        session = json.load(f)
    video_duration = session.get('duration', 72000)  # Default to 20 hours if not found

    # Find unique speakers (one timestamp per speaker)
    unique_speakers = find_unique_speakers_first_appearance(args.session_file)

    # Build speaker mapping (one frame per unique speaker - much more efficient!)
    speaker_map = build_speaker_mapping(
        args.video_id,
        unique_speakers,
        openai_client,
        max_duration=video_duration
    )

    # Update session file
    update_session_with_speaker_names(args.session_file, speaker_map)

    print("=" * 60)
    print(f"COMPLETE - Mapped {len(speaker_map)} speakers")
    print("=" * 60)


if __name__ == '__main__':
    main()
