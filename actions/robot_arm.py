"""ESP32 robot kol ile yalnizca sinirli, acik seri komutlar uzerinden iletisim.

Motorlari burada dogrudan surmuyoruz: sinirlar ESP32 yaziliminda da uygulanir.
Baglanti acilinca cogu ESP32 resetlenir; yuk tasiyan kol fiziksel olarak
desteklenmeden baglanti acilmamali/kapatilmamali.
"""

from __future__ import annotations

import threading
import time

from app_config import get_app_config_value


class RobotArmBridge:
    def __init__(self):
        self._serial = None
        self._lock = threading.RLock()
        self._selected = None

    def _find_port(self):
        configured = str(get_app_config_value("robot_arm_port", "") or "").strip()
        if configured:
            return configured

        from serial.tools import list_ports

        # Birden fazla uygun aygit varsa tahmin etme: yanlis karta motor
        # komutu gondermektense kullanicidan kesin COM secimi istenir.
        supported_ids = {
            (0x10C4, 0xEA60),  # CP210x
            (0x1A86, 0x7523),  # CH340
            (0x1A86, 0x55D4),  # CH9102
            (0x0403, 0x6001),  # FTDI
        }
        ports = [p.device for p in list_ports.comports()
                 if (p.vid, p.pid) in supported_ids]
        if len(ports) == 1:
            return ports[0]
        if ports:
            raise RuntimeError("Birden fazla USB seri kart var; robot_arm_port ayarinda COM secin.")
        raise RuntimeError("ESP32 seri baglantisi bulunamadi. USB kablosunu ve COM portunu kontrol edin.")

    def _read_until(self, prefix: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = self._serial.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if line.startswith("ERR "):
                raise RuntimeError(line)
            if line.startswith(prefix):
                return line
        raise TimeoutError(f"ESP32 '{prefix}' yaniti vermedi. Dogru yazilim yuklu mu?")

    def _connect(self):
        if self._serial is not None and self._serial.is_open:
            return
        import serial

        port = self._find_port()
        connection = serial.Serial(port, 115200, timeout=0.15, write_timeout=0.5)
        self._serial = connection
        self._selected = None
        try:
            # Port acilinca ESP32 genellikle resetlenir. READY, yalnizca
            # bizim guvenli robot_arm.ino yazilimimizdan gelir.
            try:
                self._read_until("READY JARVIS robot arm test", 2.5)
            except TimeoutError:
                # Bazi kartlar port acilinca resetlenmez; READY kacirildiysa
                # hareket ettirmeyen durum sorgusuyla yazilimi dogrula.
                status = self._send("status", "STATUS selected=", 1.0)
                if " step_remaining=" not in status:
                    raise RuntimeError("beklenmeyen robot kol yazilimi")
        except Exception:
            self.close()
            raise RuntimeError(
                f"{port} acildi ama robot_arm.ino READY yaniti gelmedi. "
                "Kodu ESP32'ye yukleyin ve Arduino Serial Monitor'u kapatin."
            )

    def close(self):
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None
        self._selected = None

    def _send(self, command: str, expected: str, timeout: float = 1.0) -> str:
        self._serial.write((command + "\n").encode("ascii"))
        return self._read_until(expected, timeout)

    def run(self, action: str, servo_index=None, value=None) -> str:
        action = str(action or "").strip().lower()
        if action not in {"status", "select", "jog", "step", "stop", "release"}:
            return "Robot kol komutu gecersiz. status, select, jog, step, stop veya release kullanin."

        # Hatalı komutla portu açıp ESP32'yi sıfırlamamak için önce doğrula.
        index = None
        if action in {"select", "jog"}:
            try:
                index = int(servo_index)
            except (TypeError, ValueError):
                return "Servo numarasi 1 ile 5 arasinda olmali."
            if index not in range(1, 6):
                return "Servo numarasi 1 ile 5 arasinda olmali."
        amount = None
        if action in {"jog", "step"}:
            try:
                amount = int(value)
            except (TypeError, ValueError):
                return "Hareket miktari sayi olmali."
            maximum = 10 if action == "jog" else 32
            if not 1 <= abs(amount) <= maximum:
                return f"{action} miktari -{maximum}..-1 veya 1..{maximum} olmali."

        with self._lock:
            try:
                self._connect()
                if action == "status":
                    return self._send("status", "STATUS ")
                if action == "stop":
                    return self._send("stop", "OK stop")
                if action == "release":
                    self._selected = None
                    return self._send("!", "OK emergency release")

                if action in {"select", "jog"}:
                    if action == "select" or self._selected != index:
                        selected = self._send(f"servo {index}", f"OK servo {index} ")
                        self._selected = index
                        if action == "select":
                            return selected
                    result = self._send(f"jog {amount}", "OK ")
                    if result.startswith("OK jog started"):
                        return self._read_until(f"DONE servo {index} ", 2.5)
                    return result

                self._send(f"step {amount}", "OK step started")
                return self._read_until("DONE step; coils=off", 1.5)
            except Exception as exc:
                self.close()
                return f"Robot kol baglantisi/komutu basarisiz: {exc}"


_BRIDGE = RobotArmBridge()


def robot_arm(action: str, servo_index=None, value=None) -> str:
    return _BRIDGE.run(action, servo_index, value)
