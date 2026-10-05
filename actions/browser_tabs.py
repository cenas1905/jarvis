"""Target browser tabs by observed UIA identity, never blind Ctrl+W."""
import json
import threading
import time
import uuid
from actions.desktop_control import windows, _focus, _lock
from actions.platform_utils import com_context

_known = {}
_owned = []
BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"}


def _snapshot():
    from pywinauto import Desktop
    rows = []
    for window in windows():
        if window["process"] not in BROWSERS:
            continue
        root = Desktop(backend="uia").window(handle=window["window_id"])
        for tab in root.descendants(control_type="TabItem"):
            # Browser chrome tabs only: web-page ARIA tabs must never be closed.
            parent = tab.parent()
            in_document = False
            while parent is not None:
                if parent.element_info.control_type == "Document":
                    in_document = True
                    break
                if parent.handle == window["window_id"]:
                    break
                parent = parent.parent()
            if in_document:
                continue
            ident = (window["window_id"], tuple(tab.element_info.runtime_id))
            rows.append((ident, window, tab))
    return rows


@com_context()
def list_tabs():
    with _lock:
        out = []
        for ident, window, tab in _snapshot():
            key = next((k for k,v in _known.items() if v==ident), None) or uuid.uuid4().hex[:12]
            _known[key] = ident
            out.append({"tab_id":key,"title":tab.window_text(),"browser":window["process"],
                        "opened_by_jarvis":key in _owned})
        return json.dumps(out, ensure_ascii=False)


@com_context()
def tracked_open(url, opener):
    with _lock:
        try:
            before = {r[0] for r in _snapshot()}
        except Exception:
            before = None
        opener(url)
        if before is not None:
            for _ in range(8):
                time.sleep(0.2)
                try:
                    added = [r for r in _snapshot() if r[0] not in before]
                except Exception:
                    break
                if len(added)==1:
                    key = uuid.uuid4().hex[:12]
                    _known[key] = added[0][0]
                    _owned.append(key)
                    return key
        return None


@com_context()
def close_tab(tab_id="", last_opened=False):
    with _lock:
        try:
            if last_opened:
                if not _owned:
                    return "JARVIS'in açtığı izlenebilir sekme yok; list_tabs ile hedef seç."
                tab_id = _owned[-1]
            ident = _known.get(tab_id)
            matches = [r for r in _snapshot() if r[0]==ident]
            if len(matches)!=1:
                return "Sekme artık bulunamıyor; hiçbir sekme kapatılmadı. Yeniden listele."
            _, window, tab = matches[0]
            _focus(window)
            tab.select()
            if not tab.is_selected():
                return "Hedef sekme seçilemedi; kapatma iptal."
            from pywinauto.keyboard import send_keys
            send_keys("^w")
            time.sleep(0.25)
            if any(r[0]==ident for r in _snapshot()):
                return "Kapatma istendi; sekme hâlâ açık/onay bekliyor."
            _known.pop(tab_id, None)
            if tab_id in _owned:
                _owned.remove(tab_id)
            return "Hedef sekme kapandı. Diğer sekmelere dokunulmadı."
        except Exception as exc:
            return "Sekme kapatılamadı: " + str(exc)
