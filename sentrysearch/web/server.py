"""FastAPI web server wrapping the SentrySearch pipeline."""

import os
import re
import shutil
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from ..store import get_data_root
from .admin_routes import router as admin_router
from .auth_db import init_auth_db, is_auth_enabled
from .auth_deps import require_search_access, require_write_access
from .auth_routes import router as auth_router

STATIC_DIR = Path(__file__).parent / "static"


def _session_secret() -> str:
    s = os.environ.get("OPTIMUS_SESSION_SECRET", "").strip()
    if s:
        return s
    return "dev-insecure-set-OPTIMUS_SESSION_SECRET"


def _upload_dir() -> Path:
    root = get_data_root()
    if root:
        return root / "uploads"
    return Path.home() / ".sentrysearch" / "uploads"


def _clips_dir() -> Path:
    root = get_data_root()
    if root:
        return root / "clips"
    return Path.home() / "sentrysearch_clips"


UPLOAD_DIR = _upload_dir()
CLIPS_DIR = _clips_dir()

app = FastAPI(title="Optimus Vision", docs_url="/api/docs")
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    same_site="lax",
    https_only=os.environ.get("OPTIMUS_SESSION_HTTPS_ONLY", "").lower() in ("1", "true", "yes"),
)

app.include_router(auth_router)
app.include_router(admin_router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
async def _startup():
    init_auth_db()


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    if not is_auth_enabled():
        return RedirectResponse("/", status_code=302)
    login_html = STATIC_DIR / "login.html"
    if not login_html.is_file():
        return HTMLResponse("login.html missing", status_code=500)
    return HTMLResponse(login_html.read_text())


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if is_auth_enabled() and not request.session.get("user"):
        return RedirectResponse("/login", status_code=302)
    return HTMLResponse((STATIC_DIR / "index.html").read_text())


# ------------------------------------------------------------------
# Stats
# ------------------------------------------------------------------

@app.get("/api/stats")
async def get_stats(_: dict = Depends(require_search_access)):
    from ..store import SentryStore, detect_backend

    backend = detect_backend() or "gemini"
    store = SentryStore(backend=backend)
    stats = store.get_stats()

    files = [
        {
            "path": f,
            "name": os.path.basename(f),
            "exists": os.path.exists(f),
        }
        for f in stats.get("source_files", [])
    ]
    return {
        "total_chunks": stats["total_chunks"],
        "unique_source_files": stats["unique_source_files"],
        "files": files,
        "backend": backend,
    }


# ------------------------------------------------------------------
# Upload & Index
# ------------------------------------------------------------------

@app.post("/api/index")
async def index_video(
    _: dict = Depends(require_write_access),
    file: UploadFile = File(...),
    chunk_duration: int = Form(30),
    overlap: int = Form(5),
    backend: str = Form("gemini"),
):
    """Upload a video file and index it."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    safe_name = file.filename.replace(" ", "_")
    save_path = UPLOAD_DIR / safe_name
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    from ..chunker import chunk_video, is_still_frame_chunk, preprocess_chunk
    from ..embedder import get_embedder, reset_embedder
    from ..store import SentryStore

    try:
        embedder = get_embedder(backend)
        store = SentryStore(backend=backend)
        abs_path = str(save_path.resolve())

        if store.is_indexed(abs_path):
            return {"status": "already_indexed", "file": abs_path, "chunks_indexed": 0}

        chunks = chunk_video(abs_path, chunk_duration=chunk_duration, overlap=overlap)
        embedded = []
        files_to_cleanup = []

        for chunk in chunks:
            if is_still_frame_chunk(chunk["chunk_path"]):
                files_to_cleanup.append(chunk["chunk_path"])
                continue

            embed_path = preprocess_chunk(chunk["chunk_path"])
            if embed_path != chunk["chunk_path"]:
                files_to_cleanup.append(embed_path)

            embedding = embedder.embed_video_chunk(embed_path)
            embedded.append({**chunk, "embedding": embedding})
            files_to_cleanup.append(chunk["chunk_path"])

        for f in files_to_cleanup:
            try:
                os.unlink(f)
            except OSError:
                pass
        if chunks:
            tmp_dir = os.path.dirname(chunks[0]["chunk_path"])
            shutil.rmtree(tmp_dir, ignore_errors=True)

        if embedded:
            store.add_chunks(embedded)

        return {
            "status": "indexed",
            "file": abs_path,
            "chunks_indexed": len(embedded),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        reset_embedder()


# ------------------------------------------------------------------
# Library (indexed files)
# ------------------------------------------------------------------

@app.delete("/api/files")
async def remove_file(
    _: dict = Depends(require_write_access),
    path: str = Query(...),
):
    from ..store import SentryStore, detect_backend

    backend = detect_backend() or "gemini"
    store = SentryStore(backend=backend)
    removed = store.remove_file(path)
    if removed == 0:
        raise HTTPException(status_code=404, detail="File not found in index")
    return {"removed_chunks": removed, "file": path}


# ------------------------------------------------------------------
# Search
# ------------------------------------------------------------------

@app.post("/api/search")
async def search(body: dict, _: dict = Depends(require_search_access)):
    from ..embedder import get_embedder, reset_embedder
    from ..search import search_footage
    from ..store import SentryStore, detect_backend

    query = body.get("query", "")
    n_results = body.get("n_results", 5)

    if not query.strip():
        raise HTTPException(status_code=400, detail="Query is required")

    try:
        backend = detect_backend() or "gemini"
        store = SentryStore(backend=backend)

        if store.get_stats()["total_chunks"] == 0:
            return {"query": query, "results": []}

        get_embedder(backend)
        results = search_footage(query, store, n_results=n_results)

        return {
            "query": query,
            "results": [
                {
                    "source_file": r["source_file"],
                    "filename": os.path.basename(r["source_file"]),
                    "start_time": r["start_time"],
                    "end_time": r["end_time"],
                    "score": round(r["similarity_score"], 4),
                }
                for r in results
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        reset_embedder()


# ------------------------------------------------------------------
# Trim & Clips
# ------------------------------------------------------------------

@app.post("/api/trim")
async def trim_result(body: dict, _: dict = Depends(require_write_access)):
    from ..trimmer import _safe_filename, trim_clip

    source_file = body.get("source_file")
    start_time = body.get("start_time")
    end_time = body.get("end_time")

    if not source_file or start_time is None or end_time is None:
        raise HTTPException(
            status_code=400,
            detail="source_file, start_time, and end_time are required",
        )

    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(source_file, start_time, end_time)
    output_path = str(CLIPS_DIR / filename)

    try:
        trim_clip(source_file, start_time, end_time, output_path)
        return {
            "filename": filename,
            "download_url": f"/api/clips/{filename}",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/clips")
async def list_clips(_: dict = Depends(require_search_access)):
    if not CLIPS_DIR.exists():
        return {"clips": []}
    clips = []
    for f in sorted(CLIPS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix == ".mp4":
            clips.append({
                "filename": f.name,
                "size_bytes": f.stat().st_size,
                "download_url": f"/api/clips/{f.name}",
            })
    return {"clips": clips}


@app.get("/api/clips/{filename}")
async def download_clip(request: Request, filename: str, _: dict = Depends(require_search_access)):
    clip_path = CLIPS_DIR / filename
    if not clip_path.exists():
        raise HTTPException(status_code=404, detail="Clip not found")
    return _range_file_response(request, clip_path)


# ------------------------------------------------------------------
# Video streaming (source files)
# ------------------------------------------------------------------

_VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov"}


@app.get("/api/video")
async def stream_video(
    request: Request,
    path: str = Query(...),
    _: dict = Depends(require_search_access),
):
    """Stream a source video file for in-browser playback."""
    video_path = Path(path)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk")
    if video_path.suffix.lower() not in _VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported video format")
    return _range_file_response(request, video_path)


def _range_file_response(request: Request, file_path: Path):
    """Serve a file with HTTP Range support for browser video playback."""
    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")

    if range_header:
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if not match:
            raise HTTPException(status_code=416, detail="Invalid Range header")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1

        def iterfile():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            iterfile(),
            status_code=206,
            media_type="video/mp4",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(length),
                "Accept-Ranges": "bytes",
            },
        )

    return FileResponse(
        str(file_path),
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes"},
    )
