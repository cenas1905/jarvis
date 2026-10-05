import unittest

from actions.api_errors import classify_gemini_api_error


class GeminiApiErrorTests(unittest.TestCase):
    def test_quota_is_not_misreported_as_invalid_key(self):
        self.assertEqual(
            classify_gemini_api_error(RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")),
            "quota",
        )

    def test_revoked_key_has_separate_classification(self):
        self.assertEqual(
            classify_gemini_api_error(RuntimeError("API_KEY_INVALID: key was revoked")),
            "invalid_key",
        )

    def test_permission_and_unknown_errors_are_not_guessed_as_quota(self):
        self.assertEqual(classify_gemini_api_error(RuntimeError("403 PERMISSION_DENIED")), "permission")
        self.assertEqual(classify_gemini_api_error(RuntimeError("socket timed out")), "other")


if __name__ == "__main__":
    unittest.main()
