# Asamblea Abierta 🏛️

> Transparency platform for Ecuador's National Assembly

**🌐 Live Site:** [luisreinoso.dev/asamblea-abierta](https://luisreinoso.dev/asamblea-abierta/)

[![License: CC0-1.0](https://img.shields.io/badge/License-CC0_1.0-lightgrey.svg)](http://creativecommons.org/publicdomain/zero/1.0/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**Asamblea Abierta** transforms National Assembly plenary sessions into full, searchable transcripts with automatic speaker identification — running entirely on local infrastructure with open models.

## 🎯 Objective

Provide Ecuadorian citizens with easy access and quick searches about what topics are discussed in each National Assembly session, who participates, and how much time they dedicate to each topic.

## ✨ Features

- ✅ **Local transcription** with [faster-whisper large-v3-turbo](https://github.com/SYSTRAN/faster-whisper)
- ✅ **Speaker diarization** with [pyannote community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)
- ✅ **Speaker identification** via overlay OCR with [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)
- ✅ **Fuzzy name matching** with Unicode normalization + surname-token fallback
- ✅ **Auto-learning** of new speakers from OCR (proposes additions to the asambleístas DB)
- ✅ **Static website** with search and navigation by speaker, topic and date
- ✅ **YouTube links** with synchronized timestamps
- ✅ **Open data** in JSON format under CC0 1.0 license
- 💰 **Zero API costs** — all inference runs locally

## 🚀 How It Works

```
1. Video Download (YouTube)            → yt-dlp
   ↓
2. Audio Extraction                    → ffmpeg (16kHz mono wav)
   ↓
3. Speaker Diarization                 → pyannote community-1 (GPU)
   ↓                                     diarization-server :8001
4. Per-segment Transcription           → faster-whisper large-v3-turbo (GPU)
   ↓                                     whisper-server :8000
5. Speaker Name Identification         → PaddleOCR (CPU) on N frames per speaker
   ↓                                     + fuzzy match vs asambleistas.json
6. Topic Classification (optional)     → local LLM via Ollama
   ↓
7. Static Site Build                   → 06/07/08 scripts → docs/
   ↓
8. Publish                             → GitHub Pages
```

### Architecture

The pipeline depends on two local HTTP services that run alongside the project:

| Service | Port | Purpose | Repo |
|---|---|---|---|
| `whisper-server` | 8000 | ASR (faster-whisper large-v3-turbo) | sibling project, see below |
| `diarization-server` | 8001 | Speaker diarization (pyannote community-1) | sibling project, see below |

Both servers expose the same kind of clean HTTP API (`POST /transcribe`, `POST /diarize`) and can be reused by any other project that needs speech or diarization.

## 📊 Example Results

**Session from January 5, 2026 (3.9 hours):**

| Metric | Previous (API-based) | Current (local stack) |
|---|---|---|
| Unique speakers | 38 (fragmented) | **22** (-42%) |
| Identified with real confidence | 0 (all faked 1.0) | **17/22 (77%)** |
| Music/jingles flagged as speaker | Yes | ✅ No |
| Unicode-equivalent duplicates | Yes ("Lucía" ≠ "Lucia") | ✅ Resolved |
| Cost per session | ~$0.35 USD | **$0.00** |

## 🛠️ Setup

### Prerequisites

- Python 3.11+
- CUDA-capable GPU (for whisper-server and diarization-server)
- ffmpeg, yt-dlp
- A [HuggingFace token](https://huggingface.co/settings/tokens) with access to `pyannote/speaker-diarization-community-1` (free, just accept the gated model terms)

### 1. Bring up the local servers

These are independent sibling projects. Clone them next to this repo, set up their venvs, and start them:

```bash
# Whisper transcription server (GPU)
cd ../whisper-server && ./run.sh           # listens on :8000

# Diarization server (GPU)
export HUGGINGFACE_TOKEN=hf_xxx
cd ../diarization-server && ./run.sh       # listens on :8001
```

### 2. Set up this project

```bash
git clone https://github.com/luisreinoso/asamblea-abierta.git
cd asamblea-abierta

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure (only for legacy 01_discover / 05_classify_topics)

`config.yml` (optional — only needed if you use the legacy YouTube API discovery or OpenAI topic classification):

```yaml
youtube:
  api_key: "your-youtube-api-key"
  channel_id: "UCiR_hqG93xvF0r5TKLhbhOg"
openai:
  api_key: "your-openai-api-key"
```

The local transcription + speaker identification pipeline does **not** need any API key.

## 📖 Usage

### Process a session end-to-end

```bash
VIDEO_ID="ryDfRcIyOJI"

# 1. Download video (audio + video)
yt-dlp -f 'bestvideo[ext=mp4]+bestaudio[ext=m4a]' \
  "https://www.youtube.com/watch?v=$VIDEO_ID" \
  -o "data/video/$VIDEO_ID.mp4"

# 2. Transcribe (diarization + ASR via local servers)
python scripts/pipeline/03_transcribe_local.py \
  --video-id $VIDEO_ID \
  --audio-file data/video/$VIDEO_ID.mp4 \
  --output data/sessions/$VIDEO_ID.json

# 3. Map speakers to real names (PaddleOCR + fuzzy match vs asambleistas.json)
python scripts/pipeline/04_map_speakers_local.py \
  --video-id $VIDEO_ID \
  --video-file data/video/$VIDEO_ID.mp4 \
  --session-file data/sessions/$VIDEO_ID.json

# 4. (optional) Topic classification
python scripts/pipeline/05_classify_topics.py --session-file data/sessions/$VIDEO_ID.json

# 5. Rebuild site data
python scripts/pipeline/06_generate_stats.py
python scripts/pipeline/07_build_search_index.py
python scripts/pipeline/08_update_catalog.py

# 6. Preview locally
cd docs && python3 -m http.server 8000
```

### Pipeline scripts

| Script | Description | Backend |
|---|---|---|
| `01_discover_videos.py` | (legacy) List new videos via YouTube API | YouTube API |
| `02_download_audio.py` | Extract audio | ffmpeg + yt-dlp |
| `03_transcribe_local.py` | Diarization + per-segment ASR | pyannote + faster-whisper (local) |
| `04_map_speakers_local.py` | Overlay OCR → fuzzy match → speaker names | PaddleOCR (local CPU) |
| `05_classify_topics.py` | Topic classification | OpenAI (legacy, optional) |
| `06_generate_stats.py` | Per-speaker, per-topic, per-month stats | pure Python |
| `07_build_search_index.py` | FlexSearch index for the frontend | pure Python |
| `08_update_catalog.py` | Master catalog of sessions | pure Python |

### Speaker mapping improvements

The OCR-based speaker mapper (`04_map_speakers_local.py`) has several quality safeguards:

- **N frames per speaker** sampled at the midpoint of the longest segments (not a single frame at first appearance) — fixes the lock-in-wrong-name-forever bug.
- **Lower-third overlay filter**: only reads text in the bottom band where Asamblea overlays live.
- **Unicode normalization + fuzzy match** against `data/speakers/asambleistas.json` — handles tildes (`Lucía`/`Lucia`), OCR typos (`Olimedo`→`Olmedo`), and partial reads (`Mario Godoy`→`Mario Godoy Naranjo` via surname-token match).
- **Per-frame majority vote** + agreement-ratio-based confidence (no hardcoded `1.0`).
- **OOV proposals**: if OCR consistently reads a name not in the DB (≥2 reads, score ≥0.95), it is suggested for review in a `*_oov_proposals.json` file instead of silently failing.

## 🏗️ Project Structure

```
asamblea-abierta/
├── data/
│   ├── sessions/          # Processed session JSONs (1 per video)
│   ├── stats/             # Aggregated statistics
│   ├── speakers/          # asambleistas.json (canonical name DB)
│   ├── topics/            # Topic taxonomy
│   ├── video/             # Downloaded videos (gitignored)
│   └── catalog.json       # Master index of all sessions
├── docs/                  # Static site published to GitHub Pages
├── scripts/pipeline/      # Processing scripts
├── temp/                  # Intermediate audio/frames (gitignored)
├── config.yml             # Legacy config (gitignored, optional)
├── requirements.txt
└── README.md
```

## 📐 Data Format

Each session in `data/sessions/<video_id>.json`:

```json
{
  "id": "VIDEO_ID",
  "title": "Sesión Extraordinaria…",
  "date": "2026-01-05T19:12:45Z",
  "duration": 13947.16,
  "source_url": "https://youtube.com/watch?v=...",
  "speakers_detected": 17,
  "segments": [
    {
      "start": 88.27,
      "end": 111.74,
      "text": "Esperamos que tengan un excelente año...",
      "speaker_id": "speaker_1",
      "speaker": {
        "id": "speaker_1",
        "name": "Adrián Castro",
        "confidence": 1.0
      }
    }
  ],
  "speaker_stats": {
    "Adrián Castro": { "segments": 19, "duration": 612.4, "word_count": 1340 }
  },
  "classification": { "summary": "...", "topics": [...], "keywords": [...] }
}
```

## ⚖️ Legal Framework

This project is **legally sound** according to:

- ✅ **LOTAIP 2023**: Law on Transparency and Access to Public Information
- ✅ **Constitution Art. 18**: Right of access to public information
- ✅ **Public Domain**: Legislative documents excluded from copyright
- ✅ **LOPDP**: Data of public officials is exempt from personal data protection in the exercise of their functions

## 🤝 Contributing

Contributions are welcome! Areas where you can help:

- 🎨 Improve website design
- 📊 Add new data visualizations
- 🔍 Improve speaker identification (more samples per speaker, better OCR prompts, multimodal verification)
- 📝 Add more historical sessions
- 🌐 Translate to other languages (Kichwa, English)
- 🐛 Report bugs or improvements

### Process

1. Fork the repository
2. Create a branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: add amazing feature'`)
4. Push to the branch
5. Open a Pull Request

## 📜 License

This project is dedicated to the public domain under the [CC0 1.0 Universal](LICENSE) license. All generated data (transcripts, statistics, etc.) is in the public domain.

## 🙏 Acknowledgments

Inspired by global parliamentary transparency projects:

- [TheyWorkForYou](https://www.theyworkforyou.com/) (UK)
- [OpenParliament](https://openparliament.ca/) (Canada)
- [Congreso Visible](https://congresovisible.uniandes.edu.co/) (Colombia)
- [ParlaMint](https://www.clarin.eu/parlamint) (EU)

Technologies & models:

- **Ecuador's National Assembly** for publishing sessions on YouTube
- **pyannote community-1** (Hervé Bredin et al.) for open speaker diarization
- **faster-whisper / Whisper large-v3-turbo** (OpenAI + SYSTRAN) for ASR
- **PaddleOCR** for offline text recognition

## 📞 Contact

- 🐛 Issues: [GitHub Issues](https://github.com/luisreinoso/asamblea-abierta/issues)
- 💬 Discussions: [GitHub Discussions](https://github.com/luisreinoso/asamblea-abierta/discussions)

---

*AI-generated transcripts from public legislative sessions — produced entirely with open, locally-hosted models.*
