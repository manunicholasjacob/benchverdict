"""Verdict assembly: turn a pair of samples into a verdict plus reason codes.

The reason code vocabulary is a stable, documented API surface. Codes are
SCREAMING_SNAKE, never renamed within a major version, and each carries one of
three severities:

``invalidating``
    A fact that makes the measurement unfit to compare. Drives verdict INVALID.
``warning``
    A condition that makes the number less trustworthy or less comparable but
    that is not on its own proof of contamination.
``info``
    A recorded observation with no judgement attached.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = [
    "Verdict",
    "Severity",
    "Reason",
    "Thresholds",
    "REASON_CODES",
    "evaluate",
    "evaluate_point_in_time",
]


class Verdict:
    """The four verdicts benchverdict can return."""

    PASS = "PASS"
    INVALID = "INVALID"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"

    ALL = (PASS, INVALID, UNSUPPORTED, UNKNOWN)


class Severity:
    INVALIDATING = "invalidating"
    WARNING = "warning"
    INFO = "info"

    ALL = (INVALIDATING, WARNING, INFO)


#: The complete reason vocabulary, code -> (default severity, what it means).
#: This table is the contract. The README renders it verbatim.
REASON_CODES: Dict[str, Any] = {
    "CGROUP_THROTTLED": (
        Severity.INVALIDATING,
        "cgroup CPU throttled time rose during the measurement.",
    ),
    "THERMAL_THROTTLED": (
        Severity.INVALIDATING,
        "An x86 core or package thermal throttle counter rose during the measurement.",
    ),
    "RPI_THROTTLE_ACTIVE": (
        Severity.INVALIDATING,
        "Raspberry Pi reported a live throttle, frequency cap, under-voltage or soft "
        "temperature limit during the measurement.",
    ),
    "MAJOR_FAULTS_EXCEEDED": (
        Severity.INVALIDATING,
        "Major page faults during the measurement exceeded the caller's max_major_faults.",
    ),
    "AFFINITY_CHANGED": (
        Severity.INVALIDATING,
        "The set of CPUs this process may run on changed during the measurement.",
    ),
    "CPU_COUNT_CHANGED": (
        Severity.INVALIDATING,
        "The number of visible CPUs changed during the measurement.",
    ),
    "CGROUP_QUOTA_SET": (
        Severity.WARNING,
        "A cgroup CPU bandwidth quota is in force, so this number is not comparable "
        "with one taken without a quota.",
    ),
    "GOVERNOR_NOT_PERFORMANCE": (
        Severity.WARNING,
        "At least one CPU is not using the performance frequency governor.",
    ),
    "FREQ_BELOW_MAX": (
        Severity.WARNING,
        "At least one CPU reported a current frequency below the configured fraction "
        "of its maximum.",
    ),
    "RPI_THROTTLE_STICKY": (
        Severity.WARNING,
        "Raspberry Pi sticky flags show throttling, capping or under-voltage has "
        "occurred since boot, though not necessarily during this measurement.",
    ),
    "THERMAL_RISE": (
        Severity.WARNING,
        "The hottest thermal zone rose by more than the allowed amount during the "
        "measurement.",
    ),
    "COMPETING_LOAD": (
        Severity.WARNING,
        "Other runnable processes were present, so the measurement shared the machine.",
    ),
    "LOW_MEMORY_HEADROOM": (
        Severity.WARNING,
        "MemAvailable was below the declared working set plus headroom, so the "
        "working set may not have stayed resident.",
    ),
    "CPUSET_RESTRICTED": (
        Severity.WARNING,
        "The effective cpuset is narrower than the set of CPUs on the machine.",
    ),
    "MAJOR_FAULT_RATIO_HIGH": (
        Severity.WARNING,
        "Major page faults during the measurement were a high fraction of the declared "
        "working set, so part of the work may have been served from storage.",
    ),
    "SHORT_DURATION": (
        Severity.WARNING,
        "The measured interval was shorter than min_duration, so throttle ratios and "
        "fault counts are not stable enough to support a confident verdict.",
    ),
    "SOURCES_UNREADABLE": (
        Severity.WARNING,
        "One or more state sources could not be read, so absence of a problem is not "
        "evidence of its absence.",
    ),
    "MAJOR_FAULTS_OBSERVED": (
        Severity.INFO,
        "Major page faults occurred during the measurement. Reported without "
        "judgement because no max_major_faults was declared.",
    ),
    "NOT_LINUX": (
        Severity.INFO,
        "benchverdict reads Linux sysfs and procfs. On any other platform it reports "
        "UNSUPPORTED rather than guessing.",
    ),
    "NO_SOURCES": (
        Severity.INFO,
        "No machine-state source could be read at all, so no verdict is possible.",
    ),
}


@dataclass(frozen=True)
class Reason:
    """One machine-readable code plus one human sentence."""

    code: str
    severity: str
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {"code": self.code, "severity": self.severity, "detail": self.detail}

    def __str__(self) -> str:
        return "[%s] %s: %s" % (self.severity, self.code, self.detail)


@dataclass
class Thresholds:
    """Every tunable, with its default and the reason the default is what it is."""

    #: Minimum measured wall time. Below this, benchverdict returns UNKNOWN rather
    #: than a confident PASS. nucliweb/webperf-snippets#312 (merged 24 Oct 2026)
    #: is the worked example: a few milliseconds of work produced CPU-throttle
    #: ratios of 1.53 and 0.92 on two refs whose code was identical, against a
    #: gate of 2.0, and raising the workload to about 20 ms settled the ratio
    #: into 3.74 to 4.02.
    min_duration: float = 1.0

    #: Declared resident working set in bytes. Enables the memory-headroom and
    #: major-fault-ratio checks. None disables both.
    working_set_bytes: Optional[int] = None

    #: Hard ceiling on major page faults during the measurement. None means the
    #: fault delta is reported as info and never invalidates, because there is
    #: no defensible universal threshold.
    max_major_faults: Optional[int] = None

    #: Major faults as a fraction of the declared working set in pages, above
    #: which a warning fires. 0.1 is a documented choice, not a measurement.
    #: On the author's Raspberry Pi 5 run a major-fault delta of 11,404 against
    #: a 272,000-page model is 4.2 percent, which is how that run was shown to
    #: be DRAM-resident rather than assumed to be.
    max_major_fault_ratio: float = 0.10

    #: scaling_cur_freq / cpuinfo_max_freq below this warns. cilium/cilium#7086
    #: is why this is checked separately from the governor: the reporter's
    #: machine showed every governor at "performance" while the clock sat at
    #: 800 MHz on a 3.70 GHz part.
    min_freq_ratio: float = 0.90

    #: Other runnable processes tolerated before COMPETING_LOAD fires.
    max_other_runnable: int = 0

    #: Allowed rise in the hottest thermal zone, in degrees Celsius.
    max_thermal_rise_c: float = 10.0

    #: Memory headroom multiplier applied to working_set_bytes.
    memory_headroom: float = 1.10

    #: Promote COMPETING_LOAD from warning to invalidating. Off by default
    #: because shared CI runners always have something runnable.
    competing_load_invalidates: bool = False

    #: Promote CGROUP_QUOTA_SET from warning to invalidating. Off by default
    #: because many users deliberately benchmark inside a quota.
    quota_invalidates: bool = False

    extra: Dict[str, Any] = field(default_factory=dict)


def _reason(code: str, detail: str, severity: Optional[str] = None) -> Reason:
    default_severity, _doc = REASON_CODES[code]
    return Reason(code=code, severity=severity or default_severity, detail=detail)


def _delta(before: Optional[int], after: Optional[int]) -> Optional[int]:
    """Delta of two counters, or None when either end was unreadable."""
    if before is None or after is None:
        return None
    return after - before


_SEVERITY_ORDER = {Severity.INVALIDATING: 0, Severity.WARNING: 1, Severity.INFO: 2}


def _sorted(reasons: List[Reason]) -> List[Reason]:
    return sorted(reasons, key=lambda r: (_SEVERITY_ORDER.get(r.severity, 9), r.code))


def _read_anything(sample: Dict[str, Any]) -> bool:
    """True when at least one state source in this sample was readable."""
    probes = (
        (sample.get("cgroup") or {}).get("version"),
        (sample.get("cpufreq") or {}).get("cpu_count"),
        (sample.get("thermal") or {}).get("max_celsius"),
        (sample.get("thermal") or {}).get("core_throttle_count"),
        (sample.get("faults") or {}).get("self_majflt"),
        (sample.get("faults") or {}).get("vmstat_pgmajfault"),
        (sample.get("load") or {}).get("loadavg_1m"),
        (sample.get("load") or {}).get("mem_available_bytes"),
        (sample.get("affinity") or {}).get("affinity_cpus"),
    )
    if any(p is not None for p in probes):
        return True
    return bool((sample.get("rpi") or {}).get("available"))


def _unsupported(reason_code: str, detail: str) -> Dict[str, Any]:
    return {
        "verdict": Verdict.UNSUPPORTED,
        "reasons": [_reason(reason_code, detail)],
        "measurement": {},
    }


def _state_reasons(
    sample: Dict[str, Any], t: Thresholds, reasons: List[Reason]
) -> None:
    """Reasons that depend only on a single sample's state, not on deltas."""
    cg = sample.get("cgroup") or {}
    freq = sample.get("cpufreq") or {}
    rpi = sample.get("rpi") or {}
    load = sample.get("load") or {}
    aff = sample.get("affinity") or {}

    if cg.get("quota_usec") is not None:
        cpus = cg.get("quota_cpus")
        detail = (
            "cgroup v%s CPU quota is %s usec per %s usec period%s. A quota costs about "
            "50 percent on Arm against about 64 percent on x86 (M. N. Jacob, "
            "\"Quota Not the Container\", CCGrid 2027 submission), so a run under a "
            "quota is not comparable with one without."
            % (
                cg.get("version"),
                cg.get("quota_usec"),
                cg.get("period_usec"),
                "" if cpus is None else " (%s CPU equivalent)" % cpus,
            )
        )
        reasons.append(
            _reason(
                "CGROUP_QUOTA_SET",
                detail,
                Severity.INVALIDATING if t.quota_invalidates else Severity.WARNING,
            )
        )

    governors = freq.get("governors") or {}
    non_perf = {g: n for g, n in governors.items() if g != "performance"}
    if non_perf:
        reasons.append(
            _reason(
                "GOVERNOR_NOT_PERFORMANCE",
                "CPU frequency governors in use: %s. Expected every CPU on "
                "\"performance\"."
                % ", ".join("%s on %d CPU(s)" % (g, n) for g, n in sorted(governors.items())),
            )
        )

    ratio = freq.get("min_freq_ratio")
    if ratio is not None and ratio < t.min_freq_ratio:
        reasons.append(
            _reason(
                "FREQ_BELOW_MAX",
                "Lowest scaling_cur_freq / cpuinfo_max_freq was %.3f, below the "
                "min_freq_ratio of %.2f. A governor reading \"performance\" does not "
                "guarantee the clock is up (cilium/cilium#7086 showed 800 MHz on a "
                "3.70 GHz part with every governor at performance)." % (ratio, t.min_freq_ratio),
            )
        )

    if rpi.get("available"):
        sticky = rpi.get("sticky") or []
        if sticky:
            reasons.append(
                _reason(
                    "RPI_THROTTLE_STICKY",
                    "vcgencmd get_throttled = %s; sticky flags set: %s. These are "
                    "since-boot flags, so they do not prove this measurement was "
                    "affected." % (rpi.get("hex"), "; ".join(sticky)),
                )
            )

    other = load.get("other_runnable")
    if other is not None and other > t.max_other_runnable:
        detail = (
            "%d other runnable process(es) present (loadavg 1m = %s). Not checking the "
            "process table before measuring produced a 32 percent measurement error "
            "and forced a public retraction in the author's own Android work "
            "(M. N. Jacob, Galaxy M21 benchmarking)."
            % (other, load.get("loadavg_1m"))
        )
        reasons.append(
            _reason(
                "COMPETING_LOAD",
                detail,
                Severity.INVALIDATING if t.competing_load_invalidates else Severity.WARNING,
            )
        )

    if t.working_set_bytes is not None:
        avail = load.get("mem_available_bytes")
        need = int(t.working_set_bytes * t.memory_headroom)
        if avail is not None and avail < need:
            reasons.append(
                _reason(
                    "LOW_MEMORY_HEADROOM",
                    "MemAvailable was %d bytes against a declared working set of %d "
                    "bytes plus %.0f percent headroom (%d bytes needed)."
                    % (avail, t.working_set_bytes, (t.memory_headroom - 1) * 100, need),
                )
            )

    cpuset = (sample.get("cgroup") or {}).get("cpuset_effective")
    cpu_count = freq.get("cpu_count")
    aff_cpus = aff.get("affinity_cpus")
    narrow = None
    if cpuset is not None and cpu_count and len(cpuset) < cpu_count:
        narrow = ("cpuset.cpus.effective", len(cpuset))
    elif aff_cpus is not None and cpu_count and len(aff_cpus) < cpu_count:
        narrow = ("sched_getaffinity", len(aff_cpus))
    if narrow is not None:
        reasons.append(
            _reason(
                "CPUSET_RESTRICTED",
                "%s allows %d of %d visible CPUs, so per-CPU and scaling numbers are "
                "taken on a subset of the machine." % (narrow[0], narrow[1], cpu_count),
            )
        )


