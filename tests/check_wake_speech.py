"""Synthetic speech check. Never records microphone audio."""
import audioop
import tempfile
import time
import wave
from pathlib import Path
import pythoncom
import win32com.client
from local_wake import WakeDetector


def check():
    pythoncom.CoInitialize()
    voice = stream = None
    try:
        detector = WakeDetector()
        if detector.keyword is None:
            raise RuntimeError("Keyword engine unavailable: " + detector.keyword_error)
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        phrases = [("Hey Jarvis", True), ("Jarvis", True),
                   ("Hey Google", False), ("Hello how are you", False)]
        failures = []
        with tempfile.TemporaryDirectory(prefix="jarvis-wake-check-") as folder:
            for index, (phrase, expected) in enumerate(phrases):
                path = Path(folder)/f"{index}.wav"
                stream = win32com.client.Dispatch("SAPI.SpFileStream")
                stream.Open(str(path), 3, False)
                voice.AudioOutputStream = stream
                voice.Speak(phrase)
                stream.Close()
                with wave.open(str(path), "rb") as source:
                    pcm = source.readframes(source.getnframes())
                    if source.getnchannels() == 2:
                        pcm = audioop.tomono(pcm, source.getsampwidth(), .5, .5)
                    pcm = audioop.lin2lin(pcm, source.getsampwidth(), 2)
                    pcm, _ = audioop.ratecv(pcm, 2, 1, source.getframerate(), 16000, None)
                pcm += bytes(32000)
                detector.reset()
                detected = False
                start = time.perf_counter()
                for offset in range(0, len(pcm), 1024):
                    if detector.feed(pcm[offset:offset+1024]):
                        detected = True
                        break
                print(phrase, "expected", expected, "detected", detected,
                      "engine", detector.last_source if detected else "-",
                      "compute_ms", round((time.perf_counter()-start)*1000))
                if detected != expected:
                    failures.append(phrase)
                detector.keyword.reset()
                keyword_detected = False
                for offset in range(0, len(pcm), 1024):
                    if detector.keyword.feed(pcm[offset:offset+1024]):
                        keyword_detected = True
                        break
                print("KEYWORD_ONLY", phrase, keyword_detected)
                if keyword_detected != expected:
                    failures.append("keyword: " + phrase)
        if failures:
            raise RuntimeError("Wake checks failed: " + str(failures))
    finally:
        stream = voice = None
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    check()
