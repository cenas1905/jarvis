"""Independent Windows reminder worker: survives closing JARVIS, no extra voice."""
from __future__ import annotations
import ctypes
import datetime as dt
import os
from pathlib import Path
import subprocess
import sys
import time
from app_paths import data_path

_MUTEX = "Local\\JARVIS.Daily.Reminders.v1"


def ensure_worker():
    if os.name != "nt":
        return False
    kernel = ctypes.windll.kernel32
    kernel.OpenMutexW.restype = ctypes.c_void_p
    handle = kernel.OpenMutexW(0x100000, False, _MUTEX)
    if handle:
        kernel.CloseHandle(ctypes.c_void_p(handle))
        return True
    if getattr(sys, "frozen", False):
        return False
    executable = Path(sys.executable).with_name("pythonw.exe")
    try:
        subprocess.Popen([str(executable if executable.exists() else sys.executable), "-m", "actions.reminder_service"],
                         cwd=Path(__file__).resolve().parent.parent,
                         creationflags=subprocess.CREATE_NO_WINDOW,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(20):
            time.sleep(0.1)
            handle = kernel.OpenMutexW(0x100000, False, _MUTEX)
            if handle:
                kernel.CloseHandle(ctypes.c_void_p(handle))
                return True
    except OSError:
        pass
    return False


def build_notification(root, row, on_close=lambda: None, visible=True):
    import tkinter as tk
    from actions.daily_life import acknowledge, change_task
    window = tk.Toplevel(root)
    window.withdraw()
    window.title("JARVIS — Hatırlatıcı")
    window.configure(bg="#101820")
    window.attributes("-topmost", True)
    window.geometry("440x240")
    tk.Label(window, text="JARVIS • HATIRLATICI", bg="#101820", fg="#4ee6c0", font=("Segoe UI", 14, "bold")).pack(pady=14)
    tk.Label(window, text=row["title"], bg="#101820", fg="white", wraplength=405, font=("Segoe UI", 13)).pack(padx=12)
    tk.Label(window, text=dt.datetime.fromtimestamp(row["due"]).strftime("%d.%m.%Y %H:%M"), bg="#101820", fg="#aabbcc").pack(pady=8)
    buttons = tk.Frame(window, bg="#101820")
    buttons.pack(pady=8)
    def close(action=None):
        if not window.winfo_exists():
            return
        if action:
            change_task(action, task_id=row["id"], due="10 dakika sonra" if action == "snooze" else "")
        window.destroy()
        on_close()
    tk.Button(buttons, text="Tamamlandı", command=lambda: close("done")).pack(side="left", padx=5)
    tk.Button(buttons, text="10 dk ertele", command=lambda: close("snooze")).pack(side="left", padx=5)
    tk.Button(buttons, text="Kapat", command=close).pack(side="left", padx=5)
    window.protocol("WM_DELETE_WINDOW", close)
    if visible:
        window.deiconify()
        window.update_idletasks()
        acknowledge(row["id"])
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    window.after(60000, close)
    return window


def run():
    import tkinter as tk
    from actions.daily_life import claim_due, import_legacy_reminders
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, _MUTEX)
    if not handle or kernel.GetLastError() == 183:
        if handle:
            kernel.CloseHandle(ctypes.c_void_p(handle))
        return
    root = tk.Tk()
    root.withdraw()
    pending = []
    def log_error(exc):
        # Logging must not stop subsequent reminder polling.
        try:
            with data_path("logs", "reminder-service.log").open("a", encoding="utf-8") as stream:
                stream.write(f"{dt.datetime.now().isoformat()} {type(exc).__name__}\n")
        except OSError:
            pass

    def tick():
        try:
            if not pending:
                row = claim_due()
                if row:
                    pending.append(build_notification(root, row, pending.clear))
        except Exception as exc:
            # Do not log titles or other personal data.
            log_error(exc)
        root.after(3000, tick)
    try:
        try:
            import_legacy_reminders()
        except Exception as exc:
            # Keep new reminders working even if an old export is corrupt.
            # Migration is transactional; the original file is left untouched.
            log_error(exc)
        root.after(100, tick)
        root.mainloop()
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


if __name__ == "__main__":
    run()
