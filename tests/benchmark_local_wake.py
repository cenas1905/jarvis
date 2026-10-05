"""Offline CPU timing only. Does not open a microphone or contact a service."""
import time
from local_wake import WakeDetector

d = WakeDetector()
start = time.perf_counter()
for _ in range(150):
    assert not d.feed(bytes(2560))
elapsed = time.perf_counter()-start
print(f"WAKE_FRAME_MEAN_MS={elapsed*1000/150:.2f}")
print("AUDIO_FRAME_MS=80")
