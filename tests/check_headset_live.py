"""Explicit interactive headset test. No audio files, tools or settings changes."""
import asyncio
import audioop
import json
import math
import time
import winsound

import pyaudio
from google import genai
from google.genai import types
from app_config import get_app_config_value
from tool_defs import TOOL_DECLARATIONS
from audio_runtime import configure_live, pcm_chunks


def report(event, **values):
    print(json.dumps(dict(event=event, **values), ensure_ascii=False), flush=True)


async def run():
    p = pyaudio.PyAudio()
    output = None
    microphone = None
    client = None
    try:
        report("devices", microphone=p.get_default_input_device_info()["name"],
               output=p.get_default_output_device_info()["name"])
        output = await asyncio.to_thread(p.open, format=pyaudio.paInt16, channels=1,
                                         rate=24000, output=True)
        client = genai.Client(api_key=get_app_config_value("gemini_api_key", ""),
                             http_options={"api_version": "v1alpha"})
        config = configure_live(types.LiveConnectConfig(
            response_modalities=["AUDIO"], input_audio_transcription={}, output_audio_transcription={},
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=get_app_config_value("voice", "Charon")))),
            system_instruction=("Türkçe kulaklık ve mikrofon bağlantısı testisin. Hiçbir araç çağırma. "
                                "Bir cümleyle kısa yanıt ver. Ses anlaşılmıyorsa anlamadığını söyle. "
                                "Söylenmeyen sözleri duyduğunu iddia etme. Kullanıcı başla derse Emrinizdeyim efendim de.")),
                                fast=bool(get_app_config_value("low_latency_voice", True)))
        started = time.perf_counter()
        async with client.aio.live.connect(model="models/gemini-2.5-flash-native-audio-latest", config=config) as session:
            report("connected", seconds=round(time.perf_counter() - started, 3), tools=len(TOOL_DECLARATIONS))
            requested = time.perf_counter()
            await session.send_client_content(turns={"parts": [{"text":
                "Sadece şunu söyle: Kulaklık testi. Bip sesinden sonra Jarvis beni duyuyor musun, iki artı iki kaç, de."}]}, turn_complete=True)
            first = None
            size = 0
            async def greeting():
                nonlocal first, size
                async for response in session.receive():
                    for audio in pcm_chunks(response):
                        if first is None:
                            first = time.perf_counter()
                        size += len(audio)
                        await asyncio.to_thread(output.write, audio)
            await asyncio.wait_for(greeting(), 25)
            report("speaker_test", first_audio_seconds=round(first-requested, 3) if first else None,
                   audio_seconds=round(size/48000, 2), playback_write_ok=bool(size))
            microphone = await asyncio.to_thread(p.open, format=pyaudio.paInt16, channels=1,
                                              rate=16000, input=True, frames_per_buffer=1024)
            await asyncio.sleep(0.6)
            await asyncio.to_thread(winsound.Beep, 660, 160)
            report("speak_now", seconds=20)
            replied = asyncio.Event()
            levels = []
            peaks = []
            input_text = []
            answer_text = []
            last_signal = None
            first_answer = None
            answer_bytes = 0
            sent_chunks = 0
            async def capture():
                nonlocal last_signal, sent_chunks
                until = time.perf_counter()+20
                try:
                    while time.perf_counter()<until and not replied.is_set():
                        block = await asyncio.to_thread(microphone.read, 1024, exception_on_overflow=False)
                        if replied.is_set():
                            break
                        rms = audioop.rms(block, 2)
                        levels.append(rms)
                        peaks.append(audioop.max(block, 2))
                        if rms > 350:
                            last_signal = time.perf_counter()
                        await session.send_realtime_input(audio=types.Blob(data=block, mime_type="audio/pcm;rate=16000"))
                        sent_chunks += 1
                finally:
                    if not replied.is_set():
                        await session.send_realtime_input(audio_stream_end=True)
            async def listen():
                nonlocal first_answer, answer_bytes
                async for response in session.receive():
                    content = response.server_content
                    if content:
                        if content.input_transcription and content.input_transcription.text:
                            input_text.append(content.input_transcription.text)
                        if content.output_transcription and content.output_transcription.text:
                            answer_text.append(content.output_transcription.text)
                    if response.tool_call:
                        raise RuntimeError("Unexpected tool call; not executed")
                    for audio in pcm_chunks(response):
                        if first_answer is None:
                            first_answer = time.perf_counter()
                            replied.set()
                        answer_bytes += len(audio)
                        await asyncio.to_thread(output.write, audio)
            timed_out = False
            try:
                await asyncio.wait_for(asyncio.gather(capture(), listen()), 35)
            except asyncio.TimeoutError:
                timed_out = True
            highest = max(levels, default=0)
            report("microphone_test", captured_seconds=round(sent_chunks*1024/16000, 2),
                   peak_rms_dbfs=round(20*math.log10(max(highest, 1)/32768), 1),
                   peak_sample=max(peaks, default=0), nonzero_blocks=sum(x>0 for x in levels),
                   heard="".join(input_text), answer="".join(answer_text),
                   response_audio_seconds=round(answer_bytes/48000, 2),
                   estimated_signal_end_to_audio_seconds=(round(first_answer-last_signal, 3)
                                                         if first_answer and last_signal else None))
            if timed_out:
                raise TimeoutError("Microphone turn incomplete")
    finally:
        for stream in (microphone, output):
            if stream:
                stream.close()
        p.terminate()
        if client:
            await client.aio.aclose()
            client.close()


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(run(), 80))
    except Exception as exc:
        report("test_failed", error_type=type(exc).__name__)
        raise SystemExit(1)
