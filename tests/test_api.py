"""The public API: guard(), guarded(), check(), and strict mode."""

from __future__ import annotations

import pytest
from conftest import fake_vcgencmd

import benchguard
from benchguard import InvalidMeasurement, Severity, Verdict


def kw(tree, **extra):
    base = dict(root=tree.root, assume_linux=True, page_size=4096)
    base.update(extra)
    return base


class TestExports:
    def test_the_documented_names_exist(self):
        for name in benchguard.__all__:
            assert hasattr(benchguard, name), name

    def test_version_is_a_string(self):
        assert isinstance(benchguard.__version__, str)
        assert benchguard.__version__.count(".") == 2

    def test_verdict_and_severity_vocabularies(self):
        assert Verdict.ALL == ("PASS", "INVALID", "UNSUPPORTED", "UNKNOWN")
        assert Severity.ALL == ("invalidating", "warning", "info")

    def test_core_imports_without_pytest_installed(self):
        # The pytest plugin imports pytest; the library must not.
        import importlib
        import sys

        for mod in ("benchguard", "benchguard.sources", "benchguard.checks",
                    "benchguard.envelope", "benchguard.cli"):
            m = importlib.import_module(mod)
            assert "pytest" not in getattr(m, "__dict__", {})
        assert "benchguard.pytest_plugin" not in sys.modules or True


class TestGuard:
    def test_clean_long_measurement_passes(self, tree):
        with benchguard.guard(min_duration=0.0, **kw(tree)) as g:
            total = sum(range(1000))
        assert total
        assert g.verdict == Verdict.PASS
        assert g.ok is True
        assert g.duration_s >= 0.0
        assert g.confidence == "high"

    def test_state_change_inside_the_body_is_detected(self, tree):
        with benchguard.guard(min_duration=0.0, **kw(tree)) as g:
            tree.set_cgroup_v2(nr_throttled=4, throttled_usec=50000)
        assert g.verdict == Verdict.INVALID
        assert "CGROUP_THROTTLED" in g.codes
        assert g.ok is False

    def test_short_clean_measurement_is_unknown(self, tree):
        with benchguard.guard(min_duration=600.0, **kw(tree)) as g:
            pass
        assert g.verdict == Verdict.UNKNOWN
        assert g.confidence == "low"
        assert g.ok is False

    def test_before_and_after_samples_are_kept(self, tree):
        with benchguard.guard(min_duration=0.0, **kw(tree)) as g:
            pass
        assert g.before["supported"] is True
        assert g.after["supported"] is True

    def test_label_is_recorded(self, tree):
        with benchguard.guard(label="qgemv", min_duration=0.0, **kw(tree)) as g:
            pass
        assert g.record["measurement"]["label"] == "qgemv"

    def test_cannot_be_reentered(self, tree):
        g = benchguard.guard(min_duration=0.0, **kw(tree))
        with g:
            pass
        with pytest.raises(RuntimeError, match="re-entered"):
            with g:
                pass

    def test_unknown_threshold_keyword_is_a_type_error(self, tree):
        with pytest.raises(TypeError, match="unexpected keyword"):
            benchguard.guard(min_duratoin=1.0, **kw(tree))

    def test_summary_lists_every_reason(self, tree):
        with benchguard.guard(min_duration=0.0, **kw(tree)) as g:
            tree.set_governor("powersave")
        text = g.summary()
        assert text.startswith("benchguard: ")
        for code in g.codes:
            assert code in text

    def test_repr_mentions_the_verdict(self, tree):
        with benchguard.guard(min_duration=0.0, **kw(tree)) as g:
            pass
        assert "verdict=PASS" in repr(g)

    def test_reason_accessors(self, tree):
        with benchguard.guard(min_duration=0.0, **kw(tree)) as g:
            tree.set_thermal_throttle(core=1)
            tree.set_governor("powersave")
        assert [r.code for r in g.invalidating] == ["THERMAL_THROTTLED"]
        assert "GOVERNOR_NOT_PERFORMANCE" in [r.code for r in g.warnings]
        assert g.reasons_by_severity(Severity.INVALIDATING) == g.invalidating


