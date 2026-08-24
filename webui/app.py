import shutil
import subprocess
import tempfile
import json
from pathlib import Path

from core.presentation import load_presentation_schemes
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

INPUT_DIR = Path("/data/input")
OUTPUT_DIR = Path("/data/output")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

app = FastAPI(title="PicFrame", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory="webui/templates")


def list_photos():
    return sorted(
        path for path in INPUT_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ) if INPUT_DIR.exists() else []


def render_card(source_name, scheme, layout, compression):
    source_path = (INPUT_DIR / Path(source_name).name).resolve()
    if not source_path.is_file():
        raise HTTPException(status_code=404, detail="Photo not found")
    schemes = load_presentation_schemes()
    if scheme not in schemes:
        raise HTTPException(status_code=400, detail="Unsupported scheme")
    if layout not in schemes[scheme].layouts:
        raise HTTPException(status_code=400, detail="Layout is not valid for this scheme")
    if compression not in {"none", "jpeg"}:
        raise HTTPException(status_code=400, detail="Unsupported compression")

    stem = source_path.stem
    destination = OUTPUT_DIR / f"{stem}-{scheme}-{layout}-{compression}"
    if destination.exists():
        raise HTTPException(status_code=409, detail="Output already exists; rename or remove it first")
    destination.mkdir(parents=True)

    with tempfile.TemporaryDirectory(prefix="picframe-") as temp_dir:
        job_dir = Path(temp_dir) / "source"
        job_dir.mkdir()
        shutil.copy2(source_path, job_dir / source_path.name)
        output_root = OUTPUT_DIR / f".job-{stem}-{scheme}-{layout}-{compression}"
        if output_root.exists():
            shutil.rmtree(output_root)
        result = subprocess.run(
            [
                "python", "generate_photo_cards.py",
                "--source", str(job_dir),
                "--output", str(output_root),
                "--scheme", scheme,
                "--layout", layout,
                "--compression", compression,
                "--photo", source_path.name,
            ],
            cwd="/app",
            capture_output=True,
            text=True,
            timeout=300,
        )

    generated_result = next((OUTPUT_DIR / f".job-{stem}-{scheme}-{layout}-{compression}").rglob("manifest.json"), None)
    if generated_result:
        result_dir = generated_result.parent
        manifest = json.loads(generated_result.read_text(encoding="utf-8"))
        result_dir.rename(destination / "result")
        shutil.rmtree(output_root)
        for key in ("output_directory", "outputs", "contact_sheet"):
            if key not in manifest:
                continue
            if key == "output_directory":
                manifest[key] = str(destination / "result")
            elif isinstance(manifest[key], list):
                manifest[key] = [str(destination / "result" / Path(path).name) for path in manifest[key]]
            elif manifest[key]:
                manifest[key] = str(destination / "result" / Path(manifest[key]).name)
        (destination / "result" / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    manifest = destination / "result" / "manifest.json"
    if result.returncode != 0 or not manifest.exists():
        message = result.stderr.strip() or result.stdout.strip() or "Generation failed"
        raise HTTPException(status_code=500, detail=message[-2000:])


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"schemes": load_presentation_schemes(), "photos": list_photos()},
    )


@app.post("/generate")
def generate(
    photo: str = Form(min_length=1),
    scheme: str = Form(),
    layout: str = Form(),
    compression: str = Form(),
):
    render_card(photo, scheme, layout, compression)
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/results", status_code=303)


@app.get("/results")
def results():
    jobs = []
    for manifest_path in sorted(OUTPUT_DIR.glob("*/result/manifest.json"), reverse=True):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        outputs = [str(Path(path).relative_to(manifest_path.parent.parent)) for path in data["outputs"]]
        contact = data.get("contact_sheet")
        contact = str(Path(contact).relative_to(manifest_path.parent.parent)) if contact else None
        jobs.append({"manifest": data, "outputs": outputs, "contact": contact})
    return {"jobs": jobs}
