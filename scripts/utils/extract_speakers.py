#!/usr/bin/env python3
"""
Extract speaker names from transcripts using pattern matching and NLP.
This identifies mentions of asambleístas in the transcript text.
"""

import json
import re
from pathlib import Path
from collections import Counter
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def extract_speaker_names(text):
    """
    Extract potential speaker names from transcript text.

    Looks for patterns like:
    - "el asambleísta [Name]"
    - "la asambleísta [Name]"
    - "el legislador [Name]"
    - Names followed by common verbs (señaló, afirmó, expresó, etc.)
    """
    speakers = []

    # Pattern 1: "el/la asambleísta [Name]" - capture 2-3 words max
    pattern1 = r'(?:el|la)\s+asambleísta\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,2})(?:\s|,|\.|\?|!|$)'
    matches1 = re.findall(pattern1, text, re.IGNORECASE)
    speakers.extend(matches1)

    # Pattern 2: "el/la legislador/a [Name]"
    pattern2 = r'(?:el|la)\s+legislador(?:a)?\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,2})(?:\s|,|\.|\?|!|$)'
    matches2 = re.findall(pattern2, text, re.IGNORECASE)
    speakers.extend(matches2)

    # Pattern 3: Names with titles (doctor, doctora, licenciado, etc.)
    pattern3 = r'(?:doctor|doctora|licenciado|licenciada|ingeniero|ingeniera)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,2})(?:\s|,|\.|\?|!|$)'
    matches3 = re.findall(pattern3, text, re.IGNORECASE)
    speakers.extend(matches3)

    # Pattern 4: "asambleístas [Name1], [Name2], [Name3]"
    pattern4 = r'asambleístas?\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)(?:,\s*([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+))*'
    matches4 = re.findall(pattern4, text, re.IGNORECASE)
    for match in matches4:
        for name in match:
            if name:
                speakers.append(name)

    # Stopwords and blacklisted phrases to filter out
    stopwords = {
        'de la', 'de las', 'del', 'de los', 'el presidente', 'la presidente',
        'le voy', 'ha sido', 'hasta este', 'contamos con', 'las distintas',
        'presidente de', 'representante de', 'respecto de', 'respecto a',
        'voy a', 'va a', 'tiene que', 'hay que', 'debe ser',
        'durante este', 'para el', 'para la', 'pero también', 'presentes contamos',
        'registrados hasta', 'la lectura', 'el orden', 'este momento', 'muy buenos'
    }

    # Common Spanish words that are NOT names
    non_name_words = {
        'durante', 'para', 'pero', 'también', 'presentes', 'contamos',
        'registrados', 'hasta', 'lectura', 'orden', 'momento', 'buenos',
        'este', 'esta', 'estos', 'estas'
    }

    # Clean and normalize names
    cleaned_speakers = []
    for name in speakers:
        # Remove extra whitespace
        name = ' '.join(name.split())
        # Title case
        name = name.title()

        # Skip if:
        # - Too short or contains numbers
        # - Contains stopwords
        # - Doesn't have at least 2 words (first + last name)
        if len(name) < 6 or re.search(r'\d', name):
            continue

        name_lower = name.lower()
        if any(stop in name_lower for stop in stopwords):
            continue

        word_count = len(name.split())
        if word_count < 2 or word_count > 4:
            continue

        # Check if first word is a non-name word
        first_word = name.split()[0].lower()
        if first_word in non_name_words:
            continue

        cleaned_speakers.append(name)

    return cleaned_speakers


def normalize_speaker_name(name):
    """Normalize speaker name by removing common suffixes and cleaning."""
    # Remove common role/title suffixes
    suffixes_to_remove = [
        r'\s+Presidente$',
        r'\s+Presidenta$',
        r'\s+Ha\s+Sido$',
        r'\s+Ha$',
        r'\s+Del?\s+',
        r'\s+De\s+La$',
    ]

    normalized = name
    for suffix in suffixes_to_remove:
        normalized = re.sub(suffix, '', normalized, flags=re.IGNORECASE)

    return normalized.strip()