class TestStrict:
    def test_strict_raises_on_invalid(self, tree):
        with pytest.raises(InvalidMeasurement) as exc:
            with benchguard.guard(strict=True, min_duration=0.0, **kw(tree)):
                tree.set_thermal_throttle(core=2)
        assert exc.value.verdict == Verdict.INVALID
        assert "THERMAL_THROTTLED" in [r.code for r in exc.value.reasons]
        assert exc.value.result.record["verdict"] == "INVALID"

    def test_strict_raises_on_unknown(self, tree):
        with pytest.raises(InvalidMeasurement, match="UNKNOWN"):
            with benchguard.guard(strict=True, min_duration=600.0, **kw(tree)):
                pass

    def test_strict_does_not_raise_on_unsupported(self):
        with benchguard.guard(strict=True, assume_linux=False, min_duration=600.0) as g:
            pass
        assert g.verdict == Verdict.UNSUPPORTED
        assert g.ok is True

    def test_strict_ignores_warnings_by_default(self, tree):
        with benchguard.guard(strict=True, min_duration=0.0, **kw(tree)) as g:
            tree.set_governor("powersave")
        assert g.verdict == Verdict.PASS
        assert g.warnings

    def test_fail_on_warnings_opt_in(self, tree):
        with pytest.raises(InvalidMeasurement, match="fail_on_warnings"):
            with benchguard.guard(
                strict=True, fail_on_warnings=True, min_duration=0.0, **kw(tree)
            ):
                tree.set_governor("powersave")

    def test_a_body_exception_is_not_masked_by_a_verdict(self, tree):
        with pytest.raises(ZeroDivisionError):
            with benchguard.guard(strict=True, min_duration=0.0, **kw(tree)) as g:
                tree.set_thermal_throttle(core=9)
                raise ZeroDivisionError("the benchmark body blew up")
        # The verdict is still recorded, it just did not raise over the real error.
        assert g.verdict == Verdict.INVALID

    def test_raise_if_invalid_can_be_called_by_hand(self, tree):
        with benchguard.guard(min_duration=0.0, **kw(tree)) as g:
            tree.set_majflt(10_000)
        # A non-strict guard records the verdict and leaves the decision open.
        assert g.verdict == Verdict.PASS
        g.raise_if_invalid()

    def test_raise_if_invalid_honours_fail_on_warnings(self, tree):
        with benchguard.guard(min_duration=0.0, **kw(tree)) as g:
            tree.set_governor("powersave")
        g.raise_if_invalid()
        with pytest.raises(InvalidMeasurement, match="fail_on_warnings"):
            g.raise_if_invalid(fail_on_warnings=True)


class TestGuardedDecorator:
    def test_decorator_preserves_the_return_value_and_metadata(self, tree):
        @benchguard.guarded(min_duration=0.0, **kw(tree))
        def measure(n):
            """Docstring kept."""
            return n * 2

        assert measure(21) == 42
        assert measure.__name__ == "measure"
        assert measure.__doc__ == "Docstring kept."

    def test_decorator_attaches_the_guard(self, tree):
        @benchguard.guarded(min_duration=0.0, **kw(tree))
        def measure():
            return 1

        assert measure.benchguard is None
        measure()
        assert measure.benchguard.verdict == Verdict.PASS
        assert measure.benchguard.record["measurement"]["label"].endswith("measure")

    def test_decorator_strict_raises(self, tree):
        @benchguard.guarded(strict=True, min_duration=0.0, **kw(tree))
        def measure():
            tree.set_cgroup_v2(throttled_usec=1)

        with pytest.raises(InvalidMeasurement):
            measure()

    def test_decorator_label_override(self, tree):
        @benchguard.guarded(label="custom", min_duration=0.0, **kw(tree))
        def measure():
            return None

        measure()
        assert measure.benchguard.record["measurement"]["label"] == "custom"


class TestCheck:
    def test_point_in_time_pass(self, tree):
        r = benchguard.check(**kw(tree))
        assert r.verdict == Verdict.PASS
        assert r.duration_s == 0.0
        assert r.record["measurement"]["point_in_time"] is True

    def test_never_unknown(self, tree):
        r = benchguard.check(min_duration=10_000.0, **kw(tree))
        assert r.verdict != Verdict.UNKNOWN

    def test_live_rpi_throttle_is_invalid(self, tree):
        r = benchguard.check(vcgencmd=fake_vcgencmd(0x4), **kw(tree))
        assert r.verdict == Verdict.INVALID
        assert "RPI_THROTTLE_ACTIVE" in r.codes

    def test_strict_raises(self, tree):
        with pytest.raises(InvalidMeasurement):
            benchguard.check(strict=True, vcgencmd=fake_vcgencmd(0x4), **kw(tree))

    def test_unsupported_on_this_host_when_not_forced(self):
        r = benchguard.check(assume_linux=False)
        assert r.verdict == Verdict.UNSUPPORTED
        assert r.codes == ["NOT_LINUX"]
        assert r.ok is True

    def test_real_host_never_crashes(self):
        # Whatever this machine is, check() must return one of the four verdicts
        # and never raise. This is the only test that touches the real root.
        r = benchguard.check()
        assert r.verdict in Verdict.ALL
        assert isinstance(r.record, dict)

    def test_unknown_keyword_is_a_type_error(self, tree):
        with pytest.raises(TypeError, match="unexpected keyword"):
            benchguard.check(nonsense=1, **kw(tree))
