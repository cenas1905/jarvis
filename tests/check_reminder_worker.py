"""Real Windows worker/singleton smoke test with isolated storage and no alerts."""
import ctypes
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


def main():
    mutex = "Local\\JARVIS.ReminderTest." + uuid.uuid4().hex
    kernel = ctypes.windll.kernel32
    kernel.OpenMutexW.restype = ctypes.c_void_p
    def exists():
        handle = kernel.OpenMutexW(0x100000, False, mutex)
        if handle:
            kernel.CloseHandle(ctypes.c_void_p(handle))
        return bool(handle)

    with tempfile.TemporaryDirectory() as directory:
        code = (
            "from pathlib import Path; import app_paths; "
            f"app_paths._data_dir_cache=Path({directory!r}); "
            "from actions import reminder_service as service; "
            f"service._MUTEX={mutex!r}; service.run()"
        )
        command = [sys.executable, "-c", code]
        child = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1],
                                 creationflags=subprocess.CREATE_NO_WINDOW,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            database = Path(directory) / "memory" / "daily_life.sqlite3"
            while not (exists() and database.exists()) and time.monotonic() < deadline:
                if child.poll() is not None:
                    raise AssertionError("worker exited early")
                time.sleep(0.1)
            assert exists() and database.exists(), "worker did not reach its polling loop"
            second = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                                    creationflags=subprocess.CREATE_NO_WINDOW,
                                    capture_output=True, timeout=5)
            assert second.returncode == 0, "duplicate worker failed"
            assert child.poll() is None, "original worker stopped"
            print("REMINDER_WORKER_STARTED_AND_POLLING")
            print("DUPLICATE_WORKER_EXITED_WITHOUT_DUPLICATE_ALERTS")
        finally:
            child.terminate()
            child.communicate(timeout=5)
    assert not exists(), "test worker mutex not released"
    print("ISOLATED_TEST_CLEANED_UP")


if __name__ == "__main__":
    main()
