#!/usr/bin/env python3
"""
05_classify_topics.py

Classifies session transcripts into topics using GPT-4o-mini.
Extracts main topics, keywords, bills mentioned, and generates a summary.
"""

import os
import sys
import json
import yaml
import argparse
from pathlib import Path
from openai import OpenAI

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


def load_topic_taxonomy():
    """Load topic taxonomy"""
    taxonomy_path = PROJECT_ROOT / "data" / "topics" / "taxonomy.json"
    if not taxonomy_path.exists():
        print(f"Error: Topic taxonomy not found: {taxonomy_path}")
        return None

    with open(taxonomy_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_transcript(transcript_path):
    """Load transcript JSON file"""
    with open(transcript_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def classify_topics(transcript_text, taxonomy, openai_api_key):
    """
    Classify transcript topics using GPT-4o-mini

    Args:
        transcript_text: Full transcript text
        taxonomy: Topic taxonomy with categories
        openai_api_key: OpenAI API key

    Returns:
        Dictionary with topics, keywords, bills, and summary
    """
    # Prepare topic categories for the prompt
    categories_list = "\n".join([f"- {cat['name']}" for cat in taxonomy['categories']])

    # Create prompt
    prompt = f"""Analiza la siguiente transcripción de una sesión de la Asamblea Nacional del Ecuador y extrae la siguiente información:

1. TEMAS PRINCIPALES: Identifica 3-5 temas principales de los siguientes categories:
{categories_list}

2. PALABRAS CLAVE: Extrae 10-15 palabras clave o frases importantes.

3. PROYECTOS DE LEY: Lista cualquier proyecto de ley o normativa mencionada (número y título si está disponible).

4. RESUMEN: Escribe un resumen de 2-3 oraciones sobre lo discutido en la sesión.

Responde en formato JSON con esta estructura:
{{
  "topics": ["Topic 1", "Topic 2", ...],
  "keywords": ["keyword1", "keyword2", ...],
  "bills": [
    {{"number": "PL-2026-001", "title": "Título del proyecto"}},
    ...
  ],
  "summary": "Resumen de la sesión..."
}}

TRANSCRIPCIÓN:
{transcript_text[:4000]}
... [transcript truncated for API call]
"""

    print("Calling GPT-4o-mini for topic classification...")

    try:
        client = OpenAI(api_key=openai_api_key)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Eres un asistente experto en análisis de sesiones legislativas de la Asamblea Nacional del Ecuador. Tu tarea es extraer temas, palabras clave, proyectos de ley mencionados y generar resúmenes concisos."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3,
            response_format={"type": "json_object"}
        )

        # Parse response
        result = json.loads(response.choices[0].message.content)

        print("✓ Topic classification complete!")
        print(f"  Topics: {len(result.get('topics', []))}")
        print(f"  Keywords: {len(result.get('keywords', []))}")
        print(f"  Bills mentioned: {len(result.get('bills', []))}")

        # Estimate cost (GPT-4o-mini: ~$0.00015/1K input tokens, ~$0.0006/1K output tokens)
        # Rough estimate: ~1000 input tokens, ~200 output tokens
        cost = (1000 * 0.00015 / 1000) + (200 * 0.0006 / 1000)
        print(f"  Estimated cost: ${cost:.6f}")

        return result

    except Exception as e:
        print(f"Error during topic classification: {e}")
        return None


def save_classified_transcript(data, output_path):
    """Save classified transcript to JSON file"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"✓ Classified transcript saved to: {output_path}")


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Classify topics in transcript using GPT-4o-mini')
    parser.add_argument('--transcript-path', required=True, help='Path to transcript JSON file')
    parser.add_argument('--output-path', help='Output JSON file path')

    args = parser.parse_args()

    print("="*60)
    print("05_CLASSIFY_TOPICS - Topic Classification with GPT-4o-mini")
    print("="*60)

    # Load configuration
    config = load_config()
    openai_api_key = config['openai']['api_key']

    # Load topic taxonomy
    print("Loading topic taxonomy...")
    taxonomy = load_topic_taxonomy()
    if not taxonomy:
        return 1

    print(f"✓ Loaded {len(taxonomy['categories'])} topic categories")

    # Load transcript
    print(f"Loading transcript from {args.transcript_path}...")
    transcript_data = load_transcript(args.transcript_path)

    # Classify topics
    classification = classify_topics(
        transcript_data.get('text', ''),
        taxonomy,
        openai_api_key
    )

    if not classification:
        return 1

    # Update transcript data
    result = transcript_data.copy()
    result['classification'] = classification

    # Print results
    print("\nClassification Results:")
    print("-" * 60)

    print("\nTopics:")
    for topic in classification.get('topics', []):
        print(f"  • {topic}")

    print("\nKeywords:")
    keywords_str = ", ".join(classification.get('keywords', [])[:10])
    print(f"  {keywords_str}...")

    if classification.get('bills'):
        print("\nBills mentioned:")
        for bill in classification['bills']:
            print(f"  • {bill.get('number', 'N/A')}: {bill.get('title', 'N/A')}")

    print("\nSummary:")
    print(f"  {classification.get('summary', 'N/A')}")

    print("-" * 60)

    # Determine output path
    if args.output_path:
        output_path = args.output_path
    else:
        transcript_filename = Path(args.transcript_path).stem
        output_path = PROJECT_ROOT / "temp" / "classified" / f"{transcript_filename}_classified.json"

    # Save result
    save_classified_transcript(result, output_path)

    print("\n" + "="*60)
    print(f"COMPLETE - Classified transcript saved to: {output_path}")
    print("="*60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
