import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiting_audio_is_never_uploaded(self):
        from main import JarvisLive
        app=object.__new__(JarvisLive)
        app.ui=MagicMock()
        app.ui.muted=False
        app._paused=False
        app._is_speaking=False
        app._speaking_lock=threading.Lock()
        app.out_queue=asyncio.Queue()
        app.session=SimpleNamespace(send_client_content=AsyncMock())
        stream=MagicMock()
        stream.read.side_effect=[bytes(1024),bytes(1024),OSError("end test")]
        owner=MagicMock()
        owner.open.return_value=stream
        owner.get_default_input_device_info.return_value={"name":"test"}
        detector=MagicMock()
        detector.feed.return_value=False
        detector.last_score=0.0
        with patch("main.pyaudio.PyAudio",return_value=owner),patch("local_wake.WakeDetector",return_value=detector),patch("main.audio_event"):
            with self.assertRaises(OSError):
                await app._listen_audio()
        self.assertTrue(app.out_queue.empty())
        app.session.send_client_content.assert_not_awaited()
        stream.close.assert_called_once()

    async def test_wake_ack_uses_existing_live_voice(self):
        from main import JarvisLive
        app=object.__new__(JarvisLive)
        app.ui=MagicMock()
        app.ui.muted=False
        app._paused=False
        app._is_speaking=False
        app._speaking_lock=threading.Lock()
        app.out_queue=asyncio.Queue()
        app.session=SimpleNamespace(send_client_content=AsyncMock())
        stream=MagicMock()
        stream.read.side_effect=[bytes(1024),OSError("end test")]
        owner=MagicMock()
        owner.open.return_value=stream
        owner.get_default_input_device_info.return_value={"name":"test"}
        detector=MagicMock()
        detector.feed.return_value=True
        detector.last_score=0.8
        with patch("main.pyaudio.PyAudio",return_value=owner),patch("local_wake.WakeDetector",return_value=detector),patch("main.audio_event"):
            with self.assertRaises(OSError):
                await app._listen_audio()
        app.session.send_client_content.assert_awaited_once()
        self.assertTrue(app.out_queue.empty())
        self.assertFalse(app.ui.wake_waiting)


if __name__=="__main__":
    unittest.main()
