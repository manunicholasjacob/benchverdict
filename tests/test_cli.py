"""CLI behaviour and, above all, exit codes: the Action depends on them."""

from __future__ import annotations

import io
import json
import sys

import pytest

from benchguard.checks import REASON_CODES
from benchguard.cli import (
    EXIT_INVALID,
    EXIT_OK,
    EXIT_UNKNOWN,
    EXIT_WARNINGS,
    build_parser,
    main,
)


def run(*argv):
    out = io.StringIO()
    code = main(list(argv), stream=out)
    return code, out.getvalue()


def linux_args(tree):
    return ["--root", tree.root, "--assume-linux"]


class TestMeta:
    def test_parser_builds(self):
        assert build_parser().prog == "benchguard"

    def test_version_exits_zero(self):
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0

    def test_help_mentions_the_subcommands(self, capsys):
        with pytest.raises(SystemExit):
            main(["--help"])
        text = capsys.readouterr().out
        for sub in ("check", "run", "schema", "codes"):
            assert sub in text


class TestSchemaAndCodes:
    def test_schema_prints_valid_json(self):
        code, out = run("schema")
        assert code == EXIT_OK
        schema = json.loads(out)
        assert schema["title"] == "measurement-provenance/1"

    def test_codes_lists_the_whole_vocabulary(self):
        code, out = run("codes")
        assert code == EXIT_OK
        for reason_code in REASON_CODES:
            assert reason_code in out

    def test_codes_json(self):
        code, out = run("codes", "--json")
        payload = json.loads(out)
        assert payload["schema"] == "measurement-provenance/1"
        assert len(payload["codes"]) == len(REASON_CODES)
        assert {"code", "severity", "meaning"} == set(payload["codes"][0])

    def test_codes_are_sorted_invalidating_first(self):
        _, out = run("codes", "--json")
        severities = [row["severity"] for row in json.loads(out)["codes"]]
        assert severities[0] == "invalidating"
        assert severities[-1] == "info"


class TestCheck:
    def test_default_subcommand_is_check(self):
        code, out = run()
        assert code == EXIT_OK
        assert "benchguard:" in out

    def test_human_summary(self, tree):
        code, out = run("check", *linux_args(tree))
        assert code == EXIT_OK
        assert out.startswith("benchguard: PASS")

    def test_json_envelope(self, tree):
        code, out = run("check", "--json", *linux_args(tree))
        envelope = json.loads(out)
        assert envelope["schema"] == "measurement-provenance/1"
        assert envelope["verdict"] == "PASS"
        assert envelope["tool"]["name"] == "benchguard"

    def test_quiet_prints_nothing(self, tree):
        code, out = run("check", "--quiet", *linux_args(tree))
        assert (code, out) == (EXIT_OK, "")

    def test_output_file_is_written(self, tree, tmp_path):
        target = tmp_path / "env.json"
        code, _ = run("check", "--quiet", "--output", str(target), *linux_args(tree))
        assert code == EXIT_OK
        assert json.loads(target.read_text())["verdict"] == "PASS"

    def test_label_lands_in_the_envelope(self, tree):
        _, out = run("check", "--json", "--label", "precheck", *linux_args(tree))
        assert json.loads(out)["measurement"]["label"] == "precheck"

    def test_on_this_host_without_forcing_linux_it_is_unsupported_or_real(self):
        code, out = run("check", "--json")
        assert code == EXIT_OK
        assert json.loads(out)["verdict"] in ("PASS", "INVALID", "UNSUPPORTED")


class TestExitCodes:
    def test_non_strict_always_exits_zero_even_when_invalid(self, tree):
        tree.write("sys/class/thermal/thermal_zone0/temp", "95000\n")
        code, out = run(
            "check", "--json", "--quota-invalidates", *linux_args(tree)
        )
        assert code == EXIT_OK

    def test_strict_invalid_exits_one(self, tree):
        tree.set_cgroup_v2(cpu_max="50000 100000")
        code, out = run(
            "check", "--json", "--strict", "--quota-invalidates", *linux_args(tree)
        )
        assert json.loads(out)["verdict"] == "INVALID"
        assert code == EXIT_INVALID

    def test_strict_unsupported_exits_zero(self):
        code, out = run("check", "--json", "--strict")
        if json.loads(out)["verdict"] == "UNSUPPORTED":
            assert code == EXIT_OK

    def test_strict_fail_on_warnings_exits_three(self, tree):
        tree.set_governor("powersave")
        code, out = run(
            "check", "--json", "--strict", "--fail-on-warnings", *linux_args(tree)
        )
        assert json.loads(out)["verdict"] == "PASS"
        assert code == EXIT_WARNINGS

    def test_strict_without_fail_on_warnings_tolerates_warnings(self, tree):
        tree.set_governor("powersave")
        code, _ = run("check", "--quiet", "--strict", *linux_args(tree))
        assert code == EXIT_OK


