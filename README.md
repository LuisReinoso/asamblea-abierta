# Asamblea Abierta 🏛️

> Transparency platform for Ecuador's National Assembly

[![License: CC0-1.0](https://img.shields.io/badge/License-CC0_1.0-lightgrey.svg)](http://creativecommons.org/publicdomain/zero/1.0/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**Asamblea Abierta** transforms National Assembly plenary sessions into full, searchable transcripts with automatic speaker identification using artificial intelligence.

## 🎯 Objective

Provide Ecuadorian citizens with easy access and quick searches about what topics are discussed in each National Assembly session, who participates, and how much time they dedicate to each topic.

## ✨ Features

- ✅ **Automatic transcription** with diarization of up to 38 speakers using ElevenLabs AI
- ✅ **Speaker identification** through video analysis with OpenAI Vision API
- ✅ **Automatic topic classification** of sessions
- ✅ **Static website** with search and navigation by speaker, topic and date
- ✅ **YouTube links** with synchronized timestamps
- ✅ **Open data** in JSON format under CC0 1.0 license

## 🚀 How It Works

### Processing Pipeline

```
1. Video Download (YouTube) ──→ yt-dlp
   ↓
2. Audio Extraction (ffmpeg) ──→ .m4a
   ↓
3. Transcription + Diarization ──→ ElevenLabs Scribe v1
   ↓                                (38 speakers, 10 hours, 3GB)
4. Speaker Identification ──→ OpenAI Vision API
   ↓                          (reads on-screen name overlays)
5. Topic Classification ──→ OpenAI GPT-4o-mini
   ↓
6. Website Generation ──→ GitHub Pages
```

### Key Technologies

- **ElevenLabs Scribe v1**: Transcription with speaker diarization (up to 32 speakers, 3GB files, 10 hours)
- **OpenAI Vision API (gpt-4o-mini)**: Reads name overlays from video
- **OpenAI GPT-4**: Topic classification and summary generation
- **GitHub Pages**: Free website hosting
- **Python 3.11+**: Processing scripts
- **ffmpeg**: Video/audio processing

## 📊 Example Results

**Session from January 5, 2026:**
- ✅ 3.9 hours of video processed
- ✅ 38 unique speakers detected by voice
- ✅ 31 speakers identified by name (82% success rate)
- ✅ 220 transcription segments
- 💰 Total cost: ~$0.45 USD

## 💰 API Costs

For a typical **4-hour session** with **30-40 speakers**:

| Service | Usage | Cost |
|---------|-------|------|
| **ElevenLabs** (transcription) | 240 min × $0.001/min | **$0.24** |
| **OpenAI Vision** (identification) | ~40 frames × $0.00255 | **$0.10** |
| **OpenAI GPT** (classification) | ~1000 tokens | **$0.001** |
| **Total per session** | | **~$0.35 USD** |

### Implemented Optimizations

1. ✅ **One frame per speaker**: Only extracts frames for unique appearances of each speaker_id
2. ✅ **Smart retry**: Tries 5 different timestamps (+10s, +30s, +60s, +120s, +180s) only if needed
3. ✅ **Frame caching**: Doesn't re-extract existing frames
4. ✅ **Boundary detection**: Respects video duration to avoid errors

## 🛠️ Installation

### Prerequisites

- Python 3.11 or higher
- ffmpeg (for video/audio processing)
- yt-dlp (for downloading YouTube videos)
- API Keys from ElevenLabs and OpenAI

### Setup

1. **Clone the repository:**
```bash
git clone https://github.com/your-username/asamblea-abierta.git
cd asamblea-abierta
```

2. **Create virtual environment:**
```bash
python3 -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows
```

3. **Install dependencies:**
```bash
pip install -r requirements.txt
```

4. **Configure APIs:**
Create a `config.yml` file in the project root:
```yaml
openai:
  api_key: "your-openai-api-key"

elevenlabs:
  api_key: "your-elevenlabs-api-key"
```

5. **Install system tools:**
```bash
# Ubuntu/Debian
sudo apt install ffmpeg yt-dlp

# macOS
brew install ffmpeg yt-dlp

# Windows (using Chocolatey)
choco install ffmpeg yt-dlp
```

## 📖 Usage

### Process a Complete Session

```bash
# 1. Download video from YouTube
yt-dlp -f 'bestvideo[ext=mp4]+bestaudio[ext=m4a]' \
  'https://www.youtube.com/watch?v=VIDEO_ID' \
  -o 'data/video/VIDEO_ID.mp4'

# 2. Extract audio
ffmpeg -i data/video/VIDEO_ID.mp4 \
  -vn -acodec copy temp/audio/VIDEO_ID.m4a

# 3. Transcribe with ElevenLabs
python scripts/pipeline/03_transcribe_elevenlabs.py \
  --audio-path temp/audio/VIDEO_ID.m4a

# 4. Identify speakers with Vision API
python scripts/pipeline/04_map_speakers_vision.py \
  --video-id VIDEO_ID \
  --session-file data/sessions/VIDEO_ID.json

# 5. Classify topics
python scripts/pipeline/05_classify_session.py \
  --session-file data/sessions/VIDEO_ID.json

# 6. Generate statistics and catalog
python scripts/pipeline/06_generate_stats.py
python scripts/pipeline/08_update_catalog.py

# 7. Serve site locally
cd docs && python3 -m http.server 8000
```

### Pipeline Scripts

| Script | Description | Input | Output | Cost |
|--------|-------------|-------|--------|------|
| `01_discover_videos.py` | Download videos from YouTube | URL | `.mp4` | Free |
| `02_download_audio.py` | Extract audio from video | `.mp4` | `.m4a` | Free |
| `03_transcribe_elevenlabs.py` | Transcription + diarization | `.m4a` | `.json` | ~$0.06/min |
| `04_map_speakers_vision.py` | Identify names | `.mp4` + `.json` | `.json` | ~$0.003/frame |
| `05_classify_session.py` | Classify topics | `.json` | `.json` | ~$0.001 |
| `06_generate_stats.py` | Generate statistics | All `.json` | `stats/*.json` | Free |
| `08_update_catalog.py` | Update catalog | All `.json` | `catalog.json` | Free |

## 🏗️ Project Structure

```
asamblea-abierta/
├── data/
│   ├── sessions/          # Processed session JSONs
│   ├── stats/             # Aggregated statistics
│   ├── frames/            # Extracted frames for Vision API (not in git)
│   ├── video/             # Downloaded videos (not in git)
│   └── catalog.json       # Index of all sessions
├── docs/                  # Static website (GitHub Pages)
│   ├── index.html         # Home page
│   ├── sessions.html      # Session list
│   ├── session-detail.html # Session detail with transcript
│   ├── speakers.html      # Speaker list
│   ├── topics.html        # Topic list
│   └── assets/            # CSS, JS, images
├── scripts/
│   └── pipeline/          # Processing scripts
├── temp/
│   └── audio/             # Extracted audio (temporary)
├── config.yml             # API configuration (not in git)
├── requirements.txt       # Python dependencies
└── README.md             # This file
```

## 📐 Technical Architecture

### Speaker Identification - Hybrid Approach

The system uses a unique technical innovation:

1. **Voice-based diarization** (ElevenLabs):
   - Identifies `speaker_0` to `speaker_N` **consistently** throughout the session
   - `speaker_0` is ALWAYS the same person in the entire recording
   - Up to 32 simultaneous speakers

2. **Visual identification** (OpenAI Vision):
   - Reads name overlays that appear on screen
   - Maps each `speaker_id` to a real name
   - Only needs to analyze **one frame per speaker** (not per segment)

3. **Smart retry strategy**:
   - Tries multiple timestamps: +10s, +30s, +60s, +120s, +180s
   - Necessary because cameras take time to switch and show the overlay
   - For a 4-hour session with 38 speakers: only ~86 frames analyzed (vs 220 segments)

### Data Format

Each session is stored as JSON with this structure:

```json
{
  "id": "VIDEO_ID",
  "title": "Extraordinary Plenary Session...",
  "date": "2026-01-05T19:12:45Z",
  "duration": 13946.958,
  "source_url": "https://youtube.com/watch?v=...",
  "segments": [
    {
      "start": 10.5,
      "end": 15.3,
      "text": "Good morning everyone...",
      "speaker_id": "speaker_0",
      "speaker": {
        "id": "speaker_0",
        "name": "Mariela Logroño",
        "confidence": 1.0
      }
    }
  ],
  "speaker_stats": [
    {
      "id": "speaker_0",
      "name": "Mariela Logroño",
      "total_time": 1740.5,
      "interventions": 31
    }
  ],
  "classification": {
    "summary": "...",
    "topics": ["Justice and State Structure"],
    "keywords": ["oversight", "transparency"]
  }
}
```

## ⚖️ Legal Framework

This project is **legally sound** according to:

- ✅ **LOTAIP 2023**: Law on Transparency and Access to Public Information
- ✅ **Constitution Art. 18**: Right of access to public information
- ✅ **Public Domain**: Legislative documents excluded from copyright
- ✅ **LOPDP**: Data of public officials is exempt from personal data protection in the exercise of their functions

## 🤝 Contributing

Contributions are welcome! This is an open source project to improve democratic transparency.

### Areas where you can help:

- 🎨 Improve website design
- 📊 Add new data visualizations
- 🔍 Improve speaker identification algorithms
- 📝 Add more historical sessions
- 🌐 Translate to other languages (Kichwa, English)
- 🐛 Report bugs or improvements

### Process:

1. Fork the repository
2. Create a branch for your feature (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📜 License

This project is dedicated to the public domain under the [CC0 1.0 Universal](LICENSE) license.

All generated data (transcripts, statistics, etc.) is in the public domain and can be freely used for any purpose without restrictions.

## 🙏 Acknowledgments

Inspired by global parliamentary transparency projects:

- [TheyWorkForYou](https://www.theyworkforyou.com/) (UK)
- [OpenParliament](https://openparliament.ca/) (Canada)
- [Congreso Visible](https://congresovisible.uniandes.edu.co/) (Colombia)
- [ParlaMint](https://www.clarin.eu/parlamint) (EU)
- [Sinar Project](https://sinarproject.org/) (Malaysia)

Technologies:
- **Ecuador's National Assembly** for publishing sessions on YouTube
- **ElevenLabs** for their transcription API with speaker diarization
- **OpenAI** for Vision and GPT APIs
- Ecuador's open source community

## 📞 Contact

Questions? Suggestions? Want to collaborate?

- 🐛 Issues: [GitHub Issues](https://github.com/your-username/asamblea-abierta/issues)
- 💬 Discussions: [GitHub Discussions](https://github.com/your-username/asamblea-abierta/discussions)

## 🌟 Support the Project

If you find this project useful:
- ⭐ Star it on GitHub
- 🐦 Share on social media with #AsambleaAbierta
- 💬 Spread the word among civic organizations and journalists
- 🤝 Contribute with code or improvements

---

*AI-generated transcripts from public legislative sessions*
