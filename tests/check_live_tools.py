"""Manual smoke test: no microphone, camera, memory, or mail access."""
import asyncio
from google import genai
from google.genai import types
from app_config import get_app_config_value
from tool_defs import TOOL_DECLARATIONS


async def check():
    client = genai.Client(api_key=get_app_config_value("gemini_api_key", ""), http_options={"api_version": "v1alpha"})
    try:
        config = types.LiveConnectConfig(response_modalities=["AUDIO"],
            system_instruction="This is an integration test. Respond briefly. Do not call tools.",
            tools=[{"function_declarations": TOOL_DECLARATIONS}])
        async with client.aio.live.connect(model="models/gemini-2.5-flash-native-audio-latest", config=config) as session:
            print("LIVE_TOOL_SCHEMA_ACCEPTED", len(TOOL_DECLARATIONS))
            await session.send_client_content(turns={"parts": [{"text": "Say only tamam."}]}, turn_complete=True)
            total = 0
            async for response in session.receive():
                total += len(response.data or b"")
                if response.server_content and response.server_content.turn_complete:
                    break
            print("LIVE_AUDIO_RECEIVED_BYTES", total)
            if not total:
                raise RuntimeError("No audio returned")
    finally:
        await client.aio.aclose()
        client.close()


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(check(), timeout=30))
    except Exception as exc:
        print("LIVE_TEST_FAILED", type(exc).__name__)
        raise SystemExit(1)

