"""Verdict assembly: deltas across a measurement, and the state at the end of it."""

from __future__ import annotations

from conftest import fake_vcgencmd, probe_for

from benchverdict.checks import (
    REASON_CODES,
    Severity,
    Thresholds,
    Verdict,
    evaluate,
    evaluate_point_in_time,
)


def around(tree, mutate=None, duration=2.0, vcgencmd=None, **threshold_kwargs):
    """Sample, optionally change the machine, sample again, evaluate."""
    probe = probe_for(tree, vcgencmd=vcgencmd)
    before = probe.sample()
    if mutate is not None:
        mutate(tree)
    after = probe.sample()
    return evaluate(before, after, duration, Thresholds(**threshold_kwargs))


def codes(result):
    return [r.code for r in result["reasons"]]


class TestVocabulary:
    def test_every_code_has_a_valid_severity_and_a_sentence(self):
        for code, (severity, doc) in REASON_CODES.items():
            assert severity in Severity.ALL, code
            assert code == code.upper() and " " not in code, code
            assert doc.endswith("."), code
            assert len(doc) > 20, code

    def test_every_emitted_code_is_in_the_vocabulary(self, tree):
        # Make everything go wrong at once and confirm no code escapes the table.
        def mutate(t):
            t.set_cgroup_v2(nr_periods=100, nr_throttled=40, throttled_usec=500000,
                            cpu_max="50000 100000")
            t.set_thermal_throttle(core=5)
            t.set_temp(90.0)
            t.set_majflt(999999)
            t.set_governor("powersave")
            t.set_cur_freq(600000)
            t.set_loadavg(runnable=9)
            t.set_mem_available(1000)
            t.set_affinity("0")

        result = around(
            tree,
            mutate,
            duration=0.01,
            vcgencmd=fake_vcgencmd(0x50005),
            working_set_bytes=1_000_000_000,
            max_major_faults=10,
        )
        assert result["reasons"]
        for reason in result["reasons"]:
            assert reason.code in REASON_CODES


class TestPass:
    def test_healthy_long_run_passes(self, tree):
        result = around(tree, duration=2.0)
        assert result["verdict"] == Verdict.PASS

    def test_pass_still_lists_unreadable_sources(self, tree):
        result = around(tree, duration=2.0)
        # No vcgencmd and no cgroup v1 on this fixture, so the absence is named.
        assert "SOURCES_UNREADABLE" in codes(result)
        assert any("vcgencmd" in u for u in result["unreadable"])


class TestCgroupThrottling:
    def test_rising_throttled_usec_invalidates(self, tree):
        result = around(
            tree,
            lambda t: t.set_cgroup_v2(nr_periods=200, nr_throttled=37, throttled_usec=812345),
        )
        assert result["verdict"] == Verdict.INVALID
        assert "CGROUP_THROTTLED" in codes(result)
        assert result["measurement"]["cgroup"]["delta_throttled_usec"] == 812345
        assert result["measurement"]["cgroup"]["delta_nr_throttled"] == 37

    def test_throttled_fraction_of_the_measurement_is_reported(self, tree):
        result = around(
            tree,
            lambda t: t.set_cgroup_v2(throttled_usec=500_000),
            duration=2.0,
        )
        assert result["measurement"]["cgroup"]["throttled_fraction_of_measurement"] == 0.25

    def test_v1_throttled_time_rise_invalidates(self, tree):
        tree.set_cgroup_v1(throttled_time_ns=1_000_000, quota_us=50000)
        result = around(tree, lambda t: t.set_cgroup_v1(throttled_time_ns=9_000_000,
                                                        quota_us=50000))
        assert result["verdict"] == Verdict.INVALID
        assert "CGROUP_THROTTLED" in codes(result)
        assert result["measurement"]["cgroup"]["delta_throttled_usec"] == 8000

    def test_a_standing_throttle_count_that_does_not_move_does_not_invalidate(self, tree):
        tree.set_cgroup_v2(nr_periods=500, nr_throttled=99, throttled_usec=123456,
                           cpu_max="max 100000")
        result = around(tree, duration=2.0)
        assert result["verdict"] == Verdict.PASS
        assert "CGROUP_THROTTLED" not in codes(result)
        assert result["measurement"]["cgroup"]["delta_throttled_usec"] == 0

    def test_quota_present_is_a_warning_by_default(self, tree):
        tree.set_cgroup_v2(cpu_max="50000 100000")
        result = around(tree, duration=2.0)
        assert result["verdict"] == Verdict.PASS
        quota = [r for r in result["reasons"] if r.code == "CGROUP_QUOTA_SET"]
        assert quota and quota[0].severity == Severity.WARNING
        assert "Quota Not the Container" in quota[0].detail
        assert result["measurement"]["cgroup"]["quota_cpus"] == 0.5

    def test_quota_can_be_made_invalidating(self, tree):
        tree.set_cgroup_v2(cpu_max="50000 100000")
        result = around(tree, duration=2.0, quota_invalidates=True)
        assert result["verdict"] == Verdict.INVALID
        assert result["reasons"][0].code == "CGROUP_QUOTA_SET"


