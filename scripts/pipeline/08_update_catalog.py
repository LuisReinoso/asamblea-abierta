#!/usr/bin/env python3
"""
08_update_catalog.py

Updates the master session catalog and topic-to-session mappings.
This script consolidates all session data for easy access by the frontend.
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
                    sessions.append({
                        'file_path': str(session_file.relative_to(PROJECT_ROOT)),
                        'data': session_data
                    })
            except Exception as e:
                print(f"Warning: Could not load {session_file}: {e}")

    return sessions


def build_session_catalog(sessions):
    """
    Build master session catalog with metadata

    Each catalog entry contains:
    - id, title, date, duration
    - speakers, topics, keywords
    - summary, url, file_path
    """
    catalog = []

    for session_info in sessions:
        session = session_info['data']

        # Extract speaker count
        speaker_count = len([s for s in session.get('speaker_stats', []) if s['id'] != 'UNIDENTIFIED'])

        # Get classification
        classification = session.get('classification', {})

        # Build catalog entry
        entry = {
            'id': session.get('id', ''),
            'title': session.get('title', ''),
            'date': session.get('date', ''),
            'duration': session.get('duration', 0),
            'url': session.get('source_url', ''),
            'file_path': session_info['file_path'],
            'speaker_count': speaker_count,
            'topics': classification.get('topics', []),
            'keywords': classification.get('keywords', [])[:10],  # Limit keywords
            'summary': classification.get('summary', ''),
            'bills_mentioned': len(classification.get('bills', []))
        }

        catalog.append(entry)

    # Sort by date (newest first)
    catalog.sort(key=lambda x: x['date'], reverse=True)

    return catalog


def build_topic_mapping(sessions):
    """Build topic-to-sessions mapping"""
    topic_map = defaultdict(list)

    for session_info in sessions:
        session = session_info['data']
        session_id = session.get('id', '')
        session_title = session.get('title', '')
        session_date = session.get('date', '')

        topics = session.get('classification', {}).get('topics', [])

        for topic in topics:
            topic_map[topic].append({
                'id': session_id,
                'title': session_title,
                'date': session_date
            })

    # Convert to list and sort
    result = []
    for topic, sessions in sorted(topic_map.items()):
        result.append({
            'topic': topic,
            'count': len(sessions),
            'sessions': sorted(sessions, key=lambda x: x['date'], reverse=True)
        })

    # Sort by count (descending)
    result.sort(key=lambda x: x['count'], reverse=True)

    return result


def save_catalog(catalog_data, output_path):
    """Save catalog to JSON file"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(catalog_data, f, indent=2, ensure_ascii=False)

    print(f"✓ Catalog saved to: {output_path}")


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Update session catalog and topic mappings')
    parser.add_argument('--catalog-path', default='data/catalog.json', help='Output path for session catalog')
    parser.add_argument('--topics-path', default='data/topics/topic-sessions.json', help='Output path for topic mappings')

    args = parser.parse_args()

    print("="*60)
    print("08_UPDATE_CATALOG - Update Session Catalog")
    print("="*60)

    # Load all sessions
    print("Loading session data...")
    sessions = load_all_sessions()
    print(f"✓ Loaded {len(sessions)} session(s)")

    if not sessions:
        print("\nNo sessions found. Creating empty catalog.")
        catalog = []
        topic_mapping = []
    else:
        # Build session catalog
        print("Building session catalog...")
        catalog = build_session_catalog(sessions)
        print(f"✓ Built catalog with {len(catalog)} entries")

        # Build topic mapping
        print("Building topic-to-session mapping...")
        topic_mapping = build_topic_mapping(sessions)
        print(f"✓ Built mapping for {len(topic_mapping)} topics")

    # Save catalog
    catalog_output = {
        'generated_at': datetime.now().isoformat(),
        'total_sessions': len(catalog),
        'sessions': catalog
    }

    catalog_path = PROJECT_ROOT / args.catalog_path
    save_catalog(catalog_output, catalog_path)

    # Save topic mapping
    topic_output = {
        'generated_at': datetime.now().isoformat(),
        'total_topics': len(topic_mapping),
        'topics': topic_mapping
    }

    topics_path = PROJECT_ROOT / args.topics_path
    save_catalog(topic_output, topics_path)

    # Print summary
    print("\n" + "="*60)
    print("CATALOG SUMMARY")
    print("="*60)
    print(f"Total sessions: {len(catalog)}")
    print(f"Total topics: {len(topic_mapping)}")

    if catalog:
        # Show date range
        dates = [s['date'] for s in catalog if s['date']]
        if dates:
            print(f"Date range: {min(dates)} to {max(dates)}")

        # Show most recent sessions
        print(f"\nMost recent sessions (top 5):")
        for session in catalog[:5]:
            print(f"  • {session['date']}: {session['title']}")

    if topic_mapping:
        print(f"\nTop 5 topics by session count:")
        for topic in topic_mapping[:5]:
            print(f"  • {topic['topic']}: {topic['count']} sessions")

    print("\n" + "="*60)
    print(f"COMPLETE - Catalog updated successfully")
    print("="*60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
