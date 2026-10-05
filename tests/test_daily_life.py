"""Offline tests: temporary stores only; no personal mail, screen or notifications."""
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock
from actions import daily_life as daily
from actions import google_personal as google
from actions import reminder_service


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        for p in (patch.object(daily, "DB_PATH", self.base / "test.sqlite3"),
                  patch.object(reminder_service, "ensure_worker", return_value=True)):
            p.start()
            self.addCleanup(p.stop)

    def add(self, title="Test", **kwargs):
        return daily.daily_life("task_add", title=title, **kwargs)

    def test_relative_time(self):
        now = dt.datetime(2026, 9, 23, 12, 0).astimezone()
        self.assertEqual(daily.parse_due("10 dakika sonra", now), now.timestamp() + 600)
        self.assertEqual(daily.parse_due("2 saat sonra", now), now.timestamp() + 7200)
        self.assertEqual(dt.datetime.fromtimestamp(daily.parse_due("yarın 18:00", now)).day, 24)

    def test_ambiguous_and_past_rejected(self):
        for value in ("akşama", "2020-01-01T10:00", "yarın", "2028-01-01", "0 dakika sonra", "yarın 26:00"):
            with self.assertRaises(ValueError):
                daily.parse_due(value)

    def test_task_no_time_no_notification(self):
        result = self.add(list_name="Alışveriş")
        self.assertIn("bildirim kurulmadı", result)
        self.assertIsNone(daily.claim_due())
        self.assertIn("Test", daily.list_tasks("alisveris"))

    def test_persistence_duplicate_and_completion(self):
        self.add()
        self.assertIn("Zaten", self.add())
        rows = daily.task_rows()
        self.assertEqual(len(rows), 1)
        self.assertIn("Tamamlandı", daily.change_task("done", task_id=rows[0]["id"]))
        self.assertEqual(daily.task_rows(), [])

    def test_simultaneous_writes(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda n: self.add(title=f"Task {n}"), range(24)))
        self.assertEqual(len(daily.task_rows()), 24)
        self.assertTrue(all("eklendi" in result for result in results))

    def test_reminder_lease_and_ack(self):
        self.add(due="10 dakika sonra")
        future = time.time() + 610
        row = daily.claim_due(future)
        self.assertIsNotNone(row)
        self.assertIsNone(daily.claim_due(future + 1))
        self.assertIsNotNone(daily.claim_due(future + 121))
        daily.acknowledge(row["id"])
        self.assertIsNone(daily.claim_due(future + 300))

    def test_only_one_worker_claims(self):
        self.add(due="1 dakika sonra")
        future = time.time() + 65
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(lambda _: daily.claim_due(future), range(4)))
        self.assertEqual(sum(r is not None for r in rows), 1)

    def test_snooze_and_delete(self):
        self.add(due="1 dakika sonra")
        row = daily.task_rows()[0]
        daily.acknowledge(row["id"])
        daily.change_task("snooze", task_id=row["id"], due="2 saat sonra")
        updated = daily.task_rows()[0]
        self.assertIsNone(updated["notified"])
        self.assertGreater(updated["due"], row["due"])
        self.assertIn("Silindi", daily.change_task("delete", task_id=row["id"]))
        self.assertEqual(daily.task_rows(), [])

    def test_ambiguous_delete_no_changes(self):
        self.add(list_name="A")
        self.add(list_name="B")
        self.assertIn("Tek kayıt", daily.change_task("delete", title="Test"))
        self.assertEqual(len(daily.task_rows()), 2)

    def test_project_lifecycle(self):
        self.assertIn("kaydedildi", daily.project("save", "Robot Kol", "Mekanik seçiliyor", "Motorları ölç", "https://example.com", str(self.base)))
        self.assertIn("Motorları ölç", daily.project("resume", "robot kol"))
        self.assertIn("Robot Kol", daily.project("resume"))
        self.assertIn("silindi", daily.project("delete", "Robot Kol"))
        self.assertIn("kayıtlı proje yok", daily.project("list").lower())

    def test_project_unsafe_link_and_missing_folder(self):
        for extra in ({"links": "file:///C:/secret"}, {"folder": str(self.base / "missing")}):
            result = daily.daily_life("project_save", name="X", summary="Test", **extra)
            self.assertIn("tamamlanmadı", result)
        self.assertIn("Kayıtlı proje yok", daily.project("list"))

    def test_migrate_legacy_once(self):
        source = self.base / "legacy.json"
        source.write_text(json.dumps({"reminders": [{"id": "old1", "title": "legacy", "due_iso": "2026-09-20T10:00:00"}]}), encoding="utf-8")
        with patch.object(daily, "data_path", return_value=source):
            daily.import_legacy_reminders()
            daily.import_legacy_reminders()
        rows = daily.task_rows()
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0]["due"], 0)

    def test_briefing_missing_accounts(self):
        with patch("actions.google_personal.connected", return_value=False), patch("actions.calendar.get_calendar_events", return_value="Yerel etkinlik"), patch("actions.win_organizer.backend_label", return_value="Yerel"), patch("actions.outlook_mail.get_recent_emails", return_value="Hesap bağlı değil"):
            self.add(title="Ekmek", list_name="Alışveriş")
            text = daily.daily_briefing()
        self.assertIn("Ekmek", text)
        self.assertIn("Yerel etkinlik", text)
        self.assertIn("Hesap bağlı değil", text)

    def test_briefing_keeps_local_and_google(self):
        with patch("actions.google_personal.connected", return_value=True), patch("actions.google_personal.google_events", return_value="Google etkinlik"), patch("actions.google_personal.google_emails", return_value="Google mail"), patch("actions.calendar.get_calendar_events", return_value="Yerel etkinlik"), patch("actions.win_organizer.backend_label", return_value="Yerel"):
            result = daily.daily_briefing()
        for text in ("Yerel etkinlik", "Google etkinlik", "Google mail"):
            self.assertIn(text, result)

    def test_worker_failure_is_not_success(self):
        with patch.object(reminder_service, "ensure_worker", return_value=False):
            result = self.add(due="10 dakika sonra")
        self.assertIn("UYARI", result)
        self.assertEqual(len(daily.task_rows()), 1)


