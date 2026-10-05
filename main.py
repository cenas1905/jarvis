#!/usr/bin/env python3
"""
JARVIS macOS — Gercek zamanli sesli yardimci cekirdegi
macOS ortamina uyarlanmis calisma akisi
"""

import asyncio
import datetime
import threading
import traceback
import os
import subprocess
import sys
import time
import re
import json
import logging
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace
from pathlib import Path

from actions.platform_utils import (
    IS_WIN,
    acquire_single_instance,
    attach_parent_console,
    configure_console_output,
    focus_window,
)

# Konsol ciktisini pythonw (konsolsuz) ve Turkce kod sayfasi icin guvenli yap.
# Diger importlardan once calismali — bazi moduller import aninda yazdirir.
configure_console_output()

import pyaudio
import psutil
from google import genai
from google.genai import types

from app_config import get_app_config_value, save_app_config
from audio_runtime import configure_live, pcm_chunks, offer_latest, clear_queue, SignalMonitor, audio_event, mix_pcm16, mix_phone_audio, choose_local_audio_device
from ui import JarvisUI
from wake_word import WakeWordListener
from actions.tts import speak_text
from memory.memory_manager import load_memory, update_memory, delete_memory, format_memory_for_prompt, recall_memory, save_fact
from memory.conversation import TurnMemory, format_history
from memory.routines import personal_routine, routine_names_for_prompt
from actions.daily_life import daily_life
from actions.open_app import open_app
from actions.desktop_control import close_app, desktop_control
from actions.code_files import save_code_file
from actions.android_device import android_device, android_input
from actions.excel_control import excel_control
from actions.sys_info  import sys_info
from actions.calendar import get_calendar_events, add_calendar_event, delete_calendar_event
from actions.reminders import get_reminders, add_reminder
from actions.browser   import browser_control
from actions.creator_tools import open_creator_workspaces, check_vercel_domain, create_blender_primitive
from actions.outlook_mail import get_recent_emails
from actions.shell     import shell_run
from actions.whatsapp  import send_whatsapp_message, save_whatsapp_contact
from actions.call_bridge import bridge_output_index, phone_call_bridge
from actions.phone_calls import (
    phone_call,
    phone_call_conversation_active,
    phone_call_waiting_for_answer,
)
from actions.call_audio import SystemLoopbackReader
from actions.api_errors import classify_gemini_api_error
from actions.media     import play_media, control_media
from actions.weather   import get_weather_summary
from actions.screen_vision import analyze_screen, save_screenshot
from actions.youtube_stats import get_youtube_channel_report
from actions.defender_security import defender_security
from actions.research import research
from actions.robot_arm import robot_arm
from actions.work_studio import work_studio

# ── Paths ───────────────────────────────────────────────────────────────────
from app_paths import resource_path, data_path

BASE_DIR        = Path(__file__).resolve().parent
PROMPT_PATH     = resource_path("core", "prompt.txt")


# Arayuz kapansa veya Python penceresiz calissa bile baglanti/mikrofon
# sorunlarini sonraki tani icin kalici olarak sakla. Konusma, API anahtari ve
# kullanici metni kesinlikle bu kayda yazilmaz.
_runtime_logger = logging.getLogger("jarvis.runtime")
_runtime_logger.setLevel(logging.INFO)
_runtime_logger.propagate = False


def runtime_event(event: str, **details) -> None:
    try:
        if not _runtime_logger.handlers:
            handler = RotatingFileHandler(
                data_path("logs", "jarvis-runtime.log"),
                maxBytes=512_000,
                backupCount=2,
                encoding="utf-8",
            )
            _runtime_logger.addHandler(handler)
        safe = {key: str(value)[:180] for key, value in details.items()}
        _runtime_logger.info(json.dumps({"time": time.time(), "event": event, **safe}, ensure_ascii=False))
    except OSError:
        pass


# ── WebcamStreamer ──────────────────────────────────────────────────────────
class WebcamStreamer:
    """
    Webcam'dan sürekli kare çeker ve en güncel JPEG'i bellekte tutar.
    Queue yerine tek bir 'latest frame' yaklaşımı — eski kare birikimi olmaz.
    """

    JPEG_QUALITY = 72
    MAX_DIM      = 640
    WARMUP       = 6
    # Gecici okuma hatalari normaldir (USB kamera, guc tasarrufu, baska
    # uygulamanin kisa erisimi). Tek hatada akisi kapatmak yerine tolere et.
    MAX_READ_FAILURES = 30
    OPEN_ATTEMPTS = 3
    CAPTURE_INTERVAL = 0.05   # ~20 FPS yakalama (CPU dostu)

    def __init__(self):
        self._latest: bytes | None = None
        self._lock   = threading.Lock()
        self._active = False
        self._thread: threading.Thread | None = None

    @property
    def is_active(self) -> bool:
        return self._active

    def get_latest_frame(self) -> bytes | None:
        """Thread-safe, her zaman en güncel kareyi döner."""
        with self._lock:
            return self._latest

    def start(self) -> str:
        with self._lock:
            if self._active:
                return "already_active"

        # Onceki cekim thread'i kamerayi serbest birakana kadar BEKLE.
        # Beklemezsek yeni VideoCapture "cihaz mesgul" diye acilamiyordu ve
        # kamera kapatilip acildiktan sonra bir daha calismiyordu.
        previous = self._thread
        if previous and previous.is_alive():
            previous.join(timeout=3.0)

        with self._lock:
            self._active = True
            self._latest = None
        t = threading.Thread(target=self._run, daemon=True)
        self._thread = t
        t.start()
        return "ok"

    def stop(self):
        with self._lock:
            self._active = False
            self._latest = None

    def _open_capture(self, cv2):
        """
        Kamerayi acar. Windows'ta DirectShow varsayilandan belirgin hizli
        acilir (~1 sn). Kamera bir onceki oturumdan henuz serbest kalmamis
        olabilecegi icin birkac kez denenir.
        """
        backends = []
        if IS_WIN:
            backends.append(("DirectShow", lambda: cv2.VideoCapture(0, cv2.CAP_DSHOW)))
        backends.append(("varsayilan", lambda: cv2.VideoCapture(0)))

        for attempt in range(1, self.OPEN_ATTEMPTS + 1):
            for label, factory in backends:
                try:
                    cap = factory()
                except Exception:
                    continue
                if cap is not None and cap.isOpened():
                    return cap
                try:
                    if cap is not None:
                        cap.release()
                except Exception:
                    pass
            if attempt < self.OPEN_ATTEMPTS:
                # Kamerayi baska bir surec/onceki thread hala tutuyor olabilir
                time.sleep(0.6)
        return None

    def _run(self):
        try:
            import cv2
        except ImportError:
            print("[Webcam] opencv-python yüklü değil.")
            with self._lock:
                self._active = False
            return

        cap = self._open_capture(cv2)
        if cap is None:
            print("[Webcam] Kamera açılamadı.")
            with self._lock:
                self._active = False
            return

        # Isınma — sensörün otomatik pozlaması oturuncaya kadar bekle
        for _ in range(self.WARMUP):
            cap.read()

        enc_params = [cv2.IMWRITE_JPEG_QUALITY, self.JPEG_QUALITY]
        read_failures = 0

        try:
            while True:
                with self._lock:
                    if not self._active:
                        break

                ret, frame = cap.read()
                if not ret or frame is None:
                    # Tek basarisiz okuma akisi oldurmesin — bir sure dene
                    read_failures += 1
                    if read_failures >= self.MAX_READ_FAILURES:
                        print("[Webcam] Kameradan kare alinamiyor, akis durduruldu.")
                        break
                    time.sleep(0.05)
                    continue
                read_failures = 0

                h, w = frame.shape[:2]
                if max(h, w) > self.MAX_DIM:
                    s = self.MAX_DIM / max(h, w)
                    frame = cv2.resize(frame, (int(w * s), int(h * s)))

                frame = cv2.flip(frame, 1)  # yatay ayna — hem UI hem AI tutarlı
                ok, buf = cv2.imencode(".jpg", frame, enc_params)
                if ok:
                    with self._lock:
                        self._latest = buf.tobytes()

                # ~20 FPS yakala. Onceden 33 FPS'ti ama her kare JPEG'e
                # kodlandigi icin bosuna CPU yiyordu; UI onizlemesi icin
                # 20 FPS gozle ayirt edilemeyecek kadar akici.
                time.sleep(self.CAPTURE_INTERVAL)
        finally:
            cap.release()
            with self._lock:
                self._active = False
                self._latest = None
            print("[Webcam] Kamera serbest bırakıldı.")


