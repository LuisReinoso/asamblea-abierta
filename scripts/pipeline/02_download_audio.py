#!/usr/bin/env python3
"""
02_download_audio.py

Downloads audio from YouTube videos using yt-dlp.
Extracts audio-only stream in M4A format for transcription.
Audio files are stored temporarily and should not be committed to git.
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(PROJECT_ROOT))


def download_audio(video_id, output_dir):
    """
    Download audio from YouTube video using yt-dlp

    Args:
        video_id: YouTube video ID
        output_dir: Directory to save audio file

    Returns:
        Path to downloaded audio file
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    video_url = f"https://www.youtube.com/watch?v={video_id}"
    output_template = str(output_dir / f"{video_id}.%(ext)s")

    print(f"Downloading audio from {video_url}...")

    try:
        # Use yt-dlp to download audio-only stream
        cmd = [
            'yt-dlp',
            '--extract-audio',  # Extract audio only
            '--audio-format', 'm4a',  # Convert to M4A format
            '--audio-quality', '0',  # Best quality
            '--no-playlist',  # Don't download playlists
            '--output', output_template,
            '--no-warnings',  # Suppress warnings
            '--no-progress',  # No progress bar (for cleaner logs)
            video_url
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

        # Determine output file path
        audio_file = output_dir / f"{video_id}.m4a"

        if audio_file.exists():
            file_size_mb = audio_file.stat().st_size / (1024 * 1024)
            print(f"✓ Audio downloaded successfully: {audio_file}")
            print(f"  File size: {file_size_mb:.2f} MB")
            return audio_file
        else:
            print(f"Error: Audio file not found at {audio_file}")
            return None

    except subprocess.CalledProcessError as e:
        print(f"Error downloading audio: {e}")
        print(f"STDOUT: {e.stdout}")
        print(f"STDERR: {e.stderr}")
        return None
    except FileNotFoundError:
        print("Error: yt-dlp not found. Please install it:")
        print("  pip install yt-dlp")
        return None


def get_video_info(video_id):
    """
    Get video metadata using yt-dlp

    Args:
        video_id: YouTube video ID

    Returns:
        Dictionary with video metadata
    """
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        cmd = [
            'yt-dlp',
            '--dump-json',
            '--no-warnings',
            video_url
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

        import json
        metadata = json.loads(result.stdout)

        return {
            'title': metadata.get('title'),
            'duration': metadata.get('duration'),  # in seconds
            'upload_date': metadata.get('upload_date'),
            'uploader': metadata.get('uploader'),
            'description': metadata.get('description')
        }

    except subprocess.CalledProcessError as e:
        print(f"Error getting video info: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"Error parsing video metadata: {e}")
        return None


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Download audio from YouTube video')
    parser.add_argument('--video-id', required=True, help='YouTube video ID')
    parser.add_argument('--output-dir', default='temp/audio', help='Output directory for audio files')
    parser.add_argument('--get-info', action='store_true', help='Get video metadata only')

    args = parser.parse_args()

    print("="*60)
    print("02_DOWNLOAD_AUDIO - Extract Audio from YouTube Video")
    print("="*60)

    if args.get_info:
        # Get video metadata
        info = get_video_info(args.video_id)
        if info:
            print("\nVideo Information:")
            print(f"  Title: {info['title']}")
            print(f"  Duration: {info['duration']} seconds ({info['duration']//60} minutes)")
            print(f"  Upload Date: {info['upload_date']}")
            print(f"  Uploader: {info['uploader']}")
            print()
            return 0
        else:
            print("Failed to get video information")
            return 1
    else:
        # Download audio
        audio_file = download_audio(args.video_id, args.output_dir)

        if audio_file:
            print("="*60)
            print(f"COMPLETE - Audio saved to: {audio_file}")
            print("="*60)
            return 0
        else:
            print("="*60)
            print("FAILED - Could not download audio")
            print("="*60)
            return 1


if __name__ == "__main__":
    sys.exit(main())
