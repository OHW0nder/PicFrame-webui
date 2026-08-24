import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

from core.batch import generate_from_source
from core.domain.events import EventLevel, PipelineStage
from core.metadata import run_exif


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
JOB_ROOT = Path(os.getenv("PICFRAME_JOB_ROOT", "/data/output/jobs"))
OUTPUT_DIR = Path(os.getenv("PICFRAME_OUTPUT_DIR", "/data/output"))

ALLOWED_UPLOAD_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".heic",
    ".heif",
    ".tif",
    ".tiff",
}
MAX_FILE_SIZE = 512 * 1024 * 1024
MAX_TOTAL_SIZE = 4 * 1024 * 1024 * 1024
PREVIEW_MAX = 1600
THUMB_MAX = 320
PREVIEW_SOURCE_MAX = 720

TEMPLATES = [
    {
        "id": "info-portrait",
        "name": "信息卡 Portrait",
        "category": "Frames",
        "scheme": "scheme1",
        "layout": "portrait",
        "before": "/static/samples/source.jpg",
        "after": "/static/samples/info-portrait.jpg",
    },
    {
        "id": "info-landscape",
        "name": "信息卡 Landscape",
        "category": "Frames",
        "scheme": "scheme1",
        "layout": "landscape",
        "before": "/static/samples/source.jpg",
        "after": "/static/samples/info-landscape.jpg",
    },
    {
        "id": "brand-watermark",
        "name": "品牌水印",
        "category": "Watermarks",
        "scheme": "scheme2",
        "layout": "watermark_right_logo",
        "before": "/static/samples/source.jpg",
        "after": "/static/samples/watermark.jpg",
    },
    {
        "id": "terminal",
        "name": "终端框",
        "category": "Editorial",
        "scheme": "scheme3",
        "layout": "gallery_ascii_terminal",
        "before": "/static/samples/source.jpg",
        "after": "/static/samples/terminal.jpg",
    },
    {
        "id": "editorial-diptych",
        "name": "画册双联",
        "category": "Editorial",
        "scheme": "scheme4",
        "layout": "editorial_diptych",
        "before": "/static/samples/source.jpg",
        "after": "/static/samples/diptych.jpg",
    },
    {
        "id": "editorial-guidance",
        "name": "引导线",
        "category": "Editorial",
        "scheme": "scheme4",
        "layout": "editorial_guidance",
        "before": "/static/samples/source.jpg",
        "after": "/static/samples/guidance.jpg",
    },
]

TEMPLATE_BY_ID = {item["id"]: item for item in TEMPLATES}

app = FastAPI(title="PicFrame", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="picframe-job")
_workers = {}
_preview_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="picframe-preview")
_preview_workers = {}
_write_lock = threading.RLock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _job_id_path(job_id):
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", job_id or ""):
        raise HTTPException(status_code=404, detail="Job not found")
    job_dir = (JOB_ROOT / job_id).resolve()
    root = JOB_ROOT.resolve()
    if root not in job_dir.parents or job_dir == root:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_dir


def _load_job(job_id):
    job_dir = _job_id_path(job_id)
    manifest_path = job_dir / "job.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Job manifest is unreadable") from exc


def _write_job(job):
    with _write_lock:
        job["updated_at"] = _now()
        path = _job_id_path(job["id"]) / "job.json"
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)


def _public_job(job):
    result = json.loads(json.dumps(job))
    template = TEMPLATE_BY_ID.get(job.get("template_id"))
    result["template"] = template
    for image in result.get("images", []):
        index = image.get("index")
        image["preview_url"] = f"/api/jobs/{job['id']}/preview/{index}"
        image["thumb_url"] = f"/api/jobs/{job['id']}/thumb/{index}"
        effect = image.get("preview_effect") or {}
        if effect.get("status") == "ready" and effect.get("file"):
            effect["url"] = f"/api/jobs/{job['id']}/preview-effect/{index}"
        image["preview_effect"] = effect
        effects = image.get("preview_effects") or {}
        for template_id, template_effect in effects.items():
            if template_effect.get("status") == "ready" and template_effect.get("file"):
                template_effect["url"] = (
                    f"/api/jobs/{job['id']}/preview-effect/{index}?template_id={template_id}"
                )
        image["preview_effects"] = effects
    return result


def _safe_file_path(job_dir, relative_path):
    target = (job_dir / relative_path).resolve()
    if job_dir.resolve() not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return target


def _register_heif():
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass


