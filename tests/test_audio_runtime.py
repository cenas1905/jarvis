import asyncio
from array import array
from types import SimpleNamespace
import unittest
import time
import subprocess
from unittest.mock import patch, MagicMock, AsyncMock

from google.genai import types
from audio_runtime import configure_live, pcm_chunks, offer_latest, clear_queue, SignalMonitor, mix_pcm16, mix_phone_audio


class AudioTests(unittest.TestCase):
    def test_shell_failure_never_claims_success(self):
        from actions import shell
        with patch.object(shell,"IS_WIN",True),patch.object(shell,"_run_windows",return_value=subprocess.CompletedProcess([],1,"","bad command")):
            self.assertTrue(shell.shell_run("not-a-command").startswith("Hata:"))

    def test_shell_empty_failure_is_still_failure(self):
        from actions import shell
        with patch.object(shell,"IS_WIN",True),patch.object(shell,"_run_windows",return_value=subprocess.CompletedProcess([],1,"","")):
            self.assertTrue(shell.shell_run("not-a-command").startswith("Hata:"))

    def test_shell_success(self):
        from actions import shell
        with patch.object(shell,"IS_WIN",True),patch.object(shell,"_run_windows",return_value=subprocess.CompletedProcess([],0,"ok","")):
            self.assertEqual(shell.shell_run("Write-Output ok"),"ok")

    def test_low_latency_keeps_voice_and_tools(self):
        config = types.LiveConnectConfig(response_modalities=["AUDIO"],
            speech_config={"voice_config":{"prebuilt_voice_config":{"voice_name":"Charon"}}},
            tools=[{"function_declarations":[{"name":"test"}]}])
        result = configure_live(config)
        self.assertEqual(result.speech_config.voice_config.prebuilt_voice_config.voice_name,"Charon")
        self.assertEqual(result.tools[0].function_declarations[0].name,"test")
        self.assertEqual(result.thinking_config.thinking_budget,0)
        self.assertEqual(result.realtime_input_config.automatic_activity_detection.silence_duration_ms,600)

    def test_fast_mode_can_be_disabled(self):
        self.assertIsNone(configure_live(types.LiveConnectConfig(),False).thinking_config)

    def test_pcm_only_no_thought_or_text(self):
        response = types.LiveServerMessage(server_content=types.LiveServerContent(model_turn=types.Content(parts=[
            types.Part(text="not audio",thought=True),
            types.Part(inline_data=types.Blob(data=b"test",mime_type="audio/pcm;rate=24000")),
            types.Part(inline_data=types.Blob(data=b"not audio",mime_type="image/png"))])))
        self.assertEqual(list(pcm_chunks(response)),[b"test"])
        self.assertEqual(list(pcm_chunks(types.LiveServerMessage())),[])

    def test_queue_does_not_accumulate_old_audio(self):
        queue = asyncio.Queue(maxsize=2)
        offer_latest(queue,1)
        offer_latest(queue,2)
        self.assertEqual(offer_latest(queue,3),1)
        self.assertEqual(queue.get_nowait(),2)
        self.assertEqual(queue.get_nowait(),3)
        clear_queue(queue)
        clear_queue(None)

    def test_mix_pcm16_blends_streams_without_clipping(self):
        primary = array("h", [30000, -30000, 1000]).tobytes()
        call = array("h", [30000, -30000, -1000]).tobytes()
        mixed = array("h")
        mixed.frombytes(mix_pcm16(primary, call))
        self.assertEqual(list(mixed), [30000, -30000, 0])

    def test_mix_pcm16_returns_primary_without_loopback(self):
        primary = array("h", [1, -2]).tobytes()
        self.assertEqual(mix_pcm16(primary, None), primary)
        self.assertEqual(mix_pcm16(primary, b""), primary)

    def test_phone_audio_is_not_halved_when_microphone_is_silent(self):
        microphone = array("h", [0, 10, -10, 0]).tobytes()
        phone = array("h", [2400, -2400, 1200, -1200]).tobytes()
        self.assertEqual(mix_phone_audio(microphone, phone), phone)

    def test_phone_audio_blends_when_user_speaks(self):
        microphone = array("h", [1200, -1200, 1200, -1200]).tobytes()
        phone = array("h", [2400, -2400, 1200, -1200]).tobytes()
        result = array("h")
        result.frombytes(mix_phone_audio(microphone, phone))
        self.assertEqual(list(result), [1800, -1800, 1200, -1200])

    def test_silence_is_only_a_rate_limited_warning(self):
        with patch("audio_runtime.time.monotonic",return_value=0):
            monitor = SignalMonitor()
        silence = bytes(1024)
        self.assertFalse(monitor.feed(silence,20)[1])
        self.assertTrue(monitor.feed(silence,60)[1])
        self.assertFalse(monitor.feed(silence,61)[1])
        voice = array("h",[1000,-1000]*256).tobytes()
        self.assertEqual(monitor.feed(voice,119)[0],1000)
        self.assertFalse(monitor.feed(silence,130)[1])


class MainAudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_audio_not_sent(self):
        from main import JarvisLive
        app = object.__new__(JarvisLive)
        app.out_queue = asyncio.Queue()
        app.out_queue.put_nowait({"data":b"stale","captured_at":0})
        app.out_queue.put_nowait({"data":b"fresh","captured_at":time.monotonic()})
        app.session = SimpleNamespace(send_realtime_input=AsyncMock(side_effect=OSError("test completed")))
        with self.assertRaises(OSError):
            await asyncio.wait_for(app._send_realtime(),1)
        app.session.send_realtime_input.assert_awaited_once()
        blob = app.session.send_realtime_input.call_args.kwargs["audio"]
        self.assertEqual(blob.data,b"fresh")
        self.assertEqual(blob.mime_type,"audio/pcm;rate=16000")

    async def test_microphone_open_failure_releases_device_owner(self):
        from main import JarvisLive
        app = JarvisLive(MagicMock())
        owner = MagicMock()
        owner.get_default_input_device_info.return_value = {"name":"test"}
        owner.open.side_effect = OSError("no device")
        with patch("main.pyaudio.PyAudio",return_value=owner),patch("main.audio_event"):
            with self.assertRaises(OSError):
                await app._listen_audio()
        owner.terminate.assert_called_once()

    async def test_output_open_failure_releases_device_owner(self):
        from main import JarvisLive
        app = JarvisLive(MagicMock())
        owner = MagicMock()
        owner.get_default_output_device_info.return_value = {"name":"test"}
        owner.open.side_effect = OSError("no device")
        with patch("main.pyaudio.PyAudio",return_value=owner),patch("main.audio_event"):
            with self.assertRaises(OSError):
                await app._play_audio()
        owner.terminate.assert_called_once()

    async def test_first_call_audio_packet_reaches_bridge(self):
        from main import JarvisLive
        app = JarvisLive(MagicMock())
        app._standby = False
        app.audio_in_queue = asyncio.Queue()
        app.audio_in_queue.put_nowait(b"first reply")
        speaker = MagicMock()
        bridge = MagicMock()
        bridge.write.side_effect = OSError("test completed")
        owner = MagicMock()
        owner.get_default_output_device_info.return_value = {"name": "headphones"}
        owner.get_device_info_by_index.return_value = {"name": "CABLE Input"}
        owner.open.side_effect = [speaker, bridge]
        with patch("main.pyaudio.PyAudio", return_value=owner), \
             patch("main.phone_call_conversation_active", return_value=True), \
             patch("main.bridge_output_index", return_value=7), \
             patch("main.audio_event"):
            with self.assertRaisesRegex(OSError, "test completed"):
                await asyncio.wait_for(app._play_audio(), 2)
        speaker.write.assert_called_once_with(b"first reply")
        bridge.write.assert_called_once_with(b"first reply")


if __name__ == "__main__":
    unittest.main()