CONTROL_TOKEN_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

# ── Model ───────────────────────────────────────────────────────────────────
LIVE_MODEL = "models/gemini-2.5-flash-native-audio-latest"

# Webcam acikken modele kac saniyede bir kare gonderilecek.
# 0.5 sn = akici "canli goruyor" hissi. Bilgisayar zorlanirsa (eski makine,
# yuksek CPU) bu degeri 1.0-1.5'e cikarmak yeterlidir.
WEBCAM_SEND_INTERVAL = 0.5

# ── Audio ───────────────────────────────────────────────────────────────────
FORMAT           = pyaudio.paInt16
CHANNELS         = 1
SEND_SAMPLE_RATE = 16000
RECV_SAMPLE_RATE = 24000
CHUNK_SIZE       = 512

# ── Tool tanımları — paylaşılan modülden ────────────────────────────────────
from tool_defs import TOOL_DECLARATIONS


def get_api_key() -> str:
    return str(get_app_config_value("gemini_api_key", "") or "")


def load_system_prompt() -> str:
    # Prompt tek dosyada tutulur; Windows'a uyarlamayi prompt_loader yapar.
    from prompt_loader import load_system_prompt as _load

    return _load()


def gemini_api_issue(exc: Exception) -> tuple[str, str] | None:
    """Return safe, actionable guidance for common key/quota failures."""
    kind = classify_gemini_api_error(exc)
    if kind == "quota":
        return (
            "quota",
            "Gemini kullanım limiti dolmuş veya istek sınırına ulaşılmış olabilir. "
            "AI Studio'da proje kotasını kontrol et. Yeni anahtar aynı projedeyse "
            "limiti sıfırlamayabilir; gerekirse başka projeden anahtar ekle. API SETTINGS'i açtım.",
        )
    if kind == "invalid_key":
        return (
            "key",
            "Gemini API anahtarı geçersiz, iptal edilmiş veya erişim izni yok. "
            "Google AI Studio'dan yeni anahtar alıp API SETTINGS'e gir; anahtarı sohbete gönderme.",
        )
    if kind in {"permission", "model_access"}:
        return (
            "access",
            "Gemini anahtarı veya Google projesinde bu API/model için erişim izni yok. "
            "AI Studio'da proje erişimini kontrol et; anahtarı değiştirmen gerekirse API SETTINGS'i aç.",
        )
    return None


