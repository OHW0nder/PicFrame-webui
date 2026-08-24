import io
import json
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from core.context import build_context
from core.presentation import normalize_presentation
import webui.app as webui


def image_bytes(size=(360, 480), color=(70, 120, 190), image_format="JPEG"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=image_format)
    return buffer.getvalue()


def exif_image_bytes():
    buffer = io.BytesIO()
    image = Image.new("RGB", (640, 480), (80, 110, 140))
    exif = Image.Exif()
    exif[0x010F] = "TestMaker"
    exif[0x0110] = "TestCamera"
    exif[0xA434] = "TestLens 50mm"
    exif[0x829A] = "1/125"
    exif[0x829D] = "2.8"
    exif[0x8827] = 200
    exif[0x920A] = "50.0 mm"
    exif[0x9003] = "2026:08:03 15:06:56"
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


class WebUITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        webui.JOB_ROOT = Path(self.temp_dir.name) / "jobs"
        webui.OUTPUT_DIR = Path(self.temp_dir.name) / "output"
        self.client = TestClient(webui.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def wait_for_job(self, job_id, timeout=45):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"completed", "failed", "interrupted"}:
                return job
            time.sleep(0.25)
        raise AssertionError(f"Job did not finish within {timeout}s: {job}")

    def test_full_upload_process_download_save_flow(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        templates = self.client.get("/api/templates").json()["templates"]
        self.assertEqual(len(templates), 6)

        files = [
            ("files", ("a.jpg", image_bytes(), "image/jpeg")),
            ("files", ("b.png", image_bytes((420, 360), (30, 90, 120), "PNG"), "image/png")),
        ]
        created = self.client.post("/api/jobs", files=files)
        self.assertEqual(created.status_code, 201)
        job = created.json()
        self.assertEqual(job["status"], "uploaded")
        self.assertEqual(len(job["images"]), 2)

        for image in job["images"]:
            self.assertEqual(self.client.get(image["preview_url"]).status_code, 200)
            self.assertEqual(self.client.get(image["thumb_url"]).status_code, 200)

        started = self.client.post(
            f"/api/jobs/{job['id']}/start",
            json={"template_id": "info-portrait", "compression": "jpeg", "scope": "all"},
        )
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["status"], "queued")

        finished = self.wait_for_job(job["id"])
        self.assertEqual(finished["status"], "completed", finished.get("error"))
        self.assertEqual(len(finished["results"]), 2)
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/result/0").status_code, 200)
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/download?index=0").status_code, 200)
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/download").status_code, 200)

        manifest_path = Path(finished["manifest"])
        self.assertTrue(manifest_path.exists())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["scheme"], "scheme1")
        self.assertEqual(manifest["layout"], "portrait")

        saved = self.client.post(f"/api/jobs/{job['id']}/save").json()
        self.assertTrue(saved["saved"])
        unsaved = self.client.post(f"/api/jobs/{job['id']}/save").json()
        self.assertFalse(unsaved["saved"])

        results = self.client.get("/results").json()["jobs"]
        self.assertTrue(any(item.get("job", {}).get("id") == job["id"] for item in results))

    def test_path_traversal_and_unknown_job_are_rejected(self):
        payload = image_bytes()
        created = self.client.post(
            "/api/jobs",
            files=[("files", ("../../evil.jpg", payload, "image/jpeg"))],
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["images"][0]["name"], "evil.jpg")

        self.assertEqual(self.client.get("/api/jobs/not-a-uuid").status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/../../etc/passwd").status_code, 404)
        self.assertEqual(
            self.client.post("/api/jobs/not-a-uuid/start", json={"template_id": "info-portrait"}).status_code,
            404,
        )

    def test_webp_and_heic_uploads_are_normalized_to_jpeg(self):
        webp = self.client.post(
            "/api/jobs",
            files=[("files", ("photo.webp", image_bytes(image_format="WEBP"), "image/webp"))],
        )
        self.assertEqual(webp.status_code, 201)
        webp_job = webp.json()
        self.assertTrue(webp_job["images"][0]["file"].endswith(".jpg"))
        self.assertEqual(self.client.get(webp_job["images"][0]["preview_url"]).status_code, 200)

        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
            heic_payload = image_bytes((200, 140), (120, 60, 30), "HEIF")
        except (ImportError, OSError):
            heic_payload = None

        if heic_payload:
            heic = self.client.post(
                "/api/jobs",
                files=[("files", ("photo.heic", heic_payload, "image/heic"))],
            )
            self.assertEqual(heic.status_code, 201)
            heic_job = heic.json()
            self.assertTrue(heic_job["images"][0]["file"].endswith(".jpg"))
            self.assertEqual(self.client.get(heic_job["images"][0]["thumb_url"]).status_code, 200)

    def test_selected_scope_generates_only_the_chosen_image(self):
        files = [
            ("files", ("a.jpg", image_bytes(), "image/jpeg")),
            ("files", ("b.jpg", image_bytes((420, 360), (30, 90, 120)), "image/jpeg")),
        ]
        job = self.client.post("/api/jobs", files=files).json()
        started = self.client.post(
            f"/api/jobs/{job['id']}/start",
            json={"template_id": "info-portrait", "compression": "jpeg", "scope": "selected", "index": 1},
        )
        self.assertEqual(started.status_code, 200)
        finished = self.wait_for_job(job["id"])
        self.assertEqual(finished["status"], "completed", finished.get("error"))
        self.assertEqual(len(finished["results"]), 1)
        self.assertEqual(finished["results"][0]["index"], 1)

    def test_upload_preserves_exif_metadata(self):
        created = self.client.post(
            "/api/jobs",
            files=[("files", ("P1006561.jpg", exif_image_bytes(), "image/jpeg"))],
        )
        self.assertEqual(created.status_code, 201)
        metadata = created.json()["images"][0]["metadata"]
        self.assertEqual(metadata["make"], "TestMaker")
        self.assertEqual(metadata["camera_model"], "TestCamera")
        self.assertEqual(metadata["lens_model"], "TestLens 50mm")
        self.assertEqual(metadata["exposure_time"], "1/125")
        self.assertEqual(str(metadata["f_number"]), "2.8")
        self.assertEqual(metadata["iso"], 200)
        self.assertEqual(metadata["focal_length"], "50.0 mm")

    def test_real_preview_generation_and_scheme4_gate(self):
        job = self.client.post(
            "/api/jobs",
            files=[("files", ("preview.jpg", image_bytes(), "image/jpeg"))],
        ).json()
        response = self.client.post(
            f"/api/jobs/{job['id']}/preview",
            json={"template_id": "info-portrait", "index": 0, "custom": {}},
        )
        self.assertEqual(response.status_code, 200)

        deadline = time.time() + 45
        while time.time() < deadline:
            current = self.client.get(f"/api/jobs/{job['id']}").json()
            effect = current["images"][0]["preview_effect"]
            if effect["status"] in {"ready", "error"}:
                break
            time.sleep(0.25)
        self.assertEqual(effect["status"], "ready", effect.get("error"))
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/preview-effect/0").status_code, 200)

        gated = self.client.post(
            f"/api/jobs/{job['id']}/preview",
            json={"template_id": "editorial-guidance", "index": 0},
        )
        self.assertEqual(gated.status_code, 400)
        self.assertIn("configured VLM key", gated.json()["detail"])

    def test_multiple_template_previews_are_isolated(self):
        job = self.client.post(
            "/api/jobs",
            files=[("files", ("preview.jpg", image_bytes(), "image/jpeg"))],
        ).json()

        def request_and_wait(template_id):
            response = self.client.post(
                f"/api/jobs/{job['id']}/preview",
                json={"template_id": template_id, "index": 0, "custom": {}},
            )
            self.assertEqual(response.status_code, 200)
            deadline = time.time() + 45
            while time.time() < deadline:
                current = self.client.get(f"/api/jobs/{job['id']}").json()
                effect = current["images"][0]["preview_effects"][template_id]
                if effect["status"] in {"ready", "error"}:
                    return effect
                time.sleep(0.25)
            raise AssertionError(f"Preview for {template_id} did not finish")

        portrait = request_and_wait("info-portrait")
        terminal = request_and_wait("terminal")
        self.assertEqual(portrait["status"], "ready", portrait.get("error"))
        self.assertEqual(terminal["status"], "ready", terminal.get("error"))
        self.assertNotEqual(portrait["file"], terminal["file"])

        portrait_url = self.client.get(
            f"/api/jobs/{job['id']}/preview-effect/0",
            params={"template_id": "info-portrait"},
        )
        terminal_url = self.client.get(
            f"/api/jobs/{job['id']}/preview-effect/0",
            params={"template_id": "terminal"},
        )
        self.assertEqual(portrait_url.status_code, 200)
        self.assertEqual(terminal_url.status_code, 200)

    def test_custom_overrides_flow_into_render_context(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            Image.new("RGB", (320, 240), (70, 120, 190)).save(tmp.name, format="JPEG")
            presentation, layout = normalize_presentation("scheme1", "portrait")
            context = build_context(
                tmp.name,
                Path(tmp.name).parent,
                presentation,
                layout,
                exif={
                    "Model": "OriginalCamera",
                    "LensModel": "OriginalLens",
                    "FNumber": "4.0",
                    "Artist": "Original Artist",
                },
                custom={
                    "camera_model": "CustomCamera",
                    "lens_model": "CustomLens",
                    "f_number": "1.8",
                    "artist": "Custom Artist",
                    "watermark_text": "Custom Watermark",
                },
            )
        self.assertEqual(context.camera_model, "CustomCamera")
        self.assertEqual(context.lens_model, "CustomLens")
        self.assertEqual(context.line_items[0], "F1.8")
        self.assertEqual(context.artist, "Custom Artist")
        self.assertEqual(context.watermark_text, "Custom Watermark")

    def test_start_stores_custom_values_on_job(self):
        job = self.client.post(
            "/api/jobs",
            files=[("files", ("custom.jpg", image_bytes(), "image/jpeg"))],
        ).json()
        started = self.client.post(
            f"/api/jobs/{job['id']}/start",
            json={
                "template_id": "info-portrait",
                "compression": "jpeg",
                "scope": "all",
                "custom": {"camera_model": "CustomCamera", "artist": "Custom Artist"},
            },
        )
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["custom"]["camera_model"], "CustomCamera")
        self.assertEqual(started.json()["custom"]["artist"], "Custom Artist")
        self.wait_for_job(job["id"])


if __name__ == "__main__":
    unittest.main()
