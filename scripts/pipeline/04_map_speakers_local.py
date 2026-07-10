#!/usr/bin/env python3
"""
04_map_speakers_local.py

Map diarization speaker_ids to real names using PaddleOCR on video frames.

Improvements over the previous OpenAI-Vision script:
  • Samples N frames per speaker (default 7) spread across all appearances
    instead of one — fixes wrong-name-locked-in-forever bug.
  • Filters detections to the lower-third overlay band where speaker names
    appear in Asamblea sessions.
  • Validates names against data/speakers/asambleistas.json with Unicode
    normalization + fuzzy matching → eliminates "Lucía vs Lucia" duplicates.
  • Uses majority vote with agreement-ratio-based confidence (no hardcoded 1.0).
  • Drops segments under a confidence threshold ("No identificado").

Runs on CPU (PaddleOCR). No external API calls. No GPU contention.
"""

import argparse
import json
import re
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from paddleocr import PaddleOCR

PROJECT_ROOT = Path(__file__).parent.parent.parent
SPEAKERS_DB = PROJECT_ROOT / "data" / "speakers" / "asambleistas.json"


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------
def extract_frame(video_path: Path, timestamp: float, output_path: Path) -> bool:
    cmd = [
        "ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(video_path),
        "-vframes", "1", "-q:v", "2", str(output_path),
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    return r.returncode == 0 and output_path.exists()


def get_video_duration(video_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return float(out.stdout.strip())


# ---------------------------------------------------------------------------
# Speakers DB + fuzzy matching
# ---------------------------------------------------------------------------
# Spanish function words that show up constantly as OCR fragments of overlay
# banners ("... DE LA ...", "Y OCEANÍA") but are never distinguishing on
# their own — must not count as a "surname" for tail-overlap matching.
SPANISH_STOPWORDS = {
    "de", "la", "el", "los", "las", "y", "del", "en", "por", "para",
}


def normalize_name(name: str) -> str:
    """Strip accents, lowercase, collapse whitespace, drop punctuation."""
    nfkd = unicodedata.normalize("NFKD", name)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    cleaned = re.sub(r"[^\w\s]", " ", no_accents.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def load_speakers_db() -> list[dict]:
    if not SPEAKERS_DB.exists():
        return []
    with open(SPEAKERS_DB, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("asambleistas", [])


def build_name_lookup(speakers: list[dict]) -> dict[str, dict]:
    """Build {normalized_name: speaker_dict} including alternate_names."""
    lookup = {}
    for s in speakers:
        keys = [s["name"], *s.get("alternate_names", [])]
        for k in keys:
            lookup[normalize_name(k)] = s
    return lookup


def match_name(candidate: str, lookup: dict[str, dict], threshold: float = 0.80) -> tuple[str | None, float]:
    """Return (canonical_name, similarity). None if no match above threshold.

    Strategy:
      1. Exact normalized (accent-stripped, lowercased) match.
      2. Full-string fuzzy match.
      3. Surname-only fuzzy match — handles cases where OCR reads "Maria Camila Zurita"
         and DB has "María Camila Zurita Salazar", or vice versa.
    """
    norm = normalize_name(candidate)
    if not norm:
        return None, 0.0
    if norm in lookup:
        return lookup[norm]["name"], 1.0

    best_name, best_score = None, 0.0
    for key, speaker in lookup.items():
        score = SequenceMatcher(None, norm, key).ratio()
        if score > best_score:
            best_score = score
            best_name = speaker["name"]

    # Surname-token overlap: if last 1-2 words of the candidate match last 1-2
    # of any DB entry, boost. Handles partial OCR reads — but ONLY when both
    # tail words are substantial (>=3 chars, not a bare function word like
    # "de"/"la"). Without this guard, a fragment like "DE LA" false-matched
    # any roster entry ending in "... de la" (e.g. "Torre de la Torre
    # Fernando de la") with 0.95 confidence — found 2026-07-10 on a full
    # session where that exact fragment got assigned to 7 different
    # speakers.
    cand_words = norm.split()
    if len(cand_words) >= 2:
        cand_tail_words = cand_words[-2:]
        if all(len(w) >= 3 and w not in SPANISH_STOPWORDS for w in cand_tail_words):
            cand_tail = " ".join(cand_tail_words)
            for key, speaker in lookup.items():
                key_words = key.split()
                if len(key_words) >= 2 and " ".join(key_words[-2:]) == cand_tail:
                    return speaker["name"], max(best_score, 0.95)

    if best_score >= threshold:
        return best_name, best_score
    return None, best_score


# ---------------------------------------------------------------------------
# Title-derived name fallback
# ---------------------------------------------------------------------------
# Many of these videos are single-intervention clips whose YouTube title
# already names the speaker (e.g. "Asambleísta Adrián Castro - Sesión 106",
# "Assemblywoman Ana Belén - Session 105"). That's a free, high-value signal
# the OCR-only pipeline wasn't using at all. Applied ONLY to the single
# speaker who dominates the video's runtime, and NEVER overrides a confident
# OCR ("db") match — it's a fallback for videos the overlay never caught.
TITLE_NAME_RE = re.compile(
    r"^(?:Asambleísta|Asambleista|Assemblyman|Assemblywoman)\s+(.+)$",
    re.IGNORECASE,
)


def extract_name_from_title(title: str) -> str | None:
    if not title:
        return None
    m = TITLE_NAME_RE.match(title.strip())
    if not m:
        return None
    # Cut off everything from " - " onward (session number, "II Intervención",
    # "Punto de información", "Cambio del orden del día", etc).
    name = re.split(r"\s+-\s+", m.group(1))[0].strip()
    return name or None


def apply_title_fallback(
    mapping: dict[str, dict],
    segments: list[dict],
    title: str | None,
    lookup: dict[str, dict],
    min_dominance: float = 0.5,
    fallback_confidence: float = 0.75,
) -> None:
    """Mutates `mapping` in place: if the title names a real asambleísta and
    that person isn't already confidently OCR-identified, assign the name to
    whichever speaker_id accounts for most of the video's talk time."""
    candidate = extract_name_from_title(title or "")
    if not candidate:
        return
    canonical, _sim = match_name(candidate, lookup)
    if not canonical:
        return

    durations: dict[str | None, float] = defaultdict(float)
    total = 0.0
    for seg in segments:
        d = seg.get("end", 0) - seg.get("start", 0)
        durations[seg.get("speaker_id")] += d
        total += d
    if total <= 0 or not durations:
        return
    dominant_id, dominant_dur = max(durations.items(), key=lambda kv: kv[1])
    if dominant_id is None or dominant_dur / total < min_dominance:
        return

    existing = mapping.get(dominant_id)
    if existing and existing.get("source") == "db":
        return  # a confident OCR read wins, always

    mapping[dominant_id] = {
        "name": canonical,
        "confidence": fallback_confidence,
        "votes": (existing or {}).get("votes", {}),
        "samples": (existing or {}).get("samples", 0),
        "successful_reads": (existing or {}).get("successful_reads", 0),
        "source": "title",
    }
    print(f"  → title fallback: {dominant_id} = {canonical!r} (from title {title!r})")


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------
class OverlayReader:
    """PaddleOCR wrapper that returns names found in the lower-third overlay."""

    def __init__(self, lang: str = "es", overlay_band: tuple[float, float] = (0.70, 0.98)):
        self.ocr = PaddleOCR(lang=lang, enable_mkldnn=False)
        self.overlay_top, self.overlay_bottom = overlay_band

    def read(self, image_path: Path) -> list[tuple[str, float]]:
        """Return list of (text, score) found inside the overlay band."""
        try:
            results = self.ocr.predict(str(image_path))
        except Exception as e:
            print(f"    OCR error on {image_path.name}: {e}", file=sys.stderr)
            return []
        out = []
        for r in results:
            txts = r.get("rec_texts", [])
            scores = r.get("rec_scores", [])
            polys = r.get("rec_polys", [])
            # Detect image height from polygon coords
            all_ys = [pt[1] for p in polys for pt in p]
            if not all_ys:
                continue
            img_height = max(max(all_ys), 1)
            y_min = img_height * self.overlay_top
            y_max = img_height * self.overlay_bottom
            for text, score, poly in zip(txts, scores, polys):
                ys = [pt[1] for pt in poly]
                y_mid = sum(ys) / len(ys)
                if y_min <= y_mid <= y_max and score >= 0.6:
                    out.append((text.strip(), float(score)))
        return out


# Known non-name UI chrome the broadcast overlay shows instead of a speaker's
# name (session status banners, generic placeholders) — confirmed false
# positives from real data (e.g. "En Pleno" got voted in as a fake "speaker"
# across 36 sessions before this filter existed).
NON_NAME_PHRASES = {
    "en pleno", "en vivo", "pleno", "comision general", "comisión general",
    "asamblea nacional", "asamblea nacional del ecuador", "del cisne",
    "samblea nacional", "isamblea nacional", "amblea naciona",
}
NON_NAME_PREFIXES = (
    "asambleísta por", "asambleista por", "asambleísta nacional", "asambleista nacional",
    # OCR often only catches the province half of "Asambleísta Por <X>" —
    # no real Spanish name starts with "por", safe to blanket-reject.
    "por ",
    # Recurring full-session broadcast chrome (title card, social handle
    # lower-third) that isn't tied to any one speaker — found 2026-07-10 on
    # a 2h+ plenary recording, attributed to 5 different diarization
    # clusters because it's on-screen for large stretches regardless of
    # who's talking.
    "continuacion de la", "continuación de la", "asambleanacional",
)


# Heuristic: speaker overlays look like person names: 2+ words, mostly letters,
# no digits, < 60 chars. Filters out things like "ASAMBLEA NACIONAL", subtitles,
# and timestamps.
def looks_like_person_name(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 60:
        return False
    if any(ch.isdigit() for ch in t):
        return False
    # A bare "-" (with or without surrounding space) means this is a
    # fragment of a "Name - Provincia - Partido" banner where only the
    # province/party half got read cleanly (e.g. "- RC", "Cotopaxi -",
    # "Nacional - Rc") — not a name. Found 2026-07-10 on a full session
    # where several of these got accepted as OOV "speaker" proposals.
    if re.search(r"(^|\s)-(\s|$)", t):
        return False
    words = [w for w in re.split(r"\s+", t) if w]
    if len(words) < 2:
        return False
    # Real Spanish names are essentially never two bare 1-2 letter tokens
    # ("DE LA", "Y A") — that's a mid-sentence fragment, not a name.
    if all(len(w) <= 2 for w in words):
        return False
    upper_words = sum(1 for w in words if w.isupper())
    if upper_words == len(words) and len(t) > 25:
        # All-caps long strings are usually titles/lower-thirds banners, not names
        return False
    norm = normalize_name(t)
    if norm in NON_NAME_PHRASES or norm.startswith(NON_NAME_PREFIXES):
        return False
    # Fragment is mostly Spanish function words ("de la", "y del") — reject.
    norm_words = norm.split()
    if norm_words and all(w in SPANISH_STOPWORDS for w in norm_words):
        return False
    return True


# ---------------------------------------------------------------------------
# Sampling timestamps per speaker
# ---------------------------------------------------------------------------
def sample_timestamps_for_speaker(
    segments: list[dict],
    speaker_id: str,
    n_segments: int,
    video_duration: float,
    frames_per_segment: int = 10,
    min_spacing: float = 2.5,
    max_total_frames: int = 25,
) -> list[float]:
    """Pick many evenly-spaced timestamps from the speaker's longest segments.

    Sampling strategy (rewritten 2026-07 — the old version sampled only 2
    fixed points, 50% and 75% into a segment. That misses the overlay
    whenever it only appears briefly right after a camera cut, which is the
    common case — the name graphic fades in for a few seconds near the
    START of a speaker's turn, not throughout it. Fix: cover the WHOLE
    segment with evenly-spaced samples instead of guessing when the overlay
    is up):
      - Pick the N longest segments for this speaker (still the best odds of
        containing a stable overlay window).
      - Within each, sample evenly from just after the start to just before
        the end (a small margin avoids the camera-cut transition frame
        itself), spaced at least `min_spacing` seconds apart, capped at
        `frames_per_segment` per segment.
      - Stop once `max_total_frames` is reached across all segments, to keep
        OCR (CPU-bound) runtime bounded.
    """
    spk_segs = [s for s in segments if s.get("speaker_id") == speaker_id]
    if not spk_segs:
        return []

    spk_segs.sort(key=lambda s: s["end"] - s["start"], reverse=True)
    chosen = spk_segs[:n_segments]

    timestamps: list[float] = []
    for seg in chosen:
        start, end = seg["start"], seg["end"]
        margin = min(1.0, (end - start) * 0.1)
        span_start, span_end = start + margin, end - margin
        if span_end <= span_start:
            span_start, span_end = start, end

        n_points = max(1, min(frames_per_segment, int((span_end - span_start) / min_spacing) + 1))
        if n_points == 1:
            points = [(span_start + span_end) / 2]
        else:
            step = (span_end - span_start) / (n_points - 1)
            points = [span_start + i * step for i in range(n_points)]

        for t in points:
            timestamps.append(min(max(t, 0.0), video_duration - 1.0))
            if len(timestamps) >= max_total_frames:
                return timestamps

    return timestamps


# ---------------------------------------------------------------------------
# Main mapping
# ---------------------------------------------------------------------------
def _canonicalize_oov(name: str) -> str:
    """Canonicalize an OOV OCR name for grouping: title-case, NFC-normalized,
    whitespace collapsed. Preserves Spanish accents (does NOT strip)."""
    nfc = unicodedata.normalize("NFC", name).strip()
    nfc = re.sub(r"\s+", " ", nfc)
    return nfc.title()


def build_speaker_mapping(
    video_path: Path,
    segments: list[dict],
    reader: OverlayReader,
    lookup: dict[str, dict],
    frames_dir: Path,
    samples_per_speaker: int = 3,
    confidence_threshold: float = 0.60,
    oov_min_reads: int = 2,
    oov_min_ocr_score: float = 0.95,
    max_frames_per_speaker: int = 25,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Build speaker→name mapping plus a list of OOV name candidates.

    Returns:
        (mapping, oov_proposals) where:
          mapping[speaker_id] = {name, confidence, votes, samples, successful_reads, source}
          oov_proposals[oov_name] = {count, avg_score, speakers, sample_segments}
                                    — names seen ≥oov_min_reads times not in DB.
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    duration = get_video_duration(video_path)
    speaker_ids = sorted({s["speaker_id"] for s in segments if s.get("speaker_id")})
    mapping: dict[str, dict] = {}
    oov_proposals: dict[str, dict] = {}

    for spk_id in speaker_ids:
        print(f"\nSpeaker {spk_id}")
        timestamps = sample_timestamps_for_speaker(
            segments, spk_id, samples_per_speaker, duration,
            max_total_frames=max_frames_per_speaker,
        )
        print(f"  sampling {len(timestamps)} frames")

        # Each detection from each frame: (ts, raw_text, score, matched_canonical, similarity)
        raw_detections: list[tuple[float, str, float, str | None, float]] = []
        # Per-frame "best name" — one vote per frame to reduce noise from
        # multiple overlay regions in the same frame.
        # Tuple: (ts, db_name, db_score, oov_name, oov_score)
        frame_votes: list[tuple[float, str | None, float, str | None, float]] = []

        for ts in timestamps:
            frame_path = frames_dir / f"{spk_id}_{int(ts):06d}.jpg"
            if not frame_path.exists() and not extract_frame(video_path, ts, frame_path):
                continue
            detections = reader.read(frame_path)

            best_db_name, best_db_score = None, 0.0
            best_oov_name, best_oov_score = None, 0.0
            for text, score in detections:
                if not looks_like_person_name(text):
                    continue
                canonical, sim = match_name(text, lookup)
                raw_detections.append((ts, text, score, canonical, sim))
                if canonical and score > best_db_score:
                    best_db_name, best_db_score = canonical, score
                elif not canonical and score > best_oov_score:
                    best_oov_name = _canonicalize_oov(text)
                    best_oov_score = score
            frame_votes.append((ts, best_db_name, best_db_score, best_oov_name, best_oov_score))

        # Aggregate one vote per frame. DB match takes priority; if no DB match,
        # accept OOV if its OCR score is high enough.
        db_votes: Counter[str] = Counter()
        oov_votes: Counter[str] = Counter()
        oov_scores: dict[str, list[float]] = defaultdict(list)
        for ts, db_name, db_score, oov_name, oov_score in frame_votes:
            if db_name:
                db_votes[db_name] += 1
            elif oov_name and oov_score >= oov_min_ocr_score:
                oov_votes[oov_name] += 1
                oov_scores[oov_name].append(oov_score)

        # Decide: DB match wins if it has enough agreement; otherwise consider OOV.
        total_voting_frames = len([f for f in frame_votes if f[1] or f[3]])
        chosen_name = None
        chosen_conf = 0.0
        source = None

        if db_votes:
            top_db, top_count = db_votes.most_common(1)[0]
            db_conf = top_count / max(total_voting_frames, 1)
            if db_conf >= confidence_threshold:
                chosen_name = top_db
                chosen_conf = db_conf
                source = "db"

        # Record ALL OOV detections as proposals (even if not chosen as mapping
        # because of low count) — the user can review and approve them.
        for oov_name, count in oov_votes.items():
            prop = oov_proposals.setdefault(oov_name, {
                "count": 0, "scores": [], "speakers": [],
            })
            prop["count"] += count
            prop["scores"].extend(oov_scores[oov_name])
            if spk_id not in prop["speakers"]:
                prop["speakers"].append(spk_id)

        if not chosen_name and oov_votes:
            top_oov, top_count = oov_votes.most_common(1)[0]
            oov_conf = top_count / max(total_voting_frames, 1)
            if top_count >= oov_min_reads and oov_conf >= confidence_threshold:
                chosen_name = top_oov
                chosen_conf = oov_conf
                source = "ocr-oov"

        if chosen_name:
            mapping[spk_id] = {
                "name": chosen_name,
                "confidence": round(chosen_conf, 2),
                "votes": {**dict(db_votes), **{f"[OOV] {k}": v for k, v in oov_votes.items()}},
                "samples": len(timestamps),
                "successful_reads": sum(db_votes.values()) + sum(oov_votes.values()),
                "source": source,
            }
            tag = "" if source == "db" else "  [OOV — propose adding to DB]"
            print(f"  → {chosen_name}  (conf={chosen_conf:.2f}, source={source}){tag}")
        else:
            mapping[spk_id] = {
                "name": "No identificado",
                "confidence": 0.0,
                "votes": {**dict(db_votes), **{f"[OOV] {k}": v for k, v in oov_votes.items()}},
                "samples": len(timestamps),
                "successful_reads": sum(db_votes.values()) + sum(oov_votes.values()),
                "source": None,
            }
            if db_votes or oov_votes:
                print(f"  → No identificado (votes={dict(db_votes)}, oov={dict(oov_votes)})")
            else:
                print(f"  → No identificado (no overlay detected in {len(timestamps)} frames)")

        # Debug log
        for ts, text, score, canonical, sim in raw_detections[:3]:
            print(f"    [{int(ts):5d}s] OCR={text!r} (s={score:.2f}) → {canonical} (sim={sim:.2f})")

    # Finalize OOV proposals
    final_oov = {}
    for name, prop in oov_proposals.items():
        scores = prop["scores"]
        final_oov[name] = {
            "count": prop["count"],
            "avg_ocr_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
            "matched_speakers": prop["speakers"],
        }

    return mapping, final_oov


def apply_mapping_to_session(session_path: Path, mapping: dict[str, dict], output_path: Path) -> None:
    with open(session_path, encoding="utf-8") as f:
        session = json.load(f)
    for seg in session.get("segments", []):
        spk_id = seg.get("speaker_id")
        info = mapping.get(spk_id, {"name": "No identificado", "confidence": 0.0})
        seg["speaker"] = {
            "id": spk_id or "UNKNOWN",
            "name": info["name"],
            "confidence": info["confidence"],
        }
    session["speaker_mapping"] = mapping
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Map speaker_ids to real names with PaddleOCR")
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--session-file", required=True, help="Session JSON (output of 03_transcribe_local.py)")
    parser.add_argument("--video-file", help="Path to video (default: data/video/<id>.mp4)")
    parser.add_argument("--output", help="Output JSON path (default: overwrites --session-file)")
    parser.add_argument("--samples", type=int, default=3, help="Longest segments inspected per speaker (each densely sampled — see sample_timestamps_for_speaker)")
    parser.add_argument("--title", default=None, help="Video title, used as a name-detection fallback for the dominant speaker (e.g. 'Asambleísta X - Sesión N')")
    parser.add_argument("--threshold", type=float, default=0.60, help="Min agreement ratio to accept a name")
    args = parser.parse_args()

    video_path = Path(args.video_file) if args.video_file else PROJECT_ROOT / "data" / "video" / f"{args.video_id}.mp4"
    if not video_path.exists():
        print(f"ERROR: video not found at {video_path}", file=sys.stderr)
        return 1

    with open(args.session_file, encoding="utf-8") as f:
        session = json.load(f)
    segments = session.get("segments", [])
    if not segments:
        print("ERROR: session has no segments", file=sys.stderr)
        return 1

    print(f"Loading PaddleOCR…")
    t0 = time.time()
    reader = OverlayReader(lang="es")
    print(f"  ready ({time.time()-t0:.1f}s)")

    speakers_db = load_speakers_db()
    lookup = build_name_lookup(speakers_db)
    print(f"Speakers DB: {len(speakers_db)} known asambleístas, {len(lookup)} lookup keys")

    # Scale the per-speaker OCR frame budget by how many distinct speakers
    # are in this video. A short clip with 1-3 speakers can afford up to 25
    # dense frames each; a full plenary session with 20-80 speakers cannot
    # (found the hard way 2026-07-10: 23 speakers x 25 frames took ~90min of
    # CPU-bound OCR alone). Total budget is shared across speakers, clamped
    # to a sane per-speaker floor/ceiling.
    n_speakers = len({s["speaker_id"] for s in segments if s.get("speaker_id")}) or 1
    total_frame_budget = 150
    max_frames_per_speaker = max(5, min(25, total_frame_budget // n_speakers))
    print(f"{n_speakers} distinct speakers → up to {max_frames_per_speaker} OCR frames each")

    frames_dir = PROJECT_ROOT / "temp" / "frames" / args.video_id
    mapping, oov_proposals = build_speaker_mapping(
        video_path, segments, reader, lookup, frames_dir,
        samples_per_speaker=args.samples,
        confidence_threshold=args.threshold,
        max_frames_per_speaker=max_frames_per_speaker,
    )
    apply_title_fallback(mapping, segments, args.title, lookup)

    output_path = Path(args.output) if args.output else Path(args.session_file)
    apply_mapping_to_session(Path(args.session_file), mapping, output_path)

    # Persist OOV proposals to temp/ — these are review artifacts, not site data.
    if oov_proposals:
        proposals_dir = PROJECT_ROOT / "temp" / "oov_proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        proposals_path = proposals_dir / f"{output_path.stem}_oov_proposals.json"
        with open(proposals_path, "w", encoding="utf-8") as f:
            json.dump(oov_proposals, f, ensure_ascii=False, indent=2)
        print(f"✓ Wrote OOV proposals: {proposals_path}")

    print(f"\n✓ Wrote {output_path}")

    # Summary
    print("\n=== Summary ===")
    identified = sum(1 for m in mapping.values() if m["name"] != "No identificado")
    db_hits = sum(1 for m in mapping.values() if m.get("source") == "db")
    oov_hits = sum(1 for m in mapping.values() if m.get("source") == "ocr-oov")
    print(f"  Identified: {identified}/{len(mapping)} speakers  (db={db_hits}, oov={oov_hits})")
    for spk_id, info in mapping.items():
        src = info.get("source") or "—"
        print(f"  {spk_id:12s}  {info['name']:30s}  conf={info['confidence']:.2f}  src={src}  reads={info['successful_reads']}")

    if oov_proposals:
        print("\n=== OOV name proposals (not in DB, detected consistently) ===")
        for name, p in sorted(oov_proposals.items(), key=lambda kv: -kv[1]["count"]):
            print(f"  '{name}'  count={p['count']}  avg_score={p['avg_ocr_score']}  speakers={p['matched_speakers']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
