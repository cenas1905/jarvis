import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock
from types import SimpleNamespace
from local_wake import is_standby_command


class StandbyTests(unittest.TestCase):
    def test_exact_commands(self):
        for text in ("Kapat!", "kapan", "Jarvis, kendini kapat.", "uykuya geç"):
            self.assertTrue(is_standby_command(text), text)
        for text in ("Spotify'ı kapat", "kapatma", "kapat sonra Excel'i aç",
                     "Sekmeyi kapat", "bilgisayarı kapat"):
            self.assertFalse(is_standby_command(text), text)

    def test_hide_does_not_exit_or_mute_wake_microphone(self):
        from main import JarvisLive
        ui=MagicMock()
        ui.muted=False
        app=JarvisLive(ui)
        app._webcam_streamer=MagicMock()
        app._webcam_streamer.is_active=False
        ui.root.after.reset_mock()
        app.request_standby()
        self.assertTrue(app._standby)
        self.assertTrue(app._sleep_requested)
        self.assertFalse(ui.muted)
        ui.root.after.assert_called_with(0,ui.root.withdraw)
        ui._shutdown.assert_not_called()


class StandbyAudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_cloud_audio_discarded_while_asleep(self):
        from main import JarvisLive
        app=object.__new__(JarvisLive)
        app._standby=True
        app.out_queue=asyncio.Queue()
        app.out_queue.put_nowait({"data":b"private", "captured_at":0})
        app.session=SimpleNamespace(send_realtime_input=AsyncMock())
        task=asyncio.create_task(app._send_realtime())
        await asyncio.sleep(.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        app.session.send_realtime_input.assert_not_awaited()


if __name__=="__main__":
    unittest.main()
