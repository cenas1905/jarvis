"""Small, local audio helpers. Diagnostics never store speech or credentials."""
from array import array
import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import time

from google.genai import types
from app_paths import data_path

_logger = logging.getLogger("jarvis.audio.metrics")
_logger.setLevel(logging.INFO)
_logger.propagate = False


def audio_event(event, **metrics):
    try:
        if not _logger.handlers:
            handler = RotatingFileHandler(data_path("logs", "audio-health.jsonl"),
                                          maxBytes=256000, backupCount=2, encoding="utf-8")
            _logger.addHandler(handler)
        _logger.info(json.dumps({"time": time.time(), "event": event, **metrics}, ensure_ascii=False))
    except OSError:
        pass


def configure_live(config, fast=True):
    """Keep the selected model/voice/tools; tune only turn latency."""
    if fast:
        config.thinking_config = types.ThinkingConfig(thinking_budget=0)
    config.realtime_input_config = types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=False, prefix_padding_ms=100, silence_duration_ms=600))
    return config


def pcm_chunks(response):
    content = response.server_content
    if content and content.model_turn:
        for part in content.model_turn.parts or []:
            blob = part.inline_data
            if blob and str(blob.mime_type or "").startswith("audio/pcm") and isinstance(blob.data, bytes):
                yield blob.data


def offer_latest(queue, item):
    """Do not block microphone capture behind slow network writes."""
    dropped = 0
    while queue.full():
        try:
            queue.get_nowait()
            dropped += 1
        except asyncio.QueueEmpty:
            break
    queue.put_nowait(item)
    return dropped


def clear_queue(queue):
    if queue is not None:
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break


async def audio_io_call(function, *args, **kwargs):
    """Let native audio I/O finish before TaskGroup cleanup closes its stream.

    Cancelling ``asyncio.to_thread`` cancels only the awaiter; PortAudio keeps
    running the native read/write call.  Shield the worker and wait for it when
    a session is cancelled, preventing stream teardown from racing that call.
    """
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        audio_event("audio_io_wait_on_cancel", operation=getattr(function, "__name__", "audio"))
        try:
            await asyncio.shield(worker)
        except BaseException:
            pass
        raise


def choose_local_audio_device(owner, direction: str, physical_name_hint: str):
    """Keep JARVIS on physical audio when Windows defaults to VB-CABLE."""
    if direction not in {"input", "output"}:
        raise ValueError("direction must be input or output")
    default = (owner.get_default_input_device_info() if direction == "input"
               else owner.get_default_output_device_info())
    if "cable" not in str(default.get("name", "")).casefold():
        return default
    channel_key = "maxInputChannels" if direction == "input" else "maxOutputChannels"
    wanted = str(physical_name_hint or "").casefold().strip()
    candidates = []
    for index in range(owner.get_device_count()):
        info = owner.get_device_info_by_index(index)
        if (not info.get(channel_key) or not wanted
                or wanted not in str(info.get("name", "")).casefold()):
            continue
        host = owner.get_host_api_info_by_index(info["hostApi"])
        candidates.append((0 if host.get("name") == "MME" else 1, index, info))
    if not candidates:
        raise RuntimeError(f"JARVIS için fiziksel {direction} aygıtı bulunamadı")
    return min(candidates, key=lambda item: item[:2])[2]


def mix_pcm16(primary: bytes, secondary: bytes | None) -> bytes:
    """Blend equal-rate mono PCM streams without clipping or retaining audio."""
    if not secondary:
        return primary
    left = array("h")
    right = array("h")
    left.frombytes(primary[: len(primary) - (len(primary) % 2)])
    right.frombytes(secondary[: len(secondary) - (len(secondary) % 2)])
    count = min(len(left), len(right))
    if not count:
        return primary
    mixed = array("h", ((left[i] + right[i]) // 2 for i in range(count)))
    return mixed.tobytes()


def mix_phone_audio(microphone: bytes, phone_audio: bytes | None,
                    mic_voice_threshold: int = 250) -> bytes:
    """Avoid attenuating call audio when the physical mic is silent/noisy."""
    if not phone_audio:
        return microphone
    samples = array("h")
    samples.frombytes(microphone[: len(microphone) - (len(microphone) % 2)])
    if not samples:
        return phone_audio
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    # The old unconditional 50/50 mix cut the remote voice by about 6 dB,
    # even when the user's headset microphone carried no useful signal.
    if rms < max(0, int(mic_voice_threshold)):
        return phone_audio
    return mix_pcm16(microphone, phone_audio)


class SignalMonitor:
    def __init__(self):
        self.started = time.monotonic()
        self.last_signal = self.started
        self.last_notice = self.started
        self.last_activity = 0.0
        self.peak = 0

    def feed(self, pcm, now=None):
        now = time.monotonic() if now is None else now
        samples = array("h", pcm)
        if not samples:
            return 0, False
        rms = int(math.sqrt(sum(x*x for x in samples)/len(samples)))
        self.peak = max(self.peak, max(abs(x) for x in samples))
        if rms >= 12:
            self.last_signal = now
        warning = now-self.last_signal >= 25 and now-self.last_notice >= 60
        if warning:
            self.last_notice = now
        return rms, warning