class TestThermal:
    def test_core_throttle_count_rise_invalidates(self, tree):
        result = around(tree, lambda t: t.set_thermal_throttle(core=2))
        assert result["verdict"] == Verdict.INVALID
        assert "THERMAL_THROTTLED" in codes(result)
        assert result["measurement"]["thermal"]["delta_core_throttle_count"] == 8

    def test_package_throttle_count_rise_invalidates(self, tree):
        result = around(tree, lambda t: t.set_thermal_throttle(core=0, package=1))
        assert result["verdict"] == Verdict.INVALID
        assert "THERMAL_THROTTLED" in codes(result)

    def test_temperature_rise_warns_but_does_not_invalidate(self, tree):
        result = around(tree, lambda t: t.set_temp(70.0), duration=2.0)
        assert result["verdict"] == Verdict.PASS
        assert "THERMAL_RISE" in codes(result)
        assert result["measurement"]["thermal"]["max_celsius_rise"] == 25.0

    def test_small_temperature_rise_is_silent(self, tree):
        result = around(tree, lambda t: t.set_temp(50.0), duration=2.0)
        assert "THERMAL_RISE" not in codes(result)


class TestRaspberryPi:
    def test_live_throttle_bit_invalidates(self, tree):
        result = around(tree, duration=2.0, vcgencmd=fake_vcgencmd(0x4))
        assert result["verdict"] == Verdict.INVALID
        assert "RPI_THROTTLE_ACTIVE" in codes(result)

    def test_sticky_only_is_a_warning(self, tree):
        result = around(tree, duration=2.0, vcgencmd=fake_vcgencmd(0x40000))
        assert result["verdict"] == Verdict.PASS
        sticky = [r for r in result["reasons"] if r.code == "RPI_THROTTLE_STICKY"]
        assert sticky and sticky[0].severity == Severity.WARNING

    def test_under_voltage_live_bit_invalidates(self, tree):
        result = around(tree, duration=2.0, vcgencmd=fake_vcgencmd(0x1))
        assert result["verdict"] == Verdict.INVALID


class TestPaging:
    def test_fault_delta_without_a_declared_ceiling_is_info_only(self, tree):
        result = around(tree, lambda t: t.set_majflt(11404), duration=2.0)
        assert result["verdict"] == Verdict.PASS
        info = [r for r in result["reasons"] if r.code == "MAJOR_FAULTS_OBSERVED"]
        assert info and info[0].severity == Severity.INFO
        assert result["measurement"]["faults"]["delta_self_majflt"] == 11404

    def test_declared_ceiling_exceeded_invalidates(self, tree):
        result = around(tree, lambda t: t.set_majflt(5000), duration=2.0,
                        max_major_faults=100)
        assert result["verdict"] == Verdict.INVALID
        assert "MAJOR_FAULTS_EXCEEDED" in codes(result)

    def test_declared_ceiling_respected_passes(self, tree):
        result = around(tree, lambda t: t.set_majflt(50), duration=2.0,
                        max_major_faults=100)
        assert result["verdict"] == Verdict.PASS

    def test_the_pi5_residency_case_passes_the_ratio_check(self, tree):
        # The author's Raspberry Pi 5 run was shown to be DRAM-resident by a
        # major-fault delta of 11,404 against a 272,000-page model: 4.2 percent,
        # under the 10 percent default. The fixture reproduces those two numbers
        # so the default threshold is pinned to a case with a known outcome.
        result = around(
            tree,
            lambda t: t.set_majflt(11404),
            duration=2.0,
            working_set_bytes=272_000 * 4096,
        )
        assert result["verdict"] == Verdict.PASS
        assert result["measurement"]["faults"]["working_set_pages"] == 272_000
        assert result["measurement"]["faults"]["major_fault_ratio"] == 0.041926
        assert "MAJOR_FAULT_RATIO_HIGH" not in codes(result)

    def test_high_fault_ratio_warns(self, tree):
        result = around(
            tree,
            lambda t: t.set_majflt(100_000),
            duration=2.0,
            working_set_bytes=272_000 * 4096,
        )
        assert result["verdict"] == Verdict.PASS
        assert "MAJOR_FAULT_RATIO_HIGH" in codes(result)

    def test_vmstat_delta_is_recorded_alongside(self, tree):
        result = around(tree, lambda t: t.set_pgmajfault(900), duration=2.0)
        assert result["measurement"]["faults"]["delta_vmstat_pgmajfault"] == 800