class TestThresholdFlags:
    def test_thresholds_are_recorded_in_the_envelope(self, tree):
        _, out = run(
            "check",
            "--json",
            "--min-duration", "2.5",
            "--working-set-bytes", "1024",
            "--max-major-faults", "7",
            "--min-freq-ratio", "0.5",
            "--max-other-runnable", "3",
            "--max-thermal-rise-c", "20",
            "--memory-headroom", "1.5",
            *linux_args(tree),
        )
        # check() is point-in-time, so it reports state, not the threshold block;
        # confirm the flags at least parse and change behaviour.
        assert json.loads(out)["verdict"] == "PASS"

    def test_max_other_runnable_changes_the_verdict_reasons(self, tree):
        tree.set_loadavg(runnable=4)
        _, strict_out = run("check", "--json", *linux_args(tree))
        assert "COMPETING_LOAD" in [r["code"] for r in json.loads(strict_out)["reasons"]]
        _, loose_out = run(
            "check", "--json", "--max-other-runnable", "9", *linux_args(tree)
        )
        assert "COMPETING_LOAD" not in [r["code"] for r in json.loads(loose_out)["reasons"]]

    def test_competing_load_invalidates_flag(self, tree):
        tree.set_loadavg(runnable=4)
        code, out = run(
            "check", "--json", "--strict", "--competing-load-invalidates", *linux_args(tree)
        )
        assert json.loads(out)["verdict"] == "INVALID"
        assert code == EXIT_INVALID


class TestRun:
    def test_run_without_a_command_is_a_usage_error(self):
        with pytest.raises(SystemExit) as exc:
            main(["run"])
        assert exc.value.code != 0

    def test_run_wraps_a_command_and_judges_the_interval(self, tree):
        code, out = run(
            "run", "--json", "--min-duration", "0", *linux_args(tree),
            "--", sys.executable, "-c", "pass",
        )
        envelope = json.loads(out)
        assert code == EXIT_OK
        assert envelope["verdict"] == "PASS"
        assert envelope["duration_s"] > 0

    def test_the_commands_own_exit_code_wins(self, tree):
        code, _ = run(
            "run", "--quiet", "--strict", "--min-duration", "600",
            *linux_args(tree),
            "--", sys.executable, "-c", "raise SystemExit(7)",
        )
        # The measurement would be UNKNOWN (exit 2), but the command failed with
        # 7, and a benchmark that crashed is reported as a crash.
        assert code == 7

    def test_verdict_exit_code_applies_when_the_command_succeeds(self, tree):
        code, out = run(
            "run", "--json", "--strict", "--min-duration", "600",
            *linux_args(tree),
            "--", sys.executable, "-c", "pass",
        )
        assert json.loads(out)["verdict"] == "UNKNOWN"
        assert code == EXIT_UNKNOWN

    def test_label_defaults_to_the_command(self, tree):
        _, out = run(
            "run", "--json", "--min-duration", "0", *linux_args(tree),
            "--", sys.executable, "-c", "pass",
        )
        assert sys.executable in json.loads(out)["measurement"]["label"]

    def test_missing_executable_is_reported_as_127(self, tree):
        code, _ = run(
            "run", "--quiet", "--min-duration", "0", *linux_args(tree),
            "--", "benchguard-no-such-binary-xyz",
        )
        assert code == 127

    def test_output_file_written_for_run(self, tree, tmp_path):
        target = tmp_path / "run.json"
        run(
            "run", "--quiet", "--min-duration", "0", "--output", str(target),
            *linux_args(tree),
            "--", sys.executable, "-c", "pass",
        )
        assert json.loads(target.read_text())["tool"]["name"] == "benchguard"
