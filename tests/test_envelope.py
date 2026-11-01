"""The measurement-provenance/1 envelope contract."""

from __future__ import annotations

import json
import re

import pytest
from conftest import probe_for

import benchverdict
from benchverdict.checks import REASON_CODES, Reason, Severity
from benchverdict.envelope import (
    SCHEMA_ID,
    build_envelope,
    load_schema,
    platform_block,
    schema_path,
    validate_envelope,
)

TOP_LEVEL_KEYS = [
    "schema",
    "tool",
    "taken_at_utc",
    "duration_s",
    "platform",
    "verdict",
    "reasons",
    "unreadable",
    "measurement",
]


def an_envelope(**kwargs):
    defaults = dict(
        verdict="PASS",
        reasons=[Reason("MAJOR_FAULTS_OBSERVED", Severity.INFO, "Seven faults.")],
        measurement={"anything": 1},
        duration_s=1.25,
        unreadable=["vcgencmd get_throttled"],
    )
    defaults.update(kwargs)
    return build_envelope(**defaults)


class TestShape:
    def test_schema_id(self):
        assert SCHEMA_ID == "measurement-provenance/1"
        assert benchverdict.SCHEMA_ID == SCHEMA_ID

    def test_exact_top_level_keys_in_order(self):
        assert list(an_envelope().keys()) == TOP_LEVEL_KEYS

    def test_tool_block(self):
        env = an_envelope()
        assert env["tool"] == {"name": "benchverdict", "version": benchverdict.__version__}

    def test_taken_at_utc_is_iso_8601_with_a_z(self):
        env = an_envelope()
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", env["taken_at_utc"]
        ), env["taken_at_utc"]

    def test_duration_is_a_float(self):
        assert isinstance(an_envelope(duration_s=3)["duration_s"], float)

    def test_platform_block_has_four_keys(self):
        assert set(platform_block()) == {"system", "release", "machine", "python"}
        assert re.fullmatch(r"\d+\.\d+\.\d+", platform_block()["python"])

    def test_reasons_are_dicts_not_dataclasses(self):
        reason = an_envelope()["reasons"][0]
        assert set(reason) == {"code", "severity", "detail"}
        assert isinstance(reason["code"], str)

    def test_measurement_carries_the_tool_specific_payload(self):
        assert an_envelope(measurement={"cgroup": {"version": 2}})["measurement"] == {
            "cgroup": {"version": 2}
        }

    def test_whole_envelope_is_json_serializable(self):
        json.dumps(an_envelope())

    def test_sibling_tool_name_can_be_overridden(self):
        env = build_envelope(
            verdict="PASS",
            reasons=[],
            measurement={},
            duration_s=1.0,
            tool_name="membw",
            tool_version="0.0.1",
        )
        assert env["tool"] == {"name": "membw", "version": "0.0.1"}
        assert env["schema"] == SCHEMA_ID
        assert list(env.keys()) == TOP_LEVEL_KEYS


class TestValidator:
    def test_a_good_envelope_has_no_problems(self):
        assert validate_envelope(an_envelope()) == []

    @pytest.mark.parametrize("key", TOP_LEVEL_KEYS)
    def test_each_missing_key_is_reported(self, key):
        env = an_envelope()
        del env[key]
        problems = validate_envelope(env)
        assert any(key in p for p in problems)

    def test_bad_verdict_rejected(self):
        env = an_envelope()
        env["verdict"] = "OK"
        assert any("verdict" in p for p in validate_envelope(env))

    def test_bad_severity_rejected(self):
        env = an_envelope()
        env["reasons"][0]["severity"] = "bad"
        assert any("severity" in p for p in validate_envelope(env))

    def test_lowercase_code_rejected(self):
        env = an_envelope()
        env["reasons"][0]["code"] = "cgroup_throttled"
        assert any("upper case" in p for p in validate_envelope(env))

    def test_wrong_schema_id_rejected(self):
        env = an_envelope()
        env["schema"] = "measurement-provenance/2"
        assert any("schema" in p for p in validate_envelope(env))

    def test_naive_timestamp_rejected(self):
        env = an_envelope()
        env["taken_at_utc"] = "2026-11-01T12:00:00"
        assert any("taken_at_utc" in p for p in validate_envelope(env))