class TestCompetingLoad:
    def test_other_runnable_processes_warn(self, tree):
        result = around(tree, lambda t: t.set_loadavg(runnable=4), duration=2.0)
        assert result["verdict"] == Verdict.PASS
        load = [r for r in result["reasons"] if r.code == "COMPETING_LOAD"]
        assert load and load[0].severity == Severity.WARNING
        assert "32 percent" in load[0].detail

    def test_competing_load_can_be_made_invalidating(self, tree):
        result = around(
            tree, lambda t: t.set_loadavg(runnable=4), duration=2.0,
            competing_load_invalidates=True,
        )
        assert result["verdict"] == Verdict.INVALID

    def test_only_this_process_runnable_is_silent(self, tree):
        result = around(tree, duration=2.0)
        assert "COMPETING_LOAD" not in codes(result)

    def test_tolerance_can_be_raised(self, tree):
        result = around(tree, lambda t: t.set_loadavg(runnable=3), duration=2.0,
                        max_other_runnable=5)
        assert "COMPETING_LOAD" not in codes(result)


class TestMemoryAndCpuset:
    def test_low_memory_headroom_warns(self, tree):
        result = around(tree, duration=2.0, working_set_bytes=7_000_000 * 1024)
        assert "LOW_MEMORY_HEADROOM" in codes(result)

    def test_enough_headroom_is_silent(self, tree):
        result = around(tree, duration=2.0, working_set_bytes=1_000_000 * 1024)
        assert "LOW_MEMORY_HEADROOM" not in codes(result)

    def test_narrow_cpuset_warns(self, tree):
        tree.write("sys/fs/cgroup/cpuset.cpus.effective", "0-1\n")
        result = around(tree, duration=2.0)
        assert "CPUSET_RESTRICTED" in codes(result)

    def test_affinity_change_during_the_measurement_invalidates(self, tree):
        result = around(tree, lambda t: t.set_affinity("0-1"), duration=2.0)
        assert result["verdict"] == Verdict.INVALID
        assert "AFFINITY_CHANGED" in codes(result)
        assert result["measurement"]["affinity"]["cpus_after"] == [0, 1]

    def test_cpu_count_change_invalidates(self, tree):
        def mutate(t):
            t.write("sys/devices/system/cpu/cpu4/cpufreq/scaling_governor", "performance\n")
            t.write("sys/devices/system/cpu/cpu4/cpufreq/scaling_cur_freq", "2400000\n")
            t.write("sys/devices/system/cpu/cpu4/cpufreq/cpuinfo_max_freq", "2400000\n")

        result = around(tree, mutate, duration=2.0)
        assert result["verdict"] == Verdict.INVALID
        assert "CPU_COUNT_CHANGED" in codes(result)


class TestFrequencyAndGovernor:
    def test_non_performance_governor_warns(self, tree):
        result = around(tree, lambda t: t.set_governor("schedutil"), duration=2.0)
        assert result["verdict"] == Verdict.PASS
        assert "GOVERNOR_NOT_PERFORMANCE" in codes(result)

    def test_clock_below_max_warns_independently_of_the_governor(self, tree):
        # The cilium/cilium#7086 case: every governor reads performance while the
        # clock sits far below the part's maximum. Both checks must be separate.
        result = around(tree, lambda t: t.set_cur_freq(800_000), duration=2.0)
        assert "GOVERNOR_NOT_PERFORMANCE" not in codes(result)
        assert "FREQ_BELOW_MAX" in codes(result)
        freq = [r for r in result["reasons"] if r.code == "FREQ_BELOW_MAX"][0]
        assert "cilium" in freq.detail


