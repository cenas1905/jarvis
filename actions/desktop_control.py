"""Targeted desktop actions at the current user's privileges; no elevation."""
from __future__ import annotations
import os
import threading
import time
import psutil
from actions.open_app import _normalize, _ALIAS_LOOKUP
from actions.platform_utils import com_context

_lock = threading.RLock()
PROCESSES = {
    "spotify": {"spotify.exe"}, "google chrome": {"chrome.exe"},
    "microsoft edge": {"msedge.exe"}, "notepad": {"notepad.exe"},
    "calculator": {"calculatorapp.exe","calculator.exe"}, "paint": {"mspaint.exe"},
    "discord": {"discord.exe"}, "whatsapp": {"whatsapp.exe"},
    "telegram": {"telegram.exe"}, "bambu studio": {"bambu-studio.exe"},
    "excel": {"excel.exe"}, "microsoft excel": {"excel.exe"},
    "word": {"winword.exe"}, "microsoft word": {"winword.exe"},
    "powerpoint": {"powerpnt.exe"}, "microsoft powerpoint": {"powerpnt.exe"},
    "vscode": {"code.exe"}, "visual studio code": {"code.exe"},
}
DENIED = {"cmd.exe","powershell.exe","pwsh.exe","windowsterminal.exe","consent.exe",
          "lockapp.exe","credentialuibroker.exe","logonui.exe","securityhealthsystray.exe",
          "sechealthui.exe","taskmgr.exe","keepass.exe","1password.exe","bitwarden.exe"}


def windows():
    import win32gui, win32process
    found = []
    def collect(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid).name().lower()
            if pid == os.getpid() or proc in DENIED:
                return
            found.append({"window_id":hwnd,"title":title,"process":proc})
        except (psutil.Error, OSError):
            pass
    win32gui.EnumWindows(collect,None)
    return found


def select_window(app_name="", window_id=0):
    rows = windows()
    if window_id:
        rows = [r for r in rows if r["window_id"] == int(window_id)]
    elif app_name:
        name = _normalize(app_name)
        if name in {"chatgpt", "chat gpt"}:
            rows = [r for r in rows if "chatgpt" in _normalize(r["title"])]
            if len(rows) != 1:
                raise ValueError("ChatGPT başlıklı tek bir Chrome/Edge penceresi bulunamadı; ilgili konuşmayı aç.")
            return rows[0]
        resolved = _normalize(_ALIAS_LOOKUP.get(name,app_name))
        procs = PROCESSES.get(resolved, {resolved if resolved.endswith(".exe") else resolved+".exe"})
        rows = [r for r in rows if r["process"] in procs or _normalize(r["title"]) == name]
    else:
        raise ValueError("Hedef uygulama adı veya önce listelenmiş window_id gerekli.")
    if len(rows) != 1:
        raise ValueError("Tek hedef pencere bulunamadı. Önce list_windows ile pencere kimliğini seç; işlem yapılmadı.")
    return rows[0]


def _focus(row):
    import win32gui, win32con
    hwnd = row["window_id"]
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd,win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.1)
    if win32gui.GetForegroundWindow() != hwnd:
        raise ValueError("Hedef pencere odaklanamadı; yanlış uygulamaya giriş gönderilmedi.")


def close_app(app_name="", window_id=0):
    """Normal WM_CLOSE only: never kills processes or discards unsaved work."""
    import win32gui, win32con
    try:
        with _lock:
            row = select_window(app_name,window_id)
            hwnd = row["window_id"]
            win32gui.PostMessage(hwnd,win32con.WM_CLOSE,0,0)
            deadline = time.monotonic()+2
            while time.monotonic()<deadline:
                if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                    return f"{row['process']} penceresi kapandı. Uygulama tepside/arka planda kalabilir."
                time.sleep(0.1)
            return "Normal kapatma isteği gönderildi; pencere hâlâ açık. Kaydetme/onay penceresi olabilir; zorla sonlandırılmadı."
    except Exception as exc:
        return "Kapatılamadı: " + str(exc)


