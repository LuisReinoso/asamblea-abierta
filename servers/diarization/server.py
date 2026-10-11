"""Diarization server — FastAPI + pyannote community-1.

Exposes:
  GET  /            — health check
  POST /diarize     — upload audio file, get speaker segments
                      Optional query params: num_speakers, min_speakers, max_speakers
"""

import logging
import os
import tempfile
from pathlib import Path

import torch
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

# Pyannote checkpoints use pickled metadata that PyTorch 2.6+ rejects by default.
# We trust the model (downloaded from HF with our token), so force weights_only=False.
_original_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    kwargs["weights_only"] = False
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_torch_load

from pyannote.audio import Pipeline  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("diarization-server")

MODEL_ID = os.environ.get("DIARIZATION_MODEL", "pyannote/speaker-diarization-community-1")
DEVICE = os.environ.get("DIARIZATION_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
HF_TOKEN = os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HF_TOKEN")

if not HF_TOKEN:
    raise RuntimeError("HUGGINGFACE_TOKEN env var is required to load the pyannote model.")

logger.info("Loading model %s on %s…", MODEL_ID, DEVICE)
pipeline = Pipeline.from_pretrained(MODEL_ID, token=HF_TOKEN)
pipeline.to(torch.device(DEVICE))
logger.info("Model loaded.")

app = FastAPI(title="Diarization Server (pyannote community-1)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def health():
    return {"status": "ok", "model": MODEL_ID, "device": DEVICE}


@app.post("/diarize")
async def diarize(
    file: UploadFile = File(...),
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
):
    """Run diarization on uploaded audio.

    Returns:
        {
          "segments": [{"start": float, "end": float, "speaker": "SPEAKER_00"}, ...],
          "num_speakers": int
        }
    """
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        kwargs = {}
        if num_speakers is not None:
            kwargs["num_speakers"] = num_speakers
        else:
            if min_speakers is not None:
                kwargs["min_speakers"] = min_speakers
            if max_speakers is not None:
                kwargs["max_speakers"] = max_speakers

        logger.info("Diarizing %s (kwargs=%s)…", tmp_path, kwargs)
        output = pipeline(tmp_path, **kwargs)

        # pyannote 4.0 returns DiarizeOutput with two Annotations:
        #   speaker_diarization              — may overlap
        #   exclusive_speaker_diarization    — no overlaps (best for ASR slicing)
        segments = []
        speakers = set()
        for turn, _, speaker in output.exclusive_speaker_diarization.itertracks(yield_label=True):
            segments.append({
                "start": round(turn.start, 3),
                "end": round(turn.end, 3),
                "speaker": speaker,
            })
            speakers.add(speaker)

        overlap_segments = []
        for turn, _, speaker in output.speaker_diarization.itertracks(yield_label=True):
            overlap_segments.append({
                "start": round(turn.start, 3),
                "end": round(turn.end, 3),
                "speaker": speaker,
            })

        logger.info("Diarized into %d exclusive segments, %d unique speakers", len(segments), len(speakers))
        return {
            "segments": segments,
            "segments_with_overlap": overlap_segments,
            "num_speakers": len(speakers),
        }
    except Exception as e:
        logger.exception("Diarization failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# Run with: uvicorn server:app --host 0.0.0.0 --port 8001
