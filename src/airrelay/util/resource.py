"""Raspberry Pi resource monitoring (CPU/RAM/temp/storage)."""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any


def _read_proc_stat() -> list[int]:
    with open("/proc/stat") as f:
        line = f.readline()
    parts = line.split()[1:8]
    return [int(x) for x in parts]


class ResourceMonitor:
    def __init__(self) -> None:
        self._prev: list[int] | None = None
        self._prev_t = 0.0

    def _cpu_pct(self) -> float:
        try:
            cur = _read_proc_stat()
            t = time.time()
            if self._prev is None:
                self._prev, self._prev_t = cur, t
                return 0.0
            d_total = sum(cur) - sum(self._prev)
            d_idle = (cur[3] + cur[4]) - (self._prev[3] + self._prev[4])
            self._prev, self._prev_t = cur, t
            if d_total <= 0:
                return 0.0
            return max(0.0, min(100.0, 100.0 * (d_total - d_idle) / d_total))
        except Exception:  # noqa: BLE001
            return 0.0

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {"cpu_pct": self._cpu_pct()}
        try:
            with open("/proc/meminfo") as f:
                info = {}
                for line in f:
                    k, _, v = line.partition(":")
                    info[k.strip()] = int(v.split()[0]) * 1024
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", info.get("MemFree", 0))
            out["ram_total"] = total
            out["ram_available"] = avail
            out["ram_pct"] = 100.0 * (1 - avail / total) if total else 0.0
        except Exception:  # noqa: BLE001
            out.update({"ram_total": 0, "ram_available": 0, "ram_pct": 0})
        out["temp_c"] = self._read_temp()
        out["storage"] = self._disk()
        return out

    def _read_temp(self) -> float | None:
        for probe in ("/sys/class/thermal/thermal_zone0/temp",):
            try:
                with open(probe) as f:
                    return int(f.read().strip()) / 1000.0
            except Exception:  # noqa: BLE001
                pass
        try:
            r = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True,
                               text=True, timeout=2)
            if r.returncode == 0:
                return float(r.stdout.replace("temp=", "").replace("'C", ""))
        except Exception:  # noqa: BLE001
            pass
        return None

    def _disk(self) -> dict[str, Any]:
        import shutil
        st = shutil.disk_usage(self._disk_target())
        return {"total": st.total, "used": st.used, "free": st.free,
                "pct": 100.0 * st.used / st.total if st.total else 0.0}

    def _disk_target(self) -> str:
        root = os.environ.get("AIRRELAY_HOME")
        return root if root else "/"
