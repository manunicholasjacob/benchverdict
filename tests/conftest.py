"""Synthetic sysfs and procfs trees.

benchverdict reads Linux sysfs and procfs. The development machine for this
release is Windows, and CI runs on Windows too, so every behavioural test
builds a fake tree in a temporary directory and points a
:class:`benchverdict.sources.Probe` at it with ``root=`` and
``assume_linux=True``. That makes the Linux code paths testable on any host and
keeps the suite deterministic: a real machine's throttle counters are not
something a test can control.

The trees here are hand-written from the kernel documentation for each file's
format. They are fixtures, not recordings of a real machine, and nothing in the
test suite is presented as a measurement.
"""

from __future__ import annotations

import os
from typing import Dict, Iterable, Optional

import pytest

pytest_plugins = ["pytester"]


def _proc_self_stat(majflt: int) -> str:
    """A /proc/self/stat line with majflt in field 12.

    Field 2 (comm) is deliberately given a name containing a space and a
    parenthesis, because that is the case that breaks a naive str.split().
    """
    tail = ["R", "1", "1", "1", "0", "-1", "4194304", "5000", "0", str(majflt), "0"]
    return "4242 (py (thread) 1) " + " ".join(tail) + " " + " ".join(["0"] * 30) + "\n"


#: A machine in the state a benchmark wants: no quota, performance governor,
#: clocks at maximum, cool, nothing else runnable, plenty of memory free.
HEALTHY: Dict[str, str] = {
    "proc/self/cgroup": "0::/\n",
    "proc/self/stat": _proc_self_stat(0),
    "proc/self/status": "Name:\tpython3\nCpus_allowed_list:\t0-3\n",
    "proc/vmstat": "nr_free_pages 100000\npgfault 123456\npgmajfault 100\n",
    "proc/loadavg": "0.00 0.01 0.05 1/180 4242\n",
    "proc/meminfo": "MemTotal:        8000000 kB\nMemFree:         4000000 kB\nMemAvailable:    6000000 kB\n",
    "sys/fs/cgroup/cgroup.controllers": "cpuset cpu io memory pids\n",
    "sys/fs/cgroup/cpu.stat": (
        "usage_usec 1000000\nuser_usec 800000\nsystem_usec 200000\n"
        "nr_periods 0\nnr_throttled 0\nthrottled_usec 0\n"
    ),
    "sys/fs/cgroup/cpu.max": "max 100000\n",
    "sys/fs/cgroup/cpuset.cpus.effective": "0-3\n",
    "sys/class/thermal/thermal_zone0/temp": "45000\n",
    "sys/class/thermal/thermal_zone0/type": "cpu-thermal\n",
}

for _cpu in range(4):
    HEALTHY["sys/devices/system/cpu/cpu%d/cpufreq/scaling_governor" % _cpu] = "performance\n"
    HEALTHY["sys/devices/system/cpu/cpu%d/cpufreq/scaling_cur_freq" % _cpu] = "2400000\n"
    HEALTHY["sys/devices/system/cpu/cpu%d/cpufreq/cpuinfo_max_freq" % _cpu] = "2400000\n"
    HEALTHY["sys/devices/system/cpu/cpu%d/thermal_throttle/core_throttle_count" % _cpu] = "0\n"
    HEALTHY["sys/devices/system/cpu/cpu%d/thermal_throttle/package_throttle_count" % _cpu] = "0\n"


