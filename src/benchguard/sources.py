"""Readers for the Linux sysfs and procfs sources benchguard samples.

Design rules enforced here:

1. Every reader is rooted at a configurable filesystem root. That is what makes
   the whole library testable on a machine that has no sysfs: tests point the
   root at a temporary directory holding synthetic files.
2. A value that could not be read is ``None``. It is never estimated, inferred
   from a sibling value, or filled with a plausible default. The path that could
   not be read is appended to ``Reader.unreadable`` so a consumer can tell
   "absent" from "clean".
3. Nothing here parses ``/proc/cpuinfo``. Unstructured text parsing is the
   failure mode that stalled cilium/cilium#7086: the reporter wrote that the
   fields are "not structured" and that parsing them risked "breaking the CI".
   Integer sysfs files are used instead wherever one exists.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence

__all__ = [
    "Reader",
    "Probe",
    "is_linux",
    "DEFAULT_PAGE_SIZE",
]

DEFAULT_PAGE_SIZE = 4096

#: Raspberry Pi ``vcgencmd get_throttled`` bit assignments.
#: Live bits are 0 to 3; the sticky "has occurred since boot" bits are 16 to 19.
RPI_THROTTLE_BITS = {
    0: "under-voltage detected",
    1: "arm frequency capped",
    2: "currently throttled",
    3: "soft temperature limit active",
}
RPI_STICKY_BITS = {
    16: "under-voltage has occurred",
    17: "arm frequency capping has occurred",
    18: "throttling has occurred",
    19: "soft temperature limit has occurred",
}


def is_linux() -> bool:
    """True when this interpreter runs on Linux."""
    return sys.platform.startswith("linux")


class Reader:
    """A root-relative, failure-recording filesystem reader."""

    def __init__(self, root: str = "/") -> None:
        self.root = root
        self.unreadable: List[str] = []

    # -- plumbing ---------------------------------------------------------
    def path(self, rel: str) -> str:
        return os.path.join(self.root, rel.lstrip("/").replace("/", os.sep))

    def _record_unreadable(self, rel: str) -> None:
        if rel not in self.unreadable:
            self.unreadable.append(rel)

    def exists(self, rel: str) -> bool:
        return os.path.exists(self.path(rel))

    def text(self, rel: str, required: bool = True) -> Optional[str]:
        """Read a file as stripped text, or return None and record the miss."""
        try:
            with open(self.path(rel), "r", encoding="utf-8", errors="replace") as fh:
                return fh.read().strip()
        except (OSError, ValueError):
            if required:
                self._record_unreadable(rel)
            return None

    def integer(self, rel: str, required: bool = True) -> Optional[int]:
        """Read a file holding a single integer. Never guesses on failure."""
        raw = self.text(rel, required=required)
        if raw is None:
            return None
        try:
            return int(raw.split()[0])
        except (ValueError, IndexError):
            self._record_unreadable(rel)
            return None

    def listdir(self, rel: str) -> List[str]:
        try:
            return sorted(os.listdir(self.path(rel)))
        except OSError:
            return []

    def keyed_ints(self, rel: str) -> Optional[Dict[str, int]]:
        """Parse a ``key value`` per line file such as cpu.stat or vmstat."""
        raw = self.text(rel)
        if raw is None:
            return None
        out: Dict[str, int] = {}
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                try:
                    out[parts[0].rstrip(":")] = int(parts[1])
                except ValueError:
                    continue
        return out

    def cpu_ids(self) -> List[int]:
        """CPU ids present under /sys/devices/system/cpu."""
        ids: List[int] = []
        for name in self.listdir("sys/devices/system/cpu"):
            m = re.fullmatch(r"cpu(\d+)", name)
            if m:
                ids.append(int(m.group(1)))
        return sorted(ids)

    def thermal_zones(self) -> List[str]:
        return [
            name
            for name in self.listdir("sys/class/thermal")
            if re.fullmatch(r"thermal_zone\d+", name)
        ]


def _parse_cpu_list(spec: str) -> List[int]:
    """Expand a sysfs cpu list such as "0-3,8" into explicit ids."""
    out: List[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo, _, hi = chunk.partition("-")
            try:
                out.extend(range(int(lo), int(hi) + 1))
            except ValueError:
                continue
        else:
            try:
                out.append(int(chunk))
            except ValueError:
                continue
    return sorted(set(out))


def _default_vcgencmd() -> Optional[Callable[[], Optional[str]]]:
    """Return a callable running the real vcgencmd, when one is installed."""
    import shutil

    exe = shutil.which("vcgencmd")
    if exe is None:
        return None

    def run() -> Optional[str]:
        try:
            proc = subprocess.run(
                [exe, "get_throttled"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()

    return run


class Probe:
    """Takes point-in-time samples of every machine-state source.

    Parameters
    ----------
    root:
        Filesystem root to read under. Tests pass a synthetic tree.
    assume_linux:
        Force the Linux code paths on. Defaults to real platform detection.
        Set it to True together with ``root`` to exercise the sysfs readers on
        a non-Linux host.
    vcgencmd:
        Zero-argument callable returning the raw text of
        ``vcgencmd get_throttled``, or None. Defaults to the installed
        ``vcgencmd`` when there is one, and to no Raspberry Pi probe otherwise.
    page_size:
        Page size in bytes used to convert a declared working set into pages.
    """

    def __init__(
        self,
        root: str = "/",
        assume_linux: Optional[bool] = None,
        vcgencmd: Optional[Callable[[], Optional[str]]] = None,
        page_size: Optional[int] = None,
    ) -> None:
        self.root = root
        self.supported = is_linux() if assume_linux is None else bool(assume_linux)
        self._vcgencmd = vcgencmd if vcgencmd is not None else (
            _default_vcgencmd() if self.supported and root == "/" else None
        )
        if page_size is not None:
            self.page_size = page_size
        else:
            try:
                self.page_size = int(os.sysconf("SC_PAGE_SIZE"))
            except (AttributeError, ValueError, OSError):
                self.page_size = DEFAULT_PAGE_SIZE

    # -- cgroup -----------------------------------------------------------
    def _cgroup_dirs(self, reader: Reader) -> Dict[str, List[str]]:
        """Candidate cgroup directories, nearest-first, for v2 and v1."""
        v2: List[str] = []
        v1: List[str] = []
        raw = reader.text("proc/self/cgroup", required=False)
        if raw:
            for line in raw.splitlines():
                parts = line.split(":", 2)
                if len(parts) != 3:
                    continue
                hid, controllers, rel = parts
                rel = rel.strip("/")
                if hid == "0" and controllers == "":
                    base = "sys/fs/cgroup"
                    v2 = self._walk_up(base, rel)
                elif "cpu" in controllers.split(","):
                    base = "sys/fs/cgroup/cpu"
                    v1 = self._walk_up(base, rel)
        if not v2:
            v2 = ["sys/fs/cgroup"]
        if not v1:
            v1 = ["sys/fs/cgroup/cpu", "sys/fs/cgroup/cpu,cpuacct"]
        return {"v2": v2, "v1": v1}

    @staticmethod
    def _walk_up(base: str, rel: str) -> List[str]:
        out = []
        parts = [p for p in rel.split("/") if p]
        while parts:
            out.append(base + "/" + "/".join(parts))
            parts.pop()
        out.append(base)
        return out

    def _sample_cgroup(self, reader: Reader) -> Dict[str, Any]:
        dirs = self._cgroup_dirs(reader)
        out: Dict[str, Any] = {
            "version": None,
            "path": None,
            "quota_usec": None,
            "period_usec": None,
            "quota_cpus": None,
            "nr_periods": None,
            "nr_throttled": None,
            "throttled_usec": None,
            "cpuset_effective": None,
        }

        # cgroup v2: cpu.stat holds nr_periods / nr_throttled / throttled_usec,
        # cpu.max holds "<quota> <period>" or "max <period>".
        for d in dirs["v2"]:
            if not reader.exists(d + "/cpu.stat"):
                continue
            stat = reader.keyed_ints(d + "/cpu.stat") or {}
            if "nr_periods" not in stat and "throttled_usec" not in stat:
                continue
            out["version"] = 2
            out["path"] = "/" + d
            out["nr_periods"] = stat.get("nr_periods")
            out["nr_throttled"] = stat.get("nr_throttled")
            out["throttled_usec"] = stat.get("throttled_usec")
            for q in dirs["v2"]:
                raw = reader.text(q + "/cpu.max", required=False)
                if raw:
                    fields = raw.split()
                    period = int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else None
                    out["period_usec"] = period
                    if fields[0] == "max":
                        out["quota_usec"] = None
                        out["quota_cpus"] = None
                    else:
                        try:
                            quota = int(fields[0])
                        except ValueError:
                            quota = None
                        out["quota_usec"] = quota
                        if quota is not None and period:
                            out["quota_cpus"] = round(quota / period, 4)
                    break
            break

        # cgroup v1: cpu.cfs_quota_us / cpu.cfs_period_us, cpu.stat's
        # throttled_time is nanoseconds, converted to usec for one unit.
        if out["version"] is None:
            for d in dirs["v1"]:
                if not reader.exists(d + "/cpu.stat"):
                    continue
                stat = reader.keyed_ints(d + "/cpu.stat") or {}
                if "throttled_time" not in stat and "nr_periods" not in stat:
                    continue
                out["version"] = 1
                out["path"] = "/" + d
                out["nr_periods"] = stat.get("nr_periods")
                out["nr_throttled"] = stat.get("nr_throttled")
                tt = stat.get("throttled_time")
                out["throttled_usec"] = None if tt is None else tt // 1000
                quota = reader.integer(d + "/cpu.cfs_quota_us", required=False)
                period = reader.integer(d + "/cpu.cfs_period_us", required=False)
                out["period_usec"] = period
                # -1 is the kernel's "no quota" sentinel. Do not treat it as a number.
                if quota is not None and quota > 0:
                    out["quota_usec"] = quota
                    if period:
                        out["quota_cpus"] = round(quota / period, 4)
                break

        for rel in ("sys/fs/cgroup/cpuset.cpus.effective", "sys/fs/cgroup/cpuset/cpuset.cpus"):
            raw = reader.text(rel, required=False)
            if raw:
                out["cpuset_effective"] = _parse_cpu_list(raw)
                break
        return out

    # -- cpu frequency ----------------------------------------------------
    def _sample_cpufreq(self, reader: Reader) -> Dict[str, Any]:
        governors: Dict[str, int] = {}
        ratios: List[float] = []
        cur: Dict[int, int] = {}
        unreadable_governor = 0
        for cpu in reader.cpu_ids():
            base = "sys/devices/system/cpu/cpu%d/cpufreq" % cpu
            gov = reader.text(base + "/scaling_governor", required=False)
            if gov:
                governors[gov] = governors.get(gov, 0) + 1
            elif reader.exists(base):
                unreadable_governor += 1
            cur_khz = reader.integer(base + "/scaling_cur_freq", required=False)
            max_khz = reader.integer(base + "/cpuinfo_max_freq", required=False)
            if cur_khz is not None:
                cur[cpu] = cur_khz
            if cur_khz is not None and max_khz:
                ratios.append(cur_khz / max_khz)
        return {
            "governors": governors or None,
            "governor_unreadable_cpus": unreadable_governor or None,
            "cur_khz": cur or None,
            # Reported only when at least one (cur, max) pair was readable.
            "min_freq_ratio": round(min(ratios), 4) if ratios else None,
            "cpu_count": len(reader.cpu_ids()) or None,
        }

    # -- thermal and power cap -------------------------------------------
    def _sample_thermal(self, reader: Reader) -> Dict[str, Any]:
        temps: Dict[str, float] = {}
        for zone in reader.thermal_zones():
            milli = reader.integer("sys/class/thermal/%s/temp" % zone, required=False)
            if milli is None:
                continue
            label = reader.text("sys/class/thermal/%s/type" % zone, required=False) or zone
            temps["%s:%s" % (zone, label)] = round(milli / 1000.0, 3)
        core = 0
        package = 0
        saw_counter = False
        for cpu in reader.cpu_ids():
            base = "sys/devices/system/cpu/cpu%d/thermal_throttle" % cpu
            c = reader.integer(base + "/core_throttle_count", required=False)
            p = reader.integer(base + "/package_throttle_count", required=False)
            if c is not None:
                core += c
                saw_counter = True
            if p is not None:
                package += p
                saw_counter = True
        return {
            "zone_celsius": temps or None,
            "max_celsius": max(temps.values()) if temps else None,
            "core_throttle_count": core if saw_counter else None,
            "package_throttle_count": package if saw_counter else None,
        }

    def _sample_rpi(self) -> Dict[str, Any]:
        if self._vcgencmd is None:
            return {"available": False, "raw": None, "value": None, "live": None, "sticky": None}
        raw = self._vcgencmd()
        if not raw:
            return {"available": False, "raw": None, "value": None, "live": None, "sticky": None}
        m = re.search(r"throttled=(0x[0-9a-fA-F]+|\d+)", raw)
        if not m:
            return {"available": False, "raw": raw, "value": None, "live": None, "sticky": None}
        token = m.group(1)
        try:
            value = int(token, 16) if token.lower().startswith("0x") else int(token)
        except ValueError:
            return {"available": False, "raw": raw, "value": None, "live": None, "sticky": None}
        live = [desc for bit, desc in RPI_THROTTLE_BITS.items() if value & (1 << bit)]
        sticky = [desc for bit, desc in RPI_STICKY_BITS.items() if value & (1 << bit)]
        return {
            "available": True,
            "raw": raw,
            "value": value,
            "hex": hex(value),
            "live": live,
            "sticky": sticky,
        }

    # -- paging -----------------------------------------------------------
    def _sample_faults(self, reader: Reader) -> Dict[str, Any]:
        majflt = None
        raw = reader.text("proc/self/stat", required=False)
        if raw:
            # Field 2 (comm) may contain spaces and parentheses, so split on the
            # last ')' before counting fields. majflt is field 12, 1-based.
            close = raw.rfind(")")
            if close != -1:
                rest = raw[close + 1:].split()
                # rest[0] is field 3 (state), so field 12 is rest[9].
                if len(rest) > 9:
                    try:
                        majflt = int(rest[9])
                    except ValueError:
                        majflt = None
        vmstat = reader.keyed_ints("proc/vmstat") or {}
        return {
            "self_majflt": majflt,
            "vmstat_pgmajfault": vmstat.get("pgmajfault"),
        }

    # -- competing load ---------------------------------------------------
    def _sample_load(self, reader: Reader) -> Dict[str, Any]:
        load1 = load5 = load15 = None
        runnable = total_procs = None
        raw = reader.text("proc/loadavg", required=False)
        if raw:
            parts = raw.split()
            try:
                load1, load5, load15 = (float(parts[0]), float(parts[1]), float(parts[2]))
            except (ValueError, IndexError):
                load1 = load5 = load15 = None
            if len(parts) > 3 and "/" in parts[3]:
                a, _, b = parts[3].partition("/")
                try:
                    runnable, total_procs = int(a), int(b)
                except ValueError:
                    runnable = total_procs = None
        meminfo = reader.text("proc/meminfo", required=False)
        mem_available = None
        mem_total = None
        if meminfo:
            for line in meminfo.splitlines():
                if line.startswith("MemAvailable:"):
                    fields = line.split()
                    if len(fields) > 1 and fields[1].isdigit():
                        mem_available = int(fields[1]) * 1024
                elif line.startswith("MemTotal:"):
                    fields = line.split()
                    if len(fields) > 1 and fields[1].isdigit():
                        mem_total = int(fields[1]) * 1024
        return {
            "loadavg_1m": load1,
            "loadavg_5m": load5,
            "loadavg_15m": load15,
            "runnable": runnable,
            # runnable counts this process too, so other_runnable is one less.
            "other_runnable": None if runnable is None else max(runnable - 1, 0),
            "total_processes": total_procs,
            "mem_available_bytes": mem_available,
            "mem_total_bytes": mem_total,
        }

    # -- affinity ---------------------------------------------------------
    def _sample_affinity(self, reader: Reader) -> Dict[str, Any]:
        cpus: Optional[Sequence[int]] = None
        getaffinity = getattr(os, "sched_getaffinity", None)
        if getaffinity is not None and self.root == "/":
            try:
                cpus = sorted(getaffinity(0))
            except OSError:
                cpus = None
        if cpus is None:
            raw = reader.text("proc/self/status", required=False)
            if raw:
                for line in raw.splitlines():
                    if line.startswith("Cpus_allowed_list:"):
                        cpus = _parse_cpu_list(line.split(":", 1)[1])
                        break
        return {
            "affinity_cpus": list(cpus) if cpus is not None else None,
            "affinity_count": len(cpus) if cpus is not None else None,
        }

    # -- public -----------------------------------------------------------
    def sample(self) -> Dict[str, Any]:
        """Take one point-in-time sample of every source."""
        import time

        reader = Reader(self.root)
        if not self.supported:
            return {
                "supported": False,
                "monotonic": time.monotonic(),
                "unreadable": ["(all sources: benchguard reads Linux sysfs and procfs)"],
            }
        sample: Dict[str, Any] = {
            "supported": True,
            "monotonic": time.monotonic(),
            "page_size": self.page_size,
            "cgroup": self._sample_cgroup(reader),
            "cpufreq": self._sample_cpufreq(reader),
            "thermal": self._sample_thermal(reader),
            "rpi": self._sample_rpi(),
            "faults": self._sample_faults(reader),
            "load": self._sample_load(reader),
            "affinity": self._sample_affinity(reader),
        }
        missing = list(reader.unreadable)
        # Call out the headline sources explicitly when they are absent, so a
        # consumer never reads silence as a clean machine.
        for rel, label in (
            ("proc/self/stat", "proc/self/stat (majflt)"),
            ("proc/loadavg", "proc/loadavg"),
        ):
            if not reader.exists(rel) and label not in missing:
                missing.append(label)
        if sample["cgroup"]["version"] is None:
            missing.append("cgroup cpu.stat (no cgroup v2 or v1 cpu controller found)")
        if sample["thermal"]["core_throttle_count"] is None:
            missing.append("cpu*/thermal_throttle/* (no x86 throttle counters)")
        if not sample["rpi"]["available"]:
            missing.append("vcgencmd get_throttled (not a Raspberry Pi, or vcgencmd absent)")
        sample["unreadable"] = missing
        return sample
