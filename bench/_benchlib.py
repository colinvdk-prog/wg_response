"""bench._benchlib — shared helpers for the wgresponse benchmark harness.

Read-only with respect to product code (wgresponse/*). Deterministic seeds
throughout; wall-clock is used ONLY to measure, never to seed.

Run any bench from the repo root with `py -3 bench/bench_<name>.py`.
"""
from __future__ import annotations

import math
import platform
import sys
import time
from pathlib import Path

# Make the repo root importable when invoked as `py -3 bench/bench_x.py`.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def env_banner() -> str:
    import multiprocessing
    return (f"python {sys.version.split()[0]} | {platform.system()} "
            f"{platform.release()} | {platform.machine()} | "
            f"{multiprocessing.cpu_count()} logical CPUs")


def peak_rss_bytes() -> "tuple[int, int]":
    """(peak_working_set_bytes, current_working_set_bytes) for THIS process.

    Windows: psapi GetProcessMemoryInfo. POSIX fallback: resource.ru_maxrss.
    """
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes as wt

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wt.DWORD),
                ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.GetCurrentProcess()
        fn = getattr(kernel32, "K32GetProcessMemoryInfo", None)
        if fn is None:
            fn = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), wt.DWORD]
        fn.restype = wt.BOOL
        if not fn(handle, ctypes.byref(pmc), pmc.cb):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        return int(pmc.PeakWorkingSetSize), int(pmc.WorkingSetSize)
    import resource  # noqa: 401 (POSIX only)
    kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return kb * 1024, kb * 1024


def timeit(fn, *args, **kwargs):
    """(result, elapsed_seconds) via perf_counter."""
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    return out, time.perf_counter() - t0


def fmt_s(x: float) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    if x >= 100:
        return f"{x:.0f}"
    if x >= 10:
        return f"{x:.1f}"
    return f"{x:.2f}"


class table:
    """Minimal fixed-width table printer."""

    def __init__(self, headers, widths=None):
        self.headers = headers
        self.widths = widths or [max(12, len(h) + 2) for h in headers]

    def _row(self, cells):
        return " | ".join(str(c).ljust(w) for c, w in zip(cells, self.widths))

    def print_header(self):
        print(self._row(self.headers))
        print("-+-".join("-" * w for w in self.widths))

    def print_row(self, cells):
        print(self._row(cells))
