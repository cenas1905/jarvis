"""Read-only access to recent mail in classic Outlook for JARVIS."""

from __future__ import annotations

import datetime as dt

from actions.platform_utils import IS_WIN, com_context


def get_recent_emails(limit: int = 5, unread_only: bool = False) -> str:
    from actions.google_personal import connected, google_emails
    if connected():
        try:
            return google_emails(limit, unread_only)
        except Exception:
            return "Google e-posta erişimi başarısız. İzin süresi/API ayarlarını kontrol et; gelen kutusunun boş olduğu anlamına gelmez."
    from actions.win_organizer import _run_with_timeout
    try:
        return _run_with_timeout(lambda: _outlook_recent_emails(limit, unread_only), 6)
    except Exception:
        return "Outlook yanıt vermedi. E-posta özeti alınamadı."


def _outlook_recent_emails(limit: int = 5, unread_only: bool = False) -> str:
    """Return compact metadata and previews; never sends, deletes, or marks mail."""
    if not IS_WIN:
        return "E-posta okuma bu sürümde Windows Outlook gerektiriyor."
    try:
        limit = max(1, min(int(limit or 5), 15))
    except (TypeError, ValueError):
        limit = 5
    try:
        with com_context():
            import win32com.client

            # Do not launch an unconfigured Outlook and block on its setup wizard.
            outlook = win32com.client.GetActiveObject("Outlook.Application")
            namespace = outlook.GetNamespace("MAPI")
            # olFolderInbox = 6
            inbox = namespace.GetDefaultFolder(6)
            items = inbox.Items
            items.Sort("[ReceivedTime]", True)
            lines: list[str] = []
            for index in range(1, min(int(items.Count), 120) + 1):
                item = items.Item(index)
                try:
                    if unread_only and not bool(item.UnRead):
                        continue
                    sender = str(getattr(item, "SenderName", "Bilinmeyen") or "Bilinmeyen").strip()
                    subject = str(getattr(item, "Subject", "(konu yok)") or "(konu yok)").strip()
                    received = getattr(item, "ReceivedTime", None)
                    if isinstance(received, dt.datetime):
                        when = received.strftime("%d.%m %H:%M")
                    else:
                        when = str(received or "")[:16]
                    body = " ".join(str(getattr(item, "Body", "") or "").split())[:180]
                    preview = f" — {body}" if body else ""
                    lines.append(f"{when} | {sender} | {subject}{preview}")
                    if len(lines) >= limit:
                        break
                except Exception:
                    continue
            if not lines:
                return "Okunacak e-posta bulunamadı." if unread_only else "Gelen kutusunda e-posta bulunamadı."
            label = "okunmamış" if unread_only else "son"
            return f"{len(lines)} {label} e-posta:\n" + "\n".join(lines)
    except Exception as exc:
        return (
            "Outlook e-postalarına erişilemedi. Klasik Outlook'ta hesabının açık "
            f"olduğundan emin ol. Ayrıntı: {type(exc).__name__}"
        )
