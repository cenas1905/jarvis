"""Offline guardrails for Gemini's natural-language tool routing prompt."""
from pathlib import Path
import re
import unittest

from tool_defs import TOOL_DECLARATIONS


ROOT = Path(__file__).resolve().parents[1]
PROMPT = (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")


class PromptRoutingTests(unittest.TestCase):
    def test_every_declared_tool_has_a_dispatch_branch(self):
        declared = {tool["name"] for tool in TOOL_DECLARATIONS}
        dispatched = set(re.findall(r'(?:if|elif) name == [\"\']([^\"\']+)', MAIN))
        self.assertEqual(declared, dispatched)

    def test_prompt_teaches_semantic_paraphrase_handling(self):
        self.assertIn("DOĞAL DİLİ ANLAMA", PROMPT)
        self.assertIn("eş anlamlılar", PROMPT)
        self.assertIn("ASR", PROMPT)
        self.assertIn("aynı niyet farklı söylenmişse aynı aracı kullan", PROMPT.casefold())
        self.assertIn("netleştirme sorusu sor", PROMPT)

    def test_prompt_requests_private_reasoning_and_bounded_initiative(self):
        lowered = PROMPT.casefold()
        self.assertIn("kendi içinde değerlendir", lowered)
        self.assertIn("düşünce zincirini", lowered)
        self.assertIn("kısa karar özeti", lowered)
        self.assertIn("tek bir ek öneriyi", lowered)

    def test_prompt_routes_deep_research_and_api_key_recovery(self):
        lowered = PROMPT.casefold()
        self.assertIn("araç seçimi", lowered)
        self.assertIn("open_api_settings çağır", lowered)
        descriptions = {tool["name"]: tool["description"] for tool in TOOL_DECLARATIONS}
        self.assertIn("api_settings", descriptions["open_api_settings"].lower())

    def test_unknown_ui_task_uses_observe_act_verify_fallback(self):
        lowered = PROMPT.casefold()
        self.assertIn("genel fallback olarak desktop_control kullan", lowered)
        self.assertIn("list_windows/inspect", lowered)
        self.assertIn("sonucu yeniden inceleyerek doğrula", lowered)
        self.assertIn("güvenli kurulum yolunu söyle", lowered)

    def test_prompt_does_not_advertise_unregistered_health_tool(self):
        self.assertNotIn("get_health_data", PROMPT)

    def test_app_tool_descriptions_match_windows_environment(self):
        descriptions = {tool["name"]: tool["description"] for tool in TOOL_DECLARATIONS}
        self.assertIn("Windows", descriptions["open_app"])
        self.assertIn("PowerShell", descriptions["shell_run"])
        self.assertIn("JARVIS yerel takvimi", descriptions["get_calendar_events"])


if __name__ == "__main__":
    unittest.main()
