"""Synthetic latency comparison. No microphone, playback or tool execution."""
import asyncio
import json
import time
from google import genai
from google.genai import types
from app_config import get_app_config_value
from main import JarvisLive, LIVE_MODEL


def tuned(config):
    result = config.model_copy(deep=True)
    result.thinking_config = types.ThinkingConfig(thinking_budget=0)
    result.realtime_input_config = types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=False, prefix_padding_ms=100, silence_duration_ms=600))
    return result


async def measure(label, config):
    client = genai.Client(api_key=get_app_config_value("gemini_api_key", ""), http_options={"api_version": "v1alpha"})
    try:
        start = time.perf_counter()
        async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
            print(json.dumps({"profile":label,"connected_seconds":round(time.perf_counter()-start,3)}),flush=True)
            for question in ("Sadece tamam de.", "İki artı iki kaç? Sadece sonucu söyle."):
                start = time.perf_counter()
                await session.send_client_content(turns={"parts":[{"text":question}]},turn_complete=True)
                first = None
                total = 0
                transcript = []
                async for response in session.receive():
                    content = response.server_content
                    if content and content.model_turn:
                        for part in content.model_turn.parts:
                            if part.inline_data and isinstance(part.inline_data.data, bytes):
                                first = first or time.perf_counter()
                                total += len(part.inline_data.data)
                    if content and content.output_transcription:
                        transcript.append(content.output_transcription.text or "")
                    if response.tool_call:
                        raise RuntimeError("Unexpected tool call; not executed")
                print(json.dumps({"profile":label,"first_audio_seconds":round(first-start,3) if first else None,
                                  "audio_bytes":total,"answer":"".join(transcript)},ensure_ascii=False),flush=True)
                if not total:
                    raise RuntimeError("No audio")
    finally:
        await client.aio.aclose()
        client.close()


async def main():
    config = JarvisLive._build_config(object.__new__(JarvisLive))
    config.thinking_config = None
    config.realtime_input_config = None
    # User data is not needed for the benchmark. Keep the same tool schemas.
    config.system_instruction = "Türkçe, kısa yanıt veren sesli asistansın. Testte araç çağırma."
    for label, candidate in (("baseline",config),("low_latency",tuned(config))):
        try:
            await asyncio.wait_for(measure(label,candidate),45)
        except Exception as exc:
            print(json.dumps({"profile":label,"error":type(exc).__name__}),flush=True)


if __name__ == "__main__":
    asyncio.run(main())
