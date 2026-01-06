#!/usr/bin/env python3
"""
Helper script to find YouTube channel ID by searching
"""

import sys
from pathlib import Path
from googleapiclient.discovery import build

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

import yaml

def load_config():
    config_path = PROJECT_ROOT / "config.yml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def search_channel(youtube_api_key, query):
    """Search for YouTube channels"""
    youtube = build('youtube', 'v3', developerKey=youtube_api_key)

    request = youtube.search().list(
        part='snippet',
        q=query,
        type='channel',
        maxResults=5
    )

    response = request.execute()

    print(f"Search results for '{query}':\n")

    for item in response.get('items', []):
        channel_id = item['id']['channelId']
        channel_title = item['snippet']['title']
        channel_desc = item['snippet']['description'][:100]

        print(f"Channel: {channel_title}")
        print(f"ID: {channel_id}")
        print(f"Description: {channel_desc}...")
        print(f"URL: https://www.youtube.com/channel/{channel_id}")
        print("-" * 60)

if __name__ == "__main__":
    config = load_config()
    api_key = config['youtube']['api_key']

    search_channel(api_key, "Asamblea Nacional Ecuador")
