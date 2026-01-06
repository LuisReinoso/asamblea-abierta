#!/usr/bin/env python3
"""
07_build_search_index.py

Builds a pre-compiled search index for FlexSearch from all sessions.
This allows for fast client-side searching in the frontend.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

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


def build_search_documents(sessions):
    """
    Build search documents from sessions

    Each document contains:
    - id: Unique session ID
    - title: Session title
    - date: Session date
    - transcript: Full transcript text (first 5000 chars for index size)
    - speakers: List of speaker names
    - topics: List of topics
    - keywords: List of keywords
    """
    documents = []

    for session in sessions:
        # Extract speaker names
        speaker_names = []
        for speaker in session.get('speaker_stats', []):
            if speaker['id'] != 'UNIDENTIFIED':
                speaker_names.append(speaker['name'])

        # Get classification data
        classification = session.get('classification', {})

        # Build document
        doc = {
            'id': session.get('id', ''),
            'title': session.get('title', ''),
            'date': session.get('date', ''),
            'url': session.get('source_url', ''),
            'transcript': session.get('text', '')[:5000],  # Limit for index size
            'speakers': speaker_names,
            'topics': classification.get('topics', []),
            'keywords': classification.get('keywords', []),
            'summary': classification.get('summary', ''),
            'duration': session.get('duration', 0)
        }

        documents.append(doc)

    return documents


def save_search_index(documents, output_path):
    """Save search index to JSON file"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Create index data structure
    index_data = {
        'generated_at': datetime.now().isoformat(),
        'total_documents': len(documents),
        'documents': documents
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(index_data, f, indent=2, ensure_ascii=False)

    # Calculate file size
    file_size_kb = output_path.stat().st_size / 1024
    print(f"✓ Search index saved to: {output_path}")
    print(f"  File size: {file_size_kb:.2f} KB")


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Build search index for FlexSearch')
    parser.add_argument('--output-path', default='data/search/index.json', help='Output path for search index')

    args = parser.parse_args()

    print("="*60)
    print("07_BUILD_SEARCH_INDEX - Build FlexSearch Index")
    print("="*60)

    # Load all sessions
    print("Loading session data...")
    sessions = load_all_sessions()
    print(f"✓ Loaded {len(sessions)} session(s)")

    if not sessions:
        print("\nNo sessions found. Creating empty index.")
        documents = []
    else:
        # Build search documents
        print("Building search documents...")
        documents = build_search_documents(sessions)
        print(f"✓ Built {len(documents)} search documents")

    # Save search index
    output_path = PROJECT_ROOT / args.output_path
    save_search_index(documents, output_path)

    # Print summary
    print("\n" + "="*60)
    print("INDEX SUMMARY")
    print("="*60)
    print(f"Total documents: {len(documents)}")

    if documents:
        total_speakers = sum(len(doc['speakers']) for doc in documents)
        total_topics = sum(len(doc['topics']) for doc in documents)
        total_keywords = sum(len(doc['keywords']) for doc in documents)

        print(f"Total speaker mentions: {total_speakers}")
        print(f"Total topic tags: {total_topics}")
        print(f"Total keywords: {total_keywords}")

        # Show sample document
        print("\nSample document (first):")
        sample = documents[0]
        print(f"  ID: {sample['id']}")
        print(f"  Title: {sample['title']}")
        print(f"  Date: {sample['date']}")
        print(f"  Speakers: {', '.join(sample['speakers'][:3])}...")
        print(f"  Topics: {', '.join(sample['topics'])}")

    print("\n" + "="*60)
    print(f"COMPLETE - Search index ready for frontend")
    print("="*60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