@com_context()
def desktop_control(action, app_name="", window_id=0, text="", keys="", x=0, y=0, amount=0):
    import win32gui, win32api, win32con
    try:
        with _lock:
            if action == "list_windows":
                import json
                return json.dumps(windows(),ensure_ascii=False)
            if action == "close":
                return close_app(app_name,window_id)
            row = select_window(app_name,window_id)
            if action == "inspect":
                from pywinauto import Desktop
                root = Desktop(backend="uia").window(handle=row["window_id"])
                left, top = win32gui.ClientToScreen(row["window_id"],(0,0))
                lines = [str(row)]
                for element in root.descendants()[:120]:
                    try:
                        if not element.is_visible():
                            continue
                        if element.element_info.element.CurrentIsPassword:
                            continue
                        rect = element.rectangle()
                        label = element.window_text()[:100]
                        kind = element.element_info.control_type
                        lines.append(f"{kind}: {label} | merkez x={(rect.left+rect.right)//2-left}, y={(rect.top+rect.bottom)//2-top}")
                    except Exception:
                        continue
                return "\n".join(lines)
            if action == "read_visible_text":
                from pywinauto import Desktop
                root = Desktop(backend="uia").window(handle=row["window_id"])
                lines = []
                seen = set()
                total = 0
                for element in root.descendants()[:500]:
                    try:
                        if not element.is_visible() or element.element_info.element.CurrentIsPassword:
                            continue
                        if element.element_info.control_type not in {"Text", "Document"}:
                            continue
                        label = " ".join((element.window_text() or "").split())
                        if not label or label in seen:
                            continue
                        label = label[:1000]
                        if total + len(label) > 16000:
                            break
                        seen.add(label)
                        lines.append(label)
                        total += len(label)
                    except Exception:
                        continue
                return "\n".join(lines) if lines else "Görünür metin okunamadı; ekran analiziyle dene."
            _focus(row)
            if action == "focus":
                return "Hedef pencere öne getirildi."
            if action == "type_text":
                if not text or len(text)>2000:
                    raise ValueError("1–2000 karakterlik metin gerekli.")
                if any(c in text for c in "\r\n\t"):
                    raise ValueError("Metne Enter/Tab gömme; bu tuşlar için ayrı ve açık komut ver.")
                from pywinauto.keyboard import send_keys
                escaped = "".join("{"+c+"}" if c in "+^%~(){}" else c for c in str(text))
                send_keys(escaped,pause=0.001,with_spaces=True,with_tabs=True,with_newlines=True,vk_packet=True)
                return "Metin hedef pencereye yazıldı; gönderme tuşuna basılmadı."
            if action == "press_keys":
                aliases={"ctrl":"^","control":"^","alt":"%","shift":"+"}
                parts=str(keys).lower().split("+")
                if len(parts)>4 or any(p not in aliases for p in parts[:-1]):
                    raise ValueError("Örnek kısayol: ctrl+a, ctrl+c, ctrl+v, enter, escape.")
                named={"enter":"ENTER","escape":"ESC","esc":"ESC","tab":"TAB","space":"SPACE",
                       "up":"UP","down":"DOWN","left":"LEFT","right":"RIGHT","backspace":"BACKSPACE",
                       "delete":"DELETE","home":"HOME","end":"END","pageup":"PGUP","pagedown":"PGDN"}
                key=parts[-1]
                if key in named:
                    key="{"+named[key]+"}"
                elif not (len(key)==1 and key.isascii() and key.isalnum()):
                    raise ValueError("Bu tuş desteklenmiyor; Windows/UAC kısayolları kullanılamaz.")
                from pywinauto.keyboard import send_keys
                send_keys("".join(aliases[p] for p in parts[:-1])+key,pause=0.03)
                return "Tuş hedef pencereye gönderildi."
            if action in ("move","click","double_click","scroll"):
                x,y=int(x),int(y)
                rect=win32gui.GetClientRect(row["window_id"])
                if not (0<=x<rect[2] and 0<=y<rect[3]):
                    raise ValueError("Koordinat hedef pencerenin dışında.")
                point=win32gui.ClientToScreen(row["window_id"],(x,y))
                hit=win32gui.WindowFromPoint(point)
                if hit!=row["window_id"] and not win32gui.IsChild(row["window_id"],hit):
                    raise ValueError("Tıklanacak nokta başka pencere tarafından kapatılmış.")
                win32api.SetCursorPos(point)
                if action=="move":
                    return "Fare imleci hedef pencere içinde hareket ettirildi; tıklanmadı."
                if action=="scroll":
                    steps=max(-10,min(10,int(amount)))
                    win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL,0,0,steps*120,0)
                else:
                    for _ in range(2 if action=="double_click" else 1):
                        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN,0,0,0,0)
                        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP,0,0,0,0)
                        time.sleep(0.05)
                return "Fare işlemi hedef pencerede yapıldı."
            raise ValueError("Bilinmeyen masaüstü işlemi.")
    except Exception as exc:
        return "Masaüstü işlemi tamamlanmadı: "+str(exc)
