#!/usr/bin/env python3
"""
Sync speakers database from Asamblea Nacional official API
Fetches the list of 151 asambleístas and updates the local database
"""

import sys
import json
import requests
from pathlib import Path
from datetime import datetime

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.append(str(PROJECT_ROOT))


def fetch_asambleistas():
    """
    Fetch asambleístas from official API
    Source: https://datos.asambleanacional.gob.ec/assemblyMan
    """
    url = "https://datos.asambleanacional.gob.ec/assemblyMan"

    print(f"Fetching asambleístas from: {url}")

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        data = response.json()
        print(f"✓ Fetched {len(data)} asambleístas")

        return data

    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")
        return None


def transform_speaker_data(api_data):
    """
    Transform API data to our internal format
    """
    speakers = []

    for idx, person in enumerate(api_data, start=1):
        # Extract data from API response
        # Note: Adjust field names based on actual API response structure
        speaker = {
            "id": f"AN-{idx:03d}",
            "name": person.get("nombre", "Unknown"),
            "party": person.get("partido", "Sin partido"),
            "province": person.get("provincia", "Sin provincia"),
            "role": person.get("cargo", "Asambleísta"),
            "committee": person.get("comision", "Sin comisión"),
            "alternate_names": []
        }

        # Add alternate name formats for better matching
        full_name = speaker["name"]
        if full_name and full_name != "Unknown":
            # Add variations: "Juan Pérez" -> ["Juan", "Pérez", "J. Pérez"]
            parts = full_name.split()
            if len(parts) >= 2:
                speaker["alternate_names"] = [
                    parts[-1],  # Last name
                    f"{parts[0][0]}. {parts[-1]}"  # Initial + last name
                ]

        speakers.append(speaker)

    return speakers


def save_speakers_database(speakers):
    """Save speakers to database file"""
    db_path = PROJECT_ROOT / "data" / "speakers" / "asambleistas.json"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    database = {
        "last_updated": datetime.now().isoformat(),
        "source": "https://datos.asambleanacional.gob.ec/assemblyMan",
        "total_count": len(speakers),
        "asambleistas": speakers
    }

    with open(db_path, 'w', encoding='utf-8') as f:
        json.dump(database, f, indent=2, ensure_ascii=False)

    print(f"✓ Database saved to: {db_path}")
    print(f"  Total asambleístas: {len(speakers)}")


def main():
    """Main function"""
    print("="*60)
    print("SYNC SPEAKERS DATABASE")
    print("="*60)

    # Fetch data from API
    api_data = fetch_asambleistas()

    if not api_data:
        print("\nFailed to fetch data from API")
        print("Creating database with placeholder data...")

        # Create placeholder for testing
        speakers = [
            {
                "id": "AN-PLACEHOLDER",
                "name": "Placeholder Name",
                "party": "Unknown",
                "province": "Unknown",
                "role": "Asambleísta",
                "committee": "Unknown",
                "alternate_names": []
            }
        ]
    else:
        # Transform API data
        print("\nTransforming data...")
        speakers = transform_speaker_data(api_data)

    # Save to database
    print("\nSaving database...")
    save_speakers_database(speakers)

    print("\n" + "="*60)
    print("SYNC COMPLETE")
    print("="*60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
