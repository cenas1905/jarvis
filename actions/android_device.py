"""Small, privacy-preserving USB Android diagnostics via Android Debug Bridge."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from actions.platform_utils import IS_WIN

WHATSAPP_PACKAGE = "com.whatsapp"
PACKAGE_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")


def _adb_path() -> str | None:
    found = shutil.which("adb")
    if found:
        return found
    if IS_WIN:
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk" / "platform-tools" / "adb.exe"
    else:
        candidate = Path.home() / "Android" / "Sdk" / "platform-tools" / "adb"
    return str(candidate) if candidate.is_file() else None


def _run(adb: str, *args: str, timeout: float = 12) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WIN else 0
    result = subprocess.run(
        [adb, *args], capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace", creationflags=flags,
    )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode:
        raise RuntimeError(output[:600] or f"ADB exited with {result.returncode}")
    return output


def _authorized_devices(adb: str) -> list[tuple[str, str]]:
    output = _run(adb, "devices", "-l")
    devices = []
    for line in output.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "device":
            model = next((item.split(":", 1)[1].replace("_", " ")
                          for item in fields[2:] if item.startswith("model:")), "Android")
            devices.append((fields[0], model))
    return devices


def _device_for(adb: str, device_id: str = "") -> tuple[str, str]:
    devices = _authorized_devices(adb)
    if not devices:
        output = _run(adb, "devices", "-l")
        if "unauthorized" in output:
            raise RuntimeError("Telefon görünüyor ancak USB hata ayıklama izni verilmemiş. Telefon ekranındaki RSA iznini onayla.")
        raise RuntimeError("Yetkilendirilmiş Android telefon bulunamadı. USB hata ayıklamayı aç, kabloyu veri aktarımı moduna al ve telefondaki izin penceresini onayla.")
    if device_id:
        match = next((item for item in devices if item[0] == device_id), None)
        if not match:
            raise RuntimeError("İstenen USB telefon ADB cihaz listesinde bulunamadı.")
        return match
    if len(devices) != 1:
        choices = ", ".join(f"{model} [device_id: {serial}]" for serial, model in devices)
        raise RuntimeError(f"{len(devices)} Android cihaz bağlı: {choices}. İstediğin telefonun device_id değerini seç.")
    return devices[0]


def _screen_size(adb: str, serial: str) -> tuple[int, int]:
    output = _run(adb, "-s", serial, "shell", "wm", "size")
    sizes = re.findall(r"(\d+)x(\d+)", output)
    if not sizes:
        raise RuntimeError("Telefon ekran boyutu alınamadı; dokunma yapılmadı.")
    width, height = map(int, sizes[-1])
    return width, height


def android_device(action: str, device_id: str = "", package_name: str = "", url: str = "") -> str:
    """Manage apps and open settings on one explicitly authorized Android device."""
    if not IS_WIN:
        return "USB Android denetimi bu sürümde yalnızca Windows'ta kullanılabilir."
    adb = _adb_path()
    if not adb:
        return "Android Platform Tools (adb) bulunamadı. Android Studio'daki SDK Platform-Tools'u kur."
    try:
        serial, model = _device_for(adb, str(device_id or "").strip())
        base = [adb, "-s", serial]
        if action == "status":
            release = _run(*base, "shell", "getprop", "ro.build.version.release")
            return f"USB telefon bağlı ve yetkili: {model}; Android {release or 'sürüm bilgisi alınamadı'}; device_id: {serial}."
        if action == "list_apps":
            output = _run(*base, "shell", "pm", "list", "packages", "-3")
            packages = sorted({line.partition(":")[2].strip() for line in output.splitlines()
                               if line.startswith("package:") and line.partition(":")[2].strip()})
            if not packages:
                return f"{model}: Kullanıcı tarafından kurulmuş uygulama paketi bulunamadı."
            shown = packages[:80]
            suffix = f" (ilk {len(shown)} paket gösteriliyor)" if len(packages) > len(shown) else ""
            return f"{model} üzerindeki uygulama paketleri{suffix}: " + ", ".join(shown)
        if action == "open_app":
            package = str(package_name or "").strip()
            if not PACKAGE_RE.fullmatch(package):
                return "Uygulamayı açmak için geçerli Android paket adı gerekli (ör. com.whatsapp)."
            installed = _run(*base, "shell", "pm", "path", package)
            if not installed.startswith("package:"):
                return f"{package} {model} telefonunda kurulu görünmüyor; açılmadı."
            _run(*base, "shell", "monkey", "-p", package,
                 "-c", "android.intent.category.LAUNCHER", "1")
            return f"{package}, {model} telefonunda açılması için başlatıldı."
        if action == "open_settings":
            _run(*base, "shell", "am", "start", "-a", "android.settings.SETTINGS")
            return f"{model} telefonunun Android Ayarlar ekranı açıldı."
        if action == "open_url":
            address = str(url or "").strip()
            if address and "://" not in address:
                address = "https://" + address
            parsed = urlparse(address)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or any(ch.isspace() for ch in address):
                return "Telefonda açmak için geçerli bir http/https adresi gerekli."
            _run(*base, "shell", "am", "start", "-a", "android.intent.action.VIEW",
                 "-d", shlex.quote(address))
            return f"Bağlantı {model} telefonunda açıldı: {address}"
        if action == "open_app_settings":
            package = str(package_name or "").strip()
            if not PACKAGE_RE.fullmatch(package):
                return "Uygulama ayarlarını açmak için geçerli Android paket adı gerekli."
            installed = _run(*base, "shell", "pm", "path", package)
            if not installed.startswith("package:"):
                return f"{package} {model} telefonunda kurulu görünmüyor; ayarları açılamadı."
            _run(*base, "shell", "am", "start", "-a",
                 "android.settings.APPLICATION_DETAILS_SETTINGS", "-d", "package:" + package)
            return f"{package} uygulama ayarları {model} telefonunda açıldı."
        if action == "open_whatsapp":
            installed = _run(*base, "shell", "pm", "path", WHATSAPP_PACKAGE)
            if not installed.startswith("package:"):
                return "WhatsApp telefonda kurulu görünmüyor; uygulama açılmadı."
            _run(*base, "shell", "monkey", "-p", WHATSAPP_PACKAGE,
                 "-c", "android.intent.category.LAUNCHER", "1")
            return "WhatsApp telefonda açılması için başlatıldı. Ekrandaki giriş/izin adımları kullanıcı tarafından tamamlanmalı."
        if action == "open_whatsapp_settings":
            installed = _run(*base, "shell", "pm", "path", WHATSAPP_PACKAGE)
            if not installed.startswith("package:"):
                return "WhatsApp telefonda kurulu görünmüyor; uygulama ayarları açılamadı."
            _run(*base, "shell", "am", "start", "-a",
                 "android.settings.APPLICATION_DETAILS_SETTINGS",
                 "-d", "package:" + WHATSAPP_PACKAGE)
            return "WhatsApp uygulama ayarları telefonda açıldı; izinler, depolama ve mobil veri durumu incelenebilir."
        if action == "diagnose_whatsapp":
            package_path = _run(*base, "shell", "pm", "path", WHATSAPP_PACKAGE)
            if not package_path.startswith("package:"):
                return f"{model} USB ile bağlı; WhatsApp ({WHATSAPP_PACKAGE}) kurulu değil veya kullanıcı 0 için görünmüyor."
            details = _run(*base, "shell", "dumpsys", "package", WHATSAPP_PACKAGE, timeout=18)
            version = re.search(r"versionName=([^\s]+)", details)
            user_state = re.search(r"User 0:.*", details)
            state = user_state.group(0) if user_state else ""
            disabled = bool(re.search(r"\benabled=(?:2|3|4)\b", state))
            stopped = bool(re.search(r"\bstopped=true\b", state))
            notes = [f"WhatsApp kurulu; {model} USB ile bağlı."]
            if version:
                notes.append("Sürüm " + version.group(1) + ".")
            notes.append("Paket bu kullanıcı için devre dışı görünüyor." if disabled else "Paket etkin görünüyor.")
            if stopped:
                notes.append("Android uygulamayı şu anda durmuş işaretliyor; açmayı deneyebilirim.")
            notes.append("Bu salt okunur denetimdir; sohbetleri, mesajları ve hesap verisini okumadım.")
            return " ".join(notes)
        if action == "repair_whatsapp":
            package_path = _run(*base, "shell", "pm", "path", WHATSAPP_PACKAGE)
            if not package_path.startswith("package:"):
                return f"{model} USB ile bağlı; WhatsApp kurulu değil. Uygulama verisi değiştirilmedi."
            details = _run(*base, "shell", "dumpsys", "package", WHATSAPP_PACKAGE, timeout=18)
            user_state = re.search(r"User 0:.*", details)
            was_disabled = bool(user_state and re.search(r"\benabled=(?:2|3|4)\b", user_state.group(0)))
            if was_disabled:
                _run(*base, "shell", "pm", "enable", "--user", "0", WHATSAPP_PACKAGE)
            _run(*base, "shell", "monkey", "-p", WHATSAPP_PACKAGE,
                 "-c", "android.intent.category.LAUNCHER", "1")
            fix = "Kapalı paket yeniden etkinleştirildi." if was_disabled else "Paket zaten etkin durumdaydı."
            return f"{model}: {fix} WhatsApp açıldı. Uygulama verisi veya sohbetler silinmedi. Ekran görüntüsünü inceleyerek kalan hatayı belirleyebilirim."
        return "İşlem tanınmadı; status, list_apps, open_app, open_url, open_settings, open_app_settings veya WhatsApp işlemlerinden birini seç."
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        return "Android USB denetimi yapılamadı: " + str(exc)


def capture_android_screen(device_id: str = "") -> tuple[bool, str, dict | None]:
    """Capture the currently displayed phone screen for a user-requested analysis."""
    if not IS_WIN:
        return False, "USB Android ekran yakalama yalnızca Windows'ta kullanılabilir.", None
    adb = _adb_path()
    if not adb:
        return False, "Android Platform Tools (adb) bulunamadı.", None
    try:
        serial, model = _device_for(adb, str(device_id or "").strip())
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            [adb, "-s", serial, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=15, creationflags=flags,
        )
        if result.returncode or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
            detail = result.stderr.decode("utf-8", "replace")[:400]
            return False, "Android ekran görüntüsü alınamadı. " + detail, None
        with tempfile.NamedTemporaryFile(prefix="jarvis-android-", suffix=".png", delete=False) as handle:
            handle.write(result.stdout)
            image_path = Path(handle.name)
        return True, "", {
            "image_path": str(image_path), "owner_name": model,
            "window_title": "Android telefon ekranı", "bounds": {}, "detail": "",
        }
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        return False, "Android ekranına erişilemedi: " + str(exc), None


def android_input(action: str, x: int = 0, y: int = 0,
                  x2: int = 0, y2: int = 0, duration_ms: int = 350,
                  device_id: str = "", text: str = "", key: str = "") -> str:
    """Perform a bounded, user-requested visible-screen action on Android."""
    if not IS_WIN:
        return "USB Android denetimi bu sürümde yalnızca Windows'ta kullanılabilir."
    adb = _adb_path()
    if not adb:
        return "Android Platform Tools (adb) bulunamadı."
    try:
        serial, _ = _device_for(adb, device_id)
        base = [adb, "-s", serial, "shell", "input"]
        width, height = _screen_size(adb, serial)
        if action == "tap":
            if not (0 <= int(x) < width and 0 <= int(y) < height):
                return "Dokunma koordinatları geçersiz. Önce Android ekranına bak."
            _run(*base, "tap", str(int(x)), str(int(y)))
            return "Telefonda dokunma yapıldı."
        if action == "swipe":
            coordinates = tuple(map(int, (x, y, x2, y2)))
            if any(value < 0 for value in coordinates) or coordinates[0] >= width or coordinates[2] >= width or coordinates[1] >= height or coordinates[3] >= height:
                return "Kaydırma koordinatları geçersiz. Önce Android ekranına bak."
            _run(*base, "swipe", *(str(value) for value in coordinates),
                 str(max(100, min(2000, int(duration_ms)))))
            return "Telefonda kaydırma yapıldı."
        if action == "back":
            _run(*base, "keyevent", "4")
            return "Android geri tuşu gönderildi."
        if action == "home":
            _run(*base, "keyevent", "3")
            return "Android ana ekrana dönüldü."
        if action == "overview":
            _run(*base, "keyevent", "187")
            return "Android son uygulamalar ekranı açıldı."
        if action == "long_press":
            if not (0 <= int(x) < width and 0 <= int(y) < height):
                return "Uzun basma koordinatları geçersiz. Önce Android ekranına bak."
            _run(*base, "swipe", str(int(x)), str(int(y)), str(int(x)), str(int(y)),
                 str(max(500, min(2000, int(duration_ms)))))
            return "Telefonda uzun basma yapıldı."
        if action == "type_text":
            value = str(text or "")
            if not value.strip() or len(value) > 2000 or any(ord(ch) < 32 for ch in value):
                return "Yazılacak metin boş olamaz, 2000 karakteri aşamaz veya satır kontrol karakteri içeremez."
            encoded = value.replace("%", "%25").replace(" ", "%s")
            _run(*base, "text", shlex.quote(encoded), timeout=15)
            return "Metin, telefonda o anda seçili alana yazıldı; gönderilmedi."
        if action == "press_key":
            keys = {
                "enter": "66", "delete": "67", "tab": "61", "space": "62",
                "escape": "111", "dpad_up": "19", "dpad_down": "20",
                "dpad_left": "21", "dpad_right": "22",
            }
            event = keys.get(str(key or "").strip().lower())
            if not event:
                return "Güvenli tuşlardan birini seç: enter, delete, tab, space, escape, dpad_up/down/left/right."
            _run(*base, "keyevent", event)
            return f"Telefona {key} tuşu gönderildi."
        return "Android girişi tap, long_press, swipe, back, home, overview, type_text veya press_key olmalı."
    except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
        return "Android işlemi yapılamadı: " + str(exc)
