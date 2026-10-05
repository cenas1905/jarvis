"""Save generated source files to a dedicated user programming folder."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from actions.platform_utils import IS_WIN

ALLOWED_EXTENSIONS = {"py", "js", "html", "css", "json", "txt", "ino", "c", "cpp", "h", "md"}


def save_code_file(filename: str, code: str) -> str:
    name = Path(str(filename or "").strip()).name
    if not name or name in {".", ".."} or len(name) > 100:
        return "Geçerli, 1–100 karakterlik bir dosya adı gerekli."
    suffix = Path(name).suffix.lower().lstrip(".")
    if suffix not in ALLOWED_EXTENSIONS:
        return "İzinli uzantılar: " + ", ".join(sorted(ALLOWED_EXTENSIONS))
    content = str(code or "")
    if not content.strip() or len(content.encode("utf-8")) > 200_000:
        return "Kaydedilecek kod boş olamaz ve 200 KB sınırını aşamaz."
    if not IS_WIN:
        return "Kod dosyası kaydetme bu sürümde yalnızca Windows'ta kullanılabilir."

    documents = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents"
    folder = documents / "JARVIS-Kod"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        safe_stem = re.sub(r"[^\w .()-]", "_", Path(name).stem, flags=re.UNICODE).strip(" .") or "kod"
        destination = folder / f"{safe_stem}.{suffix}"
        number = 2
        while destination.exists():
            destination = folder / f"{safe_stem}-{number}.{suffix}"
            number += 1
        destination.write_text(content, encoding="utf-8", newline="\n")
        subprocess.Popen(["notepad.exe", str(destination)], close_fds=True)
        return f"Kod dosyası kaydedildi ve Not Defteri'nde açıldı: {destination}. Kod çalıştırılmadı."
    except Exception as exc:
        return f"Kod dosyası kaydedilemedi: {exc}"
