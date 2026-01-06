#!/usr/bin/env python3
"""
03_transcribe_elevenlabs.py

Transcribes audio files using ElevenLabs Speech-to-Text API with speaker diarization.
Supports files up to 3GB and 10 hours - perfect for full assembly sessions!
"""

import os
import sys
import json
import yaml
import argparse
from pathlib import Path
from elevenlabs.client import ElevenLabs

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(PROJECT_ROOT))


def load_config():
    """Load configuration from config.yml"""
    config_path = PROJECT_ROOT / "config.yml"
    if not config_path.exists():
        print("Error: config.yml not found.")
        sys.exit(1)

    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def transcribe_with_elevenlabs(audio_path, api_key, language='es'):
    """
    Transcribe audio file using ElevenLabs with speaker diarization.

    Args:
        audio_path: Path to audio file
        api_key: ElevenLabs API key
        language: Language code (default: es for Spanish)

    Returns:
        Transcript data with speaker diarization
    """
    print(f"Transcribing with ElevenLabs: {audio_path.name}")
    print(f"  Size: {audio_path.stat().st_size / (1024*1024):.2f} MB")

    try:
        # Create client with extended timeout for large files
        client = ElevenLabs(
            api_key=api_key,
            timeout=3600.0  # 1 hour timeout for large files
        )

        # Upload and transcribe with diarization
        print("  Uploading audio to ElevenLabs...")
        print("  Processing with speaker diarization (this may take a few minutes)...")

        with open(audio_path, 'rb') as audio_file:
            # Call the speech-to-text API with diarization
            response = client.speech_to_text.convert(
                file=audio_file,
                model_id="scribe_v1",  # Scribe v1 supports diarization
                language_code=language,
                diarize=True,  # Enable speaker diarization
                num_speakers=None  # Auto-detect number of speakers (up to 32)
            )

        # Parse response
        # ElevenLabs returns: {text, words: [{start, end, text, speaker_id}], speakers: [...]}
        result = {
            'text': response.text,
            'language': language,
            'duration': 0,
            'speakers_detected': len(response.speakers) if hasattr(response, 'speakers') else 0,
            'segments': []
        }

        # Convert word-level data to segments
        # Group consecutive words from same speaker into segments
        if hasattr(response, 'words') and response.words:
            current_segment = None
            segment_id = 0

            for word in response.words:
                speaker_id = word.speaker_id if hasattr(word, 'speaker_id') else None

                # If same speaker continues, append to current segment
                if current_segment and current_segment.get('speaker_id') == speaker_id:
                    current_segment['text'] += ' ' + word.text
                    current_segment['end'] = word.end
                else:
                    # Save previous segment
                    if current_segment:
                        result['segments'].append(current_segment)

                    # Start new segment
                    current_segment = {
                        'id': segment_id,
                        'start': word.start,
                        'end': word.end,
                        'text': word.text,
                        'speaker_id': speaker_id
                    }
                    segment_id += 1

            # Add final segment
            if current_segment:
                result['segments'].append(current_segment)

            # Calculate duration from last word
            if response.words:
                result['duration'] = response.words[-1].end

        print(f"  ✓ Duration: {result['duration']:.2f}s ({result['duration']/60:.2f} min)")
        print(f"  ✓ Speakers detected: {result['speakers_detected']}")
        print(f"  ✓ Segments: {len(result['segments'])}")

        return result

    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return None


def save_transcription(transcription_data, output_path):
    """Save transcription to JSON file"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(transcription_data, f, indent=2, ensure_ascii=False)

    print(f"✓ Transcription saved to: {output_path}")


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description='Transcribe audio using ElevenLabs with speaker diarization'
    )
    parser.add_argument('--audio-path', required=True, help='Path to audio file')
    parser.add_argument('--output-path', help='Output JSON file path')
    parser.add_argument('--language', default='es', help='Language code (default: es)')

    args = parser.parse_args()

    print("=" * 60)
    print("03_TRANSCRIBE_ELEVENLABS - ElevenLabs Speech-to-Text")
    print("=" * 60)

    # Load configuration
    config = load_config()

    if 'elevenlabs' not in config or 'api_key' not in config['elevenlabs']:
        print("Error: ElevenLabs API key not found in config.yml")
        print("Please add:")
        print("elevenlabs:")
        print("  api_key: 'your-api-key-here'")
        sys.exit(1)

    elevenlabs_api_key = config['elevenlabs']['api_key']
    audio_path = Path(args.audio_path)

    if not audio_path.exists():
        print(f"Error: Audio file not found: {audio_path}")
        sys.exit(1)

    # Transcribe
    result = transcribe_with_elevenlabs(audio_path, elevenlabs_api_key, args.language)

    if not result:
        print("\n❌ Transcription failed")
        return 1

    # Determine output path
    if args.output_path:
        output_path = args.output_path
    else:
        output_path = PROJECT_ROOT / "data" / "sessions" / f"{audio_path.stem}.json"

    # Save
    save_transcription(result, output_path)

    # Show preview
    print("\nTranscription Preview (first 500 characters):")
    print("-" * 60)
    print(result['text'][:500] + "...")
    print("-" * 60)

    print("\n" + "=" * 60)
    print("✅ COMPLETE - Transcription with diarization saved")
    print("=" * 60)


if __name__ == '__main__':
    main()
