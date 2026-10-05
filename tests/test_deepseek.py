import unittest
from unittest.mock import Mock, patch

from actions import deepseek_api, deepseek_research
from actions import daily_text as daily_text_module
from actions import research as research_module


class DeepSeekApiTests(unittest.TestCase):
    @patch("actions.deepseek_api.requests.post")
    @patch("actions.deepseek_api.get_deepseek_api_key", return_value="test-secret")
    def test_chat_uses_flash_nonthinking_and_returns_text(self, _key, post):
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": "Kısa yanıt"}}]}
        post.return_value = response

        result = deepseek_api.chat([{"role": "user", "content": "Merhaba"}], max_tokens=50)

        self.assertEqual(result, "Kısa yanıt")
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["json"]["model"], "deepseek-flash")
        self.assertEqual(kwargs["json"]["thinking"]["type"], "disabled")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-secret")

    @patch("actions.deepseek_api.requests.post")
    @patch("actions.deepseek_api.get_deepseek_api_key", return_value="test-secret")
    def test_http_error_never_echoes_key_or_body(self, _key, post):
        response = Mock(status_code=401, text="Bearer test-secret invalid")
        post.return_value = response

        with self.assertRaises(deepseek_api.DeepSeekAPIError) as raised:
            deepseek_api.chat([{"role": "user", "content": "x"}])

        self.assertNotIn("test-secret", str(raised.exception))


class DeepSeekResearchTests(unittest.TestCase):
    def test_bing_parser_extracts_source_metadata(self):
        parser = deepseek_research._BingResults()
        parser.feed(
            '<ol><li class="b_algo"><h2><a href="https://example.com/page">Example title</a></h2>'
            '<div class="b_caption"><p>Useful summary.</p></div></li></ol>'
        )
        self.assertEqual(parser.results, [{
            "url": "https://example.com/page",
            "title": "Example title",
            "snippet": "Useful summary.",
        }])

    @patch("actions.deepseek_research.chat", return_value="Bulgu [S1].")
    @patch("actions.deepseek_research._search")
    def test_report_keeps_source_urls_and_discloses_snippet_basis(self, search, _chat):
        search.return_value = [{
            "title": "Primary source",
            "url": "https://example.com/official",
            "snippet": "A short search result.",
        }]

        report = deepseek_research.quick_research("örnek soru")

        self.assertIn("arama özetlerine dayanır", report)
        self.assertIn("https://example.com/official", report)
        messages = _chat.call_args.args[0]
        self.assertIn("okuduğunu iddia etme", messages[0]["content"])

    @patch("actions.research._write_report")
    @patch("actions.research._client")
    @patch("actions.deepseek_research.quick_research", return_value="DeepSeek ile hızlı araştırma tamamlandı.")
    @patch("actions.deepseek_api.has_deepseek_api_key", return_value=True)
    def test_configured_deepseek_avoids_gemini_search(self, _has_key, quick, gemini_client, _write):
        result = research_module.research("quick_research", "güncel konu")

        self.assertTrue(result.startswith("DeepSeek ile"))
        quick.assert_called_once_with("güncel konu")
        gemini_client.assert_not_called()


class DeepSeekTextTests(unittest.TestCase):
    @patch("actions.daily_text.genai.Client")
    @patch("actions.daily_text.deepseek_chat", return_value="Özet")
    @patch("actions.daily_text.has_deepseek_api_key", return_value=True)
    def test_selected_text_uses_deepseek_when_configured(self, _has_key, deepseek, gemini):
        result = daily_text_module.daily_text(action="summarize", text="Bu kullanıcı metnidir.")

        self.assertEqual(result, "Özet")
        deepseek.assert_called_once()
        gemini.assert_not_called()


if __name__ == "__main__":
    unittest.main()
