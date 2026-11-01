"""The pytest plugin: a benchmark test reported as invalid, not as slow.

Each case runs a nested pytest through the ``pytester`` fixture. The inner runs
set ``PYTEST_DISABLE_PLUGIN_AUTOLOAD`` and load the plugin explicitly with
``-p benchverdict.pytest_plugin``, so a case is not affected by whatever other
pytest plugins happen to be installed on the host, and is not affected by this
project's own ``filterwarnings = error``.
"""

from __future__ import annotations

import json
import os

import pytest
from conftest import HEALTHY, Tree


@pytest.fixture()
def plugin_tree(tmp_path) -> Tree:
    return Tree(str(tmp_path / "plugin-root"), HEALTHY)


@pytest.fixture()
def inner(pytester, monkeypatch):
    """Run the nested pytest in isolation from the host's plugin set."""
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")

    def run(*args: str):
        return pytester.runpytest(
            "-p", "benchverdict.pytest_plugin", "-W", "default", *args
        )


    return run


def make_test(pytester, body: str, marker: str = "") -> None:
    lines = "\n".join("    " + line for line in body.strip().splitlines())
    pytester.makepyfile(
        test_sample="import pytest\n\n%s\ndef test_measurement():\n%s\n" % (marker, lines)
    )


def throttle_source(tree: Tree) -> str:
    """Source that makes the synthetic machine start throttling mid-test."""
    path = os.path.join(tree.root, "sys", "fs", "cgroup", "cpu.stat")
    return (
        "with open(%r, 'w') as fh:\n"
        "    fh.write('nr_periods 200\\nnr_throttled 40\\nthrottled_usec 900000\\n')\n"
        % path
    )


class TestInertByDefault:
    def test_unmarked_test_is_not_guarded(self, pytester, inner, plugin_tree):
        make_test(pytester, "assert 1 + 1 == 2")
        result = inner("--benchverdict-assume-linux", "--benchverdict-root", plugin_tree.root)
        result.assert_outcomes(passed=1)
        assert "measurements judged" not in result.stdout.str()

    def test_marker_is_registered_so_strict_markers_is_satisfied(
        self, pytester, inner, plugin_tree
    ):
        make_test(pytester, "assert True", "@pytest.mark.benchverdict(min_duration=0.0)")
        result = inner("--strict-markers", "--benchverdict-assume-linux", "--benchverdict-root", plugin_tree.root)
        result.assert_outcomes(passed=1)


class TestMarkedTests:
    def test_healthy_machine_passes_and_is_summarised(self, pytester, inner, plugin_tree):
        make_test(pytester, "assert True", "@pytest.mark.benchverdict(min_duration=0.0)")
        result = inner("--benchverdict-assume-linux", "--benchverdict-root", plugin_tree.root)
        result.assert_outcomes(passed=1)
        result.stdout.fnmatch_lines(["*benchverdict * (measurement-provenance/1)*"])
        result.stdout.fnmatch_lines(["*measurements judged: PASS=1*"])

    def test_invalid_measurement_warns_but_passes_without_strict(
        self, pytester, inner, plugin_tree
    ):
        make_test(
            pytester,
            throttle_source(plugin_tree),
            "@pytest.mark.benchverdict(min_duration=0.0)",
        )
        result = inner("--benchverdict-assume-linux", "--benchverdict-root", plugin_tree.root, "-rw")
        result.assert_outcomes(passed=1)
        assert "BenchverdictWarning" in result.stdout.str()
        result.stdout.fnmatch_lines(["*measurements judged: INVALID=1*"])
        assert "CGROUP_THROTTLED" in result.stdout.str()

    def test_strict_fails_the_test_as_invalid_not_slow(self, pytester, inner, plugin_tree):
        make_test(
            pytester,
            throttle_source(plugin_tree),
            "@pytest.mark.benchverdict(min_duration=0.0)",
        )
        result = inner("--benchverdict-strict", "--benchverdict-assume-linux", "--benchverdict-root", plugin_tree.root)
        result.assert_outcomes(failed=1)
        assert "measurement INVALID, not merely slow" in result.stdout.str()
        assert "CGROUP_THROTTLED" in result.stdout.str()

    def test_marker_strict_overrides_the_command_line(self, pytester, inner, plugin_tree):
        make_test(
            pytester,
            throttle_source(plugin_tree),
            "@pytest.mark.benchverdict(min_duration=0.0, strict=True)",
        )
        result = inner("--benchverdict-assume-linux", "--benchverdict-root", plugin_tree.root)
        result.assert_outcomes(failed=1)

    def test_short_measurement_is_unknown_under_strict(self, pytester, inner, plugin_tree):
        make_test(pytester, "assert True", "@pytest.mark.benchverdict(min_duration=600.0)")
        result = inner("--benchverdict-strict", "--benchverdict-assume-linux", "--benchverdict-root", plugin_tree.root)
        result.assert_outcomes(failed=1)
        assert "measurement UNKNOWN, not merely slow" in result.stdout.str()
        assert "SHORT_DURATION" in result.stdout.str()

    def test_fail_on_warnings_opt_in(self, pytester, inner, plugin_tree):
        plugin_tree.set_governor("powersave")
        make_test(pytester, "assert True", "@pytest.mark.benchverdict(min_duration=0.0)")
        inner("--benchverdict-strict", "--benchverdict-assume-linux", "--benchverdict-root", plugin_tree.root).assert_outcomes(
            passed=1
        )
        failing = inner(
            "--benchverdict-strict",
            "--benchverdict-fail-on-warnings",
            "--benchverdict-assume-linux",
            "--benchverdict-root",
            plugin_tree.root,
        )
        failing.assert_outcomes(failed=1)
        assert "GOVERNOR_NOT_PERFORMANCE" in failing.stdout.str()


