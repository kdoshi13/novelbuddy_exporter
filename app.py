import os
import re
import tempfile
import wave
import json
import threading
from io import BytesIO
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

from flask import Flask, jsonify, request, send_file, send_from_directory

from scrape_io import (
    BASE_URL,
    fetch_props,
    get_build_id,
    html_to_text,
    parse_ch_num,
    save_docx,
    save_pdf,
)
import json as _json
from zipfile import ZipFile
from pathlib import Path as _Path

# --------------- Piper TTS setup ---------------
# Try to import Piper; if not available (e.g., on serverless), disable TTS
TTS_AVAILABLE = False
try:
    from piper import PiperVoice
    from piper.download_voices import download_voice
    TTS_AVAILABLE = True
except ImportError:
    PiperVoice = None
    download_voice = None

VOICES_DIR = Path(__file__).resolve().parent / "piper_voices"
VOICES_DIR.mkdir(exist_ok=True)

ROOT_DIR = Path(__file__).resolve().parent

DEFAULT_VOICE = "en_US-lessac-medium"
PREVIEW_TEXT = "The sun dipped below the horizon, casting long shadows across the ancient stone walls of the castle."

_voice_cache = {}           # voice_id -> PiperVoice
_voice_cache_lock = threading.Lock()
_catalog_cache = None       # cached voices.json dict
_catalog_lock = threading.Lock()


def _model_path(voice_id: str) -> Path:
    return VOICES_DIR / f"{voice_id}.onnx"


def _ensure_voice(voice_id: str) -> Path:
    """Download the voice model if it's not already present and return model path."""
    model = _model_path(voice_id)
    if not model.exists():
        download_voice(voice_id, VOICES_DIR)
    return model


def _get_voice(voice_id: str) -> PiperVoice:
    """Return a cached PiperVoice, loading (and downloading) it if needed."""
    with _voice_cache_lock:
        if voice_id not in _voice_cache:
            model = _ensure_voice(voice_id)
            _voice_cache[voice_id] = PiperVoice.load(str(model))
        return _voice_cache[voice_id]


def _list_downloaded_voices():
    """Return a sorted list of voice IDs whose .onnx files exist locally."""
    return sorted(
        p.stem for p in VOICES_DIR.glob("*.onnx")
        if (VOICES_DIR / f"{p.stem}.onnx.json").exists()
    )


def _fetch_catalog():
    """Fetch and cache the full Piper voices catalog from HuggingFace."""
    global _catalog_cache
    with _catalog_lock:
        if _catalog_cache is None:
            from piper.download_voices import VOICES_JSON
            with urlopen(VOICES_JSON) as resp:
                _catalog_cache = json.load(resp)
        return _catalog_cache


def _voice_meta(voice_id, info):
    """Extract display metadata for a single voice."""
    parts = voice_id.split("-")
    lang = parts[0] if parts else ""
    name = parts[1] if len(parts) > 1 else voice_id
    quality = parts[2] if len(parts) > 2 else "medium"
    files = info.get("files", {})
    size_bytes = sum(f.get("size_bytes", 0) for f in files.values())
    return {
        "id": voice_id,
        "language": lang,
        "name": name.replace("_", " ").title(),
        "quality": quality,
        "size_mb": round(size_bytes / 1024 / 1024, 1),
        "downloaded": _model_path(voice_id).exists(),
    }
# ------------------------------------------------


app = Flask(__name__, static_folder=None)


CONTENT_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


def safe_download_name(name):
    name = re.sub(r'[\\/:*?"<>|]', "", name or "chapters").strip()
    return name or "chapters"


