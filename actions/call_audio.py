"""Transient system-speaker loopback for a user-authorized phone call.

Audio stays in a short in-memory queue and is never written to disk. Capturing
the speaker endpoint (rather than reusing the VB-CABLE mic endpoint) prevents
the caller's audio from being fed straight back into the caller's microphone.
"""
from __future__ import annotations

import queue
import threading


class SystemLoopbackReader:
    """Read the physical speaker mix while call mode is active."""

    def __init__(self, sample_rate: int = 16000, block_frames: int = 512,
                 speaker_name_hint: str = "Headphones (Realtek"):
        self.sample_rate = int(sample_rate)
        self.block_frames = int(block_frames)
        self.speaker_name_hint = speaker_name_hint
        self.device_name = ""
        self.error = ""
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=3)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return self._ready.is_set() and not self.error and not self._stop.is_set()
        self.error = ""
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._capture, name="jarvis-call-loopback", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=3.0)
        return self._ready.is_set() and not self.error

    def _capture(self) -> None:
        try:
            import numpy as np
            import soundcard as sc

            speaker = sc.default_speaker()
            if "cable" in speaker.name.casefold():
                physical = [item for item in sc.all_speakers()
                            if self.speaker_name_hint.casefold() in item.name.casefold()]
                if not physical:
                    raise RuntimeError("Physical call speaker not found")
                speaker = physical[0]
            self.device_name = str(speaker.name)
            loopback = sc.get_microphone(id=speaker.id, include_loopback=True)
            with loopback.recorder(
                samplerate=self.sample_rate,
                blocksize=self.block_frames,
                exclusive_mode=False,
            ) as recorder:
                self._ready.set()
                while not self._stop.is_set():
                    samples = recorder.record(numframes=self.block_frames)
                    if samples is None or not len(samples):
                        continue
                    mono = np.asarray(samples, dtype=np.float32)
                    if mono.ndim > 1:
                        mono = mono.mean(axis=1)
                    pcm = np.clip(mono, -1.0, 1.0)
                    pcm = (pcm * 32767.0).astype(np.int16).tobytes()
                    while self._queue.full():
                        try:
                            self._queue.get_nowait()
                        except queue.Empty:
                            break
                    try:
                        self._queue.put_nowait(pcm)
                    except queue.Full:
                        pass
        except Exception as exc:
            # Diagnostics expose only the error class, never captured audio.
            self.error = type(exc).__name__
            self._ready.set()

    def read_latest(self) -> bytes | None:
        latest = None
        while True:
            try:
                latest = self._queue.get_nowait()
            except queue.Empty:
                return latest

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.5)
            if thread.is_alive():
                self.error = self.error or "loopback_stop_timeout"
                return
        self._thread = None
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
