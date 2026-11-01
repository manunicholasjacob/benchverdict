"""Readers: do they read the right file, and do they refuse to guess?"""

from __future__ import annotations

import pytest
from conftest import fake_vcgencmd, probe_for

from benchguard.sources import Reader, _parse_cpu_list


class TestReader:
    def test_integer_reads_an_int(self, tree):
        r = Reader(tree.root)
        assert r.integer("sys/class/thermal/thermal_zone0/temp") == 45000
        assert r.unreadable == []

    def test_missing_file_is_none_and_recorded(self, tree):
        r = Reader(tree.root)
        assert r.integer("sys/does/not/exist") is None
        assert "sys/does/not/exist" in r.unreadable

    def test_non_numeric_file_is_none_not_zero(self, tree):
        tree.write("sys/class/thermal/thermal_zone0/temp", "not a number\n")
        r = Reader(tree.root)
        assert r.integer("sys/class/thermal/thermal_zone0/temp") is None
        assert "sys/class/thermal/thermal_zone0/temp" in r.unreadable

    def test_keyed_ints_skips_unparseable_lines(self, tree):
        tree.write("proc/vmstat", "pgmajfault 7\ngarbage\nnr_free_pages notanint\n")
        r = Reader(tree.root)
        assert r.keyed_ints("proc/vmstat") == {"pgmajfault": 7}

    def test_cpu_ids_ignores_non_cpu_entries(self, tree):
        tree.write("sys/devices/system/cpu/cpuidle/placeholder", "x\n")
        tree.write("sys/devices/system/cpu/online", "0-3\n")
        assert Reader(tree.root).cpu_ids() == [0, 1, 2, 3]


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("0-3", [0, 1, 2, 3]),
        ("0,2,4", [0, 2, 4]),
        ("0-1,4-5", [0, 1, 4, 5]),
        ("2", [2]),
        ("", []),
        ("bogus", []),
        ("0-", []),
    ],
)
def test_parse_cpu_list(spec, expected):
    assert _parse_cpu_list(spec) == expected


class TestCgroup:
    def test_v2_no_quota(self, tree):
        s = probe_for(tree).sample()["cgroup"]
        assert s["version"] == 2
        assert s["quota_usec"] is None
        assert s["period_usec"] == 100000
        assert s["throttled_usec"] == 0
        assert s["cpuset_effective"] == [0, 1, 2, 3]

    def test_v2_with_quota(self, tree):
        tree.set_cgroup_v2(nr_periods=100, nr_throttled=12, throttled_usec=34567,
                           cpu_max="50000 100000")
        s = probe_for(tree).sample()["cgroup"]
        assert (s["quota_usec"], s["period_usec"], s["quota_cpus"]) == (50000, 100000, 0.5)
        assert (s["nr_periods"], s["nr_throttled"], s["throttled_usec"]) == (100, 12, 34567)

    def test_v1_quota_and_throttled_time_converted_to_usec(self, tree):
        tree.set_cgroup_v1(throttled_time_ns=5_000_000, nr_periods=50, nr_throttled=3,
                           quota_us=25000, period_us=100000)
        s = probe_for(tree).sample()["cgroup"]
        assert s["version"] == 1
        assert s["path"] == "/sys/fs/cgroup/cpu/mygroup"
        assert s["throttled_usec"] == 5000
        assert (s["quota_usec"], s["quota_cpus"]) == (25000, 0.25)

    def test_v1_quota_sentinel_minus_one_is_not_a_quota(self, tree):
        tree.set_cgroup_v1(quota_us=-1)
        s = probe_for(tree).sample()["cgroup"]
        assert s["version"] == 1
        assert s["quota_usec"] is None
        assert s["quota_cpus"] is None

    def test_nested_v2_path_walks_up_for_cpu_max(self, tree):
        tree.write("proc/self/cgroup", "0::/user.slice/app.scope\n")
        tree.write(
            "sys/fs/cgroup/user.slice/app.scope/cpu.stat",
            "nr_periods 10\nnr_throttled 0\nthrottled_usec 0\n",
        )
        tree.write("sys/fs/cgroup/cpu.max", "80000 100000\n")
        s = probe_for(tree).sample()["cgroup"]
        assert s["path"] == "/sys/fs/cgroup/user.slice/app.scope"
        assert s["quota_cpus"] == 0.8

    def test_no_cgroup_at_all_is_reported_unreadable(self, tree):
        tree.remove("sys/fs/cgroup/cpu.stat")
        sample = probe_for(tree).sample()
        assert sample["cgroup"]["version"] is None
        assert any("cgroup cpu.stat" in u for u in sample["unreadable"])


class TestCpuFreq:
    def test_healthy(self, tree):
        s = probe_for(tree).sample()["cpufreq"]
        assert s["governors"] == {"performance": 4}
        assert s["min_freq_ratio"] == 1.0
        assert s["cpu_count"] == 4

    def test_mixed_governors_counted(self, tree):
        tree.write("sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", "powersave\n")
        s = probe_for(tree).sample()["cpufreq"]
        assert s["governors"] == {"performance": 3, "powersave": 1}

    def test_freq_ratio_uses_the_lowest_cpu(self, tree):
        tree.write("sys/devices/system/cpu/cpu2/cpufreq/scaling_cur_freq", "800000\n")
        s = probe_for(tree).sample()["cpufreq"]
        assert s["min_freq_ratio"] == pytest.approx(800000 / 2400000, abs=1e-4)

    def test_absent_max_freq_yields_no_ratio_rather_than_a_guess(self, tree):
        for cpu in range(4):
            tree.remove("sys/devices/system/cpu/cpu%d/cpufreq/cpuinfo_max_freq" % cpu)
        s = probe_for(tree).sample()["cpufreq"]
        assert s["min_freq_ratio"] is None


