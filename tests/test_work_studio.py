import asyncio
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace

from actions import work_studio as studio


class StudioTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for p in (patch.object(studio, "DB_PATH", Path(temp.name)/"work.sqlite3"),
                  patch.object(studio, "REPORT_DIR", Path(temp.name)/"reports")):
            p.start()
            self.addCleanup(p.stop)

    def create(self):
        with patch.object(studio, "_launch"):
            studio.work_studio("website_brief", brief="Yerel işletmeler için site hizmetleri")
        return studio._job()["id"]

    def test_client_partial_update_keeps_audience(self):
        studio.work_studio("client_save", client_name="Deneme", audience="yerel işletmeler", sector="tasarım")
        studio.work_studio("client_save", client_name="Deneme", tone="samimi")
        self.assertEqual(studio._client("Deneme")["audience"], "yerel işletmeler")
        self.assertEqual(studio._client("Deneme")["tone"], "samimi")

    def test_duplicate_job_only_launches_once(self):
        with patch.object(studio, "_launch") as launch:
            studio.work_studio("content_plan", brief="Web tasarım hizmetleri")
            response = studio.work_studio("content_plan", brief="Web tasarım hizmetleri")
        launch.assert_called_once()
        self.assertIn("zaten", response)

    def test_completed_output_persists_and_escapes_html(self):
        key = self.create()
        with patch.object(studio, "_generate", return_value=("<script>alert(1)</script>\nSite planı", "test")):
            studio._run_job(key)
        job = studio._job(key)
        self.assertEqual(job["status"], "completed")
        self.assertIn("Site planı", studio.work_studio("status", job_id=key))
        page = Path(job["report"]).with_suffix(".html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", page)

    def test_provider_failure_is_saved_not_reported_as_complete(self):
        key = self.create()
        with patch.object(studio, "_generate", side_effect=RuntimeError("private data")):
            studio._run_job(key)
        response = studio.work_studio("status", job_id=key)
        self.assertIn("failed", response)
        self.assertNotIn("private data", response)

    def test_launch_failure_releases_running_slot(self):
        with patch.object(studio, "_launch", side_effect=OSError("test")):
            studio.work_studio("reel_script", brief="Site hizmeti")
        self.assertEqual(studio._job()["status"], "failed")


class BusyGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_exception_releases_busy_guard_and_keeps_awake(self):
        from main import JarvisLive
        from local_wake import ConversationGate
        app = object.__new__(JarvisLive)
        app._standby = False
        app._conversation_gate = ConversationGate(timeout=60)
        async def fail(call):
            self.assertEqual(app._active_tool_count, 1)
            self.assertTrue(app._conversation_gate.awake())
            raise RuntimeError("test")
        app._execute_tool_impl = fail
        with self.assertRaises(RuntimeError):
            await app._execute_tool(SimpleNamespace(name="test"))
        self.assertEqual(app._active_tool_count, 0)
        self.assertTrue(app._conversation_gate.awake())
