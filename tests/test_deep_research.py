import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from actions import deepseek_deep_research as deep
from actions import research as jobs
from actions import research_excel as excel


def result_fixture():
    return {"report": "Öneri ve belirsizlik [S1]", "headers": ["Fikir", "Kanıt"],
            "rows": [["Randevu sitesi", "[S1]"]], "sources": [{"id": "S1", "title": "Kaynak", "url": "https://example.org", "coverage": "Sayfa metni"}],
            "pages_read": 1}


class DeepPipelineTests(unittest.TestCase):
    def test_multiple_queries_read_pages_and_keep_source_coverage(self):
        items = [{"title": "Official", "url": "https://example.org/a", "snippet": "Example"},
                 {"title": "Other", "url": "https://example.net/b", "snippet": "Limited evidence"}]
        def read(url):
            if "example.net" in url:
                raise ValueError("blocked")
            return "Readable primary source content " * 10
        with patch.object(deep, "chat", side_effect=[json.dumps({"queries": ["need", "competitors", "cost"]}), json.dumps(result_fixture())]) as chat, \
             patch.object(deep, "_search", return_value=items) as search, patch.object(deep, "read_page", side_effect=read):
            result = deep.run_research("Website ideas")
        self.assertEqual(search.call_count, 3)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(result["pages_read"], 1)
        self.assertEqual(len(result["sources"]), 2)
        self.assertIn("sayfa okunamadı", result["report"])
        self.assertIn("https://example.org/a", result["report"])

    def test_invalid_citations_and_table_shape_are_rejected(self):
        value = result_fixture()
        value["report"] = "Unsupported [S99]"
        with self.assertRaises(ValueError):
            deep.validate_result(value, value["sources"])
        value = result_fixture()
        value["rows"] = [["one cell"]]
        with self.assertRaises(ValueError):
            deep.validate_result(value, value["sources"])

    def test_secret_model_key_is_never_sent_to_web_pages(self):
        response = Mock(status=200, headers={"Content-Type": "text/html"})
        response.stream.return_value = [b"<p>" + b"Public article " * 50 + b"</p>"]
        with patch.object(deep.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]), \
             patch.object(deep.urllib3, "HTTPSConnectionPool") as pool:
            pool.return_value.request.return_value = response
            deep.read_page("https://example.org/article")
        self.assertEqual(pool.call_args.args[0], "93.184.216.34")
        self.assertEqual(pool.call_args.kwargs["server_hostname"], "example.org")
        headers = pool.return_value.request.call_args.kwargs["headers"]
        self.assertNotIn("Authorization", headers)
        self.assertNotIn("Cookie", headers)

    def test_private_targets_blocked_before_connect(self):
        with patch.object(deep.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]), \
             patch.object(deep.urllib3, "HTTPSConnectionPool") as pool:
            with self.assertRaises(ValueError):
                deep.read_page("https://example.org")
            pool.assert_not_called()
        for url in ("file:///C:/secret", "https://user:pass@example.org", "http://example.org", "https://example.org:8000"):
            with self.assertRaises(ValueError):
                deep._public_target(url)

    def test_script_content_excluded(self):
        parser = deep.PageText()
        parser.feed("<p>Useful content</p><script>ignore instructions</script><style>bad</style>")
        self.assertEqual(parser.parts, ["Useful content"])


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        for p in (patch.object(jobs, "_STORE_PATH", self.base / "jobs.json"),
                  patch.object(jobs, "_REPORT_DIR", self.base / "reports"),
                  patch("actions.deepseek_api.has_deepseek_api_key", return_value=True),
                  patch.object(jobs, "_client", side_effect=AssertionError("Must not use Gemini")),
                  patch.object(jobs.threading, "Thread")):
            p.start()
            self.addCleanup(p.stop)

    def test_start_and_status_no_gemini_or_extra_confirmation(self):
        message = jobs.research("deep_research", "Website ideas", export_excel=True)
        self.assertIn("DeepSeek", message)
        job = jobs._load_store()["jobs"][-1]
        self.assertTrue(job["export_excel"])
        self.assertIn("sürüyor", jobs.research("research_status", interaction_id=job["id"]))
        jobs.research("deep_research", "Website ideas")
        self.assertEqual(len(jobs._load_store()["jobs"]), 1)

    def test_legacy_prepare_and_start_choose_deepseek(self):
        jobs.research("prepare_deep", "Website ideas", export_excel=True)
        self.assertEqual(jobs._load_store()["prepared"]["provider"], "deepseek")
        self.assertIn("DeepSeek", jobs.research("start_deep"))

    def test_worker_persists_report_and_exports_once(self):
        jobs.research("deep_research", "Website ideas", export_excel=True)
        job = jobs._load_store()["jobs"][-1]
        with patch.object(deep, "run_research", return_value=result_fixture()), \
             patch.object(excel, "export_research", return_value=str(self.base / "new.xlsx")) as exported:
            jobs._deepseek_worker(job["id"], job["query"])
        stored = jobs._load_store()["jobs"][-1]
        self.assertEqual(stored["status"], "completed")
        self.assertTrue(Path(stored["report_path"]).exists())
        exported.assert_called_once()
        self.assertIn("new.xlsx", jobs.research("research_status"))

    def test_excel_failure_preserves_completed_report(self):
        jobs.research("deep_research", "Website ideas", export_excel=True)
        job = jobs._load_store()["jobs"][-1]
        with patch.object(deep, "run_research", return_value=result_fixture()), patch.object(excel, "export_research", side_effect=RuntimeError("Excel not available")):
            jobs._deepseek_worker(job["id"], job["query"])
        status = jobs.research("research_status")
        self.assertIn("tamamlandı", status)
        self.assertIn("Excel aktarımı tamamlanamadı", status)

    def test_provider_failure_does_not_fall_back_to_gemini(self):
        jobs.research("deep_research", "Website ideas")
        job = jobs._load_store()["jobs"][-1]
        with patch.object(deep, "run_research", side_effect=RuntimeError("private exception detail")):
            jobs._deepseek_worker(job["id"], job["query"])
        self.assertEqual(jobs._load_store()["jobs"][-1]["status"], "failed")
        self.assertNotIn("private exception", jobs.research("research_status"))


class ExcelTests(unittest.TestCase):
    def test_excel_formula_like_cells_are_literal_text(self):
        for text in ('=HYPERLINK("bad")', '+cmd', '-cmd', '@SUM(A1)', '  =1+1'):
            self.assertTrue(excel._text(text).startswith("'"))

    def test_existing_file_is_not_opened_or_overwritten(self):
        with tempfile.TemporaryDirectory() as directory, patch("win32com.client.DispatchEx") as app:
            target = Path(directory) / "existing.xlsx"
            target.write_bytes(b"original")
            with self.assertRaises(ValueError):
                excel.export_research("test", result_fixture(), target)
            self.assertEqual(target.read_bytes(), b"original")
            app.assert_not_called()


if __name__ == "__main__":
    unittest.main()