class TestOptions:
    def test_benchverdict_all_guards_unmarked_tests(self, pytester, inner, plugin_tree):
        make_test(pytester, "assert True")
        result = inner(
            "--benchverdict-all",
            "--benchverdict-min-duration",
            "0",
            "--benchverdict-assume-linux",
            "--benchverdict-root",
            plugin_tree.root,
        )
        result.assert_outcomes(passed=1)
        result.stdout.fnmatch_lines(["*measurements judged: PASS=1*"])

    def test_working_set_bytes_option_is_honoured(self, pytester, inner, plugin_tree):
        plugin_tree.set_mem_available(1000)
        make_test(pytester, "assert True")
        result = inner(
            "--benchverdict-all",
            "--benchverdict-min-duration",
            "0",
            "--benchverdict-working-set-bytes",
            "4000000000",
            "--benchverdict-assume-linux",
            "--benchverdict-root",
            plugin_tree.root,
            "-rw",
        )
        result.assert_outcomes(passed=1)
        assert "BenchverdictWarning" in result.stdout.str()
        assert "LOW_MEMORY_HEADROOM" in result.stdout.str()

    def test_json_output_is_an_array_of_envelopes(self, pytester, inner, plugin_tree):
        target = pytester.path / "envelopes.json"
        make_test(pytester, "assert True", "@pytest.mark.benchverdict(min_duration=0.0)")
        result = inner(
            "--benchverdict-json", str(target), "--benchverdict-assume-linux",
            "--benchverdict-root", plugin_tree.root
        )
        result.assert_outcomes(passed=1)
        payload = json.loads(target.read_text())
        assert isinstance(payload, list) and len(payload) == 1
        assert payload[0]["schema"] == "measurement-provenance/1"
        assert payload[0]["verdict"] == "PASS"
        assert payload[0]["measurement"]["label"].endswith("test_measurement")
        assert payload[0]["tool"]["name"] == "benchverdict"

    def test_unknown_marker_keyword_is_a_usage_error(self, pytester, inner, plugin_tree):
        make_test(pytester, "assert True", "@pytest.mark.benchverdict(nonsense=1)")
        result = inner("--benchverdict-assume-linux", "--benchverdict-root", plugin_tree.root)
        assert result.ret != 0
        assert "unknown @pytest.mark.benchverdict keyword" in (
            result.stdout.str() + result.stderr.str()
        )


class TestInteractionWithRealFailures:
    def test_a_failing_body_is_not_masked_by_a_verdict(self, pytester, inner, plugin_tree):
        make_test(
            pytester,
            "assert 1 == 2, 'the code under test is wrong'",
            "@pytest.mark.benchverdict(min_duration=0.0, strict=True)",
        )
        result = inner("--benchverdict-assume-linux", "--benchverdict-root", plugin_tree.root)
        result.assert_outcomes(failed=1)
        assert "the code under test is wrong" in result.stdout.str()
        assert "not merely slow" not in result.stdout.str()

    def test_a_failure_on_an_invalid_machine_is_annotated(self, pytester, inner, plugin_tree):
        body = throttle_source(plugin_tree) + "assert 1 == 2, 'timing assertion'\n"
        make_test(pytester, body, "@pytest.mark.benchverdict(min_duration=0.0, strict=True)")
        result = inner("--benchverdict-assume-linux", "--benchverdict-root", plugin_tree.root)
        result.assert_outcomes(failed=1)
        assert "may not be about the code under test" in result.stdout.str()

    def test_unsupported_platform_never_fails_a_test(self, pytester, inner):
        make_test(
            pytester,
            "assert True",
            "@pytest.mark.benchverdict(min_duration=600.0, strict=True, assume_linux=False)",
        )
        result = inner()
        result.assert_outcomes(passed=1)
        result.stdout.fnmatch_lines(["*measurements judged: UNSUPPORTED=1*"])


class TestReportProperties:
    def test_verdict_and_codes_are_attached_to_the_report(self, pytester, inner, plugin_tree):
        pytester.makeconftest(
            """
            def pytest_runtest_logreport(report):
                if report.when == 'call':
                    props = dict(report.user_properties)
                    print('VERDICT=' + props.get('benchverdict_verdict', 'none'))
                    print('CODES=' + props.get('benchverdict_codes', 'none'))
            """
        )
        make_test(pytester, "assert True", "@pytest.mark.benchverdict(min_duration=0.0)")
        result = inner("--benchverdict-assume-linux", "--benchverdict-root", plugin_tree.root, "-s")
        result.assert_outcomes(passed=1)
        assert "VERDICT=PASS" in result.stdout.str()
        assert "CODES=" in result.stdout.str()

    def test_the_envelope_is_attached_as_a_report_property(self, pytester, inner, plugin_tree):
        pytester.makeconftest(
            """
            import json

            def pytest_runtest_logreport(report):
                if report.when == 'call':
                    props = dict(report.user_properties)
                    env = json.loads(props['benchverdict_envelope'])
                    print('SCHEMA=' + env['schema'])
            """
        )
        make_test(pytester, "assert True", "@pytest.mark.benchverdict(min_duration=0.0)")
        result = inner("--benchverdict-assume-linux", "--benchverdict-root", plugin_tree.root, "-s")
        result.assert_outcomes(passed=1)
        assert "SCHEMA=measurement-provenance/1" in result.stdout.str()
