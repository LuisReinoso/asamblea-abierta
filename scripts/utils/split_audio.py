#!/usr/bin/env python3
"""
Split large audio files into chunks for Whisper API (25 MB limit)
"""

import sys
import subprocess
from pathlib import Path

def split_audio(audio_path, chunk_duration_minutes=10):
    """
    Split audio file into chunks using ffmpeg

    Args:
        audio_path: Path to audio file
        chunk_duration_minutes: Duration of each chunk in minutes

    Returns:
        List of chunk file paths
    """
    audio_path = Path(audio_path)
    output_dir = audio_path.parent / f"{audio_path.stem}_chunks"
    output_dir.mkdir(exist_ok=True)

    chunk_duration = chunk_duration_minutes * 60  # Convert to seconds

    print(f"Splitting {audio_path.name} into {chunk_duration_minutes}-minute chunks...")
    print(f"Output directory: {output_dir}")

    # Use ffmpeg to split the audio
    cmd = [
        'ffmpeg',
        '-i', str(audio_path),
        '-f', 'segment',
        '-segment_time', str(chunk_duration),
        '-c', 'copy',
        str(output_dir / 'chunk_%03d.m4a')
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)

        # Get list of created chunks
        chunks = sorted(output_dir.glob('chunk_*.m4a'))

        print(f"✓ Created {len(chunks)} chunks")
        for chunk in chunks:
            size_mb = chunk.stat().st_size / (1024 * 1024)
            print(f"  {chunk.name}: {size_mb:.2f} MB")

        return chunks

    except subprocess.CalledProcessError as e:
        print(f"Error splitting audio: {e}")
        print(f"STDERR: {e.stderr.decode()}")
        return None
    except FileNotFoundError:
        print("Error: ffmpeg not found. Please install it:")
        print("  sudo apt-get install ffmpeg")
        return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python split_audio.py <audio_file> [chunk_duration_minutes]")
        sys.exit(1)

    audio_file = sys.argv[1]
    chunk_minutes = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    chunks = split_audio(audio_file, chunk_minutes)

    if chunks:
        print(f"\n✓ Successfully split into {len(chunks)} chunks")
        sys.exit(0)
    else:
        print("\n❌ Failed to split audio")
        sys.exit(1)
