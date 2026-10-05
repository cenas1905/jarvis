"""Robot kol seri protokolunun donanimsiz, hareket uretmeyen testleri."""

from collections import deque
import unittest

from actions.robot_arm import RobotArmBridge


class FakeSerial:
    def __init__(self):
        self.is_open = True
        self.commands = []
        self.replies = deque()

    def write(self, data):
        command = data.decode("ascii").strip()
        self.commands.append(command)
        if command.startswith("servo "):
            self.replies.append(f"OK {command} taban selected; PWM henuz acilmadi\n")
        elif command.startswith("jog "):
            self.replies.extend(["OK jog started target_us=1560\n",
                                 "DONE servo 1 us=1560\n"])
        elif command.startswith("step "):
            self.replies.extend(["OK step started count=8\n",
                                 "DONE step; coils=off\n"])
        elif command == "stop":
            self.replies.append("OK stop\n")
        elif command == "!":
            self.replies.append("OK emergency release\n")
        elif command == "status":
            self.replies.append("STATUS selected=none moving=none step_remaining=0\n")

    def readline(self):
        return self.replies.popleft().encode() if self.replies else b""

    def close(self):
        self.is_open = False


class RobotArmBridgeTests(unittest.TestCase):
    def setUp(self):
        self.bridge = RobotArmBridge()
        self.fake = FakeSerial()
        self.bridge._serial = self.fake

    def test_jog_selects_one_servo_and_waits_for_done(self):
        self.assertIn("DONE servo 1", self.bridge.run("jog", 1, 10))
        self.assertEqual(self.fake.commands, ["servo 1", "jog 10"])

    def test_bounds_rejected_before_serial_command(self):
        self.assertIn("olmali", self.bridge.run("jog", 1, 11))
        self.assertIn("olmali", self.bridge.run("step", value=33))
        self.assertIn("olmali", self.bridge.run("select", servo_index=0))
        self.assertEqual(self.fake.commands, [])

    def test_step_then_stop_release(self):
        self.assertIn("coils=off", self.bridge.run("step", value=8))
        self.assertEqual(self.bridge.run("stop"), "OK stop")
        self.assertEqual(self.bridge.run("release"), "OK emergency release")
        self.assertEqual(self.fake.commands, ["step 8", "stop", "!"])

    def test_no_automatic_motion_on_status(self):
        self.assertIn("STATUS", self.bridge.run("status"))
        self.assertEqual(self.fake.commands, ["status"])


if __name__ == "__main__":
    unittest.main()