class Tree:
    """A mutable synthetic filesystem root."""

    def __init__(self, base: str, files: Optional[Dict[str, str]] = None) -> None:
        self.root = base
        for rel, content in (files or {}).items():
            self.write(rel, content)

    def write(self, rel: str, content: str) -> None:
        path = os.path.join(self.root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="ascii", newline="\n") as fh:
            fh.write(content)

    def remove(self, rel: str) -> None:
        path = os.path.join(self.root, rel.replace("/", os.sep))
        if os.path.exists(path):
            os.remove(path)

    def remove_many(self, rels: Iterable[str]) -> None:
        for rel in rels:
            self.remove(rel)

    # -- convenience mutators used by the delta tests ---------------------
    def set_cgroup_v2(
        self,
        nr_periods: int = 0,
        nr_throttled: int = 0,
        throttled_usec: int = 0,
        cpu_max: str = "max 100000",
    ) -> None:
        self.write(
            "sys/fs/cgroup/cpu.stat",
            "usage_usec 1000000\nnr_periods %d\nnr_throttled %d\nthrottled_usec %d\n"
            % (nr_periods, nr_throttled, throttled_usec),
        )
        self.write("sys/fs/cgroup/cpu.max", cpu_max + "\n")

    def set_cgroup_v1(
        self,
        throttled_time_ns: int = 0,
        nr_periods: int = 0,
        nr_throttled: int = 0,
        quota_us: int = -1,
        period_us: int = 100000,
    ) -> None:
        self.remove("sys/fs/cgroup/cpu.stat")
        self.remove("sys/fs/cgroup/cpu.max")
        self.write("proc/self/cgroup", "4:cpu,cpuacct:/mygroup\n")
        self.write(
            "sys/fs/cgroup/cpu/mygroup/cpu.stat",
            "nr_periods %d\nnr_throttled %d\nthrottled_time %d\n"
            % (nr_periods, nr_throttled, throttled_time_ns),
        )
        self.write("sys/fs/cgroup/cpu/mygroup/cpu.cfs_quota_us", "%d\n" % quota_us)
        self.write("sys/fs/cgroup/cpu/mygroup/cpu.cfs_period_us", "%d\n" % period_us)

    def set_majflt(self, value: int) -> None:
        self.write("proc/self/stat", _proc_self_stat(value))

    def set_pgmajfault(self, value: int) -> None:
        self.write("proc/vmstat", "pgfault 123456\npgmajfault %d\n" % value)

    def set_thermal_throttle(self, core: int, package: int = 0) -> None:
        for cpu in range(4):
            self.write(
                "sys/devices/system/cpu/cpu%d/thermal_throttle/core_throttle_count" % cpu,
                "%d\n" % core,
            )
            self.write(
                "sys/devices/system/cpu/cpu%d/thermal_throttle/package_throttle_count" % cpu,
                "%d\n" % package,
            )

    def set_temp(self, celsius: float) -> None:
        self.write("sys/class/thermal/thermal_zone0/temp", "%d\n" % int(celsius * 1000))

    def set_governor(self, governor: str) -> None:
        for cpu in range(4):
            self.write(
                "sys/devices/system/cpu/cpu%d/cpufreq/scaling_governor" % cpu, governor + "\n"
            )

    def set_cur_freq(self, khz: int) -> None:
        for cpu in range(4):
            self.write(
                "sys/devices/system/cpu/cpu%d/cpufreq/scaling_cur_freq" % cpu, "%d\n" % khz
            )

    def set_loadavg(self, runnable: int, total: int = 180, load1: float = 0.0) -> None:
        self.write("proc/loadavg", "%.2f 0.01 0.05 %d/%d 4242\n" % (load1, runnable, total))

    def set_mem_available(self, kb: int) -> None:
        self.write(
            "proc/meminfo",
            "MemTotal:        8000000 kB\nMemFree:         1000 kB\nMemAvailable:    %d kB\n" % kb,
        )

    def set_affinity(self, cpu_list: str) -> None:
        self.write("proc/self/status", "Name:\tpython3\nCpus_allowed_list:\t%s\n" % cpu_list)


@pytest.fixture()
def tree(tmp_path) -> Tree:
    """A healthy synthetic Linux tree, ready to be made unhealthy."""
    return Tree(str(tmp_path / "root"), HEALTHY)


@pytest.fixture()
def empty_tree(tmp_path) -> Tree:
    """A root with nothing in it: every source unreadable."""
    base = str(tmp_path / "bare")
    os.makedirs(base, exist_ok=True)
    return Tree(base, {})


def probe_for(tree_obj: Tree, vcgencmd=None):
    """A Probe pinned to a synthetic tree, with the Linux paths forced on."""
    from benchverdict.sources import Probe

    return Probe(root=tree_obj.root, assume_linux=True, vcgencmd=vcgencmd, page_size=4096)


def fake_vcgencmd(value: int):
    """A stand-in for ``vcgencmd get_throttled`` returning a fixed bitmask."""

    def run():
        return "throttled=%s" % hex(value)

    return run
