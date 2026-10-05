import time
import unittest
from unittest.mock import patch

from actions import phone_calls


class PhoneCallTests(unittest.TestCase):
    def setUp(self):
        phone_calls._reset_pending_for_tests()
        self.config_patch = patch("actions.phone_calls.save_app_config")
        self.config_patch.start()

    def tearDown(self):
        phone_calls._reset_pending_for_tests()
        self.config_patch.stop()

    @patch("actions.phone_calls._confirm_phone_link")
    @patch("actions.phone_calls._stage_phone_link", return_value=(42, "Babam", "Babam"))
    def test_direct_call_dials_exact_contact_once(self, stage, dial):
        result = phone_calls.phone_call(
            "dial", recipient_name="Babam", message="Eve ne zaman geleceğini sor"
        )
        self.assertIn("arama başlatma", result)
        stage.assert_called_once_with("Babam", "")
        dial.assert_called_once_with(42, "Babam", "Babam")

    @patch("actions.phone_calls._confirm_phone_link")
    @patch("actions.phone_calls._stage_phone_link", return_value=(42, "Babam", "Babam"))
    def test_prepare_never_dials_and_returns_confirmation_prompt(self, stage, dial):
        result = phone_calls.phone_call(
            "prepare", recipient_name="Babam", message="Eve gelirken dört ekmek al."
        )
        self.assertIn("Henüz arama yapılmadı", result)
        self.assertIn("dört ekmek", result)
        stage.assert_called_once_with("Babam", "")
        dial.assert_not_called()

    @patch("actions.phone_calls._confirm_phone_link")
    @patch("actions.phone_calls._stage_phone_link", return_value=(42, "Babam", "Babam"))
    def test_only_matching_confirmation_starts_call(self, stage, dial):
        phone_calls.phone_call("prepare", recipient_name="Babam")
        wrong = phone_calls.phone_call("confirm", recipient_name="Annem")
        self.assertIn("arama yapılmadı", wrong)
        dial.assert_not_called()

        result = phone_calls.phone_call("confirm", recipient_name="Babam")
        self.assertIn("yanıtlandığı doğrulanmadı", result)
        dial.assert_called_once_with(42, "Babam", "Babam")

    @patch("actions.phone_calls._confirm_phone_link")
    @patch("actions.phone_calls._stage_phone_link", return_value=(42, "Babam", "Babam"))
    def test_cancel_clears_pending_without_dialing(self, stage, dial):
        phone_calls.phone_call("prepare", recipient_name="Babam")
        result = phone_calls.phone_call("cancel")
        self.assertIn("kimse aranmadı", result)
        self.assertIn("güncel bir arama yok", phone_calls.phone_call("confirm", recipient_name="Babam"))
        dial.assert_not_called()

    @patch("actions.phone_calls._confirm_phone_link")
    @patch("actions.phone_calls._stage_phone_link", return_value=(42, "numara", "+900000000000"))
    def test_number_requires_same_number_confirmation(self, stage, dial):
        phone_calls.phone_call("prepare", phone_number="+900000000000")
        wrong = phone_calls.phone_call("confirm", phone_number="+900000000000")
        self.assertIn("eşleşmiyor", wrong)
        dial.assert_not_called()

        phone_calls.phone_call("confirm", phone_number="+900000000000")
        dial.assert_called_once_with(42, "numara", "+900000000000")

    @patch("actions.phone_calls._stage_phone_link")
    def test_requires_exactly_one_recipient_input(self, stage):
        self.assertIn("Tek bir", phone_calls.phone_call("prepare"))
        self.assertIn("Tek bir", phone_calls.phone_call(
            "prepare", recipient_name="Babam", phone_number="+900000000000"
        ))
        stage.assert_not_called()

    @patch("actions.phone_calls._confirm_phone_link")
    @patch("actions.phone_calls._stage_phone_link", return_value=(42, "Babam", "Babam"))
    def test_confirmation_expires(self, stage, dial):
        phone_calls.phone_call("prepare", recipient_name="Babam")
        phone_calls._pending["expires_at"] = time.monotonic() - 1
        result = phone_calls.phone_call("confirm", recipient_name="Babam")
        self.assertIn("güncel bir arama yok", result)
        dial.assert_not_called()

    def test_turkish_name_normalization_and_phone_validation(self):
        self.assertEqual(phone_calls._normalize_name("İpek"), phone_calls._normalize_name("ipek"))
        self.assertEqual(phone_calls._normalize_phone("+900000000000"), "+900000000000")
        with self.assertRaises(ValueError):
            phone_calls._normalize_phone("123")

    @patch("actions.phone_calls._confirm_phone_link")
    @patch("actions.phone_calls._stage_phone_link", return_value=(42, "Babam", "Babam"))
    def test_live_conversation_requires_a_confirmed_call(self, stage, dial):
        self.assertIn("Aktif JARVIS araması yok", phone_calls.phone_call("conversation_start"))
        phone_calls.phone_call("prepare", recipient_name="Babam", message="Randevu al")
        phone_calls.phone_call("confirm", recipient_name="Babam")
        self.assertFalse(phone_calls.phone_call_conversation_active())
        self.assertTrue(phone_calls.phone_call_waiting_for_answer())
        result = phone_calls.phone_call("conversation_start")
        self.assertIn("canlı dinleme modu açıldı", result)
        self.assertIn("Randevu al", result)
        self.assertTrue(phone_calls.phone_call_conversation_active())
        self.assertFalse(phone_calls.phone_call_waiting_for_answer())
        phone_calls.phone_call("conversation_stop")
        self.assertFalse(phone_calls.phone_call_conversation_active())
        self.assertFalse(phone_calls.phone_call_waiting_for_answer())
        dial.assert_called_once()


if __name__ == "__main__":
    unittest.main()
