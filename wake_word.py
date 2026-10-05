"""Wake-word listener used by the optional hands-free mode.

It owns the microphone only while wake-only mode is enabled.  This avoids
fighting the Gemini Live microphone stream and keeps the original live mode
unchanged for users who do not opt in.
"""

import threading
import speech_recognition as sr

WAKE_WORDS = ("jarvis", "cerwis", "jarviz")


class WakeWordListener:
    """
    Arka planda sürekli mikrofonu dinler.
    Wake-word duyulunca, sonrasındaki konuşmayı yakalar
    ve on_wake(text) callback'ini çağırır.
    """

    def __init__(self, on_command, on_wake, ui):
        self.on_command = on_command
        self.on_wake    = on_wake
        self.ui        = ui
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold    = 300
        self.recognizer.dynamic_energy_threshold = True
        self._thread   = None
        self._running  = False

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("[WakeWord] 🎤 Dinleniyor... 'Jarvis' diyerek başlatın.")

    def stop(self):
        self._running = False

    def stop_and_wait(self, timeout=6):
        """Release SpeechRecognition's microphone before another audio owner starts."""
        self._running = False
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout)))

    def _loop(self):
        with sr.Microphone() as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            while self._running:
                if self.ui.muted:
                    import time; time.sleep(0.2)
                    continue
                try:
                    # Kısa bir ses parçası al (wake-word tespiti için)
                    audio = self.recognizer.listen(source, timeout=5,
                                                   phrase_time_limit=4)
                    text = self.recognizer.recognize_google(audio,
                                                            language="tr-TR").lower()
                    print(f"[WakeWord] Duyuldu: {text}")

                    wake_word = next((word for word in WAKE_WORDS if word in text), None)
                    if wake_word:
                        # Wake-word sonrasındaki kısım varsa onu al
                        after = text.split(wake_word, 1)[-1].strip()
                        # Always acknowledge a wake first.  The callback
                        # brings the JARVIS screen forward and uses the chosen
                        # TTS provider (ElevenLabs when configured).
                        self.on_wake()
                        if len(after) > 3:
                            # "Jarvis müzik aç" → "müzik aç"
                            self.on_command(after)
                        else:
                            # Sadece "Jarvis" dediyse komutu bekle.  The
                            # acknowledgement has just completed, therefore
                            # its own voice is not mistaken for a command.
                            self.ui.set_state("LISTENING")
                            try:
                                audio2 = self.recognizer.listen(source,
                                                                timeout=6,
                                                                phrase_time_limit=8)
                                cmd = self.recognizer.recognize_google(
                                    audio2, language="tr-TR")
                                if cmd.strip():
                                    self.on_command(cmd.strip())
                            except (sr.WaitTimeoutError, sr.UnknownValueError):
                                pass

                except sr.WaitTimeoutError:
                    pass
                except sr.UnknownValueError:
                    pass
                except Exception as e:
                    print(f"[WakeWord] Hata: {e}")