class TestDurationConfidence:
    def test_short_clean_run_is_unknown_not_pass(self, tree):
        result = around(tree, duration=0.004)
        assert result["verdict"] == Verdict.UNKNOWN
        assert "SHORT_DURATION" in codes(result)
        assert result["measurement"]["confidence"] == "low"

    def test_short_duration_detail_cites_the_webperf_snippets_numbers(self, tree):
        result = around(tree, duration=0.004)
        detail = [r for r in result["reasons"] if r.code == "SHORT_DURATION"][0].detail
        assert "1.53" in detail and "0.92" in detail
        assert "webperf-snippets#312" in detail

    def test_an_invalidating_fact_beats_a_short_duration(self, tree):
        # A throttle counter that rose, rose, however short the interval.
        result = around(
            tree, lambda t: t.set_thermal_throttle(core=1), duration=0.004
        )
        assert result["verdict"] == Verdict.INVALID

    def test_min_duration_is_configurable(self, tree):
        assert around(tree, duration=0.5, min_duration=0.1)["verdict"] == Verdict.PASS
        assert around(tree, duration=0.5, min_duration=5.0)["verdict"] == Verdict.UNKNOWN


class TestUnsupported:
    def test_non_linux_pair_is_unsupported(self, tree):
        from benchverdict.sources import Probe

        probe = Probe(root=tree.root, assume_linux=False)
        result = evaluate(probe.sample(), probe.sample(), 2.0)
        assert result["verdict"] == Verdict.UNSUPPORTED
        assert codes(result) == ["NOT_LINUX"]

    def test_unsupported_is_not_invalid(self, tree):
        from benchverdict.sources import Probe

        probe = Probe(root=tree.root, assume_linux=False)
        result = evaluate(probe.sample(), probe.sample(), 2.0)
        assert result["verdict"] != Verdict.INVALID

    def test_empty_root_on_linux_is_unknown_not_pass(self, empty_tree):
        result = around(empty_tree, duration=2.0)
        # No invalidating evidence exists, but neither does any evidence of
        # health, and a clean bill of health from zero readings would be a lie.
        assert result["verdict"] == Verdict.UNKNOWN
        assert "NO_SOURCES" in codes(result)
        assert "SOURCES_UNREADABLE" in codes(result)
        assert len(result["unreadable"]) >= 4

    def test_blind_point_in_time_read_is_unknown(self, empty_tree):
        result = evaluate_point_in_time(probe_for(empty_tree).sample())
        assert result["verdict"] == Verdict.UNKNOWN
        assert "NO_SOURCES" in codes(result)

    def test_no_sources_is_not_reported_when_something_was_read(self, tree):
        assert "NO_SOURCES" not in codes(around(tree, duration=2.0))


class TestPointInTime:
    def test_healthy_state_passes(self, tree):
        result = evaluate_point_in_time(probe_for(tree).sample())
        assert result["verdict"] == Verdict.PASS
        assert result["measurement"]["point_in_time"] is True

    def test_never_returns_unknown_for_duration(self, tree):
        result = evaluate_point_in_time(probe_for(tree).sample())
        assert result["verdict"] != Verdict.UNKNOWN
        assert "SHORT_DURATION" not in codes(result)

    def test_live_rpi_throttle_invalidates_a_point_read(self, tree):
        sample = probe_for(tree, vcgencmd=fake_vcgencmd(0x4)).sample()
        result = evaluate_point_in_time(sample)
        assert result["verdict"] == Verdict.INVALID

    def test_counters_are_absolute_not_deltas(self, tree):
        tree.set_cgroup_v2(nr_periods=10, nr_throttled=2, throttled_usec=4321)
        result = evaluate_point_in_time(probe_for(tree).sample())
        assert result["measurement"]["cgroup"]["throttled_usec"] == 4321
        assert "delta_throttled_usec" not in result["measurement"]["cgroup"]

    def test_state_warnings_still_fire(self, tree):
        tree.set_governor("powersave")
        tree.set_cgroup_v2(cpu_max="50000 100000")
        result = evaluate_point_in_time(probe_for(tree).sample())
        assert {"GOVERNOR_NOT_PERFORMANCE", "CGROUP_QUOTA_SET"} <= set(codes(result))
        assert result["verdict"] == Verdict.PASS

    def test_unsupported(self, tree):
        from benchverdict.sources import Probe

        result = evaluate_point_in_time(Probe(root=tree.root, assume_linux=False).sample())
        assert result["verdict"] == Verdict.UNSUPPORTED


class TestReasonOrdering:
    def test_invalidating_reasons_come_first(self, tree):
        def mutate(t):
            t.set_cgroup_v2(throttled_usec=100000, cpu_max="50000 100000")
            t.set_governor("powersave")
            t.set_majflt(10)

        result = around(tree, mutate, duration=2.0)
        severities = [r.severity for r in result["reasons"]]
        assert severities == sorted(
            severities,
            key=lambda s: {Severity.INVALIDATING: 0, Severity.WARNING: 1, Severity.INFO: 2}[s],
        )
        assert result["reasons"][0].severity == Severity.INVALIDATING
