"""Offline wake detection. No recording and no network transcription."""
import time
import re
import json
import os
import numpy as np


def is_standby_command(text):
    clean = re.sub(r"[^\w\s]", " ", str(text).casefold()).strip()
    clean = " ".join(clean.split())
    return clean in {"kapat", "kapan", "jarvis kapat", "jarvis kapan", "kendini kapat",
                     "jarvis kendini kapat", "uykuya geç", "beklemeye geç"}


class KeywordDetector:
    """Streaming local keyword recognition; no microphone ownership or uploads."""
    def __init__(self, recognizer=None):
        if recognizer is None:
            from vosk import Model, KaldiRecognizer, SetLogLevel
            from app_paths import data_path
            SetLogLevel(-1)
            # Vosk's Windows file API cannot open a UTF-8 absolute path containing
            # "Yeni klasör". Launchers set cwd to the application directory.
            model_path = os.path.relpath(data_path("models", "vosk-model-small-en-us-0.15"))
            self.model = Model(model_path)
            if self.model.vosk_model_find_word("jarvis") < 0:
                raise RuntimeError("Yerel kelime modelinde Jarvis bulunamadı.")
            phrases = ["hey jarvis", "jarvis", "hey google", "hey siri", "hello", "hey", "[unk]"]
            if self.model.vosk_model_find_word("jervis") >= 0:
                phrases += ["hey jervis", "jervis"]
            self.grammar = json.dumps(phrases)
            recognizer = KaldiRecognizer(self.model, 16000, self.grammar)
            recognizer.SetWords(True)
        self.recognizer = recognizer
        self.utterance_samples = 0
        self.speech_samples = 0
        self.silent_samples = 0

    def reset(self):
        if hasattr(self, "model"):
            from vosk import KaldiRecognizer
            self.recognizer = KaldiRecognizer(self.model, 16000, self.grammar)
            self.recognizer.SetWords(True)
        else:
            self.recognizer.Reset()
        self.utterance_samples = 0
        self.speech_samples = 0
        self.silent_samples = 0

    def feed(self, pcm):
        final = self.recognizer.AcceptWaveform(pcm)
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(samples*samples))) if samples.size else 0.0
        if rms >= 250:
            self.speech_samples += samples.size
            self.silent_samples = 0
        else:
            self.silent_samples += samples.size
        # Commit at a short local pause instead of Vosk's long default endpoint.
        flush = not final and self.speech_samples >= 1600 and self.silent_samples >= 5120
        payload = (self.recognizer.Result() if final else
                   self.recognizer.FinalResult() if flush else self.recognizer.PartialResult())
        final = final or flush
        result = json.loads(payload)
        phrase = str(result.get("text" if final else "partial", "")).strip()
        match = phrase in {"hey jarvis", "jarvis", "hey jervis", "jervis"}
        self.utterance_samples += len(pcm)//2
        # A partial can still change from Jarvis to another word; commit only
        # after the short local pause or the recognizer's complete utterance.
        if match and final:
            self.reset()
            return True
        if final or self.utterance_samples >= 16000*8:
            self.reset()
        return False


class WakeDetector:
    def __init__(self, threshold=0.5, model=None, keyword=None):
        supplied_model = model is not None
        if model is None:
            from openwakeword.model import Model
            model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        self.model = model
        self.threshold = max(0.25, min(0.9, float(threshold)))
        self.pending = bytearray()
        self.last_score = 0.0
        self.last_source = ""
        self.keyword = keyword
        self.keyword_error = ""
        if self.keyword is None and not supplied_model:
            try:
                self.keyword = KeywordDetector()
            except Exception as exc:
                self.keyword_error = type(exc).__name__

    def reset(self):
        self.pending.clear()
        self.model.reset()
        if self.keyword:
            self.keyword.reset()

    def feed(self, pcm):
        self.pending.extend(pcm)
        while len(self.pending) >= 2560:  # 80 ms PCM16 at 16 kHz
            samples = np.frombuffer(bytes(self.pending[:2560]), dtype=np.int16)
            del self.pending[:2560]
            scores = self.model.predict(samples)
            self.last_score = max((float(v) for v in scores.values()), default=0.0)
            if self.last_score >= self.threshold:
                self.last_source = "openwakeword"
                self.reset()
                return True
        if self.keyword and self.keyword.feed(pcm):
            self.last_source = "keyword"
            self.reset()
            return True
        return False


class ConversationGate:
    def __init__(self, timeout=60):
        self.timeout = timeout
        self.until = 0.0

    def awake(self, now=None):
        return (time.monotonic() if now is None else now) < self.until

    def touch(self, now=None):
        self.until = (time.monotonic() if now is None else now) + self.timeout

    def sleep(self):
        self.until = 0.0