def _open_upload_image(path):
    _register_heif()
    try:
        with Image.open(path) as source:
            source.seek(0)
            image = ImageOps.exif_transpose(source)
            if image.mode in {"RGBA", "LA"} or "A" in image.getbands():
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.getchannel("A"))
                return background
            return image.convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unsupported or unreadable image") from exc


def _save_derivative(image, path, max_size, quality=86):
    derivative = image.copy()
    derivative.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    derivative.save(path, format="JPEG", quality=quality, optimize=True)
    return path


def _copy_exif(source_path, target_path):
    try:
        subprocess.run(
            [
                "exiftool",
                "-overwrite_original",
                "-TagsFromFile",
                str(source_path),
                "-all:all",
                "-Orientation#=1",
                str(target_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return
    except Exception:
        pass
    try:
        with Image.open(source_path) as source:
            exif = source.getexif()
            if not exif:
                return
            exif[0x0112] = 1
            with Image.open(target_path) as target:
                target.save(target_path, exif=exif)
    except Exception:
        pass


def _extract_metadata(path):
    try:
        data = run_exif(path)
    except Exception:
        return {}
    return {
        "make": data.get("Make"),
        "camera_model": data.get("CameraModelName") or data.get("Model"),
        "lens_model": data.get("LensModel") or data.get("LensID") or data.get("Lens"),
        "exposure_time": data.get("ExposureTime") or data.get("ShutterSpeed"),
        "f_number": data.get("FNumber") or data.get("Aperture"),
        "iso": data.get("ISO"),
        "focal_length": data.get("FocalLength"),
        "exposure_compensation": data.get("ExposureCompensation"),
        "white_balance": data.get("WhiteBalance"),
        "date": data.get("DateTimeOriginal") or data.get("CreateDate"),
        "gps_latitude": data.get("GPSLatitude"),
        "gps_longitude": data.get("GPSLongitude"),
        "gps_altitude": data.get("GPSAltitude") or data.get("Altitude"),
        "artist": data.get("Artist") or data.get("By-line") or data.get("Creator") or data.get("Photographer"),
    }


def _preview_key(template_id, index, custom):
    payload = {"template_id": template_id, "index": index, "custom": custom}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


def _sanitize_display_name(filename):
    name = Path(filename or "image.jpg").name.strip()
    name = re.sub(r"[\x00-\x1f\x7f\\/:*?\"<>|]", "_", name)
    if name in {"", ".", ".."}:
        name = "image.jpg"
    if len(name) > 180:
        stem, suffix = Path(name).stem, Path(name).suffix
        suffix = suffix[:20]
        name = stem[: 180 - len(suffix)] + suffix
    return name


def _safe_zip_name(name):
    name = Path(name).name
    return re.sub(r"[^A-Za-z0-9._ -]", "_", name) or "image.jpg"


def _normalize_upload(job_id, raw_path, index, display_name):
    job_dir = _job_id_path(job_id)
    image = _open_upload_image(raw_path)
    stem = f"img_{index:03d}"
    originals_dir = job_dir / "originals"
    previews_dir = job_dir / "previews"
    thumbs_dir = job_dir / "thumbs"
    originals_dir.mkdir(parents=True, exist_ok=True)
    previews_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    original_path = originals_dir / f"{stem}.jpg"
    preview_path = previews_dir / f"{stem}.jpg"
    thumb_path = thumbs_dir / f"{stem}.jpg"
    image.save(original_path, format="JPEG", quality=92, optimize=True)
    _copy_exif(raw_path, original_path)
    _save_derivative(image, preview_path, PREVIEW_MAX)
    _save_derivative(image, thumb_path, THUMB_MAX, quality=78)
    return {
        "index": index,
        "name": display_name,
        "file": f"originals/{original_path.name}",
        "preview": f"previews/{preview_path.name}",
        "thumb": f"thumbs/{thumb_path.name}",
        "width": image.width,
        "height": image.height,
        "metadata": _extract_metadata(original_path),
        "status": "pending",
        "saved": False,
        "result": None,
        "error": None,
        "preview_effect": {
            "template_id": None,
            "status": "idle",
            "file": None,
            "error": None,
            "preview_key": None,
        },
        "preview_effects": {},
    }


def _scan_jobs():
    jobs = []
    if not JOB_ROOT.exists():
        return jobs
    for job_dir in sorted(JOB_ROOT.iterdir(), reverse=True):
        manifest_path = job_dir / "job.json"
        if not manifest_path.is_file():
            continue
        try:
            jobs.append(_load_job(job_dir.name))
        except (OSError, json.JSONDecodeError):
            continue
    jobs.sort(key=lambda job: job.get("created_at", ""), reverse=True)
    return jobs


def _mark_interrupted_jobs():
    for job in _scan_jobs():
        if job.get("status") not in {"processing", "queued"}:
            continue
        job["status"] = "interrupted"
        job["error"] = "Service restarted while the job was still running."
        job["progress"] = {
            **job.get("progress", {}),
            "stage": "interrupted",
            "message": "任务因服务重启而中断",
        }
        for image in job.get("images", []):
            if image.get("status") in {"processing", "queued"}:
                image["status"] = "interrupted"
        _write_job(job)


@app.on_event("startup")
def startup():
    JOB_ROOT.mkdir(parents=True, exist_ok=True)
    _mark_interrupted_jobs()


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/api/templates")
def templates():
    return {"templates": TEMPLATES}


@app.post("/api/jobs", status_code=201)
async def create_job(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="At least one image is required")
    job_id = str(uuid.uuid4())
    job_dir = JOB_ROOT / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "originals").mkdir()
    (job_dir / "previews").mkdir()
    (job_dir / "thumbs").mkdir()
    (job_dir / "working").mkdir()
    (job_dir / "result").mkdir()
    (job_dir / "preview-src").mkdir()
    (job_dir / "preview-work").mkdir()
    (job_dir / "preview").mkdir()

    job = {
        "id": job_id,
        "status": "uploaded",
        "created_at": _now(),
        "updated_at": _now(),
        "template_id": None,
        "scheme": None,
        "layout": None,
        "compression": "jpeg",
        "scope": "all",
        "scope_index": None,
        "custom": {},
        "saved": False,
        "message": "Uploaded",
        "progress": {"current": 0, "total": len(files), "stage": "uploaded", "percent": 0, "message": "等待选择模板"},
        "images": [],
        "results": [],
        "manifest": None,
        "error": None,
    }

    total_size = 0
    try:
        for index, upload in enumerate(files):
            display_name = _sanitize_display_name(upload.filename)
            ext = Path(display_name).suffix.lower()
            if ext not in ALLOWED_UPLOAD_EXTENSIONS:
                raise HTTPException(status_code=400, detail=f"Unsupported file type: {display_name}")
            raw_suffix = ext or ".bin"
            raw_path = job_dir / "originals" / f"raw_{index:03d}_{Path(display_name).stem}{raw_suffix}"
            try:
                size = 0
                with raw_path.open("wb") as raw_file:
                    while True:
                        chunk = await upload.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_FILE_SIZE:
                            raise HTTPException(status_code=413, detail=f"{display_name} exceeds 512 MiB")
                        raw_file.write(chunk)
                total_size += size
                if total_size > MAX_TOTAL_SIZE:
                    raise HTTPException(status_code=413, detail="Total upload exceeds 4 GiB")
                image_meta = _normalize_upload(job_id, raw_path, index, display_name)
                job["images"].append(image_meta)
            finally:
                if raw_path.exists():
                    raw_path.unlink()
        _write_job(job)
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail="Failed to create job")
    return _public_job(job)


@app.get("/api/jobs")
def jobs_list():
    return {"jobs": [_public_job(job) for job in _scan_jobs()]}


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str):
    return _public_job(_load_job(job_id))