def deduplicate_speakers(speaker_counts):
    """Deduplicate speaker names that are likely the same person."""
    # Group by base name (first 2 words)
    base_names = {}
    for name, count in speaker_counts.items():
        # Normalize the name
        normalized = normalize_speaker_name(name)
        words = normalized.split()

        # Use first 2 words as base
        if len(words) >= 2:
            base = ' '.join(words[:2])
            if base not in base_names:
                base_names[base] = []
            base_names[base].append((normalized, count))

    # Select the most mentioned version of each name
    deduplicated = {}
    for base, variants in base_names.items():
        # Sort by count (descending) and name length (prefer shorter)
        variants.sort(key=lambda x: (-x[1], len(x[0])))
        best_name, total_count = variants[0]

        # Sum all counts for this person
        total = sum(count for _, count in variants)
        deduplicated[best_name] = total

    return deduplicated


def identify_speakers_in_session(session_file):
    """Extract speakers from a single session JSON file."""
    logger.info(f"Processing {session_file.name}")

    with open(session_file, 'r', encoding='utf-8') as f:
        session = json.load(f)

    text = session.get('text', '')
    if not text:
        logger.warning(f"No transcript text in {session_file.name}")
        return []

    # Extract speaker names
    speaker_names = extract_speaker_names(text)

    # Count occurrences
    speaker_counts = Counter(speaker_names)

    # Deduplicate similar names
    deduplicated = deduplicate_speakers(speaker_counts)

    # Filter: only keep names mentioned at least 2 times
    significant_speakers = [
        name for name, count in deduplicated.items()
        if count >= 2
    ]

    logger.info(f"Found {len(significant_speakers)} speakers mentioned 2+ times:")
    for name in sorted(significant_speakers):
        count = deduplicated[name]
        logger.info(f"  - {name} ({count} mentions)")

    return significant_speakers


def update_speaker_database(speakers_from_sessions):
    """Update the speakers database with newly discovered speakers."""
    speakers_file = Path('data/speakers/asambleistas.json')

    # Load existing database
    if speakers_file.exists():
        with open(speakers_file, 'r', encoding='utf-8') as f:
            db = json.load(f)
    else:
        db = {
            'last_updated': datetime.now().isoformat(),
            'source': 'Extracted from session transcripts',
            'total_count': 0,
            'asambleistas': []
        }

    # Get existing names (excluding placeholder)
    existing_names = {
        a['name'] for a in db['asambleistas']
        if a['name'] != 'Placeholder Name'
    }

    # Add new speakers
    new_speakers_added = 0
    for name in speakers_from_sessions:
        if name not in existing_names:
            # Generate ID from name
            speaker_id = 'AN-' + name.replace(' ', '-').upper()

            db['asambleistas'].append({
                'id': speaker_id,
                'name': name,
                'party': 'Unknown',
                'province': 'Unknown',
                'role': 'Asambleísta',
                'committee': 'Unknown',
                'alternate_names': []
            })
            existing_names.add(name)
            new_speakers_added += 1
            logger.info(f"Added new speaker: {name}")

    # Update metadata
    db['last_updated'] = datetime.now().isoformat()
    db['total_count'] = len(db['asambleistas'])

    # Save
    speakers_file.parent.mkdir(parents=True, exist_ok=True)
    with open(speakers_file, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    logger.info(f"\nSpeaker database updated:")
    logger.info(f"  - Total speakers: {db['total_count']}")
    logger.info(f"  - New speakers added: {new_speakers_added}")

    return db


def main():
    """Main function to extract speakers from all sessions."""
    sessions_dir = Path('data/sessions')

    if not sessions_dir.exists():
        logger.error(f"Sessions directory not found: {sessions_dir}")
        return

    # Process all session files
    all_speakers = set()
    for session_file in sessions_dir.glob('*.json'):
        speakers = identify_speakers_in_session(session_file)
        all_speakers.update(speakers)

    logger.info(f"\n=== Total unique speakers found: {len(all_speakers)} ===")

    # Update speaker database
    if all_speakers:
        update_speaker_database(all_speakers)
    else:
        logger.warning("No speakers found in transcripts")


if __name__ == '__main__':
    main()