class JarvisLive:
    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._audio_turn_complete = True
        self._audio_writing = False
        self._speaking_lock = threading.Lock()
        self._music_proc    = None
        self._webcam_streamer = WebcamStreamer()
        self._wake_listener = None
        self._fallback_lock = threading.Lock()
        self._fallback_busy = False
        self._elevenlabs_active = False
        self._response_started = False
        self._last_input_activity = None
        self._standby = bool(get_app_config_value("local_wake_enabled", True))
        self._sleep_requested = False

        self.ui.on_text_command  = self._on_text_command
        self.ui.on_pause_toggle  = self._on_pause_toggle
        self.ui.on_effects_state_change = self._on_effects_state_change
        self.ui.on_webcam_toggle = self._on_webcam_toggle_ui
        self._paused             = False
        self.ui.root.after(0, lambda: self.ui.root.protocol("WM_DELETE_WINDOW", self.request_standby))
        self.ui.root.after(0, lambda: self.ui.root.bind("<F12>", lambda e: self.ui._shutdown()))

    def request_standby(self):
        """Hide immediately; local wake input survives. Never terminate Windows."""
        phone_call("conversation_stop")
        self._standby = True
        self._sleep_requested = True
        self.set_speaking(False)
        self.ui.wake_waiting = True
        self.ui.root.after(0, self.ui.root.withdraw)
        if self._webcam_streamer.is_active:
            self._webcam_streamer.stop()
            self.ui.set_webcam_active(False)
        if self._loop:
            self._loop.call_soon_threadsafe(clear_queue, self.out_queue)
            self._loop.call_soon_threadsafe(clear_queue, self.audio_in_queue)
        audio_event("standby_requested")

    def _on_pause_toggle(self, paused: bool):
        self._paused = paused
        if paused:
            self._stop_music()

    def _on_effects_state_change(self, enabled: bool):
        # Interface effects must not control user-requested music playback.
        pass

    def _on_webcam_toggle_ui(self, activate: bool):
        if activate:
            status = self._webcam_streamer.start()
            self.ui.set_webcam_active(status == "ok" or status == "already_active")
        else:
            self._webcam_streamer.stop()
            self.ui.set_webcam_active(False)

    def _focus_ui_section_for_tool(self, tool_name: str, args: dict):
        if tool_name == "sys_info":
            query = str(args.get("query", "")).strip().lower()
            if query in {"time", "saat", "zaman", "date", "tarih"}:
                self.ui.focus_panel("time", duration_ms=5200)
            else:
                self.ui.focus_panel("system", duration_ms=5200)
        elif tool_name == "get_weather":
            self.ui.focus_panel("weather", duration_ms=5600)

    def _on_text_command(self, text: str):
        from local_wake import is_standby_command
        if is_standby_command(text):
            self.request_standby()
            return
        self._standby = False
        if hasattr(self, "_conversation_gate"):
            self._conversation_gate.touch()
        if self._paused:
            return
        self.ui.write_log(f"Siz: {text}")
        threading.Thread(target=self._remember_text, args=(text,), daemon=True).start()
        if not self._loop or not self.session:
            self.ui.write_log("SYS: Gemini Live yok; DeepSeek yedek görev yolu deneniyor.")
            if self._loop:
                self._schedule_fallback_command(text)
            else:
                self.ui.write_log("ERR: JARVIS komut motoru henüz başlamadı.")
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _on_wake(self):
        """Bring the assistant forward and acknowledge the wake word."""
        self._standby = False
        try:
            self.ui.root.after(0, self.ui.root.deiconify)
            self.ui.root.after(40, lambda: focus_window("J.A.R.V.I.S"))
        except Exception:
            pass
        # This runs in the wake listener thread.  Blocking here is deliberate:
        # it prevents the acknowledgement from being recognised as a command.
        speak_text("Emrinizdeyim efendim.", blocking=True)

    def _on_wake_command(self, text: str):
        """Deliver the STT command to the already-connected Live session."""
        clean = str(text or "").strip()
        if not clean or self._paused:
            return
        self.ui.write_log(f"Siz: {clean}")
        threading.Thread(target=self._remember_text, args=(clean,), daemon=True).start()
        if not self._loop:
            self.ui.write_log("ERR: Komut motoru henüz hazır değil.")
            return
        if not self.session:
            self._schedule_fallback_command(clean)
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": clean}]}, turn_complete=True
            ),
            self._loop,
        )

    def _schedule_fallback_command(self, text: str):
        """Run a bounded DeepSeek tool-routing turn when Gemini Live is unavailable."""
        if not self._loop or self._loop.is_closed():
            self.ui.write_log("ERR: Görev yolu hazır değil; JARVIS bağlantısını tekrar deniyor.")
            return
        future = asyncio.run_coroutine_threadsafe(self._deepseek_fallback_command(text), self._loop)
        def completed(done):
            try:
                done.result()
            except Exception as exc:
                self.ui.root.after(0, self.ui.write_log, f"ERR: Yedek görev yolu — {type(exc).__name__}: {str(exc)[:140]}")
        future.add_done_callback(completed)

    async def _deepseek_fallback_command(self, text: str):
        """Use DeepSeek to select one already-implemented, allowlisted local action."""
        from actions.deepseek_api import chat, has_deepseek_api_key, DeepSeekAPIError
        if not has_deepseek_api_key():
            message = "DeepSeek anahtarı ayarlı değil; Gemini Live de bağlı değil. API ayarlarından anahtar ekleyebilirsin."
            self.ui.root.after(0, self.ui.write_log, "ERR: " + message)
            return
        with self._fallback_lock:
            if self._fallback_busy:
                self.ui.root.after(0, self.ui.write_log, "SYS: Önceki yedek görev hâlâ çalışıyor.")
                return
            self._fallback_busy = True
        try:
            from tool_defs import TOOL_DECLARATIONS
            allowed = {
                "research", "browser_control", "open_app", "close_app", "sys_info",
                "get_weather", "get_calendar_events", "add_calendar_event", "get_reminders",
                "add_reminder", "desktop_control", "excel_control", "analyze_screen",
                "save_screenshot", "open_creator_workspaces", "check_vercel_domain",
                "create_blender_primitive", "get_recent_emails", "save_memory", "recall_memory",
                "daily_life", "daily_text", "personal_routine", "send_whatsapp_message", "work_studio",
            }
            definitions = [item for item in TOOL_DECLARATIONS if item.get("name") in allowed]
            catalog = [{"name": item["name"], "description": item.get("description", ""),
                        "parameters": item.get("parameters", {})} for item in definitions]
            plan_text = await asyncio.to_thread(chat, [
                {"role": "system", "content": (
                    "Sen JARVIS'in görev yönlendiricisisin. Kullanıcı isteğini verilen yerel araç kataloğundaki "
                    "tek bir araca yönlendir veya araç gerekmiyorsa yanıt ver. Yalnız JSON: "
                    "{\"tool\":\"araç_adı veya none\",\"args\":{},\"answer\":\"kısa Türkçe yanıt\"}. "
                    "Sadece katalogda olan araçları seç; argümanları isteğe göre çıkar, kullanıcıdan gelmeyen kişi, "
                    "dosya, tarih, bağlantı veya sayı uydurma. Çok adımlı araştırma/fikir bulma için research "
                    "deep_research çağır. Web'de anlık arama için browser_control search. Excel'de fikir/tablo "
                    "istenirse research export_excel=true. Mesaj gönderme, arama, takvim ekleme, dosya/ayar değiştirme "
                    "gibi dış etkili işlerde yalnız kullanıcı bunu açıkça istediyse aracı seç ve aracın kendi onay "
                    "kurallarını aşma. Terminal/shell, silme, satın alma, yayınlama ve JARVIS'i kapatma araçları "
                    "bu yedek modda kullanılamaz. Kullanıcı genel bilgi veya fikir istiyorsa doğrudan yanıtla; "
                    "'internetten araştır/derin araştır' derse mutlaka araştırma aracı seç.\nARAÇLAR:\n" +
                    json.dumps(catalog, ensure_ascii=False)
                )},
                {"role": "user", "content": text},
            ], max_tokens=1200, timeout=45, json_output=True)
            try:
                plan = json.loads(plan_text)
            except ValueError:
                plan = {"tool": "none", "args": {}, "answer": plan_text[:1000]}
            name = str(plan.get("tool", "none"))
            args = plan.get("args", {})
            if name not in allowed or not isinstance(args, dict):
                name, args = "none", {}
            self.ui.root.after(0, self.ui.write_log, f"SYS: DeepSeek yedek modu — {name if name != 'none' else 'yanıt'}")
            answer = str(plan.get("answer", "") or "").strip()
            if name != "none":
                fake_call = SimpleNamespace(id="fallback-local", name=name, args=args)
                response = await self._execute_tool(fake_call)
                tool_result = response.response.get("result", "")
                if name == "research" and str(args.get("action", "")) == "deep_research":
                    answer = tool_result
                else:
                    answer = await asyncio.to_thread(chat, [
                        {"role": "system", "content": "JARVIS'sin. Yerel araç sonucunu Türkçe, kısa ve dürüstçe aktar. Başarısız işi başarılı deme. Araç asenkron başladıysa tamamlandı deme. Kullanıcı aksiyonuyla ilgili önemli sınırlamayı belirt."},
                        {"role": "user", "content": text},
                        {"role": "assistant", "content": "Yerel araç sonucu: " + str(tool_result)[:7000]},
                    ], max_tokens=500, timeout=35)
            if not answer:
                answer = "İsteği aldım ama güvenli bir yerel araca eşleyemedim; Gemini bağlantısı gelince tekrar deneyebilirsin."
            self.ui.root.after(0, self.ui.write_log, "JARVIS: " + answer[:1200])
            if not self.ui.muted and not getattr(self, "_standby", False):
                self.set_speaking(True)
                try:
                    await asyncio.to_thread(speak_text, answer[:1600], None, True)
                finally:
                    self.set_speaking(False)
        except DeepSeekAPIError as exc:
            self.ui.root.after(0, self.ui.write_log, "ERR: DeepSeek yedek yolu — " + str(exc))
        finally:
            with self._fallback_lock:
                self._fallback_busy = False

    def _wake_only_enabled(self) -> bool:
        if get_app_config_value("local_wake_enabled", True):
            return False
        return bool(get_app_config_value("wake_word_enabled", False))

    def _uses_elevenlabs_voice(self) -> bool:
        return self._elevenlabs_active

    async def _prepare_voice_output(self):
        """Use Gemini audio whenever the selected ElevenLabs voice is unavailable."""
        requested = str(get_app_config_value("tts_provider", "system") or "system").lower()
        self._elevenlabs_active = False
        if requested != "elevenlabs":
            return
        from actions.elevenlabs_tts import check_voice_access, set_session_voice_enabled
        ok, reason = await asyncio.to_thread(check_voice_access)
        self._elevenlabs_active = ok
        set_session_voice_enabled(ok)
        if not ok:
            self.ui.write_log(f"SYS: {reason}. Gemini'in canlı sesi kullanılacak.")
            self.ui.write_debug(reason, level="WARN")

    async def _interrupt_audio(self):
        try:
            if self.audio_in_queue:
                while not self.audio_in_queue.empty():
                    try:
                        self.audio_in_queue.get_nowait()
                    except Exception:
                        break
            if self.session:
                await self.session.send_realtime_input(audio_stream_end=True)
            self.set_speaking(False)
        except Exception:
            pass

    def _stop_music(self):
        proc = self._music_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        self._music_proc = None

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        else:
            self.ui.set_state("LISTENING")

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.ui.write_debug(f"{tool_name}: {short}", level="ERROR")
        self.ui.set_state("ERROR")

    @staticmethod
    def _result_looks_like_error(result) -> bool:
        text = str(result or "").strip().lower()
        if not text:
            return False
        error_markers = (
            "hata",
            "error",
            "alinamadi",
            "alınamadı",
            "bulunamadi",
            "bulunamadı",
            "acilamadi",
            "açılamadı",
            "tamamlanamadi",
            "tamamlanamadı",
            "gecersiz",
            "geçersiz",
            "izin gerekiyor",
            "izin gerekli",
            "baglanti",
            "bağlantı",
            "gerekli.",
        )
        return any(marker in text for marker in error_markers)

    @staticmethod
    def _should_play_success_sfx(tool_name: str, args: dict, result) -> bool:
        action_tools = {
            "open_app",
            "add_calendar_event",
            "add_reminder",
            "delete_calendar_event",
            "remove_calendar_event",
        }
        if tool_name in action_tools:
            return True

        if tool_name == "send_whatsapp_message":
            text = str(result or "").lower()
            if bool(args.get("send_now", False)):
                return "gönderildi" in text or "gonderildi" in text
            return False

        return False

    @staticmethod
    def _clean_transcript_text(text: str) -> tuple[str, bool]:
        raw = str(text or "")
        had_noise = False
        if CONTROL_TOKEN_RE.search(raw):
            had_noise = True
            raw = CONTROL_TOKEN_RE.sub(" ", raw)
        cleaned = []
        for ch in raw:
            if ch in "\n\r\t" or ord(ch) >= 32:
                cleaned.append(ch)
            else:
                had_noise = True
        normalized = " ".join("".join(cleaned).split())
        return normalized.strip(), had_noise

    def _remember_text(self, text):
        try:
            TurnMemory().save(text)
        except Exception as exc:
            audio_event("memory_write_error", error_type=type(exc).__name__)
            self.ui.write_debug("Konuşma hafızaya kaydedilemedi; disk erişimini kontrol et.", level="WARN")

    async def _persist_memory(self, turn, user_text, assistant_text=""):
        if not user_text.strip():
            return
        if phone_call_conversation_active():
            turn.disabled = True
        try:
            await asyncio.to_thread(turn.save, user_text, assistant_text)
        except Exception as exc:
            audio_event("memory_write_error", error_type=type(exc).__name__)
            self.ui.write_debug("Konuşma hafızaya kaydedilemedi; disk erişimini kontrol et.", level="WARN")

    def _build_config(self) -> types.LiveConnectConfig:
        import datetime
        memory  = load_memory()
        mem_str = format_memory_for_prompt(memory)
        sys_p   = load_system_prompt()
        now     = datetime.datetime.now()
        time_ctx = f"[ŞU ANKİ ZAMAN]\n{now.strftime('%A, %d %B %Y — %H:%M')}\n\n"

        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str + "\n\n")
        try:
            recent = format_history(limit=4, char_limit=1800)
            if recent:
                parts.append(recent + "\n\n")
        except Exception as exc:
            audio_event("memory_read_error", error_type=type(exc).__name__)
        routines = routine_names_for_prompt()
        if routines:
            parts.append(routines + "\n\n")
        parts.append(sys_p)

        return configure_live(types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=str(get_app_config_value("voice", "Charon") or "Charon")
                    )
                )
            ),
        ), fast=bool(get_app_config_value("low_latency_voice", True)))

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        # Sessizlik zaman aşımı uzun süren görevleri yarıda uyutmasın.
        self._active_tool_count = getattr(self, "_active_tool_count", 0) + 1
        gate = getattr(self, "_conversation_gate", None)
        if gate:
            gate.touch()
        try:
            return await self._execute_tool_impl(fc)
        finally:
            self._active_tool_count -= 1
            if gate and not getattr(self, "_standby", False):
                gate.touch()

    async def _execute_tool_impl(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})
        if name == "phone_call":
            print(f"[JARVIS] 🔧 phone_call action={args.get('action')} recipient=<gizli>")
        else:
            print(f"[JARVIS] 🔧 {name} {args}")
        self.ui.set_state("THINKING")

        loop   = asyncio.get_event_loop()
        result = "Tamam."
        had_exception = False

        try:
            if name == "assistant_standby":
                self.request_standby()
                result = "JARVIS beklemede. Pencere gizlendi. Sesli yanıt verme; Hey Jarvis bekleniyor."
            elif name == "open_api_settings":
                self.ui.open_api_settings()
                result = "Gemini API ayar penceresini açtım. Yeni anahtarı yalnızca bu pencerede girip Kaydet'e bas; anahtarı sesli söyleme veya sohbete yapıştırma."
            elif getattr(self, "_standby", False):
                result = "JARVIS beklemede; işlem yapılmadı. Kullanıcı yeniden uyandırmalı."
            elif name == "defender_security":
                result = await loop.run_in_executor(
                    None, lambda: defender_security(args.get("action", "status")))
            elif name == "research":
                result = await loop.run_in_executor(
                    None, lambda: research(
                        args.get("action", ""),
                        args.get("query", ""),
                        args.get("interaction_id", ""),
                        bool(args.get("confirmed", False)),
                        bool(args.get("export_excel", False)),
                    ))
            elif name == "work_studio":
                result = await loop.run_in_executor(None, lambda: work_studio(**args))
            elif name == "close_app":
                result = await loop.run_in_executor(None, lambda: close_app(**args))
            elif name == "desktop_control":
                result = await loop.run_in_executor(None, lambda: desktop_control(**args))
            elif name == "android_device":
                action = args.get("action", "status")
                if action in {"tap", "long_press", "swipe", "type_text", "press_key", "back", "home", "overview"}:
                    result = await loop.run_in_executor(None, lambda: android_input(
                        action,
                        x=args.get("x", 0), y=args.get("y", 0),
                        x2=args.get("x2", 0), y2=args.get("y2", 0),
                        duration_ms=args.get("duration_ms", 350),
                        device_id=args.get("device_id", ""),
                        text=args.get("text", ""), key=args.get("key", "")
                    ))
                else:
                    result = await loop.run_in_executor(
                        None, lambda: android_device(
                            action, args.get("device_id", ""),
                            args.get("package_name", ""), args.get("url", "")
                        )
                    )
            elif name == "save_code_file":
                result = await loop.run_in_executor(
                    None, lambda: save_code_file(args.get("filename", ""), args.get("code", ""))
                )
            elif name == "phone_call_bridge":
                result = await loop.run_in_executor(
                    None, lambda: phone_call_bridge(args.get("action", "status"))
                )
            elif name == "phone_call":
                result = await loop.run_in_executor(None, lambda: phone_call(
                    args.get("action", ""),
                    args.get("recipient_name", ""),
                    args.get("phone_number", ""),
                    args.get("message", ""),
                ))
            elif name == "excel_control":
                result = await loop.run_in_executor(None, lambda: excel_control(**args))
            elif name == "daily_life":
                result = await loop.run_in_executor(None, lambda: daily_life(**args))

            elif name == "daily_text":
                from actions.daily_text import daily_text
                result = await loop.run_in_executor(None, lambda: daily_text(**args))

            elif name == "personal_routine":
                result = await loop.run_in_executor(None, lambda: personal_routine(
                    args.get("action", ""), args.get("name", ""),
                    args.get("apps", ""), args.get("workspaces", ""),
                    args.get("urls", ""), args.get("searches", ""), args.get("folders", ""),
                ))

            elif name == "recall_memory":
                result = await loop.run_in_executor(
                    None, lambda: recall_memory(args.get("query", "")))

            elif name == "save_memory":
                result = await asyncio.to_thread(save_fact, args.get("category", "notes"),
                                                 args.get("key", ""), args.get("value", ""))

            elif name == "delete_memory":
                result = delete_memory(
                    args.get("category", ""),
                    args.get("key", ""),
                    args.get("match_text", ""),
                )

            elif name == "open_app":
                r = await loop.run_in_executor(
                    None, lambda: open_app(args.get("app_name", "")))
                result = r or f"{args.get('app_name')} açıldı."

            elif name == "sys_info":
                self._focus_ui_section_for_tool(name, args)
                r = await loop.run_in_executor(
                    None, lambda: sys_info(args.get("query", "all")))
                result = r or "Bilgi alındı."

            elif name == "get_weather":
                self._focus_ui_section_for_tool(name, args)
                r = await loop.run_in_executor(
                    None, lambda: get_weather_summary(args.get("location") or None))
                result = r or "Hava durumu bilgisi alindi."

            elif name == "get_calendar_events":
                r = await loop.run_in_executor(
                    None,
                    lambda: get_calendar_events(
                        args.get("query", "today"),
                        int(args.get("limit", 6) or 6),
                    ),
                )
                result = r or "Takvim bilgisi alindi."

            elif name == "add_calendar_event":
                r = await loop.run_in_executor(
                    None,
                    lambda: add_calendar_event(
                        args.get("title", ""),
                        args.get("start_iso", ""),
                        args.get("end_iso", ""),
                        args.get("notes", ""),
                        args.get("location", ""),
                        args.get("calendar_name", ""),
                        bool(args.get("all_day", False)),
                    ),
                )
                result = r or "Takvim etkinligi eklendi."

            elif name == "delete_calendar_event":
                r = await loop.run_in_executor(
                    None,
                    lambda: delete_calendar_event(
                        args.get("title", ""),
                        args.get("start_iso", ""),
                        args.get("calendar_name", ""),
                        bool(args.get("delete_all_matches", False)),
                    ),
                )
                result = r or "Takvim etkinligi silindi."

            elif name == "get_reminders":
                r = await loop.run_in_executor(
                    None,
                    lambda: get_reminders(
                        args.get("query", "upcoming"),
                        int(args.get("limit", 8) or 8),
                        args.get("list_name", ""),
                    ),
                )
                result = r or "Animsatici bilgisi alindi."

            elif name == "add_reminder":
                r = await loop.run_in_executor(
                    None,
                    lambda: add_reminder(
                        args.get("title", ""),
                        args.get("due_iso", ""),
                        args.get("notes", ""),
                        args.get("list_name", ""),
                        args.get("priority", ""),
                        bool(args.get("all_day", False)),
                    ),
                )
                result = r or "Animsatici eklendi."

            elif name == "browser_control":
                r = await loop.run_in_executor(
                    None, lambda: browser_control(
                        args.get("action"),
                        args.get("url"),
                        args.get("query"),
                        args.get("tab_id", "")
                    ))
                result = r or "Tamam."

            elif name == "open_creator_workspaces":
                r = await loop.run_in_executor(
                    None, lambda: open_creator_workspaces(args.get("workspaces", "")))
                result = r or "Çalışma alanları açıldı."

            elif name == "check_vercel_domain":
                r = await loop.run_in_executor(
                    None, lambda: check_vercel_domain(args.get("domain", "")))
                result = r or "Vercel alan adı kontrol edildi."

            elif name == "get_recent_emails":
                r = await loop.run_in_executor(
                    None, lambda: get_recent_emails(
                        int(args.get("limit", 5) or 5), bool(args.get("unread_only", False))
                    ))
                result = r or "E-posta özeti alındı."

            elif name == "create_blender_primitive":
                r = await loop.run_in_executor(
                    None, lambda: create_blender_primitive(
                        args.get("kind", "cube"), args.get("name", "JARVIS nesnesi"), args.get("size", 2.0)
                    ))
                result = r or "Blender sahnesi başlatıldı."

            elif name == "shell_run":
                r = await loop.run_in_executor(
                    None, lambda: shell_run(args.get("command", "")))
                result = r or "Komut çalıştırıldı."

            elif name == "toggle_webcam":
                action = str(args.get("action", "start")).strip().lower()
                if action == "start":
                    status = self._webcam_streamer.start()
                    if status == "ok":
                        self.ui.set_webcam_active(True)
                        result = (
                            "Webcam akışı başlatıldı. "
                            "Artık kameranı görüyorum — dilediğin zaman soru sorabilirsin."
                        )
                    elif status == "already_active":
                        result = "Webcam zaten açık, görüntü alıyorum."
                    else:
                        result = "Webcam başlatılamadı: opencv-python yüklü değil."
                else:
                    self._webcam_streamer.stop()
                    self.ui.set_webcam_active(False)
                    result = "Webcam akışı durduruldu."

            elif name == "robot_arm":
                result = await loop.run_in_executor(
                    None, lambda: robot_arm(
                        args.get("action", "status"),
                        args.get("servo_index"),
                        args.get("value"),
                    ),
                )

            elif name == "play_media":
                r = await loop.run_in_executor(
                    None,
                    lambda: play_media(
                        args.get("query", ""),
                        args.get("provider", "auto"),
                        bool(args.get("autoplay", True)),
                    ),
                )
                result = r or "Medya oynatma başlatıldı."

            elif name == "control_media":
                r = await loop.run_in_executor(
                    None, lambda: control_media(args.get("action", "pause")))
                result = r or "Medya komutu gonderildi."

            elif name == "get_youtube_channel_report":
                r = await loop.run_in_executor(
                    None,
                    lambda: get_youtube_channel_report(
                        args.get("query", "overview"),
                        args.get("handle", ""),
                        int(args.get("video_limit", 6) or 6),
                    ),
                )
                result = r or "YouTube kanal raporu alindi."

            elif name == "analyze_screen":
                r = await loop.run_in_executor(
                    None,
                    lambda: analyze_screen(
                        args.get("query", "Ekranda ne var?"),
                        args.get("target", "active_window"),
                        args.get("device_id", ""),
                    ),
                )
                result = r or "Ekran analizi tamamlandi."

            elif name == "save_screenshot":
                result = await loop.run_in_executor(
                    None, lambda: save_screenshot(
                        args.get("target", "active_window"), args.get("device_id", "")
                    )
                )

            elif name == "send_whatsapp_message":
                r = await loop.run_in_executor(
                    None,
                    lambda: send_whatsapp_message(
                        args.get("message", ""),
                        args.get("phone_number", ""),
                        args.get("recipient_name", ""),
                        bool(args.get("send_now", False)),
                        args.get("app_target", "auto"),
                    ),
                )
                result = r or "WhatsApp işlemi tamamlandı."

            elif name == "save_whatsapp_contact":
                r = await loop.run_in_executor(
                    None,
                    lambda: save_whatsapp_contact(
                        args.get("display_name", ""),
                        args.get("phone_number", ""),
                        args.get("aliases", ""),
                    ),
                )
                result = r or "WhatsApp kişisi kaydedildi."

            else:
                result = f"Bilinmeyen araç: {name}"

        except Exception as e:
            result = f"Hata: {e}"
            had_exception = True
            traceback.print_exc()
            self.speak_error(name, e)

        tool_failed = self._result_looks_like_error(result)
        if tool_failed:
            if not had_exception:
                self.ui.set_state("ERROR")
        elif self._should_play_success_sfx(name, args, result):
            self.ui.play_success_sfx()

        if not tool_failed and not self.ui.muted:
            self.ui.set_state("LISTENING")

        if name == "phone_call":
            print("[JARVIS] 📤 phone_call → sonuç ayrıntıları gizli")
        else:
            print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            if getattr(self, "_standby", False):
                continue
            if time.monotonic() - msg["captured_at"] > 0.75:
                continue
            # Explicit rate avoids ambiguity; a wedged write must reconnect.
            async with asyncio.timeout(5):
                await self.session.send_realtime_input(
                    audio=types.Blob(data=msg["data"], mime_type="audio/pcm;rate=16000"))

    async def _stream_webcam_frames(self):
        """
        Webcam aktifken her 1.5s'de EN GÜNCEL kareyi session'a gönderir.
        Queue'suz 'latest frame' yaklaşımı: model hep şimdiki görüntüyü görür.
        """
        _last_sent: bytes | None = None
        while True:
            if getattr(self, "_standby", False):
                await asyncio.sleep(0.2)
                continue
            if not self._webcam_streamer.is_active:
                await asyncio.sleep(0.2)
                continue

            jpeg = self._webcam_streamer.get_latest_frame()
            if jpeg is None or jpeg is _last_sent:
                await asyncio.sleep(0.2)
                continue

            _last_sent = jpeg
            try:
                await self.session.send_realtime_input(
                    media={"data": jpeg, "mime_type": "image/jpeg"}
                )
            except Exception as e:
                print(f"[Webcam] Frame gönderilemedi: {e}")

            await asyncio.sleep(WEBCAM_SEND_INTERVAL)

    async def _update_ui_webcam_preview(self):
        """UI önizlemesini ~24 FPS günceller. AI akışından bağımsız."""
        frame_interval = 1.0 / 24.0   # ~0.0417 sn → 24 FPS
        while True:
            if self._webcam_streamer.is_active:
                jpeg = self._webcam_streamer.get_latest_frame()
                if jpeg:
                    self.ui.update_webcam_preview(jpeg)
            await asyncio.sleep(frame_interval)

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mikrofon başladı")
        # Re-enumerate devices each connection, including newly connected headsets.
        owner = pyaudio.PyAudio()
        stream = None
        phone_loopback = None
        monitor = SignalMonitor()
        dropped_total = 0
        wake_meter_at = time.monotonic()
        wake_peak_rms = 0
        wake_peak_score = 0.0
        try:
            from local_wake import WakeDetector, ConversationGate
            use_wake = bool(get_app_config_value("local_wake_enabled", True))
            detector = await asyncio.to_thread(WakeDetector) if use_wake else None
            if detector:
                audio_event("wake_engines_ready", keyword=bool(detector.keyword),
                            keyword_error=detector.keyword_error)
            gate = getattr(self, "_conversation_gate", None) or ConversationGate(timeout=60)
            # Yeniden bağlantı, devam eden konuşmayı yeni/uyuyan oturum saymasın.
            if not getattr(self, "_standby", True):
                gate.touch()
            self._conversation_gate = gate
            self._manual_wake = False
            # Pencerenin sessizlikte kaybolması ancak kullanıcı özellikle
            # isterse açık olur. Yerel wake dinleme, pencere görünürken de
            # çalışır; bu iki davranışı birbirine bağlamak kararsız görünmeye
            # yol açıyordu.
            hide_when_waiting = bool(get_app_config_value("wake_word_hide_window", False))
            self.ui.root.after(0, lambda: self.ui.root.bind("<F8>", lambda e: setattr(self, "_manual_wake", True)))
            info = choose_local_audio_device(
                owner, "input", get_app_config_value("microphone_name_hint", "External Microphone")
            )
            microphone_index = info.get("index")
            stream = await asyncio.to_thread(owner.open, format=FORMAT, channels=CHANNELS,
                rate=SEND_SAMPLE_RATE, input=True, input_device_index=microphone_index,
                frames_per_buffer=CHUNK_SIZE)
            audio_event("microphone_open", device=info["name"], sample_rate=SEND_SAMPLE_RATE)
            self.ui.write_debug("Mikrofon: " + info["name"])
            while True:
                if getattr(self, "_sleep_requested", False):
                    self._sleep_requested = False
                    gate.sleep()
                    clear_queue(self.out_queue)
                    clear_queue(self.audio_in_queue)
                    if detector:
                        detector.reset()
                if self.ui.muted is True or self._paused:
                    if phone_loopback is not None:
                        await asyncio.to_thread(phone_loopback.stop)
                        phone_loopback = None
                        audio_event("phone_loopback_closed", reason="muted_or_paused")
                    if stream is not None:
                        stream.close()
                        stream = None
                    clear_queue(self.out_queue)
                    gate.sleep()
                    if detector:
                        detector.reset()
                    self.ui.wake_waiting = False
                    await asyncio.sleep(0.1)
                    continue
                if stream is None:
                    stream = await asyncio.to_thread(owner.open, format=FORMAT, channels=CHANNELS,
                        rate=SEND_SAMPLE_RATE, input=True, input_device_index=microphone_index,
                        frames_per_buffer=CHUNK_SIZE)
                call_mode = phone_call_conversation_active()
                if call_mode and phone_loopback is None:
                    candidate = SystemLoopbackReader(
                        sample_rate=SEND_SAMPLE_RATE, block_frames=CHUNK_SIZE,
                        speaker_name_hint=get_app_config_value(
                            "speaker_name_hint", "Headphones (Realtek"
                        ),
                    )
                    if await asyncio.to_thread(candidate.start):
                        phone_loopback = candidate
                        audio_event("phone_loopback_open", device=phone_loopback.device_name,
                                    sample_rate=SEND_SAMPLE_RATE)
                        self.ui.write_debug(
                            "Telefon görüşmesi sesi JARVIS'e bağlandı: " + phone_loopback.device_name
                        )
                    else:
                        error_type = candidate.error or "loopback_start_timeout"
                        audio_event("phone_loopback_error", error_type=error_type)
                        candidate.stop()
                        phone_call("conversation_stop")
                        self.ui.write_debug(
                            "Telefon sesi yakalanamadı. Windows varsayılan hoparlör/kulaklık aygıtını kontrol et.",
                            level="ERROR",
                        )
                elif not call_mode and phone_loopback is not None:
                    await asyncio.to_thread(phone_loopback.stop)
                    phone_loopback = None
                    audio_event("phone_loopback_closed")
                data = await asyncio.to_thread(
                    stream.read, CHUNK_SIZE, exception_on_overflow=False)
                if phone_loopback is not None:
                    call_audio = phone_loopback.read_latest()
                    if phone_loopback.error:
                        audio_event("phone_loopback_error",
                                    error_type=phone_loopback.error)
                        await asyncio.to_thread(phone_loopback.stop)
                        phone_loopback = None
                        phone_call("conversation_stop")
                        self.ui.write_debug(
                            "Telefon ses bağlantısı kesildi; görüşme modu durduruldu.", level="ERROR"
                        )
                    elif call_audio:
                        with self._speaking_lock:
                            jarvis_speaking = self._is_speaking
                        # The outgoing JARVIS voice is also present in the
                        # speaker loopback. Drop that path while speaking to
                        # prevent the assistant hearing its own voice; the
                        # user's physical mic remains available.
                        if not jarvis_speaking:
                            data = mix_phone_audio(data, call_audio)
                with self._speaking_lock:
                    jarvis_speaking = self._is_speaking
                now = time.monotonic()
                if detector:
                    if getattr(self, "_active_tool_count", 0) and not getattr(self, "_standby", False):
                        gate.touch(now)
                    if jarvis_speaking and not getattr(self, "_standby", False):
                        gate.touch(now)
                    if call_mode or phone_call_waiting_for_answer():
                        # Long pauses/hold audio must not put an active phone
                        # conversation into wake-word standby.
                        gate.touch(now)
                        self._standby = False
                        waiting = False
                    else:
                        waiting = not gate.awake(now)
                    if (waiting and hide_when_waiting
                            and not getattr(self, "_standby", False)):
                        self.ui.root.after(0, self.ui.root.withdraw)
                    if waiting:
                        self._standby = True
                    self.ui.wake_waiting = waiting
                    if waiting:
                        clear_queue(self.out_queue)
                        rms, _ = monitor.feed(data, now)
                        wake_peak_rms = max(wake_peak_rms, rms)
                        heard_wake = detector.feed(data) if not self._manual_wake else False
                        wake_peak_score = max(wake_peak_score, detector.last_score)
                        if now - wake_meter_at >= 10:
                            audio_event("wake_diagnostics",
                                        peak_rms=wake_peak_rms,
                                        peak_score=round(wake_peak_score, 3),
                                        threshold=detector.threshold)
                            wake_meter_at = now
                            wake_peak_rms = 0
                            wake_peak_score = 0.0
                        if self._manual_wake or heard_wake:
                            source = "keyboard" if self._manual_wake else "voice"
                            self._manual_wake = False
                            self._standby = False
                            gate.touch(now)
                            self.ui.wake_waiting = False
                            self.ui.root.after(0, self.ui.show_from_wake)
                            runtime_event("wake_detected", source=source,
                                          engine=detector.last_source)
                            audio_event("local_wake_detected", source=source,
                                        engine=detector.last_source)
                            async with asyncio.timeout(5):
                                await self.session.send_client_content(
                                    turns={"parts":[{"text":"Kullanıcı Hey Jarvis diyerek uyandırdı. Yalnızca 'Emrinizdeyim efendim.' de ve komutunu bekle."}]},
                                    turn_complete=True)
                        continue
                if not jarvis_speaking and not self.ui.muted and not self._paused:
                    now = time.monotonic()
                    rms, warning = monitor.feed(data, now)
                    if rms >= 250:
                        if detector:
                            gate.touch(now)
                        self._last_input_activity = now
                        if now-monitor.last_activity > 0.15:
                            self.ui.mark_user_activity(True)
                            monitor.last_activity = now
                    if warning:
                        self.ui.write_debug("Mikrofonda sinyal yok. Konuşuyorsan kulaklığın bağlantısını ve Windows mikrofon ayarını kontrol et.", level="WARN")
                        audio_event("microphone_no_signal", seconds=round(now-monitor.last_signal))
                    dropped = offer_latest(self.out_queue, {"data": data, "captured_at": now})
                    dropped_total += dropped
                    if dropped and dropped_total % 50 == 1:
                        audio_event("input_congestion", dropped_frames=dropped_total)
                else:
                    clear_queue(self.out_queue)
        except Exception as e:
            audio_event("microphone_error", error_type=type(e).__name__)
            runtime_event("microphone_error", error_type=type(e).__name__)
            print(f"[JARVIS] ❌ Mikrofon: {e}")
            raise
        finally:
            if phone_loopback is not None:
                phone_loopback.stop()
            if stream is not None:
                stream.close()
            owner.terminate()

    async def _receive_audio(self):
        print("[JARVIS] 👂 Alım başladı")
        out_buf, in_buf = [], []
        memory_turn = TurnMemory()
        output_noise = False
        output_noise_samples = []
        try:
            while True:
                async for response in self.session.receive():
                    sc = response.server_content
                    if sc and sc.input_transcription and sc.input_transcription.text:
                        from local_wake import is_standby_command
                        candidate = " ".join(in_buf + [sc.input_transcription.text.strip()])
                        if getattr(sc.input_transcription, "finished", False) and is_standby_command(candidate):
                            self.request_standby()
                    if getattr(self, "_standby", False):
                        clear_queue(self.audio_in_queue)
                        await self._persist_memory(memory_turn, " ".join(in_buf), " ".join(out_buf))
                        in_buf, out_buf = [], []
                        memory_turn = TurnMemory()
                        if response.tool_call:
                            await self.session.send_tool_response(function_responses=[
                                types.FunctionResponse(id=fc.id, name=fc.name,
                                    response={"result":"JARVIS beklemede; işlem yapılmadı."})
                                for fc in response.tool_call.function_calls])
                        continue
                    if response.server_content and response.server_content.interrupted:
                        await self._persist_memory(memory_turn, " ".join(in_buf), " ".join(out_buf))
                        in_buf, out_buf = [], []
                        memory_turn = TurnMemory()
                        clear_queue(self.audio_in_queue)
                        self._audio_turn_complete = True
                        self._response_started = False
                        self.set_speaking(False)
                    for audio in pcm_chunks(response):
                        # Gemini Live still provides the reasoning, turn
                        # timing, tools and transcription.  In ElevenLabs
                        # mode its native audio is intentionally discarded and
                        # the completed transcription is synthesized below.
                        if not self._uses_elevenlabs_voice():
                            self._audio_turn_complete = False
                            self.audio_in_queue.put_nowait(audio)
                            if not self._response_started:
                                self._response_started = True
                                delay = (round(time.monotonic()-self._last_input_activity,3)
                                         if self._last_input_activity is not None else None)
                                audio_event("first_response_audio", estimated_signal_gap_seconds=delay)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            self.set_speaking(True)
                            raw_txt = sc.output_transcription.text.strip()
                            if raw_txt:
                                txt, had_noise = self._clean_transcript_text(raw_txt)
                                if had_noise:
                                    output_noise = True
                                    if len(output_noise_samples) < 4:
                                        output_noise_samples.append(raw_txt)
                                if txt:
                                    out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                in_buf.append(txt)
                                self.ui.mark_user_activity(True)

                        if phone_call_conversation_active():
                            memory_turn.disabled = True
                        if sc.input_transcription and getattr(sc.input_transcription, "finished", False):
                            await self._persist_memory(memory_turn, " ".join(in_buf), " ".join(out_buf))

                        if sc.turn_complete:
                            self._response_started = False
                            self._audio_turn_complete = True
                            if not self._audio_writing and self.audio_in_queue.empty():
                                self.set_speaking(False)

                            full_in = " ".join(in_buf).strip()
                            await self._persist_memory(memory_turn, full_in, " ".join(out_buf))
                            memory_turn = TurnMemory()
                            if full_in:
                                self.ui.write_log(f"Siz: {full_in}")
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"JARVIS: {full_out}")
                                if self._uses_elevenlabs_voice() and not self.ui.muted:
                                    self.set_speaking(True)
                                    await asyncio.to_thread(
                                        speak_text, full_out, None, True
                                    )
                                    self.set_speaking(False)
                                if output_noise_samples:
                                    self.ui.write_debug(
                                        "Kısmen filtrelenen ses transcripti: " + " | ".join(output_noise_samples),
                                        level="WARN",
                                    )
                            elif output_noise:
                                self.ui.write_log("ERR: JARVIS sesli yanıtını çözümlerken bir hata oluştu.")
                                if output_noise_samples:
                                    self.ui.write_debug(
                                        "Filtrelenen ham transcript: " + " | ".join(output_noise_samples),
                                        level="WARN",
                                    )
                                self.ui.set_state("ERROR")
                            out_buf = []
                            output_noise = False
                            output_noise_samples = []

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses)

        except Exception as e:
            print(f"[JARVIS] ❌ Alım: {e}")
            traceback.print_exc()
            raise

        finally:
            # Preserve recognized speech even if the response is interrupted by
            # a quota/network error before turn_complete arrives.
            await self._persist_memory(memory_turn, " ".join(in_buf), " ".join(out_buf))

    async def _play_audio(self):
        print("[JARVIS] 🔊 Ses çalma başladı")
        owner = pyaudio.PyAudio()
        stream = None
        bridge_stream = None
        bridge_enabled = bool(get_app_config_value("phone_call_bridge_enabled", False))
        bridge_check_at = 0.0
        bridge_retry_at = 0.0
        bridge_signal_logged = False
        try:
            info = choose_local_audio_device(
                owner, "output", get_app_config_value("speaker_name_hint", "Headphones (Realtek")
            )
            stream = await asyncio.to_thread(owner.open, format=FORMAT, channels=CHANNELS,
                rate=RECV_SAMPLE_RATE, output=True, output_device_index=info.get("index"),
                frames_per_buffer=512)
            audio_event("speaker_open", device=info["name"], sample_rate=RECV_SAMPLE_RATE)
            while True:
                chunk = await self.audio_in_queue.get()
                if getattr(self, "_standby", False):
                    continue
                # The phone bridge may be enabled while this task waits for
                # the next audio packet. Refresh it before writing that first
                # packet, or a short entire reply can be local-only.
                now = time.monotonic()
                if phone_call_conversation_active():
                    bridge_enabled = True
                elif now >= bridge_check_at or bridge_stream is not None:
                    bridge_enabled = bool(
                        get_app_config_value("phone_call_bridge_enabled", False)
                    )
                    bridge_check_at = now + 0.5
                if bridge_enabled and bridge_stream is None and now >= bridge_retry_at:
                    bridge_index = bridge_output_index(owner)
                    if bridge_index is None:
                        audio_event("phone_bridge_unavailable")
                        bridge_retry_at = now + 5.0
                    else:
                        try:
                            bridge_info = owner.get_device_info_by_index(bridge_index)
                            bridge_stream = await asyncio.to_thread(
                                owner.open,
                                format=FORMAT,
                                channels=CHANNELS,
                                rate=RECV_SAMPLE_RATE,
                                output=True,
                                output_device_index=bridge_index,
                                frames_per_buffer=512,
                            )
                            audio_event("phone_bridge_open",
                                        device=bridge_info.get("name", ""),
                                        sample_rate=RECV_SAMPLE_RATE)
                            bridge_signal_logged = False
                            print(f"[JARVIS] 📞 Ses köprüsü açık: {bridge_info.get('name', '')}")
                        except Exception as bridge_error:
                            audio_event("phone_bridge_error",
                                        error_type=type(bridge_error).__name__)
                            print(f"[JARVIS] ⚠️ Telefon köprüsü açılamadı: {bridge_error}")
                            bridge_retry_at = now + 5.0
                elif not bridge_enabled and bridge_stream is not None:
                    await asyncio.to_thread(bridge_stream.close)
                    bridge_stream = None
                    bridge_signal_logged = False
                    audio_event("phone_bridge_closed")
                self._audio_writing = True
                self.set_speaking(True)
                try:
                    await asyncio.to_thread(stream.write, chunk)
                    if bridge_stream is not None:
                        await asyncio.to_thread(bridge_stream.write, chunk)
                        if not bridge_signal_logged:
                            peak = max((abs(sample) for sample in memoryview(chunk).cast("h")), default=0)
                            if peak > 300:
                                audio_event("phone_bridge_signal", peak=peak)
                                bridge_signal_logged = True
                finally:
                    self._audio_writing = False
                    if self._audio_turn_complete and self.audio_in_queue.empty():
                        self.set_speaking(False)
        except Exception as e:
            audio_event("speaker_error", error_type=type(e).__name__)
            print(f"[JARVIS] ❌ Ses: {e}")
            raise
        finally:
            self.set_speaking(False)
            if bridge_stream is not None:
                bridge_stream.close()
            if stream is not None:
                stream.close()
            owner.terminate()

    async def run(self):
        # A crash/restart can leave the persistent preference on. Start safely:
        # only an explicitly confirmed live-call mode may route speech to CABLE.
        try:
            save_app_config({"phone_call_bridge_enabled": False})
        except Exception:
            audio_event("phone_bridge_startup_reset_failed")
        connect_attempts = 0
        api_settings_prompted = False
        self._loop = asyncio.get_running_loop()
        while True:
            # Duraklatılmışsa bağlanma, bekle
            if self._paused:
                await asyncio.sleep(1)
                continue

            client = None
            try:
                # Client'ı her bağlanışta yeniden oluştur ve anahtarı tazeden oku.
                # Böylece yeni girilen API anahtarı anında geçerli olur; ilk
                # deneme başarısız olsa bile otomatik tekrar (3sn) kendini onarır.
                client = genai.Client(
                    api_key=get_api_key(),
                    http_options={"api_version": "v1alpha"}
                )
                print("[JARVIS] 🔌 Bağlanıyor...")
                connection_started = time.monotonic()
                self.ui.set_state("THINKING")
                await self._prepare_voice_output()
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=6)
                    self._audio_turn_complete = True
                    self._audio_writing = False

                    print("[JARVIS] ✅ Bağlandı.")
                    audio_event("live_connected", seconds=round(time.monotonic()-connection_started,3))
                    if self._wake_listener is not None:
                        await asyncio.to_thread(self._wake_listener.stop_and_wait, 7)
                        self._wake_listener = None
                    self._response_started = False
                    self._last_input_activity = None
                    connect_attempts = 0          # başarılı bağlantı → sayaç sıfırla
                    api_settings_prompted = False
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: JARVIS hazır. Dinliyorum...")
                    print("[JARVIS] 🧩 Oturum arayüzü hazır.")

                    if self._wake_only_enabled() and self._wake_listener is None:
                        self._wake_listener = WakeWordListener(
                            self._on_wake_command, self._on_wake, self.ui
                        )
                        self._wake_listener.start()

                    tg.create_task(self._send_realtime())
                    # In wake-only mode SpeechRecognition owns the microphone
                    # until "Jarvis" is heard.  Normal mode keeps the original
                    # low-latency Gemini audio stream intact.
                    if not self._wake_only_enabled():
                        tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    if not self._uses_elevenlabs_voice():
                        tg.create_task(self._play_audio())
                    tg.create_task(self._stream_webcam_frames())
                    tg.create_task(self._update_ui_webcam_preview())
                    print("[JARVIS] 🧩 Oturum görevleri başlatıldı.")

            except Exception as e:
                audio_event("live_reconnect", error_type=type(e).__name__)
                runtime_event("live_reconnect", error_type=type(e).__name__)
                self.session = None
                print(f"[JARVIS] ⚠️ {e}")
                traceback.print_exc()
                api_issue = gemini_api_issue(e)
                if api_issue and not api_settings_prompted:
                    api_settings_prompted = True
                    _kind, guidance = api_issue
                    self.ui.root.after(0, self.ui.write_log, "ERR: " + guidance)
                    self.ui.open_api_settings(guidance)
                else:
                    self.ui.root.after(0, self.ui.write_log,
                        "ERR: Gemini Live bağlantısı kesildi; yeniden bağlanıyorum. DeepSeek varsa Hey Jarvis ile yedek görev modu kullanılabilir.")
                try:
                    from actions.deepseek_api import has_deepseek_api_key
                    if has_deepseek_api_key() and self._wake_listener is None:
                        self._wake_listener = WakeWordListener(
                            self._on_wake_command, self._on_wake, self.ui
                        )
                        self._wake_listener.start()
                        self.ui.root.after(0, self.ui.write_log,
                            "SYS: Gemini Live bağlı değil; Hey Jarvis ile DeepSeek yedek görev modu hazır.")
                        audio_event("deepseek_fallback_wake_ready")
                except Exception as wake_error:
                    audio_event("deepseek_fallback_wake_error", error_type=type(wake_error).__name__)
                    self.ui.root.after(0, self.ui.write_log,
                        "ERR: Yedek mikrofon başlatılamadı; F3 araştırma paneli ve yazı alanı kullanılabilir.")
                self.set_speaking(False)
                # Webcam akışını durdur — yeni session'da yeniden başlayacak
                if self._webcam_streamer.is_active:
                    self._webcam_streamer.stop()
                    self.ui.set_webcam_active(False)

                connect_attempts += 1
                # İlk birkaç deneme sessiz: yeni girilen API anahtarı Google
                # tarafında saniyeler içinde aktifleşebilir. Kullanıcıya hemen
                # "hatalı anahtar" göstermeyip kısa aralıkla otomatik tekrar dene.
                if connect_attempts <= 3:
                    self.ui.set_state("INITIALISING")
                    print(f"[JARVIS] 🔄 Bağlanmayı tekrar deniyor ({connect_attempts}/3)...")
                    await asyncio.sleep(2)
                else:
                    self.ui.write_log(
                        "ERR: JARVIS bağlantı veya ses cihazı hatası. Kulaklığı, interneti "
                        "ve API erişimini kontrol et."
                    )
                    self.ui.set_state("ERROR")
                    print("[JARVIS] 🔄 5 saniyede yeniden bağlanıyor...")
                    await asyncio.sleep(5)
            finally:
                self.session = None
                self.set_speaking(False)
                if client is not None:
                    try:
                        await client.aio.aclose()
                        client.close()
                    except Exception:
                        pass


