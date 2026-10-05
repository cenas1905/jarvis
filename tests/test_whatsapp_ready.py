import unittest
from unittest.mock import MagicMock, patch
from actions.whatsapp import _ready_send_controls, _auto_send_win


def control(text):
    item = MagicMock()
    item.is_visible.return_value = True
    item.is_enabled.return_value = True
    item.get_value.return_value = text
    item.window_text.return_value = text
    return item


class ReadySendTests(unittest.TestCase):
    def window(self, edits, buttons):
        w = MagicMock()
        w.descendants.side_effect = lambda control_type: edits if control_type == "Edit" else buttons
        return w

    def test_matching_draft_and_button(self):
        edit, button = control("Merhaba"), control("Gönder")
        self.assertEqual(_ready_send_controls(self.window([edit], [button]), "Merhaba"), (edit, button))

    def test_wrong_draft_is_not_sent(self):
        self.assertIsNone(_ready_send_controls(self.window([control("Başka mesaj")], [control("Send")]), "Merhaba"))

    def test_ambiguous_buttons_are_rejected(self):
        self.assertIsNone(_ready_send_controls(self.window([control("Merhaba")], [control("Send"), control("Gönder")]), "Merhaba"))

    def test_message_path_does_not_use_blind_enter(self):
        with patch("actions.whatsapp._send_ready_composer_win", return_value=(True, "ok")) as send, patch("actions.whatsapp._press_win") as press:
            self.assertTrue(_auto_send_win(7, "Merhaba")[0])
            send.assert_called_once_with("Merhaba")
            press.assert_not_called()
