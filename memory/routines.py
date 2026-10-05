"""Reusable, user-defined JARVIS routines made from existing safe actions."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from memory.memory_manager import _memory_lock
from urllib.parse import urlparse

from app_paths import data_path

ROUTINES_FILE = data_path("memory", "routines.json")
_lock = threading.RLock()
_WORKSPACES = {"vercel", "vercel_domains", "instagram", "youtube", "gmail", "tiktok"}


def _parts(value: str, limit: int = 5) -> list[str]:
    return [part.strip() for part in re.split(r"[,;\n]+", str(value or "")) if part.strip()][:limit]


def _read() -> dict:
    try:
        data = json.loads(ROUTINES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    ROUTINES_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".routines-", suffix=".json", dir=ROUTINES_FILE.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, ROUTINES_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def routine_names_for_prompt() -> str:
    names = [entry.get("name", "") for entry in _read().values() if isinstance(entry, dict)]
    names = [str(name)[:50] for name in names if name]
    return "[KAYITLI KİŞİSEL RUTİNLER] " + ", ".join(names[:20]) if names else ""


def personal_routine(action: str, name: str = "", apps: str = "",
                     workspaces: str = "", urls: str = "", searches: str = "", folders: str = "") -> str:
    action = str(action or "").strip().lower()
    label = " ".join(str(name or "").split())
    key = label.casefold()
    if action == "list":
        names = [entry.get("name", "") for entry in _read().values() if isinstance(entry, dict)]
        return "Kayıtlı rutinler: " + ", ".join(names[:30]) if names else "Kayıtlı rutin yok."
    if not 1 <= len(label) <= 50:
        return "Rutin adı 1-50 karakter olmalı."
    if action == "save":
        app_list = _parts(apps)
        workspace_list = [part.lower() for part in _parts(workspaces)]
        url_list = _parts(urls)
        search_list = _parts(searches)
        folder_list = [s.strip() for s in re.split(r"[;\n]+", folders) if s.strip()]
        if any(not Path(s).is_absolute() or not Path(s).is_dir() for s in folder_list):
            return "Klasörler mevcut ve tam yol olmalı; noktalı virgülle ayır."
        if len(app_list) + len(workspace_list) + len(url_list) + len(search_list) + len(folder_list) > 8:
            return "Bir rutin en fazla 8 işlem içerebilir."
        if not (app_list or workspace_list or url_list or search_list or folder_list):
            return "Rutine uygulama, çalışma alanı, web adresi veya arama ekle."
        unknown = [item for item in workspace_list if item not in _WORKSPACES]
        if unknown:
            return "Bilinmeyen çalışma alanı: " + ", ".join(unknown)
        for url in url_list:
            parsed = urlparse(url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                return "Web adresleri https:// veya http:// ile başlamalı."
        with _lock, _memory_lock():
            data = _read()
            if key not in data and len(data) >= 30:
                return "En fazla 30 rutin kaydedilebilir; önce birini sil."
            data[key] = {"name": label, "apps": app_list, "workspaces": workspace_list,
                         "urls": url_list, "searches": search_list, "folders": folder_list}
            _write(data)
        return f"'{label}' rutini kaydedildi."
    if action == "delete":
        with _lock, _memory_lock():
            data = _read()
            if key not in data:
                return "Bu isimde rutin bulunamadı."
            del data[key]
            _write(data)
        return f"'{label}' rutini silindi."
    if action == "run":
        entry = _read().get(key)
        if not isinstance(entry, dict):
            return "Bu isimde rutin bulunamadı. Önce kaydet."
        from actions.open_app import open_app
        from actions.browser import browser_control
        from actions.creator_tools import open_creator_workspaces
        results = []
        for folder in entry.get("folders", []):
            # Open directories only: never execute a saved file or command.
            if not Path(folder).is_absolute() or not Path(folder).is_dir():
                results.append("Klasör bulunamadı: " + folder)
                continue
            try:
                if os.name == "nt":
                    os.startfile(folder)
                    results.append("Klasör açıldı: " + folder)
                else:
                    results.append("Klasör açma bu sürümde Windows gerektiriyor.")
            except OSError:
                results.append("Klasör açılamadı: " + folder)
        for app in entry.get("apps", []):
            results.append(open_app(app))
        if entry.get("workspaces"):
            results.append(open_creator_workspaces(",".join(entry["workspaces"])))
        for url in entry.get("urls", []):
            results.append(browser_control("open_url", url=url))
        for query in entry.get("searches", []):
            results.append(browser_control("search", query=query))
        return f"'{entry.get('name', label)}' rutini: " + "; ".join(str(result)[:120] for result in results)
    return "Rutin eylemi save, run, list veya delete olmalı."
