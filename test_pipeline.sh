#!/bin/bash
# Test the complete pipeline with a short video

# Activate virtual environment
source .venv/bin/activate

VIDEO_ID="XbhzRYE2OKg"
VIDEO_TITLE="Annabella_Azin_Sesion_057"

echo "=========================================="
echo "TESTING COMPLETE PIPELINE"
echo "Video: $VIDEO_TITLE"
echo "=========================================="

# Step 1: Download audio
echo ""
echo "Step 1: Downloading audio..."
python scripts/pipeline/02_download_audio.py \
    --video-id "$VIDEO_ID" \
    --output-dir temp/audio

if [ $? -ne 0 ]; then
    echo "Error downloading audio"
    exit 1
fi

# Step 2: Transcribe
echo ""
echo "Step 2: Transcribing with OpenAI Whisper..."
python scripts/pipeline/03_transcribe.py \
    --audio-path "temp/audio/${VIDEO_ID}.m4a" \
    --output-path "temp/transcripts/${VIDEO_TITLE}.json"

if [ $? -ne 0 ]; then
    echo "Error transcribing"
    exit 1
fi

# Step 3: Identify speakers
echo ""
echo "Step 3: Identifying speakers..."
python scripts/pipeline/04_identify_speakers.py \
    --transcript-path "temp/transcripts/${VIDEO_TITLE}.json" \
    --output-path "temp/identified/${VIDEO_TITLE}_identified.json"

if [ $? -ne 0 ]; then
    echo "Error identifying speakers"
    exit 1
fi

# Step 4: Classify topics
echo ""
echo "Step 4: Classifying topics..."
python scripts/pipeline/05_classify_topics.py \
    --transcript-path "temp/identified/${VIDEO_TITLE}_identified.json" \
    --output-path "temp/classified/${VIDEO_TITLE}_classified.json"

if [ $? -ne 0 ]; then
    echo "Error classifying topics"
    exit 1
fi

echo ""
echo "=========================================="
echo "PIPELINE TEST COMPLETE!"
echo "=========================================="
echo ""
echo "Results saved to:"
echo "  - Transcript: temp/transcripts/${VIDEO_TITLE}.json"
echo "  - Identified: temp/identified/${VIDEO_TITLE}_identified.json"
echo "  - Classified: temp/classified/${VIDEO_TITLE}_classified.json"
echo ""
echo "To view the final result:"
echo "  cat temp/classified/${VIDEO_TITLE}_classified.json | jq '.classification'"
