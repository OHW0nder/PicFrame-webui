import io
import json
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

import webui.app as webui


def image_bytes(size=(360, 480), color=(70, 120, 190), image_format="JPEG"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=image_format)
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


if __name__ == "__main__":
    unittest.main()
