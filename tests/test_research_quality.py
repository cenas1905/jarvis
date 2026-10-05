import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from actions.research import _research_failure, _research_input, research


class ResearchQualityTests(unittest.TestCase):
    def test_deep_research_requires_comparison_uncertainty_and_sources(self):
        prompt = _research_input("Robot arm servos", deep=True).casefold()
        self.assertIn("çok adımlı", prompt)
        self.assertIn("birbirinden bağımsız kaynakları karşılaştır", prompt)
        self.assertIn("belirsizlikler", prompt)
        self.assertIn("kaynak listesi", prompt)

    def test_quick_research_requests_citations_and_dates(self):
        prompt = _research_input("Today's news", deep=False).casefold()
        self.assertIn("google search", prompt)
        self.assertIn("kaynak bağlantılarıyla", prompt)
        self.assertIn("tarihi", prompt)

    def test_failure_text_distinguishes_quota_from_invalid_key_without_echoing_secret(self):
        quota = _research_failure("Araştırma", RuntimeError("429 RESOURCE_EXHAUSTED quota"))
        invalid = _research_failure("Araştırma", RuntimeError("API_KEY_INVALID"))
        self.assertIn("proje kotası", quota)
        self.assertNotIn("masaüstündeki API SETTINGS", quota)
        self.assertIn("API SETTINGS", invalid)
        self.assertNotIn("API_KEY_INVALID", invalid)

    def test_quick_research_is_stateless_and_keeps_citations(self):
        interaction = SimpleNamespace(
            id="test-report",
            output_text="Güncel bulgu [1].",
            steps=[SimpleNamespace(type="model_output", content=[SimpleNamespace(
                type="text", text="Güncel bulgu [1].", annotations=[SimpleNamespace(
                    type="url_citation", url="https://example.org/source", title="Primary source"
                )]
            )])],
        )
        api = SimpleNamespace(create=Mock(return_value=interaction))
        client = SimpleNamespace(interactions=api, close=Mock())
        with patch("actions.research._client", return_value=(client, "")), \
             patch("actions.deepseek_api.has_deepseek_api_key", return_value=False), \
             patch("actions.research._write_report", return_value="report.md"):
            result = research("quick_research", "Test topic")

        self.assertIn("https://example.org/source", result)
        self.assertIn("Primary source", result)
        self.assertFalse(api.create.call_args.kwargs["store"])


if __name__ == "__main__":
    unittest.main()
