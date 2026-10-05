"""Confirm-before-dial calling through the Windows Phone Link app.

This module only stages a contact in Phone Link until a separate explicit
confirmation arrives. It never places a call during the prepare action.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
import unicodedata

from actions.platform_utils import IS_WIN
from app_config import save_app_config


PHONE_LINK_AUMID = "Microsoft.YourPhone_8wekyb3d8bbwe!App"
PENDING_TTL_SECONDS = 120
_lock = threading.RLock()
_pending: dict | None = None
_active_call: dict | None = None


def _normalize_name(value: str) -> str:
    value = (value or "").strip().casefold().replace("ı", "i")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value)


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("0"):
        digits = "90" + digits[1:]
    elif len(digits) == 10:
        digits = "90" + digits
    if not 8 <= len(digits) <= 15:
        raise ValueError("Telefon numarası uluslararası biçimde olmalı.")
    return "+" + digits


def _phone_link_windows():
    from pywinauto import Desktop
    import psutil

    matches = []
    for window in Desktop(backend="uia").windows():
        try:
            title = (window.window_text() or "").casefold()
            process = psutil.Process(window.process_id()).name().casefold()
            if process == "phoneexperiencehost.exe" and (
                "telefon bağlantısı" in title or "phone link" in title
            ):
                matches.append(window)
        except Exception:
            continue
    return matches


def _get_phone_link_window():
    windows = _phone_link_windows()
    if not windows:
        try:
            subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{PHONE_LINK_AUMID}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
        except Exception as exc:
            raise RuntimeError("Telefon Bağlantısı açılamadı.") from exc
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            windows = _phone_link_windows()
            if windows:
                break
            time.sleep(0.25)
    if len(windows) != 1:
        raise RuntimeError(
            "Tek bir Telefon Bağlantısı penceresi bulunamadı; arama hazırlanmadı."
        )
    # Desktop.windows() yields UIAWrapper objects. The search code below uses
    # child_window(), which belongs to a WindowSpecification instead.
    from pywinauto import Desktop
    return Desktop(backend="uia").window(handle=windows[0].handle)


def _search_controls(window, activate_calls: bool = True):
    calls_tab = window.child_window(auto_id="CallingNodeAutomationId", control_type="TabItem")
    if not calls_tab.exists(timeout=1):
        raise RuntimeError("Telefon Bağlantısı'nda Aramalar bölümü bulunamadı.")
    if activate_calls:
        calls_tab.click_input()

    suggestions = window.child_window(
        auto_id="ContactSuggestionsBox", control_type="Group"
    )
    if not suggestions.exists(timeout=2):
        raise RuntimeError("Kişi arama alanı bulunamadı; arama hazırlanmadı.")
    search = suggestions.child_window(auto_id="TextBox", control_type="Edit")
    if not search.exists(timeout=1):
        raise RuntimeError("Telefon Bağlantısı kişi arama alanı bulunamadı.")
    return suggestions, search


def _set_search_text(search, text: str):
    try:
        search.set_edit_text(text)
    except Exception:
        search.click_input()
        search.type_keys("^a{BACKSPACE}", set_foreground=True)
        search.type_keys(text, with_spaces=True, set_foreground=True)


def _search_value(search) -> str:
    try:
        return str(search.get_value() or "").strip()
    except Exception:
        try:
            return str(search.window_text() or "").strip()
        except Exception:
            return ""


def _exact_contact_rows(suggestions, expected_name: str):
    expected = _normalize_name(expected_name)
    found = []
    seen = set()
    for element in suggestions.descendants():
        try:
            kind = element.element_info.control_type
            if kind not in {"ListItem", "DataItem", "TreeItem"}:
                continue
            # Phone Link combines the contact name and phone details in the
            # row's accessible name. The actual name is a child Text control.
            names = [child.window_text().strip() for child in element.descendants()
                     if child.element_info.control_type == "Text"]
            exact_names = [name for name in names if _normalize_name(name) == expected]
            if len(exact_names) != 1:
                continue
            rect = element.rectangle()
            key = (rect.left, rect.top, rect.right, rect.bottom, exact_names[0])
            if key not in seen:
                seen.add(key)
                found.append((element, exact_names[0]))
        except Exception:
            continue
    return found


def _stage_phone_link(recipient_name: str, phone_number: str) -> tuple[int, str, str]:
    if not IS_WIN:
        raise RuntimeError("Telefon araması şu anda yalnızca Windows Phone Link ile destekleniyor.")
    window = _get_phone_link_window()
    window.set_focus()
    suggestions, search = _search_controls(window)

    if phone_number:
        target = _normalize_phone(phone_number)
        _set_search_text(search, target)
        time.sleep(0.25)
        entered = re.sub(r"\D", "", _search_value(search))
        if entered != re.sub(r"\D", "", target):
            raise RuntimeError("Numara arama alanında doğrulanamadı; arama hazırlanmadı.")
        return int(window.handle), recipient_name.strip() or "numara", target

    name = (recipient_name or "").strip()
    if not name:
        raise ValueError("Aranacak kişi adı veya telefon numarası gerekli.")
    _set_search_text(search, name)
    deadline = time.monotonic() + 3.0
    matches = []
    while time.monotonic() < deadline:
        matches = _exact_contact_rows(suggestions, name)
        if matches:
            break
        time.sleep(0.15)
    if len(matches) != 1:
        if len(matches) > 1:
            raise RuntimeError("Bu adla birden fazla kişi bulundu; arama yapılmadı.")
        raise RuntimeError(
            "Telefon Bağlantısı'nda bu adla tek ve tam eşleşme bulunamadı; "
            "arama yapılmadı. Rehberdeki görünen adıyla tekrar dene."
        )
    _, exact_name = matches[0]
    return int(window.handle), exact_name, name


def _confirm_phone_link(window_handle: int, expected_name: str, query: str) -> None:
    if not IS_WIN:
        raise RuntimeError("Telefon araması yalnızca Windows Phone Link ile destekleniyor.")
    window = _get_phone_link_window()
    if int(window.handle) != int(window_handle):
        raise RuntimeError("Telefon Bağlantısı penceresi değişti; arama yapılmadı.")
    window.set_focus()
    suggestions, search = _search_controls(window, activate_calls=False)

    if query.startswith("+"):
        if re.sub(r"\D", "", _search_value(search)) != re.sub(r"\D", "", query):
            raise RuntimeError("Arama numarası değişmiş; arama yapılmadı.")
    else:
        if _normalize_name(_search_value(search)) != _normalize_name(query):
            raise RuntimeError("Aranan kişi değişmiş; arama yapılmadı.")
        matches = _exact_contact_rows(suggestions, expected_name)
        if len(matches) != 1:
            raise RuntimeError("Kişi eşleşmesi artık tekil değil; arama yapılmadı.")
        # Selecting a result only stages it. The actual Dial click below is
        # reached only after this separate confirm action.
        matches[0][0].click_input()
        time.sleep(0.25)

    dial = window.child_window(auto_id="ButtonCall", control_type="Button")
    if not dial.exists(timeout=1) or not dial.is_enabled() or not dial.is_visible():
        raise RuntimeError("Ara düğmesi hazır değil; arama yapılmadı.")
    dial.click_input()


def phone_call(action: str, recipient_name: str = "", phone_number: str = "", message: str = "") -> str:
    """Prepare a call, then place it only after a separate confirm action."""
    global _pending, _active_call
    action = str(action or "").strip().casefold()
    recipient_name = str(recipient_name or "").strip()
    phone_number = str(phone_number or "").strip()
    message = str(message or "").strip()
    with _lock:
        if action == "dial":
            # The user's direct "X'i ara" command already authorizes the call.
            # Keep the same exact-contact validation as prepare/confirm.
            prepared = phone_call("prepare", recipient_name, phone_number, message)
            if _pending is None:
                return prepared
            return phone_call("confirm", recipient_name, phone_number, message)

        if action == "cancel":
            _pending = None
            return "Bekleyen arama iptal edildi; kimse aranmadı."

        if action == "conversation_start":
            if not _active_call or time.monotonic() > _active_call["expires_at"]:
                _active_call = None
                return "Aktif JARVIS araması yok; telefon görüşmesi dinleme modu açılmadı."
            try:
                save_app_config({"phone_call_bridge_enabled": True})
            except Exception:
                return "Telefonun ses köprüsü açılamadı; JARVIS arama moduna geçmedi."
            _active_call["conversation"] = True
            request = _active_call.get("message", "")
            objective = (
                f"Kullanıcının amacı: {request}"
                if request else "Kullanıcının amacını doğal biçimde netleştir."
            )
            return (
                "Telefon görüşmesi canlı dinleme modu açıldı. Karşıdaki kişiyle Türkçe, "
                "kısa ve doğal konuş; JARVIS olduğunu belirt. "
                f"{objective} Kullanıcının aynen iletmeni istediği kısa sözleri değiştirme; "
                "şaka amaçlıysa şaka olduğunu doğal biçimde belirt. "
                "Bilinmeyen tarih, saat veya fiyatı uydurma; randevuyu "
                "ancak karşı taraf açıkça kabul edince kesinleşmiş say. Randevu tarihi "
                "ve saati netleşirse add_calendar_event ile kullanıcının takvimine ekle; "
                "araç başarılı demeden kaydedildiğini söyleme. Sonucu kullanıcıya bildir."
            )

        if action == "conversation_stop":
            _active_call = None
            try:
                save_app_config({"phone_call_bridge_enabled": False})
            except Exception:
                pass
            return "Telefon görüşmesi dinleme modu kapatıldı; telefon araması Phone Link'te açık kalır."

        if action == "prepare":
            if bool(recipient_name) == bool(phone_number):
                return "Tek bir kişi adı veya telefon numarası ver; arama hazırlanmadı."
            try:
                hwnd, display_name, query = _stage_phone_link(recipient_name, phone_number)
            except Exception as exc:
                _pending = None
                return str(exc)
            _pending = {
                "window_handle": hwnd,
                "display_name": display_name,
                "requested_name": recipient_name,
                "query": query,
                "message": message,
                "expires_at": time.monotonic() + PENDING_TTL_SECONDS,
            }
            note = f" İletilecek söz: {message!r}." if message else ""
            return (
                f"{display_name} Telefon Bağlantısı'nda tek eşleşme olarak bulundu. "
                f"Henüz arama yapılmadı.{note} Gerçek aramayı başlatmadan önce "
                "kullanıcıdan açık bir 'evet, ara' onayı al."
            )

        if action == "confirm":
            if not _pending or time.monotonic() > _pending["expires_at"]:
                _pending = None
                return "Onaylanacak güncel bir arama yok; kimse aranmadı. Önce aramayı yeniden hazırla."
            if _pending["query"].startswith("+"):
                try:
                    confirmed_number = _normalize_phone(phone_number)
                except ValueError:
                    return "Onay için aynı telefon numarası gerekli; arama yapılmadı."
                if confirmed_number != _pending["query"]:
                    return "Onaylanan numara hazırlanan numarayla eşleşmiyor; arama yapılmadı."
            elif _normalize_name(recipient_name) not in {
                _normalize_name(_pending["display_name"]),
                _normalize_name(_pending["requested_name"]),
            }:
                return "Onaylanan kişi hazırlanan kişiyle eşleşmiyor; arama yapılmadı."
            pending = _pending
            try:
                # Keep pre-answer JARVIS speech local. Conversation mode enables
                # remote output only after the user confirms pickup.
                save_app_config({"phone_call_bridge_enabled": False})
                _confirm_phone_link(
                    pending["window_handle"], pending["display_name"], pending["query"]
                )
            except Exception as exc:
                _pending = None
                return str(exc)
            _pending = None
            _active_call = {
                "display_name": pending["display_name"],
                "message": pending["message"],
                "conversation": False,
                "expires_at": time.monotonic() + 2 * 60 * 60,
                "answer_deadline": time.monotonic() + 180,
            }
            message_note = (
                f" Bekleyen söz: {pending['message']!r}."
                if pending["message"] else ""
            )
            return (
                f"Telefon Bağlantısı {pending['display_name']} için arama başlatma "
                f"işlemini yaptı; görüşmenin yanıtlandığı doğrulanmadı.{message_note} "
                "Karşı taraf cevap verince kullanıcı 'Açtı, konuş' derse "
                "phone_call(action='conversation_start') çağır. O zamana kadar "
                "karşı tarafın duyduğunu veya görüşmenin tamamlandığını söyleme."
            )

        return (
            "phone_call action dial, prepare, confirm, cancel, conversation_start veya "
            "conversation_stop olmalı."
        )


def phone_call_conversation_active() -> bool:
    """Whether the local audio runtime should read the phone-call cable."""
    with _lock:
        return bool(
            _active_call
            and _active_call.get("conversation")
            and time.monotonic() <= _active_call.get("expires_at", 0)
        )


def phone_call_waiting_for_answer() -> bool:
    """Keep local voice control awake briefly while Phone Link is ringing."""
    with _lock:
        return bool(
            _active_call
            and not _active_call.get("conversation")
            and time.monotonic() <= _active_call.get("answer_deadline", 0)
        )


def _reset_pending_for_tests():
    global _pending, _active_call
    with _lock:
        _pending = None
        _active_call = None