def run_selftest() -> int:
    """
    Kurulum tanisi: `JARVIS.exe --selftest`

    Arayuzu acmadan yollari, kritik modulleri ve temel araclari dener.
    Destek isterken kullaniciya "bunu calistirip ciktiyi gonder" demek icin.
    """
    import app_paths

    # Penceresiz .exe'de konsol yoktur → ciktiyi dosyaya da yaz.
    lines: list[str] = []

    def out(text=""):
        lines.append(str(text))
        print(text)

    out("J.A.R.V.I.S — kurulum tanisi\n")
    out(app_paths.describe())

    ok = bad = 0

    def check(label, fn):
        nonlocal ok, bad
        try:
            detail = fn()
            out(f"  [OK]   {label}" + (f" — {detail}" if detail else ""))
            ok += 1
        except Exception as exc:
            out(f"  [HATA] {label} — {type(exc).__name__}: {exc}")
            bad += 1

    out("\nKritik moduller:")
    for mod in ("win32com.client", "pythoncom", "win32gui", "cv2",
                "pyaudio", "mss", "psutil", "PIL.Image", "google.genai"):
        check(mod, lambda m=mod: __import__(m) and "")

    out("\nGomulu kaynaklar:")
    for rel in (("core", "prompt.txt"), ("Fonts", "Grift-Regular.ttf"), ("SFX", "HUD.mp3")):
        check("/".join(rel), lambda r=rel: "bulundu" if app_paths.resource_path(*r).exists()
              else (_ for _ in ()).throw(FileNotFoundError(app_paths.resource_path(*r))))

    out("\nYazma izni:")

    def _write_probe():
        p = app_paths.data_path("config", ".probe")
        p.write_text("ok", encoding="utf-8")
        p.unlink()
        return str(p.parent)

    check("veri klasorune yazilabiliyor", _write_probe)

    out("\nAyarlar:")
    check("API anahtari girilmis mi",
          lambda: "evet" if get_app_config_value("gemini_api_key", "") else
          (_ for _ in ()).throw(ValueError("API anahtari girilmemis")))

    out("\nAraclar:")
    check("sys_info", lambda: sys_info("battery"))
    check("takvim", lambda: get_calendar_events("today"))
    def _shell_probe():
        result = shell_run("Write-Output JARVIS_SHELL_OK")
        if result.strip() != "JARVIS_SHELL_OK":
            raise RuntimeError(result)
        return "gerçek komut çalıştı"
    check("PowerShell", _shell_probe)
    check("COM (kisayol/Outlook icin)", lambda: _com_probe())

    out(f"\nSONUC: {ok} basarili, {bad} hatali")

    try:
        log = app_paths.data_path("jarvis_tani.log")
        log.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nRapor kaydedildi: {log}")
    except Exception:
        pass

    return 1 if bad else 0


