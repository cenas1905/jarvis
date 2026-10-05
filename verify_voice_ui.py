"""Developer verification. --live plays two short Gemini turns, no microphone."""
import asyncio
import contextlib
import sys
import time
from unittest.mock import patch


async def check_voice(live=False):
    from main import JarvisLive, LIVE_MODEL, get_api_key
    from google import genai
    from google.genai import types

    class UI:
        muted = False
        speaking = False
        output_audio_level = 0.0
        def set_state(self, state):
            self.speaking = state == "SPEAKING"
        def write_log(self, text):
            if text.startswith("JARVIS:"):
                print(text, flush=True)
        def write_debug(self, *args, **kwargs):
            pass
        def mark_user_activity(self, *args):
            pass

    ui = UI()
    jarvis = JarvisLive(ui)
    jarvis.audio_in_queue = asyncio.Queue()
    jarvis._turn_finished = asyncio.Event()
    jarvis._turn_finished.set()
    if live:
        client = genai.Client(api_key=get_api_key(), http_options={"api_version": "v1alpha"})
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"], output_audio_transcription={},
            system_instruction="Türkçe, kısa, doğal ve canlı konuş. İstenen cümleyi aynen söyle.",
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede"))),
        )
        async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
            jarvis.session = session
            tasks = [asyncio.create_task(jarvis._receive_audio()),
                     asyncio.create_task(jarvis._play_audio())]
            try:
                start = time.monotonic()
                await jarvis._greet()
                assert not jarvis._is_speaking, "Greeting playback did not return to idle"
                print(f"GREETING_DRAINED seconds={time.monotonic()-start:.2f}", flush=True)
                jarvis._turn_finished.clear()
                await session.send_client_content(
                    turns={"parts": [{"text": "Yalnızca 'Hazırım, sizi dinliyorum.' de."}]},
                    turn_complete=True)
                await asyncio.wait_for(jarvis._turn_finished.wait(), 20)
                await jarvis.audio_in_queue.join()
                assert not jarvis._is_speaking
                print("TWO_TURNS_SAME_SESSION_AOEDE_OK", flush=True)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    else:
        class Session:
            async def send_client_content(self, **kwargs):
                jarvis.audio_in_queue.put_nowait(bytes(48))
                jarvis._turn_finished.set()
        jarvis.session = Session()
        with patch("main.speak_text", side_effect=AssertionError("Unexpected second voice")):
            greeting = asyncio.create_task(jarvis._greet())
            await asyncio.sleep(.02)
            assert not greeting.done(), "Greeting must wait for actual audio playback"
            jarvis.audio_in_queue.get_nowait()
            jarvis.audio_in_queue.task_done()
            await greeting
        print("GREETING_USES_LIVE_AND_WAITS_FOR_PLAYBACK_OK")


def check_ui():
    from ui import JarvisUI
    ui = JarvisUI()
    ui.root.attributes("-fullscreen", False)
    ui._resize_surface(1000, 700)
    ui.root.geometry("1000x700")
    for state in ("LISTENING", "SPEAKING", "THINKING", "ERROR"):
        ui.set_state(state)
        ui.output_audio_level = 0.7
        ui._draw()
        ui.root.update()
        assert not ui.log_frame.winfo_ismapped()
        assert not ui._phone_btn_canvas.winfo_ismapped()
    ui._toggle_room_mode()
    ui.root.update()
    assert ui.log_frame.winfo_ismapped()
    ui._toggle_room_mode()
    ui.root.update()
    assert not ui.log_frame.winfo_ismapped()
    ui.set_state("LISTENING")
    print("ROOM_RESIZE_STATES_AND_F2_OK", flush=True)
    ui.root.after(1200, ui.root.destroy)
    ui.root.mainloop()


if __name__ == "__main__":
    if "--ui" in sys.argv:
        check_ui()
    else:
        asyncio.run(check_voice("--live" in sys.argv))