@app.get("/api/jobs/{job_id}/preview/{index}")
def job_preview(job_id: str, index: int):
    job = _load_job(job_id)
    if index < 0 or index >= len(job.get("images", [])):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(_safe_file_path(_job_id_path(job_id), job["images"][index]["preview"]))


@app.get("/api/jobs/{job_id}/thumb/{index}")
def job_thumb(job_id: str, index: int):
    job = _load_job(job_id)
    if index < 0 or index >= len(job.get("images", [])):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(_safe_file_path(_job_id_path(job_id), job["images"][index]["thumb"]))


@app.get("/api/jobs/{job_id}/result/{index}")
def job_result(job_id: str, index: int):
    job = _load_job(job_id)
    result = next((item for item in job.get("results", []) if item.get("index") == index), None)
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")
    return FileResponse(_safe_file_path(_job_id_path(job_id), result["file"]))


@app.post("/api/jobs/{job_id}/preview")
def generate_job_preview(job_id: str, payload: dict = Body(default=None)):
    job = _load_job(job_id)
    payload = payload or {}
    template_id = payload.get("template_id") or job.get("template_id") or "info-portrait"
    template = TEMPLATE_BY_ID.get(template_id)
    if not template:
        raise HTTPException(status_code=400, detail="Unsupported template")
    index = payload.get("index", 0)
    if not isinstance(index, int) or not 0 <= index < len(job.get("images", [])):
        raise HTTPException(status_code=400, detail="Invalid image index")
    if template["scheme"] == "scheme4":
        raise HTTPException(status_code=400, detail="Scheme4 preview requires a configured VLM key")

    custom = dict(job.get("custom") or {})
    custom.update(payload.get("custom") or {})
    job["custom"] = custom
    key = _preview_key(template_id, index, custom)
    image = job["images"][index]
    effects = dict(image.get("preview_effects") or {})
    effect = effects.get(template_id) or {}
    if effect.get("status") == "ready" and effect.get("preview_key") == key:
        image["preview_effects"] = effects
        _write_job(job)
        return _public_job(_load_job(job_id))

    effect = {
        "template_id": template_id,
        "status": "generating",
        "file": None,
        "error": None,
        "preview_key": key,
    }
    effects[template_id] = effect
    image["preview_effects"] = effects
    image["preview_effect"] = effect
    _write_job(job)
    future = _preview_executor.submit(_execute_preview, job_id, index, template_id, key)
    _preview_workers[(job_id, index, template_id)] = future
    return _public_job(_load_job(job_id))