def export_bytes(text, file_format, base_name):
    if file_format == "txt":
        return text.encode("utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, f"{base_name}.{file_format}")
        if file_format == "docx":
            path = save_docx(text, path)
        else:
            path = save_pdf(text, path)
        with open(path, "rb") as f:
            return f.read()


@app.get("/")
def home():
    return send_from_directory(ROOT_DIR, "index.html")


@app.get("/app.js")
def app_js():
    return send_from_directory(ROOT_DIR, "app.js")


@app.get("/styles.css")
def styles_css():
    return send_from_directory(ROOT_DIR, "styles.css")


@app.post("/api/build")
def api_build():
    payload = request.get_json(silent=True) or {}
    novel_slug = (payload.get("novel_slug") or "").strip()
    if not novel_slug:
        return jsonify({"error": "Novel slug is required."}), 400

    try:
        return jsonify({"build_id": get_build_id(novel_slug)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/chapter")
def api_chapter():
    payload = request.get_json(silent=True) or {}
    novel_slug = (payload.get("novel_slug") or "").strip()
    chapter_slug = (payload.get("chapter_slug") or "chapter-1").strip()
    build_id = (payload.get("build_id") or "").strip()

    if not novel_slug:
        return jsonify({"error": "Novel slug is required."}), 400

    try:
        if not build_id:
            build_id = get_build_id(novel_slug)

        props = fetch_props(build_id, novel_slug, chapter_slug)
        if props is None:
            return jsonify({"error": f"Could not fetch {chapter_slug}."}), 502

        ch = props.get("initialChapter", {})
        next_info = props.get("nextChapter") or {}
        ch_slug = ch.get("slug", chapter_slug)
        ch_name = ch.get("name", chapter_slug)
        raw = ch.get("content", "")

        return jsonify({
            "build_id": build_id,
            "chapter": {
                "chapter_num": parse_ch_num(ch_name, ch_slug),
                "title": ch_name,
                "slug": ch_slug,
                "url": BASE_URL + ch.get("url", f"/{novel_slug}/{ch_slug}"),
                "word_count": ch.get("word_count", ""),
                "text": html_to_text(raw) if raw else "(No content)",
            },
            "next_slug": next_info.get("slug", "") if isinstance(next_info, dict) else "",
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/export")
def api_export():
    payload = request.get_json(silent=True) or {}
    text = payload.get("text") or ""
    file_format = (payload.get("format") or "txt").lower()
    base_name = safe_download_name(payload.get("filename"))

    if not text.strip():
        return jsonify({"error": "Nothing to export."}), 400
    if file_format not in CONTENT_TYPES:
        return jsonify({"error": "Format must be txt, docx, or pdf."}), 400

    try:
        data = export_bytes(text, file_format, base_name)
        return send_file(
            BytesIO(data),
            as_attachment=True,
            download_name=f"{base_name}.{file_format}",
            mimetype=CONTENT_TYPES[file_format],
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


# --------------- Piper TTS endpoints ---------------

@app.get("/api/tts/voices")
def api_tts_voices():
    """Return the list of downloaded voice models."""
    if not TTS_AVAILABLE:
        return jsonify({"error": "TTS not available in this deployment. Install piper-tts to enable."}), 503
    return jsonify({"voices": _list_downloaded_voices()})


@app.post("/api/tts/download")
def api_tts_download():
    """Download a new voice model by id (e.g. en_US-lessac-medium)."""
    if not TTS_AVAILABLE:
        return jsonify({"error": "TTS not available in this deployment."}), 503
    payload = request.get_json(silent=True) or {}
    voice_id = (payload.get("voice_id") or "").strip()
    if not voice_id:
        return jsonify({"error": "voice_id is required."}), 400
    try:
        _ensure_voice(voice_id)
        return jsonify({"status": "ok", "voice_id": voice_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/tts")
def api_tts():
    """
    Synthesize text to WAV audio using Piper TTS.
    Accepts JSON: { text, voice_id? }
    Returns: audio/wav binary.
    """
    if not TTS_AVAILABLE:
        return jsonify({"error": "TTS not available in this deployment."}), 503
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    voice_id = (payload.get("voice_id") or "").strip()

    if not text:
        return jsonify({"error": "No text provided."}), 400

    # Use first downloaded voice, or download default
    if not voice_id:
        downloaded = _list_downloaded_voices()
        voice_id = downloaded[0] if downloaded else DEFAULT_VOICE

    try:
        voice = _get_voice(voice_id)
    except Exception as exc:
        return jsonify({"error": f"Could not load voice '{voice_id}': {exc}"}), 500

    try:
        buf = BytesIO()
        with wave.open(buf, "wb") as wav_file:
            voice.synthesize_wav(text, wav_file)
        buf.seek(0)
        return send_file(buf, mimetype="audio/wav",
                         download_name="chapter.wav",
                         as_attachment=False)
    except Exception as exc:
        return jsonify({"error": f"Synthesis failed: {exc}"}), 500


@app.get("/api/tts/catalog")
def api_tts_catalog():
    """Return all available English voices with metadata."""
    if not TTS_AVAILABLE:
        return jsonify({"error": "TTS not available in this deployment."}), 503
    try:
        catalog = _fetch_catalog()
        en_voices = [
            _voice_meta(vid, catalog[vid])
            for vid in sorted(catalog)
            if vid.startswith("en_")
        ]
        return jsonify({"voices": en_voices})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---- Local file upload / chapter parsing ----
UPLOAD_DIR = ROOT_DIR / "tmp_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


def _save_upload(filestorage):
    name = re.sub(r'[^0-9A-Za-z._-]', '_', filestorage.filename or "upload")
    stem = Path(name).stem[:80] or "upload"
    suffix = Path(name).suffix
    target = UPLOAD_DIR / f"{stem}_{uuid4().hex[:8]}{suffix}"
    filestorage.save(str(target))
    return target


def _parse_txt(path: _Path):
    text = path.read_text(encoding='utf-8', errors='ignore').lstrip('\ufeff')
    # Split on lines that look like chapter headings
    parts = re.split(r'(?m)^(?:CHAPTER[:\s]|Chapter\s+\d+|CHAPTER\s+\d+).*$', text)
    # Fallback: if split produced one part, then try CHAPTER: markers
    if len(parts) <= 1:
        parts = re.split(r'(?m)^CHAPTER:\s*', text)

    chapters = []
    # If we used CHAPTER: split, the first part may be preface
    if len(parts) == 1:
        chapters.append({"title": "Full Text", "text": text})
        return chapters

    # Try to extract chapter titles by scanning lines
    lines = text.splitlines()
    titles = []
    for i, line in enumerate(lines):
        if re.match(r'^(?:CHAPTER[:\s]|Chapter\s+\d+|CHAPTER\s+\d+)', line.strip()):
            titles.append((i, line.strip()))

    if not titles:
        # Fall back to equal-sized chunks
        approx = max(1, len(parts))
        for i, p in enumerate(parts):
            chapters.append({"title": f"Part {i+1}", "text": p.strip()})
        return chapters

    # Build chapters based on line indices
    idx = 0
    for n, (line_no, title) in enumerate(titles):
        # find start index in text by counting lines
        start_line = line_no
        start_pos = sum(len(l) + 1 for l in lines[:start_line])
        # end is next title or EOF
        end_pos = None
        if n + 1 < len(titles):
            next_start = titles[n+1][0]
            end_pos = sum(len(l) + 1 for l in lines[:next_start])
        else:
            end_pos = len(text)
        chapter_text = text[start_pos:end_pos].strip()
        chapters.append({"title": title, "text": chapter_text})

    return chapters


def _parse_docx(path: _Path):
    try:
        from docx import Document
    except Exception:
        raise RuntimeError("python-docx is not installed")

    doc = Document(str(path))
    chapters = []
    current = {"title": None, "text": []}
    for p in doc.paragraphs:
        text = p.text.strip()
        style = getattr(p.style, 'name', '') if getattr(p, 'style', None) else ''
        is_heading = bool(style and style.lower().startswith('heading')) or bool(re.match(r'^(?:CHAPTER[:\s]|Chapter\s+\d+)', text))
        if is_heading and current["title"] is not None:
            chapters.append({"title": current["title"], "text": '\n'.join(current["text"]).strip()})
            current = {"title": text or "Chapter", "text": []}
        elif is_heading:
            current["title"] = text or "Chapter"
        else:
            current["text"].append(text)

    # flush
    if current["title"] is not None or current["text"]:
        chapters.append({"title": current["title"] or "Untitled", "text": '\n'.join(current["text"]).strip()})

    return chapters


def _parse_pdf(path: _Path):
    try:
        import PyPDF2
    except Exception:
        raise RuntimeError("PyPDF2 is not installed")
    reader = PyPDF2.PdfReader(str(path))
    text_parts = []
    for page in reader.pages:
        try:
            text_parts.append(page.extract_text() or "")
        except Exception:
            text_parts.append("")
    text = "\n".join(text_parts)
    # Reuse txt parsing heuristics
    fake = path.with_suffix('.txt')
    fake.write_text(text, encoding='utf-8')
    chapters = _parse_txt(fake)
    try:
        fake.unlink()
    except Exception:
        pass
    return chapters


@app.post('/api/upload')
def api_upload():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided."}), 400
    f = request.files['file']
    if not f or not f.filename:
        return jsonify({"error": "Invalid file."}), 400

    try:
        target = _save_upload(f)
        ext = target.suffix.lower().lstrip('.')
        if ext == 'txt':
            chapters = _parse_txt(target)
        elif ext == 'docx':
            chapters = _parse_docx(target)
        elif ext == 'pdf':
            chapters = _parse_pdf(target)
        else:
            return jsonify({"error": "Unsupported file type."}), 400

        meta = {"filename": target.name, "count": len(chapters)}
        # Save chapters to JSON for later retrieval
        meta_path = target.with_suffix(target.suffix + '.json')
        with open(meta_path, 'w', encoding='utf-8') as fh:
            _json.dump(chapters, fh, ensure_ascii=False)

        return jsonify({"status": "ok", "id": target.name, "chapters": [{"index": i, "title": c.get('title') or f'Chapter {i+1}', "len": len(c.get('text', ''))} for i, c in enumerate(chapters)]})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get('/api/upload/<id>/chapters')
def api_upload_chapters(id):
    file = UPLOAD_DIR / id
    meta = file.with_suffix(file.suffix + '.json')
    if not meta.exists():
        return jsonify({"error": "Uploaded file not found or not parsed."}), 404
    try:
        with open(meta, 'r', encoding='utf-8') as fh:
            chapters = _json.load(fh)
        return jsonify({"id": id, "count": len(chapters), "chapters": [{"index": i, "title": c.get('title') or f'Chapter {i+1}', "len": len(c.get('text',''))} for i, c in enumerate(chapters)]})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get('/api/upload/<id>/chapter/<int:index>')
def api_upload_chapter(id, index):
    file = UPLOAD_DIR / id
    meta = file.with_suffix(file.suffix + '.json')
    if not meta.exists():
        return jsonify({"error": "Uploaded file not found or not parsed."}), 404
    try:
        with open(meta, 'r', encoding='utf-8') as fh:
            chapters = _json.load(fh)
        if index < 0 or index >= len(chapters):
            return jsonify({"error": "Chapter index out of range."}), 400
        return jsonify({"index": index, "title": chapters[index].get('title'), "text": chapters[index].get('text', '')})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500



@app.post("/api/tts/preview")
def api_tts_preview():
    """
    Generate a short audio preview for a downloaded voice.
    Accepts JSON: { voice_id }
    Returns: audio/wav binary.
    """
    if not TTS_AVAILABLE:
        return jsonify({"error": "TTS not available in this deployment."}), 503
    payload = request.get_json(silent=True) or {}
    voice_id = (payload.get("voice_id") or "").strip()
    if not voice_id:
        return jsonify({"error": "voice_id is required."}), 400

    if not _model_path(voice_id).exists():
        return jsonify({"error": f"Voice '{voice_id}' is not downloaded yet."}), 400

    try:
        voice = _get_voice(voice_id)
        buf = BytesIO()
        with wave.open(buf, "wb") as wav_file:
            voice.synthesize_wav(PREVIEW_TEXT, wav_file)
        buf.seek(0)
        return send_file(buf, mimetype="audio/wav",
                         download_name=f"preview_{voice_id}.wav",
                         as_attachment=False)
    except Exception as exc:
        return jsonify({"error": f"Preview failed: {exc}"}), 500


@app.post("/api/tts/download-all")
def api_tts_download_all():
    """
    Download all English voices. Returns a streaming JSON log.
    """
    if not TTS_AVAILABLE:
        return jsonify({"error": "TTS not available in this deployment."}), 503
    try:
        catalog = _fetch_catalog()
        en_ids = sorted(vid for vid in catalog if vid.startswith("en_"))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    results = []
    for vid in en_ids:
        if _model_path(vid).exists():
            results.append({"voice_id": vid, "status": "already_downloaded"})
            continue
        try:
            download_voice(vid, VOICES_DIR)
            results.append({"voice_id": vid, "status": "downloaded"})
        except Exception as exc:
            results.append({"voice_id": vid, "status": "error", "error": str(exc)})

    return jsonify({"results": results, "total": len(en_ids)})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
