"""Read-only Gmail + Calendar through user-approved desktop OAuth (PKCE).
No model/API key is a substitute for account authorization. Tokens are Windows
DPAPI encrypted and never embedded in prompts or logs.
"""
from __future__ import annotations
import base64
import datetime as dt
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from app_paths import data_path

CLIENT_FILE = data_path("config", "google_oauth_client.json")
TOKEN_FILE = Path(os.environ.get("LOCALAPPDATA", str(data_path("config")))) / "JARVIS" / "google_readonly.dpapi"
SCOPES = ("https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/calendar.events.readonly")
_lock = threading.RLock()


def connected():
    return TOKEN_FILE.is_file()


def _read_token():
    if not connected():
        raise ValueError("Google hesabı bağlı değil. GOOGLE_BAGLA.bat ile bağla.")
    import win32crypt
    return json.loads(win32crypt.CryptUnprotectData(TOKEN_FILE.read_bytes(), None, None, None, 0)[1].decode("utf-8"))


def _save_token(payload):
    import win32crypt
    encrypted = win32crypt.CryptProtectData(json.dumps(payload).encode("utf-8"), "JARVIS read-only Google", None, None, None, 0)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=TOKEN_FILE.parent, prefix=".google-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, TOKEN_FILE)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _request(url, data=None, token=None, timeout=6):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Google erişimi başarısız (HTTP {exc.code}); API etkinliğini ve hesap iznini kontrol et.") from None


def access_token():
    with _lock:
        record = _read_token()
        if record.get("expires_at", 0) > time.time() + 60:
            return record["access_token"]
        fresh = _request("https://oauth2.googleapis.com/token", {
            "client_id": record["client_id"], "client_secret": record.get("client_secret", ""),
            "refresh_token": record["refresh_token"], "grant_type": "refresh_token"})
        record.update(fresh)
        record["expires_at"] = time.time() + fresh.get("expires_in", 3600)
        _save_token(record)
        return record["access_token"]


def google_events():
    token = access_token()
    start = dt.datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    params = urllib.parse.urlencode({"timeMin": start.isoformat(), "timeMax": (start + dt.timedelta(days=1)).isoformat(),
                                    "singleEvents": "true", "orderBy": "startTime", "maxResults": 10})
    data = _request("https://www.googleapis.com/calendar/v3/calendars/primary/events?" + params, token=token)
    lines = [str(e.get("start", {}).get("dateTime") or e.get("start", {}).get("date", "")) + " | " + str(e.get("summary", "(başlık yok)"))[:200]
             for e in data.get("items", []) if e.get("status") != "cancelled"]
    return "Google birincil takvim (bugün):\n" + ("\n".join(lines) or "Etkinlik yok.")


def google_emails(limit=5, unread_only=True):
    token = access_token()
    params = urllib.parse.urlencode({"maxResults": max(1, min(int(limit), 10)), "q": "in:inbox is:unread" if unread_only else "in:inbox"})
    data = _request("https://gmail.googleapis.com/gmail/v1/users/me/messages?" + params, token=token)
    from concurrent.futures import ThreadPoolExecutor
    def metadata(message):
        result = _request("https://gmail.googleapis.com/gmail/v1/users/me/messages/" + urllib.parse.quote(message["id"], safe="") +
                          "?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date", token=token)
        headers = {h["name"].lower(): h["value"] for h in result.get("payload", {}).get("headers", [])}
        return " | ".join(headers.get(k, "")[:220] for k in ("date", "from", "subject")) + " — " + result.get("snippet", "")[:200]
    with ThreadPoolExecutor(max_workers=5) as pool:
        lines = list(pool.map(metadata, data.get("messages", [])))
    return "Gmail (yalnızca okuma):\n" + ("\n".join(lines) or "Bu filtrede e-posta yok.")


def authorize():
    if not CLIENT_FILE.exists():
        print("Google Cloud'da Gmail API ve Google Calendar API'yi etkinleştir.")
        print("OAuth istemcisini Desktop app / Masaüstü uygulaması türünde oluştur; hesabını test kullanıcısı ekle.")
        print("İndirilen istemci JSON dosyasını şu adla kaydet: " + str(CLIENT_FILE))
        print("Gemini API anahtarı bu dosyanın yerine geçmez. Sonra bu dosyayı yeniden çalıştır.")
        return 1
    client = json.loads(CLIENT_FILE.read_text(encoding="utf-8")).get("installed", {})
    if not client.get("client_id"):
        raise ValueError("Masaüstü uygulaması türünde OAuth istemci dosyası gerekli.")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    received = {}
    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if not secrets.compare_digest(params.get("state", [""])[0], state):
                self.send_error(400, "Invalid state")
                return
            received.update({k: v[0] for k, v in params.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("JARVIS: Yetkilendirme yanıtı alındı. Kurulum penceresine dönebilirsin.".encode("utf-8"))
    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = 1
        redirect = f"http://127.0.0.1:{server.server_port}/"
        params = {"client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
                  "scope": " ".join(SCOPES), "state": state, "code_challenge": challenge,
                  "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"}
        print("Tarayıcıda yalnızca Gmail/takvim okuma izinlerini onayla. Gönderme veya silme izni istenmez.")
        webbrowser.open("https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params))
        deadline = time.monotonic() + 180
        while not received and time.monotonic() < deadline:
            server.handle_request()
        if not received.get("code"):
            print("İzin verilmedi veya süre doldu; hesap bağlanmadı.")
            return 1
        token = _request("https://oauth2.googleapis.com/token", {
            "code": received["code"], "client_id": client["client_id"], "client_secret": client.get("client_secret", ""),
            "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    granted = set(token.get("scope", "").split())
    if not set(SCOPES).issubset(granted) or not token.get("refresh_token"):
        raise ValueError("Gerekli salt-okunur izinlerin tamamı verilmedi; bağlantı kaydedilmedi.")
    token.update(client_id=client["client_id"], client_secret=client.get("client_secret", ""), expires_at=time.time() + token.get("expires_in", 3600))
    _save_token(token)
    print("Google hesabı bağlandı. JARVIS'e 'Bugün ne var?' diyebilirsin.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(authorize())
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc))
        raise SystemExit(1)