def _com_probe() -> str:
    from actions.platform_utils import com_context

    with com_context():
        import win32com.client

        shell = win32com.client.Dispatch("WScript.Shell")
        del shell
        return "WScript.Shell olusturuldu"


def _dispatch_web_modes():
    """
    Telefon/web modlari. .exe icinde "python server.py" calistirilamadigi icin
    JARVIS.exe kendini bu bayraklarla yeniden cagirir.
    """
    argv = sys.argv[1:]

    if "--work-job" in argv:
        try:
            job_index = argv.index("--work-job")
            job_id = argv[job_index + 1]
        except (ValueError, IndexError):
            raise SystemExit(2)
        from actions.work_studio import run_job
        raise SystemExit(run_job(job_id))

    if "--web" in argv:
        # Penceresiz .exe'nin ciktisi hicbir yere gitmiyordu; TELEFON.bat'in
        # konsoluna baglan ki adres ve QR kodu gorunsun.
        attach_parent_console()
        from jarvis_web.launcher import run_orchestrator

        raise SystemExit(run_orchestrator())

    if "--web-server" in argv:
        # server.py kendi argparse'ini kullaniyor — kendi bayragimizi cikar
        sys.argv = [sys.argv[0]] + [a for a in argv if a != "--web-server"]
        from jarvis_web.server import main as server_main

        raise SystemExit(server_main() or 0)

    if "--web-agent" in argv:
        sys.argv = [sys.argv[0]] + [a for a in argv if a != "--web-agent"]
        from jarvis_web.agent import main as agent_main

        try:
            asyncio.run(agent_main())
        except KeyboardInterrupt:
            pass
        raise SystemExit(0)


