"""Check the restored streaming path; --live plays two short replies, no mic."""
import asyncio
import sys
import time
from unittest.mock import patch


class UI:
    muted = False
    def set_state(self, state):
        pass
    def write_log(self, text):
        pass
    def write_debug(self, *args, **kwargs):
        pass
    def mark_user_activity(self, *args):
        pass


async def check(live=False):
    from main import JarvisLive, LIVE_MODEL, get_api_key
    from app_config import load_app_config
    from tool_defs import TOOL_DECLARATIONS
    cfg = load_app_config()
    assert not cfg['wake_word_enabled']
    assert isinstance(cfg['voice_only_mode'], bool)
    assert cfg['voice'] == 'Charon'
    assert cfg['tts_provider'] == 'system'
    names = {t['name'] for t in TOOL_DECLARATIONS}
    assert {'open_creator_workspaces', 'check_vercel_domain',
            'get_recent_emails', 'create_blender_primitive'} <= names
    jarvis = JarvisLive(UI())
    jarvis.audio_in_queue = asyncio.Queue()
    assert not jarvis._wake_only_enabled()
    if not live:
        class Stream:
            def write(self, chunk):
                time.sleep(.02)
            def close(self):
                pass
        with patch('main.pya.open', return_value=Stream()):
            task = asyncio.create_task(jarvis._play_audio())
            try:
                for _ in range(2):
                    jarvis._audio_turn_complete = False
                    jarvis.audio_in_queue.put_nowait(bytes(480))
                    while not jarvis._audio_writing:
                        await asyncio.sleep(.001)
                    jarvis._audio_turn_complete = True
                    while jarvis._audio_writing:
                        await asyncio.sleep(.001)
                    assert not jarvis._is_speaking, 'Microphone left blocked after playback'
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        print('FAST_MODE_TOOLS_AND_TWO_TURN_PLAYBACK_OK')
        return
    from google import genai
    client = genai.Client(api_key=get_api_key(), http_options={'api_version': 'v1alpha'})
    async with client.aio.live.connect(model=LIVE_MODEL, config=jarvis._build_config()) as session:
        jarvis.session = session
        first_audio = asyncio.Event()
        class AudioQueue(asyncio.Queue):
            def put_nowait(self, data):
                first_audio.set()
                super().put_nowait(data)
        jarvis.audio_in_queue = AudioQueue()
        tasks = [asyncio.create_task(jarvis._receive_audio()), asyncio.create_task(jarvis._play_audio())]
        try:
            for n, phrase in enumerate(['Hazırım efendim.', 'Sizi dinliyorum.'], 1):
                first_audio.clear()
                start = time.monotonic()
                await session.send_client_content(turns={'role':'user', 'parts':[{'text':f"Sadece şu cümleyi söyle, araç kullanma: {phrase}"}]}, turn_complete=True)
                await asyncio.wait_for(first_audio.wait(), 25)
                print(f'TURN_{n}_FIRST_AUDIO_SECONDS={time.monotonic()-start:.2f}', flush=True)
                async def drained():
                    while not (jarvis._audio_turn_complete and jarvis.audio_in_queue.empty() and not jarvis._audio_writing):
                        for task in tasks:
                            if task.done():
                                task.result()
                        await asyncio.sleep(.02)
                await asyncio.wait_for(drained(), 25)
                assert not jarvis._is_speaking
            print('TWO_LIVE_TURNS_CHARON_MIC_GATE_RELEASED_OK', flush=True)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == '__main__':
    asyncio.run(check('--live' in sys.argv))