@app.get("/api/jobs/{job_id}/preview-effect/{index}")
def job_preview_effect(job_id: str, index: int, template_id: str | None = None):
    job = _load_job(job_id)
    if index < 0 or index >= len(job.get("images", [])):
        raise HTTPException(status_code=404, detail="Image not found")
    image = job["images"][index]
    if template_id:
        effect = (image.get("preview_effects") or {}).get(template_id) or {}
    else:
        effect = image.get("preview_effect") or {}
    if effect.get("status") != "ready" or not effect.get("file"):
        raise HTTPException(status_code=404, detail="Preview not found")
    return FileResponse(_safe_file_path(_job_id_path(job_id), effect["file"]))


@app.post("/api/jobs/{job_id}/save")
def toggle_save(job_id: str):
    job = _load_job(job_id)
    job["saved"] = not bool(job.get("saved", False))
    _write_job(job)
    return _public_job(job)


@app.post("/api/jobs/{job_id}/start")
def start_job(job_id: str, payload: dict = Body(default=None)):
    job = _load_job(job_id)
    if job.get("status") in {"processing", "queued"}:
        raise HTTPException(status_code=409, detail="Job is already running")
    if not job.get("images"):
        raise HTTPException(status_code=400, detail="Job has no images")

    payload = payload or {}
    template_id = payload.get("template_id") or job.get("template_id") or "info-portrait"
    template = TEMPLATE_BY_ID.get(template_id)
    if not template:
        raise HTTPException(status_code=400, detail="Unsupported template")
    compression = payload.get("compression", job.get("compression") or "jpeg")
    if compression not in {"none", "jpeg"}:
        raise HTTPException(status_code=400, detail="Compression must be jpeg or none")
    scope = payload.get("scope", job.get("scope") or "all")
    if scope not in {"all", "selected"}:
        raise HTTPException(status_code=400, detail="Scope must be all or selected")
    scope_index = payload.get("index")
    if scope == "selected":
        if scope_index is None:
            scope_index = 0
        if not isinstance(scope_index, int) or not 0 <= scope_index < len(job["images"]):
            raise HTTPException(status_code=400, detail="Invalid image index")
    else:
        scope_index = None

    custom = dict(job.get("custom") or {})
    if payload.get("custom"):
        custom.update(payload["custom"])
    job["status"] = "queued"
    job["custom"] = custom
    job["template_id"] = template["id"]
    job["scheme"] = template["scheme"]
    job["layout"] = template["layout"]
    job["compression"] = compression
    job["scope"] = scope
    job["scope_index"] = scope_index
    job["message"] = "Queued"
    job["error"] = None
    job["results"] = []
    job["progress"] = {
        "current": 0,
        "total": len(job["images"]) if scope == "all" else 1,
        "stage": "queued",
        "percent": 0,
        "message": "任务已进入队列",
    }
    for image in job["images"]:
        image["status"] = "queued" if scope == "all" or image["index"] == scope_index else "pending"
        image["result"] = None
        image["error"] = None
    _write_job(job)

    future = _executor.submit(_execute_job, job_id)
    _workers[job_id] = future
    return _public_job(_load_job(job_id))