def main():
    if "--selftest" in sys.argv:
        raise SystemExit(run_selftest())

    _dispatch_web_modes()

    if os.environ.get("TERM_PROGRAM") == "vscode":
        print("[JARVIS] VS Code icinden baslatildi.")

    # Zaten calisan bir JARVIS varsa yenisini acma, mevcut pencereyi one getir.
    if not acquire_single_instance():
        print("[JARVIS] Zaten calisiyor — mevcut pencere one getiriliyor.")
        focus_window("J.A.R.V.I.S")
        return

    ui = JarvisUI()
    # Independent, lightweight local timer; never changes the voice pipeline.
    from actions.reminder_service import ensure_worker
    threading.Thread(target=ensure_worker, daemon=True).start()

    # Optional room-mode: JARVIS starts with Windows but stays out of sight
    # until the wake word brings its animated screen forward.
    if (not os.environ.get("JARVIS_VISIBLE")
            and bool(get_app_config_value("local_wake_enabled", True))
            and bool(get_app_config_value("wake_word_hide_window", False))):
        ui.root.withdraw()

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Kapatılıyor...")
        except Exception as exc:
            runtime_event("fatal_runner", error_type=type(exc).__name__)
            ui.root.after(0, ui.write_log,
                          "ERR: JARVIS beklenmedik biçimde durdu. Hata kaydı alındı.")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()
