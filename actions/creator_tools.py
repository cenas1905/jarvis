"""Safe creator workflows: dashboards, Vercel domain checks and simple Blender scenes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

from app_config import get_app_config_value
from actions.platform_utils import IS_WIN, open_path, quiet_popen_kwargs


WORKSPACE_URLS = {
    "vercel": "https://vercel.com/dashboard",
    "vercel_domains": "https://vercel.com/dashboard/domains",
    "instagram": "https://www.instagram.com/accounts/insights/",
    "youtube": "https://studio.youtube.com/",
    "gmail": "https://mail.google.com/",
    "tiktok": "https://www.tiktok.com/creator-center/",
}


def open_creator_workspaces(workspaces: str) -> str:
    """Open a requested set of dashboards in separate browser tabs/windows."""
    requested = [part.strip().lower() for part in str(workspaces or "").split(",") if part.strip()]
    if not requested:
        return "Açılacak çalışma alanı belirtilmedi."
    opened, unknown = [], []
    for name in requested[:6]:
        url = WORKSPACE_URLS.get(name)
        if not url:
            unknown.append(name)
            continue
        ok, _ = open_path(url)
        if ok:
            opened.append(name)
    bits = []
    if opened:
        bits.append("Açıldı: " + ", ".join(opened))
    if unknown:
        bits.append("Bilinmeyen çalışma alanı: " + ", ".join(unknown))
    return ". ".join(bits) or "Hiçbir çalışma alanı açılamadı."


def check_vercel_domain(domain: str = "") -> str:
    """Check a domain with the Vercel API when the owner has supplied a token."""
    selected = str(domain or get_app_config_value("vercel_domain", "") or "").strip().lower()
    token = str(get_app_config_value("vercel_api_token", "") or "").strip()
    if not selected:
        return "Kontrol edilecek alan adı belirtilmedi."
    if not token:
        open_creator_workspaces("vercel_domains")
        return (
            f"Vercel alan adı panelini açtım. '{selected}' için otomatik kontrol istersen "
            "ayar dosyasına vercel_api_token eklenmeli."
        )
    try:
        response = requests.get(
            f"https://api.vercel.com/v6/domains/{selected}",
            headers={"Authorization": f"Bearer {token}"}, timeout=(5, 15),
        )
        try:
            if response.status_code == 404:
                return f"'{selected}' Vercel hesabında bulunamadı veya erişim izni yok."
            if not response.ok:
                return f"Vercel alan adı kontrolü tamamlanamadı (HTTP {response.status_code})."
            data = response.json()
        finally:
            response.close()
        verified = bool(data.get("verified", False))
        nameservers = data.get("nameservers") or []
        status = "doğrulanmış" if verified else "ekli ama henüz doğrulanmamış"
        extra = f" Nameserver: {', '.join(map(str, nameservers[:3]))}." if nameservers else ""
        return f"'{selected}' Vercel'de {status}.{extra}"
    except requests.RequestException:
        return "Vercel API bağlantısı kurulamadı; paneli açıp tekrar kontrol et."
    except (ValueError, TypeError):
        return "Vercel beklenmeyen bir yanıt döndürdü."


def _find_blender() -> Path | None:
    direct = shutil.which("blender.exe") or shutil.which("blender")
    if direct:
        return Path(direct)
    if IS_WIN:
        roots = [Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Blender Foundation"]
        for root in roots:
            if root.exists():
                candidates = sorted(root.glob("Blender*/blender.exe"), reverse=True)
                if candidates:
                    return candidates[0]
    return None


def create_blender_primitive(kind: str, name: str, size: float = 2.0) -> str:
    """Create a named cube, sphere or cylinder without arbitrary script execution."""
    blender = _find_blender()
    if not blender:
        return "Blender bulunamadı. Blender'ı kurup tekrar dene."
    kind = str(kind or "cube").lower().strip()
    operators = {
        "cube": "bpy.ops.mesh.primitive_cube_add(size=size)",
        "sphere": "bpy.ops.mesh.primitive_uv_sphere_add(radius=size / 2)",
        "cylinder": "bpy.ops.mesh.primitive_cylinder_add(radius=size / 2, depth=size)",
    }
    if kind not in operators:
        return "Şimdilik cube, sphere veya cylinder oluşturabilirim."
    try:
        size = max(0.05, min(float(size), 100.0))
    except (TypeError, ValueError):
        size = 2.0
    safe_name = "".join(ch for ch in str(name or kind) if ch.isalnum() or ch in " _-").strip()[:60] or kind
    out_dir = Path.home() / "Documents" / "JARVIS-3D"
    out_dir.mkdir(parents=True, exist_ok=True)
    blend_path = out_dir / f"{safe_name}.blend"
    script = f'''import bpy\nfor item in list(bpy.data.objects):\n    bpy.data.objects.remove(item, do_unlink=True)\nsize = {size!r}\n{operators[kind]}\nobj = bpy.context.active_object\nobj.name = {safe_name!r}\nbpy.ops.wm.save_as_mainfile(filepath={str(blend_path)!r})\n'''
    try:
        handle = tempfile.NamedTemporaryFile(prefix="jarvis-blender-", suffix=".py", delete=False, mode="w", encoding="utf-8")
        handle.write(script)
        handle.close()
        subprocess.Popen([str(blender), "--python", handle.name], **quiet_popen_kwargs())
        return f"Blender açıldı; {kind} sahnesi hazırlanıyor: {blend_path}"
    except Exception as exc:
        return f"Blender sahnesi başlatılamadı: {type(exc).__name__}"