@app.get("/api/jobs/{job_id}/download")
def download_job(job_id: str, index: int | None = None):
    job = _load_job(job_id)
    results = job.get("results", [])
    if not results:
        raise HTTPException(status_code=404, detail="No results to download")
    job_dir = _job_id_path(job_id)

    if index is not None:
        result = next((item for item in results if item.get("index") == index), None)
        if not result:
            raise HTTPException(status_code=404, detail="Result not found")
        path = _safe_file_path(job_dir, result["file"])
        download_name = f"{result.get('index', 0):03d}_{_safe_zip_name(result.get('name', path.name))}"
        return FileResponse(path, filename=download_name)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for result in results:
            path = _safe_file_path(job_dir, result["file"])
            archive.write(path, f"{result.get('index', 0):03d}_{_safe_zip_name(result.get('name', path.name))}")
    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="picframe-{job_id}.zip"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)


def _handle_progress(job_id, event):
    with _write_lock:
        job = _load_job(job_id)
        stage = getattr(event.stage, "value", str(event.stage))
        level = getattr(event.level, "value", str(event.level))
        current = int(event.current_index or 0)
        total = int(event.total_items or len(job.get("images", [])))
        percent = round(current * 100 / total) if total else 0
        job["status"] = "processing"
        job["message"] = event.message
        job["progress"] = {
            "current": current,
            "total": total,
            "stage": stage,
            "percent": min(100, max(0, percent)),
            "message": event.message,
        }

        if stage == PipelineStage.RENDERING.value:
            index = current - 1
            images = job.get("images", [])
            if 0 <= index < len(images):
                if level == EventLevel.INFO.value:
                    images[index]["status"] = "processing"
                elif level == EventLevel.SUCCESS.value:
                    images[index]["status"] = "completed"
                elif level == EventLevel.ERROR.value:
                    images[index]["status"] = "error"
                    images[index]["error"] = event.message
        _write_job(job)


def _match_result_index(job, output_path):
    output = Path(output_path)
    for image in job.get("images", []):
        source_name = Path(image.get("file", "")).name
        if output.stem == f"{Path(source_name).stem}_card":
            return image["index"]
    scope_index = job.get("scope_index")
    if scope_index is not None and job.get("scope") == "selected":
        return scope_index
    return None


