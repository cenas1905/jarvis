import unittest
from unittest.mock import patch

from actions.call_bridge import choose_bridge_output, phone_call_bridge


class CallBridgeTests(unittest.TestCase):
    def test_prefers_mme_cable_input(self):
        devices = [
            {"index": 6, "name": "CABLE Input (VB-Audio Virtual Cable)",
             "maxOutputChannels": 2, "host_api_name": "Windows WASAPI"},
            {"index": 15, "name": "CABLE Input (VB-Audio Virtual Cable)",
             "maxOutputChannels": 2, "host_api_name": "MME"},
        ]
        self.assertEqual(choose_bridge_output(devices)["index"], 15)

    def test_ignores_recording_cable_output(self):
        devices = [
            {"index": 2, "name": "CABLE Output (VB-Audio Virtual Cable)",
             "maxOutputChannels": 0, "host_api_name": "MME"},
        ]
        self.assertIsNone(choose_bridge_output(devices))

    def test_requires_matching_cable_input(self):
        devices = [
            {"index": 1, "name": "Headphones (Realtek Audio)",
             "maxOutputChannels": 2, "host_api_name": "MME"},
        ]
        self.assertIsNone(choose_bridge_output(devices))

    @patch("actions.call_bridge.save_app_config")
    @patch("actions.call_bridge.IS_WIN", True)
    def test_enable_only_sets_local_preference(self, save):
        result = phone_call_bridge("enable")
        self.assertIn("açıldı", result)
        save.assert_called_once_with({"phone_call_bridge_enabled": True})


if __name__ == "__main__":
    unittest.main()
