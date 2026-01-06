#!/usr/bin/env python3
"""
06_generate_stats.py

Generates speaker participation statistics and topic distribution data.
Creates monthly and all-time statistics for visualization.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(PROJECT_ROOT))


def load_all_sessions():
    """Load all session files from data/sessions"""
    sessions_dir = PROJECT_ROOT / "data" / "sessions"
    sessions = []

    if not sessions_dir.exists():
        print(f"Warning: Sessions directory not found: {sessions_dir}")
        return sessions

    # Recursively find all JSON files in sessions directory
    for session_file in sessions_dir.rglob("*.json"):
        if session_file.name != "index.json":
            try:
                with open(session_file, 'r', encoding='utf-8') as f:
                    session_data = json.load(f)
                    sessions.append(session_data)
            except Exception as e:
                print(f"Warning: Could not load {session_file}: {e}")

    return sessions


def generate_speaker_stats(sessions):
    """Generate speaker participation statistics"""
    speaker_stats = defaultdict(lambda: {
        'total_time': 0,
        'total_interventions': 0,
        'sessions_attended': set(),
        'topics_discussed': set()
    })

    for session in sessions:
        session_id = session.get('id', 'unknown')
        speaker_list = session.get('speaker_stats', [])

        for speaker in speaker_list:
            speaker_id = speaker['id']
            if speaker_id == 'UNIDENTIFIED':
                continue

            stats = speaker_stats[speaker_id]
            stats['id'] = speaker_id
            stats['name'] = speaker['name']
            stats['party'] = speaker.get('party')
            stats['province'] = speaker.get('province')
            stats['total_time'] += speaker.get('total_time', 0)
            stats['total_interventions'] += speaker.get('interventions', 0)
            stats['sessions_attended'].add(session_id)

            # Add topics from this session
            for topic in session.get('classification', {}).get('topics', []):
                stats['topics_discussed'].add(topic)

    # Convert sets to counts
    result = []
    for speaker_id, stats in speaker_stats.items():
        result.append({
            'id': stats['id'],
            'name': stats['name'],
            'party': stats.get('party'),
            'province': stats.get('province'),
            'total_time': stats['total_time'],
            'total_interventions': stats['total_interventions'],
            'sessions_attended': len(stats['sessions_attended']),
            'topics_discussed': len(stats['topics_discussed'])
        })

    # Sort by total time (descending)
    result.sort(key=lambda x: x['total_time'], reverse=True)

    return result


def generate_topic_stats(sessions):
    """Generate topic distribution statistics"""
    topic_stats = defaultdict(lambda: {
        'count': 0,
        'sessions': []
    })

    for session in sessions:
        session_id = session.get('id', 'unknown')
        topics = session.get('classification', {}).get('topics', [])

        for topic in topics:
            topic_stats[topic]['count'] += 1
            topic_stats[topic]['sessions'].append({
                'id': session_id,
                'title': session.get('title', ''),
                'date': session.get('date', '')
            })

    # Convert to list
    result = []
    for topic, stats in topic_stats.items():
        result.append({
            'topic': topic,
            'count': stats['count'],
            'sessions': stats['sessions']
        })

    # Sort by count (descending)
    result.sort(key=lambda x: x['count'], reverse=True)

    return result


def generate_monthly_stats(sessions):
    """Generate monthly statistics"""
    monthly_stats = defaultdict(lambda: {
        'sessions_count': 0,
        'total_duration': 0,
        'speakers': set(),
        'topics': set()
    })

    for session in sessions:
        date_str = session.get('date', '')
        if not date_str:
            continue

        try:
            date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            month_key = date.strftime('%Y-%m')

            stats = monthly_stats[month_key]
            stats['sessions_count'] += 1
            stats['total_duration'] += session.get('duration', 0)

            # Count unique speakers
            for speaker in session.get('speaker_stats', []):
                if speaker['id'] != 'UNIDENTIFIED':
                    stats['speakers'].add(speaker['id'])

            # Count topics
            for topic in session.get('classification', {}).get('topics', []):
                stats['topics'].add(topic)

        except Exception as e:
            print(f"Warning: Could not parse date {date_str}: {e}")

    # Convert to list
    result = []
    for month, stats in sorted(monthly_stats.items()):
        result.append({
            'month': month,
            'sessions_count': stats['sessions_count'],
            'total_duration': stats['total_duration'],
            'unique_speakers': len(stats['speakers']),
            'unique_topics': len(stats['topics'])
        })

    return result


def save_stats(stats_data, output_path):
    """Save statistics to JSON file"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(stats_data, f, indent=2, ensure_ascii=False)

    print(f"✓ Statistics saved to: {output_path}")


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Generate statistics from session data')
    parser.add_argument('--output-dir', default='data/stats', help='Output directory for statistics')

    args = parser.parse_args()

    print("="*60)
    print("06_GENERATE_STATS - Generate Statistics")
    print("="*60)

    # Load all sessions
    print("Loading session data...")
    sessions = load_all_sessions()
    print(f"✓ Loaded {len(sessions)} session(s)")

    if not sessions:
        print("\nNo sessions found. Nothing to process.")
        return 0

    # Generate statistics
    print("\nGenerating statistics...")

    # Speaker statistics
    print("  • Speaker participation stats...")
    speaker_stats = generate_speaker_stats(sessions)

    # Topic statistics
    print("  • Topic distribution stats...")
    topic_stats = generate_topic_stats(sessions)

    # Monthly statistics
    print("  • Monthly stats...")
    monthly_stats = generate_monthly_stats(sessions)

    # Combined statistics
    all_stats = {
        'generated_at': datetime.now().isoformat(),
        'total_sessions': len(sessions),
        'speaker_stats': speaker_stats,
        'topic_stats': topic_stats,
        'monthly_stats': monthly_stats
    }

    # Save statistics
    output_dir = PROJECT_ROOT / args.output_dir
    save_stats(all_stats, output_dir / "all-time.json")

    # Print summary
    print("\n" + "="*60)
    print("STATISTICS SUMMARY")
    print("="*60)
    print(f"Total sessions: {len(sessions)}")
    print(f"Unique speakers: {len(speaker_stats)}")
    print(f"Unique topics: {len(topic_stats)}")
    print(f"Months covered: {len(monthly_stats)}")

    if speaker_stats:
        print(f"\nTop 5 speakers by participation time:")
        for speaker in speaker_stats[:5]:
            print(f"  • {speaker['name']}: {speaker['total_time']/60:.1f} min ({speaker['sessions_attended']} sessions)")

    if topic_stats:
        print(f"\nTop 5 topics:")
        for topic in topic_stats[:5]:
            print(f"  • {topic['topic']}: {topic['count']} sessions")

    print("\n" + "="*60)
    print(f"COMPLETE - Statistics saved to: {output_dir}")
    print("="*60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