def _finalize_job(job_id, generation):
    job = _load_job(job_id)
    job_dir = _job_id_path(job_id)
    result_dir = job_dir / "result"
    result_dir.mkdir(parents=True, exist_ok=True)
    manifest = None
    source_manifest = Path(generation["manifest"])
    if source_manifest.exists():
        manifest = json.loads(source_manifest.read_text(encoding="utf-8"))

    results = []
    for output in generation["outputs"]:
        output_path = Path(output)
        copied = result_dir / output_path.name
        shutil.copy2(output_path, copied)
        index = _match_result_index(job, output_path)
        image = next((item for item in job["images"] if item["index"] == index), None)
        name = image["name"] if image else output_path.name
        results.append({
            "index": index,
            "name": name,
            "file": f"result/{copied.name}",
            "saved": bool(image and image.get("saved")),
        })
        if image:
            image["status"] = "completed"
            image["result"] = f"result/{copied.name}"

    contact_sheet = generation.get("contact_sheet")
    if contact_sheet and Path(contact_sheet).exists():
        shutil.copy2(contact_sheet, result_dir / "contact-sheet.jpg")

    if manifest:
        manifest["output_directory"] = str(result_dir)
        manifest["outputs"] = [str(result_dir / Path(path).name) for path in manifest.get("outputs", [])]
        if manifest.get("contact_sheet"):
            manifest["contact_sheet"] = str(result_dir / "contact-sheet.jpg")
        (result_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    job["results"] = sorted(results, key=lambda item: item["index"] or 0)
    job["manifest"] = str(result_dir / "manifest.json")
    job["message"] = f"生成完成 {len(results)} 张"
    job["progress"] = {
        "current": len(results),
        "total": len(job["images"]) if job.get("scope") == "all" else 1,
        "stage": "complete",
        "percent": 100,
        "message": job["message"],
    }
    job["status"] = "completed" if results else "failed"
    if job["status"] == "failed":
        job["error"] = "No outputs generated"
    _write_job(job)


def _fail_job(job_id, exc):
    job = _load_job(job_id)
    job["status"] = "failed"
    job["error"] = str(exc)[-2000:]
    job["message"] = "生成失败"
    job["progress"]["stage"] = "failed"
    job["progress"]["message"] = "生成失败"
    for image in job.get("images", []):
        if image.get("status") in {"processing", "queued"}:
            image["status"] = "error"
            image["error"] = str(exc)[-500:]
    _write_job(job)


def _make_preview_source(job_id, index, image_meta):
    job_dir = _job_id_path(job_id)
    source_dir = job_dir / "preview-src"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / f"img_{index:03d}.jpg"
    if source_path.exists():
        return source_path
    original_path = _safe_file_path(job_dir, image_meta["file"])
    with Image.open(original_path) as source:
        preview = source.convert("RGB")
        preview.thumbnail((PREVIEW_SOURCE_MAX, PREVIEW_SOURCE_MAX), Image.Resampling.LANCZOS)
        preview.save(source_path, format="JPEG", quality=88, optimize=True)
    _copy_exif(original_path, source_path)
    return source_path


def _execute_preview(job_id, index, template_id, preview_key):
    try:
        job = _load_job(job_id)
        job_dir = _job_id_path(job_id)
        template = TEMPLATE_BY_ID[template_id]
        image = job["images"][index]
        source_path = _make_preview_source(job_id, index, image)
        job = _load_job(job_id)
        generation = generate_from_source(
            source_path.parent,
            output_dir=job_dir / "preview-work",
            layout=template["layout"],
            scheme=template["scheme"],
            compression="jpeg",
            photo=source_path.name,
            custom=job.get("custom") or {},
        )
        if not generation["outputs"]:
            raise ValueError("Preview generation produced no output")
        output = Path(generation["outputs"][0])
        preview_dir = job_dir / "preview"
        preview_dir.mkdir(parents=True, exist_ok=True)
        target = preview_dir / f"{template_id}_{index}.jpg"
        shutil.copy2(output, target)

        with _write_lock:
            job = _load_job(job_id)
            image = job["images"][index]
            effects = dict(image.get("preview_effects") or {})
            effect = {
                "template_id": template_id,
                "status": "ready",
                "file": f"preview/{target.name}",
                "error": None,
                "preview_key": preview_key,
            }
            effects[template_id] = effect
            image["preview_effects"] = effects
            image["preview_effect"] = effect
            _write_job(job)
    except Exception as exc:
        with _write_lock:
            try:
                job = _load_job(job_id)
                effect = job["images"][index].get("preview_effect") or {}
                effect.update({"status": "error", "error": str(exc)[-500:], "preview_key": preview_key})
                image = job["images"][index]
                effects = dict(image.get("preview_effects") or {})
                effects[template_id] = effect
                image["preview_effects"] = effects
                image["preview_effect"] = effect
                _write_job(job)
            except Exception:
                pass
    finally:
        _preview_workers.pop((job_id, index, template_id), None)


def _execute_job(job_id):
    try:
        job = _load_job(job_id)
        job_dir = _job_id_path(job_id)
        source_dir = job_dir / "originals"
        selected = None
        if job.get("scope") == "selected":
            index = job.get("scope_index", 0)
            selected = Path(job["images"][index]["file"]).name
        generation = generate_from_source(
            source_dir,
            output_dir=job_dir / "working",
            layout=job["layout"],
            scheme=job["scheme"],
            compression=job["compression"],
            photo=selected,
            event_callback=lambda event: _handle_progress(job_id, event),
            custom=job.get("custom") or {},
        )
        _finalize_job(job_id, generation)
    except Exception as exc:
        try:
            _fail_job(job_id, exc)
        except Exception:
            pass
    finally:
        _workers.pop(job_id, None)


@app.get("/results")
def results():
    jobs = []
    for manifest_path in sorted(OUTPUT_DIR.glob("*/result/manifest.json"), reverse=True):
        if JOB_ROOT.resolve() in manifest_path.resolve().parents:
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        outputs = [str(Path(path).name) for path in data.get("outputs", [])]
        contact = data.get("contact_sheet")
        contact = str(Path(contact).name) if contact else None
        jobs.append({"manifest": data, "outputs": outputs, "contact": contact})

    for job in _scan_jobs():
        result_entries = job.get("results", [])
        outputs = [item["file"] for item in result_entries]
        contact = "result/contact-sheet.jpg" if (JOB_ROOT / job["id"] / "result" / "contact-sheet.jpg").exists() else None
        jobs.append({
            "job": _public_job(job),
            "manifest": job.get("manifest"),
            "outputs": outputs,
            "contact": contact,
        })
    return {"jobs": jobs}
