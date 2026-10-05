"""Safe Windows Defender status, scan, and detection-history helpers.

JARVIS never deletes files directly. Microsoft Defender owns remediation and
quarantine according to the user's Windows Security policy.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from app_paths import data_path

try:
    import psutil
except Exception:  # pragma: no cover - optional diagnostic fallback
    psutil = None


_LOCK = threading.RLock()
_RUNNING_PROCESSES: dict[int, subprocess.Popen] = {}
_STATE_PATH = data_path("security", "defender_scan.json")


def _load_scan_state() -> dict:
    try:
        value = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save_scan_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_STATE_PATH)


def _defender_scan_exe() -> str | None:
    program_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    platform_dir = program_data / "Microsoft" / "Windows Defender" / "Platform"
    candidates = list(platform_dir.glob("*/MpCmdRun.exe")) if platform_dir.is_dir() else []

    def version_key(path: Path):
        parts = []
        for piece in path.parent.name.split("."):
            digits = "".join(ch for ch in piece if ch.isdigit())
            parts.append(int(digits or 0))
        return tuple(parts)

    if candidates:
        candidates.sort(key=version_key, reverse=True)
        return str(candidates[0])
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    fallback = Path(program_files) / "Windows Defender" / "MpCmdRun.exe"
    return str(fallback) if fallback.is_file() else None


def _process_matches_scan(pid: int, exe_path: str, started_epoch: float) -> bool:
    if not psutil:
        return False
    try:
        proc = psutil.Process(pid)
        if proc.name().casefold() != "mpcmdrun.exe":
            return False
        actual_exe = os.path.normcase(os.path.abspath(proc.exe()))
        expected_exe = os.path.normcase(os.path.abspath(exe_path))
        if actual_exe != expected_exe:
            return False
        if proc.create_time() < started_epoch - 3:
            return False
        command = " ".join(proc.cmdline()).casefold()
        return "-scan" in command and "-scantype" in command
    except Exception:
        return False


def _defender_events() -> tuple[list[dict] | None, str]:
    """Read recent Defender detections/remediation without exposing file paths."""
    wevtutil = shutil.which("wevtutil.exe") or shutil.which("wevtutil")
    if not wevtutil:
        return None, "Windows olay günlüğü aracı bulunamadı."
    channel = "Microsoft-Windows-Windows Defender/Operational"
    query = "*[System[(EventID=1116 or EventID=1117 or EventID=1118 or EventID=1119)]]"
    try:
        result = subprocess.run(
            [wevtutil, "qe", channel, "/q:" + query, "/f:xml", "/c:20", "/rd:true"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "Defender olay günlüğü zamanında yanıt vermedi."
    except Exception as exc:
        return None, f"Defender olay günlüğü okunamadı ({type(exc).__name__})."
    if result.returncode != 0:
        return None, "Defender olay günlüğüne erişilemedi; Windows Güvenliği'ndeki Koruma geçmişini açın."

    output = (result.stdout or "").strip()
    if not output:
        return [], ""
    try:
        root = ET.fromstring("<Events>" + output + "</Events>")
    except ET.ParseError:
        return None, "Defender olay günlüğünden okunabilir kayıt alınamadı."

    records: list[dict] = []
    for event in root.iter():
        if event.tag.rsplit("}", 1)[-1] != "Event":
            continue
        event_id = ""
        timestamp = ""
        fields: dict[str, str] = {}
        for child in event.iter():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "EventID" and not event_id:
                event_id = str(child.text or "").strip()
            elif tag == "TimeCreated" and not timestamp:
                timestamp = str(child.attrib.get("SystemTime", "")).strip()
            elif tag == "Data":
                name = str(child.attrib.get("Name", "")).strip().casefold()
                if name:
                    fields[name] = str(child.text or "").strip()
        if event_id not in {"1116", "1117", "1118", "1119"}:
            continue
        name = fields.get("threat name") or fields.get("threatname") or fields.get("name") or ""
        threat_id = fields.get("threat id") or fields.get("threatid") or ""
        action = fields.get("action name") or fields.get("action") or ""
        action_status = fields.get("action status") or ""
        records.append({
            "event_id": event_id,
            "time": timestamp,
            "name": name[:120],
            "threat_id": threat_id[:40],
            "action": action[:80],
            "action_status": action_status[:80],
        })
    return records, ""


def _history_summary() -> str:
    records, error = _defender_events()
    if error:
        return error
    if not records:
        return (
            "Yakın zamandaki Defender olay günlüğünde tehdit algılama kaydı görünmüyor. "
            "Bu tek başına bilgisayarın kesinlikle virüssüz olduğunu kanıtlamaz."
        )
    detections = [item for item in records if item["event_id"] == "1116"]
    actions = [item for item in records if item["event_id"] == "1117"]
    failures = [item for item in records if item["event_id"] in {"1118", "1119"}]
    non_remediating = [
        item for item in actions
        if item.get("action", "").strip().casefold() in {"allow", "no action", "none"}
    ]
    names = []
    for item in detections[:5]:
        label = item.get("name") or ("Tehdit " + item["threat_id"] if item.get("threat_id") else "Tehdit algılandı")
        if label not in names:
            names.append(label)
    parts = [f"Son Defender olaylarında {len(detections)} algılama, {len(actions)} eylem kaydı var"]
    if failures:
        parts.append(f"{len(failures)} giderme hatası da kaydedilmiş")
    if non_remediating:
        parts.append("en az bir eylem kaydı izin verme/işlem yapmama gösteriyor")
    if names:
        parts.append("tehdit adı: " + ", ".join(names))
    if failures or non_remediating or (detections and not actions):
        parts.append("temizleme doğrulanmadı; Windows Güvenliği > Koruma geçmişi'nde kullanıcı adımı gerekebilir")
    elif actions:
        parts.append("Defender eylem kaydı var; eylemin karantina/temizleme olup olmadığını Koruma geçmişinden doğrula")
    return ". ".join(parts) + "."


def _defender_service_status() -> str:
    if not psutil:
        return "unknown"
    try:
        return str(psutil.win_service_get("WinDefend").status()).lower()
    except Exception:
        return "not_found"


def _active_third_party_antivirus() -> list[str]:
    """Best-effort detection from running AV services; never changes them."""
    if not psutil:
        return []
    markers = {
        "Bitdefender": ("vsserv", "virus shield", "bdprot"),
        "Avast": ("avast antivirus", "avast service"),
        "AVG": ("avg antivirus", "avg service"),
        "ESET": ("ekrn", "eset service"),
        "Kaspersky": ("kaspersky antivirus", "avp service"),
        "Malwarebytes": ("malwarebytes service",),
        "McAfee": ("mcshield", "mcafee antivirus"),
        "Norton": ("norton antivirus", "symantec antivirus"),
        "Sophos": ("sophos antivirus", "sophos endpoint"),
        "Trend Micro": ("trend micro antivirus", "tmccsf"),
        "Webroot": ("webroot antivirus",),
    }
    found: list[str] = []
    try:
        for service in psutil.win_service_iter():
            try:
                if service.status().lower() != "running":
                    continue
                identity = (service.name() + " " + service.display_name()).casefold()
                for vendor, hints in markers.items():
                    if vendor not in found and any(hint in identity for hint in hints):
                        found.append(vendor)
            except Exception:
                continue
    except Exception:
        return []
    return found


def _scan_status_text(include_history: bool = True) -> str:
    with _LOCK:
        state = _load_scan_state()
        if not state:
            status_text = "Kayıtlı JARVIS Defender taraması yok."
        elif state.get("status") == "running" and _process_matches_scan(
            int(state.get("pid", 0) or 0),
            str(state.get("exe", "")),
            float(state.get("started_epoch", 0) or 0),
        ):
            label = "hızlı" if state.get("scan_type") == 1 else "tam"
            status_text = f"Defender {label} taraması sürüyor; JARVIS taramayı başlattı."
        else:
            pid = int(state.get("pid", 0) or 0)
            proc = _RUNNING_PROCESSES.get(pid)
            exit_code = proc.poll() if proc is not None else None
            state["status"] = "ended"
            if exit_code is not None:
                state["exit_code"] = int(exit_code)
            _save_scan_state(state)
            when = str(state.get("started_at", ""))
            label = "hızlı" if state.get("scan_type") == 1 else "tam"
            if exit_code == 0:
                status_text = (
                    f"Defender {label} tarama işlemi tamamlandı (çıkış kodu 0). "
                    "Bu, tehdit bulunmadığı veya bulunanların Defender tarafından giderildiği anlamına gelir; karantina geçmişini de kontrol ediyorum."
                )
            elif exit_code is not None:
                status_text = (
                    f"Defender {label} tarama işlemi hata/ek işlem koduyla bitti "
                    f"(çıkış kodu {exit_code}). Windows Güvenliği'ndeki koruma geçmişini kontrol edin."
                )
            else:
                status_text = (
                    f"En son {label} Defender taraması ({when}) başlatılmıştı; "
                    "tarama komutu artık çalışmıyor. Kesin sonucu koruma geçmişinden doğrulamak gerekir."
                )

    if include_history:
        return status_text + " " + _history_summary()
    return status_text


def defender_security(action: str = "status") -> str:
    """Run a Windows Defender check/scan. No direct file deletion is performed."""
    if os.name != "nt":
        return "Virüs koruma aracı yalnızca Windows'ta kullanılabilir."

    action = str(action or "status").strip().lower()
    if action == "status":
        service = _defender_service_status()
        scanner = bool(_defender_scan_exe())
        if service == "running" and scanner:
            return (
                "Microsoft Defender hizmeti çalışıyor ve tarama aracı mevcut. "
                "Bu kontrol gerçek zamanlı korumanın açık olup olmadığını doğrulamaz; "
                "kesin durum için Windows Güvenliği'ne bak."
            )
        if service == "running":
            return "Microsoft Defender hizmeti çalışıyor; tarama aracı bulunamadı. Windows Güvenliği'nde bileşen durumunu kontrol edin."
        if service not in {"not_found", "unknown"}:
            third_party = _active_third_party_antivirus()
            if third_party:
                return (
                    f"Microsoft Defender hizmeti {service}; çalışan koruma hizmeti {', '.join(third_party)}. "
                    "JARVIS ikinci antivirüs taraması başlatmayacak; o uygulamanın kendi Tarama bölümünü kullan."
                )
            return f"Microsoft Defender hizmet durumu: {service}. JARVIS bunu değiştirmedi; Windows Güvenliği'nde etkin antivirüsü kontrol edin."
        if scanner:
            return "Defender tarama aracı mevcut; hizmet/gerçek zamanlı koruma durumunu doğrulayamadım. Windows Güvenliği'ni kontrol edin."
        return "Microsoft Defender hizmeti veya tarama aracı doğrulanamadı. Başka bir antivirüs etkin olabilir; Windows Güvenliği'ni kontrol edin."

    if action == "history":
        return _history_summary()

    if action == "update_signatures":
        if _defender_service_status() != "running":
            third_party = _active_third_party_antivirus()
            if third_party:
                return f"{', '.join(third_party)} koruması çalışıyor, Microsoft Defender hizmeti etkin değil. Çakışmayı önlemek için Defender güncellemesi başlatmadım."
            return "Microsoft Defender hizmeti çalışmıyor; güvenlik zekâsı güncellemesini başlatmadım. Windows Güvenliği'nde etkin antivirüsü kontrol edin."
        exe = _defender_scan_exe()
        if not exe:
            return "Microsoft Defender güncelleme aracı bulunamadı. Windows Güvenliği'ni açıp güncellemeleri denetleyin."
        log_path = data_path("logs", "defender", f"signature-update-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
                proc = subprocess.Popen(
                    [exe, "-SignatureUpdate"],
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        except Exception as exc:
            return f"Defender güncellemesi başlatılamadı ({type(exc).__name__})."
        if proc.poll() is not None and proc.returncode != 0:
            return f"Defender güncelleme işlemi hata koduyla bitti ({proc.returncode}); Windows Güvenliği'ni kontrol edin."
        return "Defender güvenlik zekâsı güncellemesi arka planda başlatıldı; bu sırada JARVIS kullanılabilir."

    if action == "scan_status":
        return _scan_status_text(include_history=True)

    if action not in {"quick_scan", "full_scan"}:
        return "Bu Defender işlemi tanınmıyor. status, history, update_signatures, quick_scan, full_scan veya scan_status kullanın."

    if _defender_service_status() != "running":
        third_party = _active_third_party_antivirus()
        if third_party:
            return (
                f"Microsoft Defender hizmeti çalışmıyor; {', '.join(third_party)} koruması çalışıyor görünüyor. "
                "Çakışan tarama başlatmadım. Virüs taramasını bu antivirüsün kendi uygulamasından başlatmalısın."
            )
        return "Microsoft Defender hizmeti çalışmıyor; tarama başlatmadım. Windows Güvenliği'nde etkin antivirüsü kontrol edin."

    exe = _defender_scan_exe()
    if not exe:
        return "Microsoft Defender tarama aracı bulunamadı. Windows Güvenliği'ni açıp oradan tarama başlatın."

    with _LOCK:
        old = _load_scan_state()
        if old.get("status") == "running" and _process_matches_scan(
            int(old.get("pid", 0) or 0), str(old.get("exe", "")),
            float(old.get("started_epoch", 0) or 0),
        ):
            return "Bir Defender taraması zaten sürüyor. Yeni tarama başlatmadım; sonucu 'tarama durumu' diyerek sorabilirsin."

        scan_type = 1 if action == "quick_scan" else 2
        label = "hızlı" if scan_type == 1 else "tam"
        started = datetime.now().astimezone()
        log_path = data_path("logs", "defender", f"scan-{started.strftime('%Y%m%d-%H%M%S')}.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
                proc = subprocess.Popen(
                    [exe, "-Scan", "-ScanType", str(scan_type)],
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        except Exception as exc:
            return f"Defender taraması başlatılamadı ({type(exc).__name__}). Yönetici izni gerekebilir."

        _RUNNING_PROCESSES[proc.pid] = proc
        state = {
            "pid": proc.pid,
            "scan_type": scan_type,
            "exe": exe,
            "started_at": started.isoformat(timespec="seconds"),
            "started_epoch": time.time(),
            "status": "running",
            "log_path": str(log_path),
        }
        _save_scan_state(state)

    if proc.poll() is not None:
        code = proc.returncode
        state["status"] = "ended"
        state["exit_code"] = code
        _save_scan_state(state)
        if code == 0:
            return f"Defender {label} tarama komutu tamamlandı (kod 0); algılama/karantina geçmişini kontrol ediyorum."
        return f"Defender {label} tarama komutu hemen hata verdi (kod {code}). Yönetici izni gerekebilir; Windows Güvenliği'ni kontrol edin."

    return (
        f"Microsoft Defender {label} taraması başlatıldı ve arka planda sürüyor. "
        "Defender bulduğu tehditleri Windows Güvenliği'ndeki politikasına göre karantinaya alır veya giderir; "
        "JARVIS dosyaları kendi başına silmez. Sonucu 'virüs taraması durumu' diyerek sor."
    )
