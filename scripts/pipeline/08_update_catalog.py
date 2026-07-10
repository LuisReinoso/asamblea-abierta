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
import filecmp
import shutil
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Static-site data sync
# ---------------------------------------------------------------------------
# GitHub Pages cannot follow symlinks, so docs/data is a real copy of data/.
# Every time the site is rebuilt we mirror data/ → docs/data/, and we also
# warn at the start if the two trees were out of sync (so manual edits to one
# but not the other don't slip through).

def _list_relative(root: Path) -> set:
    return {p.relative_to(root) for p in root.rglob("*") if p.is_file()}


def check_docs_data_sync(verbose: bool = True) -> tuple[bool, list[str]]:
    """Return (in_sync, list_of_drift_paths). Compares data/ ↔ docs/data/."""
    src = PROJECT_ROOT / "data"
    dst = PROJECT_ROOT / "docs" / "data"
    if not dst.exists():
        return False, ["docs/data does not exist"]
    drift: list[str] = []
    src_files = _list_relative(src)
    dst_files = _list_relative(dst)
    for rel in sorted(src_files - dst_files):
        drift.append(f"missing in docs/data: {rel}")
    for rel in sorted(dst_files - src_files):
        drift.append(f"stale in docs/data: {rel}")
    for rel in sorted(src_files & dst_files):
        if not filecmp.cmp(src / rel, dst / rel, shallow=False):
            drift.append(f"differs: {rel}")
    if drift and verbose:
        print(f"⚠ docs/data is out of sync with data/ ({len(drift)} differences)")
    return len(drift) == 0, drift


def sync_docs_data() -> tuple[int, int, int]:
    """Mirror data/ → docs/data/. Returns (copied, deleted, unchanged)."""
    src = PROJECT_ROOT / "data"
    dst = PROJECT_ROOT / "docs" / "data"
    dst.mkdir(parents=True, exist_ok=True)
    copied = deleted = unchanged = 0

    src_files = _list_relative(src)
    dst_files = _list_relative(dst)

    # Copy new + changed
    for rel in src_files:
        s = src / rel
        d = dst / rel
        if d.exists() and filecmp.cmp(s, d, shallow=False):
            unchanged += 1
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        copied += 1

    # Remove orphan files in docs/data that no longer exist in data/
    for rel in dst_files - src_files:
        try:
            (dst / rel).unlink()
            deleted += 1
        except OSError:
            pass

    # Clean up empty directories in docs/data
    for d in sorted((p for p in dst.rglob("*") if p.is_dir()), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass

    return copied, deleted, unchanged


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
            'video_type': session.get('video_type', 'clip'),
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

    # Sanity check: warn if data/ and docs/data/ have drifted (manual edits).
    in_sync, drift = check_docs_data_sync(verbose=True)
    if not in_sync:
        for line in drift[:5]:
            print(f"    {line}")
        if len(drift) > 5:
            print(f"    … and {len(drift) - 5} more")
        print("  → will be reconciled by the final sync step below")

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

    # Mirror data/ → docs/data/ so GitHub Pages serves the fresh files.
    print("\nSyncing docs/data/ from data/ for GitHub Pages…")
    copied, deleted, unchanged = sync_docs_data()
    print(f"  copied/updated: {copied}   deleted: {deleted}   unchanged: {unchanged}")

    print("\n" + "="*60)
    print(f"COMPLETE - Catalog updated successfully")
    print("="*60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
