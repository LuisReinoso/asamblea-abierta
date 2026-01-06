#!/usr/bin/env python3
"""
01_discover_videos.py

Discovers new Asamblea Nacional session videos from YouTube using the YouTube Data API v3.
Filters for videos uploaded since the last run and saves metadata to data/sessions/index.json
"""

import os
import sys
import json
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(PROJECT_ROOT))


def load_config():
    """Load configuration from config.yml"""
    config_path = PROJECT_ROOT / "config.yml"
    if not config_path.exists():
        print("Error: config.yml not found. Copy config.yml.template and fill in your API keys.")
        sys.exit(1)

    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def load_session_index():
    """Load existing session index"""
    index_path = PROJECT_ROOT / "data" / "sessions" / "index.json"
    if index_path.exists():
        with open(index_path, 'r') as f:
            return json.load(f)
    return {"sessions": [], "last_updated": None}


def save_session_index(index_data):
    """Save session index"""
    index_path = PROJECT_ROOT / "data" / "sessions" / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)

    with open(index_path, 'w') as f:
        json.dump(index_data, f, indent=2, ensure_ascii=False)

    print(f"✓ Session index saved to {index_path}")


def discover_videos(youtube_api_key, channel_id, published_after=None):
    """
    Discover new videos from the Asamblea Nacional YouTube channel

    Args:
        youtube_api_key: YouTube Data API v3 key
        channel_id: YouTube channel ID
        published_after: RFC 3339 formatted date-time (e.g., "2026-01-01T00:00:00Z")

    Returns:
        List of video metadata dictionaries
    """
    try:
        youtube = build('youtube', 'v3', developerKey=youtube_api_key)

        # Search for videos in the channel
        search_params = {
            'part': 'id,snippet',
            'channelId': channel_id,
            'type': 'video',
            'order': 'date',
            'maxResults': 50
        }

        if published_after:
            search_params['publishedAfter'] = published_after

        print(f"Searching for videos in channel {channel_id}...")
        if published_after:
            print(f"  Published after: {published_after}")

        request = youtube.search().list(**search_params)
        response = request.execute()

        videos = []
        for item in response.get('items', []):
            video_id = item['id']['videoId']
            snippet = item['snippet']

            video_metadata = {
                'video_id': video_id,
                'title': snippet['title'],
                'description': snippet['description'],
                'published_at': snippet['publishedAt'],
                'channel_title': snippet['channelTitle'],
                'url': f"https://www.youtube.com/watch?v={video_id}",
                'discovered_at': datetime.now().isoformat()
            }

            videos.append(video_metadata)

        print(f"✓ Found {len(videos)} video(s)")
        return videos

    except HttpError as e:
        print(f"Error calling YouTube API: {e}")
        sys.exit(1)


def filter_new_videos(discovered_videos, existing_index):
    """Filter out videos that already exist in the index"""
    existing_video_ids = {s['video_id'] for s in existing_index.get('sessions', [])}

    new_videos = [v for v in discovered_videos if v['video_id'] not in existing_video_ids]

    print(f"✓ Found {len(new_videos)} new video(s) (out of {len(discovered_videos)} total)")
    return new_videos


def main():
    """Main function"""
    print("="*60)
    print("01_DISCOVER_VIDEOS - Find New Asamblea Nacional Sessions")
    print("="*60)

    # Load configuration
    config = load_config()
    youtube_api_key = config['youtube']['api_key']
    channel_id = config['youtube']['channel_id']

    # Load existing index
    index_data = load_session_index()

    # Determine published_after date (last 30 days if no last_updated)
    published_after = None
    if index_data.get('last_updated'):
        # Get videos published since last update
        published_after = index_data['last_updated']
    else:
        # First run: get videos from last 30 days
        days_back = 30
        published_after = (datetime.now() - timedelta(days=days_back)).isoformat() + 'Z'
        print(f"First run: searching videos from last {days_back} days")

    # Discover videos
    discovered_videos = discover_videos(youtube_api_key, channel_id, published_after)

    # Filter new videos
    new_videos = filter_new_videos(discovered_videos, index_data)

    # Update index
    if new_videos:
        if 'sessions' not in index_data:
            index_data['sessions'] = []

        index_data['sessions'].extend(new_videos)
        index_data['last_updated'] = datetime.now().isoformat() + 'Z'
        save_session_index(index_data)

        print("\nNew videos discovered:")
        for video in new_videos:
            print(f"  • {video['title']}")
            print(f"    URL: {video['url']}")
            print(f"    Published: {video['published_at']}")
            print()
    else:
        print("\n✓ No new videos found")

    print("="*60)
    print(f"COMPLETE - {len(new_videos)} new video(s) added to index")
    print("="*60)

    return 0 if len(new_videos) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