def evaluate(
    before: Dict[str, Any],
    after: Dict[str, Any],
    duration_s: float,
    thresholds: Optional[Thresholds] = None,
) -> Dict[str, Any]:
    """Evaluate a measurement from the samples taken around it.

    Returns a dict with keys ``verdict``, ``reasons`` (list of Reason) and
    ``measurement`` (the tool-specific provenance payload).
    """
    t = thresholds or Thresholds()

    if not before.get("supported") or not after.get("supported"):
        res = _unsupported(
            "NOT_LINUX",
            "This platform exposes none of the Linux sysfs and procfs sources "
            "benchverdict samples, so no verdict is offered.",
        )
        res["measurement"] = {"duration_s": duration_s, "samples_taken": 2}
        res["unreadable"] = sorted(
            set(list(before.get("unreadable") or []) + list(after.get("unreadable") or []))
        )
        return res

    reasons: List[Reason] = []
    measurement: Dict[str, Any] = {
        "min_duration_s": t.min_duration,
        "confidence": "high" if duration_s >= t.min_duration else "low",
        "thresholds": {
            "min_duration": t.min_duration,
            "working_set_bytes": t.working_set_bytes,
            "max_major_faults": t.max_major_faults,
            "max_major_fault_ratio": t.max_major_fault_ratio,
            "min_freq_ratio": t.min_freq_ratio,
            "max_other_runnable": t.max_other_runnable,
            "max_thermal_rise_c": t.max_thermal_rise_c,
            "memory_headroom": t.memory_headroom,
            "competing_load_invalidates": t.competing_load_invalidates,
            "quota_invalidates": t.quota_invalidates,
        },
    }

    # -- cgroup throttling delta -----------------------------------------
    cb, ca = before.get("cgroup") or {}, after.get("cgroup") or {}
    d_throttled_usec = _delta(cb.get("throttled_usec"), ca.get("throttled_usec"))
    d_nr_throttled = _delta(cb.get("nr_throttled"), ca.get("nr_throttled"))
    d_nr_periods = _delta(cb.get("nr_periods"), ca.get("nr_periods"))
    measurement["cgroup"] = {
        "version": ca.get("version"),
        "path": ca.get("path"),
        "quota_usec": ca.get("quota_usec"),
        "period_usec": ca.get("period_usec"),
        "quota_cpus": ca.get("quota_cpus"),
        "cpuset_effective": ca.get("cpuset_effective"),
        "delta_nr_periods": d_nr_periods,
        "delta_nr_throttled": d_nr_throttled,
        "delta_throttled_usec": d_throttled_usec,
        "throttled_fraction_of_measurement": (
            round(d_throttled_usec / (duration_s * 1e6), 6)
            if d_throttled_usec is not None and duration_s > 0
            else None
        ),
    }
    throttle_delta = [
        ("throttled time", d_throttled_usec, "usec"),
        ("throttled periods", d_nr_throttled, "periods"),
    ]
    rose = [(name, val, unit) for name, val, unit in throttle_delta if val is not None and val > 0]
    if rose:
        bits = ", ".join("%s rose by %d %s" % (n, v, u) for n, v, u in rose)
        frac = measurement["cgroup"]["throttled_fraction_of_measurement"]
        reasons.append(
            _reason(
                "CGROUP_THROTTLED",
                "cgroup v%s CPU bandwidth control denied this process CPU time during "
                "the measurement: %s%s. Measured from %s/cpu.stat as a delta across the "
                "measured interval, which is what distinguishes quota denial from "
                "general CPU pressure (cf. VictoriaMetrics/VictoriaMetrics#10430)."
                % (
                    ca.get("version"),
                    bits,
                    "" if frac is None else " (%.4f of the measured wall time)" % frac,
                    ca.get("path"),
                ),
            )
        )

    # -- thermal and power cap -------------------------------------------
    tb, ta = before.get("thermal") or {}, after.get("thermal") or {}
    d_core = _delta(tb.get("core_throttle_count"), ta.get("core_throttle_count"))
    d_pkg = _delta(tb.get("package_throttle_count"), ta.get("package_throttle_count"))
    temp_rise = None
    if tb.get("max_celsius") is not None and ta.get("max_celsius") is not None:
        temp_rise = round(ta["max_celsius"] - tb["max_celsius"], 3)
    measurement["thermal"] = {
        "max_celsius_before": tb.get("max_celsius"),
        "max_celsius_after": ta.get("max_celsius"),
        "max_celsius_rise": temp_rise,
        "zone_celsius_after": ta.get("zone_celsius"),
        "delta_core_throttle_count": d_core,
        "delta_package_throttle_count": d_pkg,
    }
    hot = [(n, v) for n, v in (("core", d_core), ("package", d_pkg)) if v is not None and v > 0]
    if hot:
        reasons.append(
            _reason(
                "THERMAL_THROTTLED",
                "x86 thermal throttle counters rose during the measurement: %s. These "
                "are event counters, so any rise means the CPU clamped itself while "
                "the number was being taken."
                % ", ".join("%s_throttle_count +%d" % (n, v) for n, v in hot),
            )
        )
    if temp_rise is not None and temp_rise > t.max_thermal_rise_c:
        reasons.append(
            _reason(
                "THERMAL_RISE",
                "The hottest thermal zone rose %.1f C during the measurement, above "
                "the max_thermal_rise_c of %.1f C. Temperature alone is not proof of "
                "throttling; it is a warning that the run was not thermally steady."
                % (temp_rise, t.max_thermal_rise_c),
            )
        )

    # -- Raspberry Pi -----------------------------------------------------
    rb, ra = before.get("rpi") or {}, after.get("rpi") or {}
    measurement["rpi"] = {
        "available": bool(ra.get("available")),
        "raw_after": ra.get("raw"),
        "value_before": rb.get("value"),
        "value_after": ra.get("value"),
        "live_after": ra.get("live"),
        "sticky_after": ra.get("sticky"),
        "sticky_newly_set": None,
    }
    if ra.get("available"):
        live = list(ra.get("live") or [])
        if live:
            reasons.append(
                _reason(
                    "RPI_THROTTLE_ACTIVE",
                    "vcgencmd get_throttled = %s at the end of the measurement with "
                    "live bit(s) set: %s." % (ra.get("hex"), "; ".join(live)),
                )
            )
        if rb.get("available") and rb.get("value") is not None and ra.get("value") is not None:
            newly = sorted(set(ra.get("sticky") or []) - set(rb.get("sticky") or []))
            measurement["rpi"]["sticky_newly_set"] = newly
            if newly:
                reasons.append(
                    _reason(
                        "RPI_THROTTLE_ACTIVE",
                        "Raspberry Pi sticky flags were set during the measurement "
                        "itself (newly set: %s), which is a since-boot flag changing "
                        "inside the measured interval." % "; ".join(newly),
                    )
                )

    # -- paging -----------------------------------------------------------
    fb, fa = before.get("faults") or {}, after.get("faults") or {}
    d_majflt = _delta(fb.get("self_majflt"), fa.get("self_majflt"))
    d_pgmajfault = _delta(fb.get("vmstat_pgmajfault"), fa.get("vmstat_pgmajfault"))
    pages = None
    ratio = None
    if t.working_set_bytes is not None:
        page_size = int(before.get("page_size") or after.get("page_size") or 4096)
        pages = max(t.working_set_bytes // page_size, 1)
        if d_majflt is not None:
            ratio = round(d_majflt / pages, 6)
    measurement["faults"] = {
        "delta_self_majflt": d_majflt,
        "delta_vmstat_pgmajfault": d_pgmajfault,
        "working_set_pages": pages,
        "major_fault_ratio": ratio,
    }
    if d_majflt is not None and d_majflt > 0:
        if t.max_major_faults is not None and d_majflt > t.max_major_faults:
            reasons.append(
                _reason(
                    "MAJOR_FAULTS_EXCEEDED",
                    "%d major page faults during the measurement, above the declared "
                    "max_major_faults of %d. A working set that is not resident turns "
                    "a memory benchmark into a storage benchmark."
                    % (d_majflt, t.max_major_faults),
                )
            )
        elif ratio is not None and ratio > t.max_major_fault_ratio:
            reasons.append(
                _reason(
                    "MAJOR_FAULT_RATIO_HIGH",
                    "%d major page faults against a declared working set of %d pages "
                    "(%.4f), above max_major_fault_ratio of %.2f."
                    % (d_majflt, pages, ratio, t.max_major_fault_ratio),
                )
            )
        else:
            reasons.append(
                _reason(
                    "MAJOR_FAULTS_OBSERVED",
                    "%d major page faults during the measurement%s. Reported as an "
                    "observation. For scale, the author's Raspberry Pi 5 run was shown "
                    "to be DRAM-resident by a major-fault delta of 11,404 against a "
                    "272,000-page model, rather than assumed to be."
                    % (
                        d_majflt,
                        "" if ratio is None else " (%.4f of the declared working set)" % ratio,
                    ),
                )
            )

    # -- competing load, affinity, cpu count ------------------------------
    lb, la = before.get("load") or {}, after.get("load") or {}
    measurement["load"] = {
        "loadavg_1m_before": lb.get("loadavg_1m"),
        "loadavg_1m_after": la.get("loadavg_1m"),
        "other_runnable_before": lb.get("other_runnable"),
        "other_runnable_after": la.get("other_runnable"),
        "total_processes_after": la.get("total_processes"),
        "mem_available_bytes_before": lb.get("mem_available_bytes"),
        "mem_available_bytes_after": la.get("mem_available_bytes"),
        "mem_total_bytes": la.get("mem_total_bytes"),
    }

    ab, aa = before.get("affinity") or {}, after.get("affinity") or {}
    measurement["affinity"] = {
        "cpus_before": ab.get("affinity_cpus"),
        "cpus_after": aa.get("affinity_cpus"),
    }
    if (
        ab.get("affinity_cpus") is not None
        and aa.get("affinity_cpus") is not None
        and ab["affinity_cpus"] != aa["affinity_cpus"]
    ):
        reasons.append(
            _reason(
                "AFFINITY_CHANGED",
                "The CPUs this process was allowed to run on changed from %s to %s "
                "during the measurement." % (ab["affinity_cpus"], aa["affinity_cpus"]),
            )
        )

    freq_b, freq_a = before.get("cpufreq") or {}, after.get("cpufreq") or {}
    measurement["cpufreq"] = {
        "governors_after": freq_a.get("governors"),
        "min_freq_ratio_before": freq_b.get("min_freq_ratio"),
        "min_freq_ratio_after": freq_a.get("min_freq_ratio"),
        "cpu_count": freq_a.get("cpu_count"),
    }
    if (
        freq_b.get("cpu_count") is not None
        and freq_a.get("cpu_count") is not None
        and freq_b["cpu_count"] != freq_a["cpu_count"]
    ):
        reasons.append(
            _reason(
                "CPU_COUNT_CHANGED",
                "Visible CPU count changed from %d to %d during the measurement."
                % (freq_b["cpu_count"], freq_a["cpu_count"]),
            )
        )

    # State reasons are read off the later sample, which is the state the
    # number was actually produced under.
    _state_reasons(after, t, reasons)

    # -- duration and readability ----------------------------------------
    if duration_s < t.min_duration:
        reasons.append(
            _reason(
                "SHORT_DURATION",
                "Measured interval was %.4f s, below min_duration of %.2f s. Throttle "
                "ratios over a few milliseconds of work are not stable: "
                "nucliweb/webperf-snippets#312 (merged 24 Oct 2026) recorded CPU "
                "throttle ratios of 1.53 and 0.92 on two refs whose code was identical, "
                "against a gate of 2.0, and lengthening the work to about 20 ms settled "
                "the ratio into 3.74 to 4.02." % (duration_s, t.min_duration),
            )
        )

    unreadable = sorted(
        set(list(before.get("unreadable") or []) + list(after.get("unreadable") or []))
    )
    if unreadable:
        reasons.append(
            _reason(
                "SOURCES_UNREADABLE",
                "%d state source(s) could not be read: %s. benchverdict reports them "
                "rather than filling in a plausible value."
                % (len(unreadable), "; ".join(unreadable)),
            )
        )

    blind = not _read_anything(after)
    if blind:
        reasons.append(
            _reason(
                "NO_SOURCES",
                "Not one state source was readable on a platform that reports itself "
                "as Linux, so this verdict rests on no evidence at all.",
            )
        )

    verdict = _verdict_from(reasons, duration_s, t, blind)
    return {
        "verdict": verdict,
        "reasons": _sorted(reasons),
        "measurement": measurement,
        "unreadable": unreadable,
    }


def _verdict_from(
    reasons: List[Reason], duration_s: float, t: Thresholds, blind: bool = False
) -> str:
    if any(r.severity == Severity.INVALIDATING for r in reasons):
        # An invalidating fact stands regardless of duration: a throttle counter
        # that rose, rose. It is only the clean bill of health that needs time.
        return Verdict.INVALID
    if blind:
        # No evidence of a problem is not evidence of no problem.
        return Verdict.UNKNOWN
    if duration_s < t.min_duration:
        return Verdict.UNKNOWN
    return Verdict.PASS


def evaluate_point_in_time(
    sample: Dict[str, Any], thresholds: Optional[Thresholds] = None
) -> Dict[str, Any]:
    """Evaluate current machine state with no deltas available.

    Returns PASS, INVALID or UNSUPPORTED. It never returns UNKNOWN for duration,
    because a point-in-time read makes no claim about an interval.
    """
    t = thresholds or Thresholds()
    if not sample.get("supported"):
        res = _unsupported(
            "NOT_LINUX",
            "This platform exposes none of the Linux sysfs and procfs sources "
            "benchverdict samples, so no verdict is offered.",
        )
        res["unreadable"] = list(sample.get("unreadable") or [])
        return res

    reasons: List[Reason] = []
    cg = sample.get("cgroup") or {}
    rpi = sample.get("rpi") or {}
    thermal = sample.get("thermal") or {}
    freq = sample.get("cpufreq") or {}
    load = sample.get("load") or {}

    if rpi.get("available") and (rpi.get("live") or []):
        reasons.append(
            _reason(
                "RPI_THROTTLE_ACTIVE",
                "vcgencmd get_throttled = %s with live bit(s) set right now: %s."
                % (rpi.get("hex"), "; ".join(rpi["live"])),
            )
        )

    _state_reasons(sample, t, reasons)

    measurement: Dict[str, Any] = {
        "point_in_time": True,
        "confidence": "state-only",
        "cgroup": {
            "version": cg.get("version"),
            "path": cg.get("path"),
            "quota_usec": cg.get("quota_usec"),
            "period_usec": cg.get("period_usec"),
            "quota_cpus": cg.get("quota_cpus"),
            "nr_periods": cg.get("nr_periods"),
            "nr_throttled": cg.get("nr_throttled"),
            "throttled_usec": cg.get("throttled_usec"),
            "cpuset_effective": cg.get("cpuset_effective"),
        },
        "cpufreq": {
            "governors": freq.get("governors"),
            "min_freq_ratio": freq.get("min_freq_ratio"),
            "cpu_count": freq.get("cpu_count"),
        },
        "thermal": {
            "max_celsius": thermal.get("max_celsius"),
            "zone_celsius": thermal.get("zone_celsius"),
            "core_throttle_count": thermal.get("core_throttle_count"),
            "package_throttle_count": thermal.get("package_throttle_count"),
        },
        "rpi": {
            "available": bool(rpi.get("available")),
            "raw": rpi.get("raw"),
            "value": rpi.get("value"),
            "live": rpi.get("live"),
            "sticky": rpi.get("sticky"),
        },
        "faults": {
            "self_majflt": (sample.get("faults") or {}).get("self_majflt"),
            "vmstat_pgmajfault": (sample.get("faults") or {}).get("vmstat_pgmajfault"),
        },
        "load": dict(load),
        "affinity": dict(sample.get("affinity") or {}),
    }

    unreadable = list(sample.get("unreadable") or [])
    if unreadable:
        reasons.append(
            _reason(
                "SOURCES_UNREADABLE",
                "%d state source(s) could not be read: %s. benchverdict reports them "
                "rather than filling in a plausible value."
                % (len(unreadable), "; ".join(unreadable)),
            )
        )

    if not _read_anything(sample):
        reasons.append(
            _reason(
                "NO_SOURCES",
                "Not one state source was readable on a platform that reports itself "
                "as Linux, so this verdict rests on no evidence at all.",
            )
        )
        verdict = Verdict.UNKNOWN
    elif any(r.severity == Severity.INVALIDATING for r in reasons):
        verdict = Verdict.INVALID
    else:
        verdict = Verdict.PASS
    return {
        "verdict": verdict,
        "reasons": _sorted(reasons),
        "measurement": measurement,
        "unreadable": unreadable,
    }
