# -*- coding:utf-8 -*-
import ast
import hashlib
import io
import os
import tempfile
import threading
import unittest
import uuid
from datetime import datetime
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_PATH = os.path.join(
    ROOT,
    "plugins",
    "xiaoyou_life_photo",
    "__init__.py",
)
COMPOSE_PATH = os.path.join(ROOT, "docker-compose.yml")


class SilentLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def load_methods(names, **extra):
    tree = ast.parse(read(PLUGIN_PATH), filename=PLUGIN_PATH)
    selected = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "XiaoyouLifePhoto":
            continue
        selected = [
            item
            for item in node.body
            if isinstance(item, ast.FunctionDef) and item.name in set(names)
        ]
        break
    namespace = {
        "os": os,
        "io": io,
        "hashlib": hashlib,
        "uuid": uuid,
        "datetime": datetime,
        "logger": SilentLogger(),
        **extra,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), PLUGIN_PATH, "exec"), namespace)
    return namespace


class FakeDownloadResponse:
    def __init__(self, raw):
        self.raw = raw
        self.status_code = 200
        self.headers = {"Content-Length": str(len(raw))}
        self.iterated = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def iter_content(self, chunk_size):
        self.iterated = True
        for offset in range(0, len(self.raw), max(1, chunk_size // 3)):
            yield self.raw[offset : offset + max(1, chunk_size // 3)]


class LifePhotoDeliveryPipelineTest(unittest.TestCase):
    def test_user_request_keeps_photo_before_caption_without_placeholder(self):
        source = read(PLUGIN_PATH)
        handler = source[
            source.index("    def on_handle_context")
            : source.index("    def prefetch_route_decision")
        ]
        worker = source[
            source.index("    def _run_user_photo_job")
            : source.index("    def _photo_job_is_current")
        ]

        self.assertIn("self._enqueue_user_photo_job(job)", handler)
        self.assertIn("e_context.action = EventAction.BREAK_PASS", handler)
        self.assertNotIn("等我一下", handler)
        self.assertIn('image_path=share["path"]', worker)
        self.assertIn("parts=[caption] if caption else []", worker)
        self.assertIn("freshness_check=lambda: self._photo_job_is_current(job)", worker)

    def test_pending_queue_keeps_only_latest_job_per_session(self):
        methods = load_methods(["_enqueue_user_photo_job"])

        class Dummy:
            _enqueue_user_photo_job = methods["_enqueue_user_photo_job"]

            def __init__(self):
                self._photo_job_condition = threading.Condition(threading.RLock())
                self._pending_photo_jobs = {}

            def _ensure_photo_worker(self):
                return None

        subject = Dummy()
        self.assertTrue(subject._enqueue_user_photo_job({"session_id": "yoyo", "job_id": "old"}))
        self.assertTrue(subject._enqueue_user_photo_job({"session_id": "yoyo", "job_id": "new"}))
        self.assertEqual("new", subject._pending_photo_jobs["yoyo"]["job_id"])
        self.assertEqual(1, len(subject._pending_photo_jobs))

    def test_reference_image_is_lightweight_and_cached_under_data(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow unavailable")

        with tempfile.TemporaryDirectory() as temporary:
            source_path = os.path.join(temporary, "reference.png")
            cache_dir = os.path.join(temporary, "data", "reference_cache")
            Image.new("RGB", (2200, 1800), (132, 87, 119)).save(
                source_path,
                format="PNG",
            )
            methods = load_methods(
                ["_lightweight_reference"],
                REFERENCE_CACHE_DIR=cache_dir,
            )

            class Dummy:
                _lightweight_reference = methods["_lightweight_reference"]

                @staticmethod
                def _valid_image_bytes(raw):
                    return bool(raw) and (
                        raw.startswith(b"\xff\xd8\xff")
                        or raw.startswith(b"\x89PNG\r\n\x1a\n")
                    )

            with mock.patch.dict(
                os.environ,
                {
                    "XIAOYOU_LIFE_PHOTO_REFERENCE_MAX_SIDE": "1280",
                    "XIAOYOU_LIFE_PHOTO_REFERENCE_JPEG_QUALITY": "88",
                    "XIAOYOU_LIFE_PHOTO_REFERENCE_TARGET_MAX_KB": "500",
                },
            ):
                raw, mime = Dummy()._lightweight_reference(source_path)
                cached, cached_mime = Dummy()._lightweight_reference(source_path)

            self.assertEqual("image/jpeg", mime)
            self.assertEqual(mime, cached_mime)
            self.assertEqual(raw, cached)
            self.assertLessEqual(len(raw), 500 * 1024)
            self.assertEqual(1, len(os.listdir(cache_dir)))
            with Image.open(io.BytesIO(raw)) as image:
                self.assertLessEqual(max(image.size), 1280)

    def test_url_result_is_streamed_directly_to_generated_storage(self):
        jpeg = b"\xff\xd8\xff\xe0" + (b"x" * 2048)
        response = FakeDownloadResponse(jpeg)

        class FakeSession:
            def get(self, url, stream, timeout):
                self.url = url
                self.stream = stream
                self.timeout = timeout
                return response

        with tempfile.TemporaryDirectory() as temporary:
            methods = load_methods(
                ["_download_generated_image"],
                GENERATED_DIR=os.path.join(temporary, "generated"),
            )

            class Dummy:
                _download_generated_image = methods["_download_generated_image"]

                def __init__(self):
                    self._http = FakeSession()

            path = Dummy()._download_generated_image("https://example.test/photo")

            self.assertTrue(response.iterated)
            self.assertTrue(os.path.isfile(path))
            self.assertTrue(path.endswith(".jpg"))
            with open(path, "rb") as handle:
                self.assertEqual(jpeg, handle.read())

    def test_deploy_defaults_select_fast_quality_preserving_path(self):
        compose = read(COMPOSE_PATH)
        self.assertIn("XIAOYOU_LIFE_PHOTO_PLANNER_MODEL: 'qwen3.7-plus'", compose)
        self.assertIn("XIAOYOU_LIFE_PHOTO_PLANNER_ENABLE_THINKING: 'false'", compose)
        self.assertIn("XIAOYOU_LIFE_PHOTO_ASYNC_USER_REQUESTS: 'true'", compose)
        self.assertIn("XIAOYOU_LIFE_PHOTO_RESPONSE_FORMAT: 'url'", compose)
        self.assertIn("XIAOYOU_LIFE_PHOTO_REFERENCE_TARGET_MAX_KB: '500'", compose)
        self.assertIn("SEEDREAM_MODEL: 'doubao-seedream-5-0-lite-260128'", compose)
        self.assertIn("XIAOYOU_LIFE_PHOTO_SIZE: '2K'", compose)


if __name__ == "__main__":
    unittest.main()