class TestBundledSchema:
    def test_schema_file_ships_with_the_package(self):
        import os

        assert os.path.isfile(schema_path())
        assert schema_path().endswith("measurement-provenance-1.schema.json")

    def test_schema_parses_and_pins_the_contract(self):
        schema = load_schema()
        assert schema["title"] == "measurement-provenance/1"
        assert schema["properties"]["schema"]["const"] == SCHEMA_ID
        assert set(schema["required"]) == set(TOP_LEVEL_KEYS)
        assert schema["properties"]["verdict"]["enum"] == [
            "PASS",
            "INVALID",
            "UNSUPPORTED",
            "UNKNOWN",
        ]

    def test_schema_code_pattern_accepts_every_real_code(self):
        pattern = load_schema()["properties"]["reasons"]["items"]["properties"]["code"][
            "pattern"
        ]
        for code in REASON_CODES:
            assert re.fullmatch(pattern, code), code

    def test_jsonschema_agrees_when_a_2020_12_validator_is_installed(self):
        jsonschema = pytest.importorskip(
            "jsonschema", reason="jsonschema is not a dependency of benchverdict"
        )
        validator_cls = getattr(jsonschema, "Draft202012Validator", None)
        if validator_cls is None:
            pytest.skip(
                "installed jsonschema %s predates draft 2020-12, which the bundled "
                "schema declares" % getattr(jsonschema, "__version__", "?")
            )
        validator_cls.check_schema(load_schema())
        validator_cls(load_schema()).validate(an_envelope())

    def test_every_live_envelope_satisfies_the_schema_when_possible(self, tree):
        jsonschema = pytest.importorskip("jsonschema")
        validator_cls = getattr(jsonschema, "Draft202012Validator", None)
        if validator_cls is None:
            pytest.skip("installed jsonschema predates draft 2020-12")
        validator = validator_cls(load_schema())
        validator.validate(benchverdict.check(root=tree.root, assume_linux=True).record)
        validator.validate(benchverdict.check(assume_linux=False).record)


class TestLiveResults:
    def test_guard_record_is_the_envelope(self, tree):
        with benchverdict.guard(
            root=tree.root, assume_linux=True, min_duration=0.0
        ) as g:
            pass
        assert g.record is g.to_envelope()
        assert g.record == g.envelope()
        assert list(g.record.keys()) == TOP_LEVEL_KEYS
        assert validate_envelope(g.record) == []

    def test_check_result_envelope_validates(self, tree):
        result = benchverdict.check(root=tree.root, assume_linux=True)
        assert validate_envelope(result.to_envelope()) == []
        assert result.record["duration_s"] == 0.0

    def test_unsupported_envelope_validates(self):
        result = benchverdict.check(assume_linux=False)
        assert result.verdict == "UNSUPPORTED"
        assert validate_envelope(result.to_envelope()) == []

    def test_unreadable_sources_land_in_the_envelope(self, empty_tree):
        result = benchverdict.check(root=empty_tree.root, assume_linux=True)
        assert result.record["unreadable"]
        assert all(isinstance(u, str) and u for u in result.record["unreadable"])

    def test_label_is_recorded_under_measurement(self, tree):
        result = benchverdict.check(root=tree.root, assume_linux=True, label="decode-7B")
        assert result.record["measurement"]["label"] == "decode-7B"

    def test_to_json_round_trips(self, tree):
        result = benchverdict.check(root=tree.root, assume_linux=True)
        assert json.loads(result.to_json()) == result.record

    def test_every_emitted_envelope_on_a_sick_machine_validates(self, tree):
        from benchverdict.checks import Thresholds, evaluate

        probe = probe_for(tree)
        before = probe.sample()
        tree.set_cgroup_v2(nr_throttled=5, throttled_usec=9999, cpu_max="50000 100000")
        tree.set_thermal_throttle(core=4)
        tree.set_majflt(50000)
        after = probe.sample()
        result = evaluate(before, after, 0.01, Thresholds(max_major_faults=1))
        env = build_envelope(
            verdict=result["verdict"],
            reasons=result["reasons"],
            measurement=result["measurement"],
            duration_s=0.01,
            unreadable=result["unreadable"],
        )
        assert env["verdict"] == "INVALID"
        assert validate_envelope(env) == []
        json.dumps(env)