class TestThermal:
    def test_temperature_converted_from_millidegrees(self, tree):
        s = probe_for(tree).sample()["thermal"]
        assert s["max_celsius"] == 45.0
        assert "thermal_zone0:cpu-thermal" in s["zone_celsius"]

    def test_throttle_counters_summed_over_cpus(self, tree):
        tree.set_thermal_throttle(core=3, package=1)
        s = probe_for(tree).sample()["thermal"]
        assert s["core_throttle_count"] == 12
        assert s["package_throttle_count"] == 4

    def test_absent_counters_are_none_not_zero(self, tree):
        for cpu in range(4):
            tree.remove(
                "sys/devices/system/cpu/cpu%d/thermal_throttle/core_throttle_count" % cpu
            )
            tree.remove(
                "sys/devices/system/cpu/cpu%d/thermal_throttle/package_throttle_count" % cpu
            )
        sample = probe_for(tree).sample()
        assert sample["thermal"]["core_throttle_count"] is None
        assert any("thermal_throttle" in u for u in sample["unreadable"])


class TestRaspberryPi:
    def test_absent_vcgencmd_is_unavailable_not_clean(self, tree):
        sample = probe_for(tree).sample()
        assert sample["rpi"]["available"] is False
        assert any("vcgencmd" in u for u in sample["unreadable"])

    def test_all_clear(self, tree):
        s = probe_for(tree, vcgencmd=fake_vcgencmd(0x0)).sample()["rpi"]
        assert s["available"] is True
        assert s["live"] == [] and s["sticky"] == []

    def test_live_and_sticky_bits_decoded(self, tree):
        # 0x50005 = bits 0, 2, 16, 18: under-voltage now, throttled now, and the
        # matching since-boot sticky flags.
        s = probe_for(tree, vcgencmd=fake_vcgencmd(0x50005)).sample()["rpi"]
        assert s["value"] == 0x50005
        assert set(s["live"]) == {"under-voltage detected", "currently throttled"}
        assert set(s["sticky"]) == {"under-voltage has occurred", "throttling has occurred"}

    def test_sticky_only_means_no_live_throttle(self, tree):
        s = probe_for(tree, vcgencmd=fake_vcgencmd(0x80000)).sample()["rpi"]
        assert s["live"] == []
        assert s["sticky"] == ["soft temperature limit has occurred"]

    def test_arm_freq_capped_and_soft_temp_bits(self, tree):
        s = probe_for(tree, vcgencmd=fake_vcgencmd(0xA)).sample()["rpi"]
        assert set(s["live"]) == {"arm frequency capped", "soft temperature limit active"}

    def test_decimal_output_is_accepted(self, tree):
        s = probe_for(tree, vcgencmd=lambda: "throttled=5").sample()["rpi"]
        assert s["value"] == 5

    def test_garbage_output_is_unavailable_not_zero(self, tree):
        s = probe_for(tree, vcgencmd=lambda: "command not found").sample()["rpi"]
        assert s["available"] is False
        assert s["value"] is None


class TestFaults:
    def test_majflt_parsed_past_a_comm_containing_spaces_and_parens(self, tree):
        tree.set_majflt(11404)
        s = probe_for(tree).sample()["faults"]
        assert s["self_majflt"] == 11404
        assert s["vmstat_pgmajfault"] == 100

    def test_truncated_stat_line_is_none(self, tree):
        tree.write("proc/self/stat", "4242 (py) R 1\n")
        assert probe_for(tree).sample()["faults"]["self_majflt"] is None


class TestLoad:
    def test_loadavg_and_runnable(self, tree):
        tree.set_loadavg(runnable=5, total=200, load1=3.5)
        s = probe_for(tree).sample()["load"]
        assert s["loadavg_1m"] == 3.5
        assert s["runnable"] == 5
        # other_runnable excludes this process
        assert s["other_runnable"] == 4
        assert s["total_processes"] == 200

    def test_meminfo_converted_from_kb_to_bytes(self, tree):
        s = probe_for(tree).sample()["load"]
        assert s["mem_available_bytes"] == 6000000 * 1024
        assert s["mem_total_bytes"] == 8000000 * 1024


class TestAffinity:
    def test_read_from_proc_status_when_root_is_synthetic(self, tree):
        s = probe_for(tree).sample()["affinity"]
        assert s["affinity_cpus"] == [0, 1, 2, 3]
        assert s["affinity_count"] == 4

    def test_narrow_affinity(self, tree):
        tree.set_affinity("0-1")
        assert probe_for(tree).sample()["affinity"]["affinity_cpus"] == [0, 1]


class TestUnsupportedAndEmpty:
    def test_non_linux_short_circuits(self, tree):
        from benchguard.sources import Probe

        sample = Probe(root=tree.root, assume_linux=False).sample()
        assert sample["supported"] is False
        assert sample["unreadable"]
        assert "cgroup" not in sample

    def test_empty_root_reads_nothing_and_says_so(self, empty_tree):
        sample = probe_for(empty_tree).sample()
        assert sample["supported"] is True
        assert sample["cgroup"]["version"] is None
        assert sample["faults"]["self_majflt"] is None
        assert sample["load"]["loadavg_1m"] is None
        assert len(sample["unreadable"]) >= 4