class IntegrationTests(unittest.TestCase):
    def test_notification_buttons_without_showing_personal_data(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(daily, "change_task") as change:
                window = reminder_service.build_notification(root, {"id": "test-id", "title": "Test", "due": time.time()}, visible=False)
                root.update_idletasks()
                buttons = [button for child in window.winfo_children() if isinstance(child, tk.Frame) for button in child.winfo_children()]
                self.assertEqual([b.cget("text") for b in buttons], ["Tamamlandı", "10 dk ertele", "Kapat"])
                buttons[1].invoke()
                change.assert_called_once_with("snooze", task_id="test-id", due="10 dakika sonra")
        finally:
            root.destroy()

    def test_existing_memory_save_recall_delete(self):
        from memory import memory_manager as memory
        with tempfile.TemporaryDirectory() as directory, patch.object(memory, "MEMORY_FILE", Path(directory) / "memory.json"):
            memory.update_memory({"preferences": {"test": {"value": "Kısa konuş"}}})
            self.assertIn("Kısa konuş", memory.recall_memory("test"))
            memory.delete_memory("preferences", "test")
            self.assertNotIn("test", memory.load_memory().get("preferences", {}))

    def test_tool_declarations_validate(self):
        from tool_defs import TOOL_DECLARATIONS
        from google.genai import types
        names = [item["name"] for item in TOOL_DECLARATIONS]
        self.assertEqual(len(names), len(set(names)))
        for declaration in TOOL_DECLARATIONS:
            types.FunctionDeclaration(**declaration)
        self.assertIn("daily_life", names)
        self.assertIn("daily_text", names)

    def test_windows_reminder_routes_to_new_store(self):
        from actions import reminders
        with patch.object(reminders, "IS_WIN", True), patch.object(daily, "daily_life", return_value="ok") as handler:
            self.assertEqual(reminders.add_reminder("Test", "10 dakika sonra"), "ok")
        handler.assert_called_once()

    def test_routine_folders_no_execution(self):
        from memory import routines
        from memory import memory_manager
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch.object(routines, "ROUTINES_FILE", base / "routines.json"), patch.object(memory_manager, "MEMORY_FILE", base / "memory.json"):
                self.assertIn("kaydedildi", routines.personal_routine("save", "Test", folders=str(base)))
                with patch.object(routines.os, "startfile") as opened:
                    self.assertIn("Klasör açıldı", routines.personal_routine("run", "Test"))
                    opened.assert_called_once_with(str(base))
                self.assertIn("Klasörler", routines.personal_routine("save", "Bad", folders=str(base / "run.exe")))

    def test_clipboard_only_when_requested(self):
        from actions import daily_text
        with patch.object(daily_text, "clipboard_text") as clipboard:
            self.assertIn("Ctrl+C", daily_text.daily_text())
            clipboard.assert_not_called()

    def test_draft_never_sends(self):
        from actions import daily_text
        fake = MagicMock()
        fake.__enter__.return_value.models.generate_content.return_value.text = "Merhaba"
        with patch.object(daily_text, "has_deepseek_api_key", return_value=False), patch.object(daily_text, "get_app_config_value", return_value="test"), patch.object(daily_text.genai, "Client", return_value=fake):
            result = daily_text.daily_text("draft", "hello")
        self.assertIn("gönderilmedi", result)
        self.assertIn("Merhaba", result)

    def test_google_dpapi_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(google, "TOKEN_FILE", Path(directory) / "token.dpapi"):
            google._save_token({"refresh_token": "only-a-test"})
            self.assertNotIn(b"only-a-test", google.TOKEN_FILE.read_bytes())
            self.assertEqual(google._read_token()["refresh_token"], "only-a-test")

    def test_google_scopes_readonly(self):
        self.assertTrue(all(scope.endswith(".readonly") for scope in google.SCOPES))

    def test_google_missing_account_no_network(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(google, "TOKEN_FILE", Path(directory) / "missing"), patch.object(google, "_request") as request:
            with self.assertRaises(ValueError):
                google.access_token()
            request.assert_not_called()

    def test_screen_prompt_treats_image_as_data(self):
        from actions.screen_vision import _vision_prompt
        self.assertIn("güvenilmeyen", _vision_prompt("hata nedir", "App", "Window"))

    def test_screen_temp_image_is_deleted(self):
        from actions import screen_vision as screen
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.png"
            Image.new("RGB", (100, 100), "white").save(path)
            with patch.object(screen, "_capture_active_window", return_value=(True, "", {"image_path": str(path), "owner_name": "Test", "window_title": "Fake"})), patch.object(screen, "_analyze_with_gemini", return_value="Test sonucu"):
                self.assertIn("Test sonucu", screen.analyze_screen("Test"))
            self.assertFalse(path.exists())

    def test_agent_routes(self):
        from jarvis_web import agent
        with patch.object(agent, "daily_life", return_value="test"):
            self.assertEqual(agent.execute_tool("daily_life", {"action": "status"}), "test")


if __name__ == "__main__":
    unittest.main()
