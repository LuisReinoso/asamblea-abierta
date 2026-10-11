"""Whisper server — FastAPI + faster-whisper.

Reference implementation of the ASR service the pipeline calls
(scripts/pipeline/03_transcribe_local.py). Any server that honours the same
contract works as a drop-in replacement.

Exposes:
  GET  /            — health check
  POST /transcribe  — upload audio file, get text + timed segments
                      Optional query param: language (default WHISPER_LANGUAGE)
"""

import gc
import logging
import os
import tempfile
from pathlib import Path

import torch
from fastapi import FastAPI, File, HTTPException, UploadFile
from faster_whisper import WhisperModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("whisper-server")

MODEL_ID = os.environ.get("WHISPER_MODEL", "deepdml/faster-whisper-large-v3-turbo-ct2")
DEVICE = os.environ.get("WHISPER_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE", "int8_float16" if DEVICE == "cuda" else "int8")
DEFAULT_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "es")

logger.info("Loading model %s (device=%s, compute=%s)…", MODEL_ID, DEVICE, COMPUTE_TYPE)
model = WhisperModel(MODEL_ID, device=DEVICE, compute_type=COMPUTE_TYPE)
logger.info("Model loaded.")

app = FastAPI(title="Whisper Server (faster-whisper)")


@app.get("/")
def health():
    return {"status": "ok", "model": MODEL_ID, "device": DEVICE, "compute_type": COMPUTE_TYPE}


@app.post("/transcribe")
def transcribe(file: UploadFile = File(...), language: str | None = None):
    """Transcribe an uploaded audio file (wav/mp3/ogg/flac/webm).

    Returns:
        {
          "text": str,
          "segments": [{"start": float, "end": float, "text": str, "no_speech_prob": float}, ...]
        }
    """
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(file.file.read())
        tmp.flush()
        try:
            segments_iter, _info = model.transcribe(
                tmp.name, language=language or DEFAULT_LANGUAGE, beam_size=5, vad_filter=True,
            )
            segments = [
                {
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": seg.text,
                    "no_speech_prob": round(seg.no_speech_prob, 3),
                }
                for seg in segments_iter
            ]
        except Exception as e:
            logger.exception("Transcription failed")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            gc.collect()
            if DEVICE == "cuda":
                torch.cuda.empty_cache()
    text = " ".join(s["text"].strip() for s in segments).strip()
    return {"text": text, "segments": segments}
