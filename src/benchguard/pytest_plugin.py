"""pytest plugin: fail a benchmark test because it was invalid, not because it was slow.

A timing assertion that fails under a CPU quota tells you the wrong thing: it
reports a slow function when what happened was that the kernel took the CPU
away. This plugin wraps the call phase of a test in a :func:`benchguard.guard`
and, when the machine state invalidates the interval, fails the test with the
reason codes attached, so the report says INVALID rather than slow.

Enable it per test with the marker::

    @pytest.mark.benchguard(min_duration=1.0, working_set_bytes=1_114_112_000)
    def test_decode_latency(benchmark):
        ...

or for every test in a run with ``--benchguard-all``. Without either, the
plugin is inert.

Options
-------
``--benchguard-all``
    Guard every test, not only marked ones.
``--benchguard-strict``
    Fail a test whose measurement is INVALID or UNKNOWN. Without it the
    verdict is reported as a warning and a terminal summary section.
``--benchguard-fail-on-warnings``
    With ``--benchguard-strict``, also fail on warning-severity reasons.
``--benchguard-min-duration SECONDS``
    Default minimum measured interval (default 1.0).
``--benchguard-working-set-bytes BYTES``
    Default declared working set.
``--benchguard-json FILE``
    Write one measurement-provenance/1 envelope per guarded test to FILE as a
    JSON array.
``--benchguard-root PATH``
    Filesystem root for the state readers. For the plugin's own tests.
"""

from __future__ import annotations

import json
import warnings
from typing import Any, Dict, List, Optional

import pytest

from . import Guard, Verdict, guard
from ._version import SCHEMA_ID, __version__

MARKER = "benchguard"

_MARKER_KEYS = (
    "min_duration",
    "working_set_bytes",
    "max_major_faults",
    "max_major_fault_ratio",
    "min_freq_ratio",
    "max_other_runnable",
    "max_thermal_rise_c",
    "memory_headroom",
    "competing_load_invalidates",
    "quota_invalidates",
)


class BenchguardWarning(UserWarning):
    """Issued when a guarded test was not valid and strict mode is off."""


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("benchguard", "machine-state validity for benchmarks")
    group.addoption(
        "--benchguard-all",
        action="store_true",
        default=False,
        help="Guard every test, not only those marked with @pytest.mark.benchguard.",
    )
    group.addoption(
        "--benchguard-strict",
        action="store_true",
        default=False,
        help="Fail a test whose measurement is INVALID or UNKNOWN.",
    )
    group.addoption(
        "--benchguard-fail-on-warnings",
        action="store_true",
        default=False,
        help="With --benchguard-strict, also fail on warning-severity reasons.",
    )
    group.addoption(
        "--benchguard-min-duration",
        action="store",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Default minimum measured interval below which the verdict is UNKNOWN.",
    )
    group.addoption(
        "--benchguard-working-set-bytes",
        action="store",
        type=int,
        default=None,
        metavar="BYTES",
        help="Default declared resident working set.",
    )
    group.addoption(
        "--benchguard-json",
        action="store",
        default=None,
        metavar="FILE",
        help="Write one envelope per guarded test to FILE as a JSON array.",
    )
    group.addoption(
        "--benchguard-root",
        action="store",
        default="/",
        metavar="PATH",
        help="Filesystem root for the state readers (testing aid).",
    )
    group.addoption(
        "--benchguard-assume-linux",
        action="store_true",
        default=False,
        help="Force the Linux state readers on. Only useful together with "
        "--benchguard-root, to exercise the readers against a synthetic tree.",
    )


def pytest_configure(config: Any) -> None:
    config.addinivalue_line(
        "markers",
        "benchguard(**thresholds): validate the machine state around this test and "
        "report the measurement as INVALID rather than slow when the state "
        "contaminated it. Accepts any benchguard.Thresholds field, plus strict=True.",
    )
    config._benchguard_records = []  # type: ignore[attr-defined]


def _options(config: Any, marker: Optional[Any]) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "min_duration": config.getoption("--benchguard-min-duration"),
        "working_set_bytes": config.getoption("--benchguard-working-set-bytes"),
        "root": config.getoption("--benchguard-root"),
    }
    if config.getoption("--benchguard-assume-linux"):
        opts["assume_linux"] = True
    strict = bool(config.getoption("--benchguard-strict"))
    fail_on_warnings = bool(config.getoption("--benchguard-fail-on-warnings"))
    if marker is not None:
        for key, value in (marker.kwargs or {}).items():
            if key == "strict":
                strict = bool(value)
            elif key == "fail_on_warnings":
                fail_on_warnings = bool(value)
            elif key in _MARKER_KEYS or key in ("root", "assume_linux", "vcgencmd", "page_size"):
                opts[key] = value
            else:
                raise pytest.UsageError(
                    "unknown @pytest.mark.benchguard keyword %r. Valid: %s, strict, "
                    "fail_on_warnings, root, assume_linux, vcgencmd, page_size"
                    % (key, ", ".join(_MARKER_KEYS))
                )
    return {"guard_kwargs": opts, "strict": strict, "fail_on_warnings": fail_on_warnings}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: Any) -> Any:
    marker = item.get_closest_marker(MARKER)
    if marker is None and not item.config.getoption("--benchguard-all"):
        yield
        return

    opts = _options(item.config, marker)
    g: Guard = guard(label=item.nodeid, strict=False, **opts["guard_kwargs"])

    with g:
        outcome = yield

    envelope = g.to_envelope()
    item.config._benchguard_records.append(envelope)  # type: ignore[attr-defined]
    item.user_properties.append(("benchguard_verdict", g.verdict))
    item.user_properties.append(("benchguard_codes", ",".join(g.codes)))
    item.user_properties.append(("benchguard_envelope", json.dumps(envelope)))

    body_failed = outcome.excinfo is not None

    if body_failed:
        # Never mask the test's own failure with a verdict. Annotate instead, so
        # a timing assertion that failed on a throttled machine is readable as
        # such in the report.
        if g.verdict == Verdict.INVALID:
            item.add_report_section(
                "call",
                "benchguard",
                "This test failed on a machine benchguard judged INVALID, so the "
                "failure may not be about the code under test.\n" + g.summary(),
            )
        return

    if g.verdict == Verdict.UNSUPPORTED:
        return

    should_fail = opts["strict"] and (
        g.verdict in (Verdict.INVALID, Verdict.UNKNOWN)
        or (opts["fail_on_warnings"] and g.warnings)
    )
    if should_fail:
        pytest.fail(
            "benchguard: measurement %s, not merely slow.\n%s" % (g.verdict, g.summary()),
            pytrace=False,
        )
    elif g.verdict != Verdict.PASS or g.warnings:
        warnings.warn(BenchguardWarning(g.summary()), stacklevel=1)
        item.add_report_section("call", "benchguard", g.summary())


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int = 0, config: Any = None) -> None:
    cfg = config if config is not None else getattr(terminalreporter, "config", None)
    records: List[Dict[str, Any]] = list(getattr(cfg, "_benchguard_records", []) or [])
    if not records:
        return
    counts: Dict[str, int] = {}
    for rec in records:
        counts[rec["verdict"]] = counts.get(rec["verdict"], 0) + 1
    terminalreporter.write_sep("=", "benchguard %s (%s)" % (__version__, SCHEMA_ID))
    terminalreporter.write_line(
        "measurements judged: "
        + ", ".join("%s=%d" % (k, counts[k]) for k in sorted(counts))
    )
    for rec in records:
        if rec["verdict"] == Verdict.PASS and not rec["reasons"]:
            continue
        label = (rec.get("measurement") or {}).get("label", "(unlabelled)")
        terminalreporter.write_line("  %-11s %s" % (rec["verdict"], label))
        for reason in rec["reasons"]:
            terminalreporter.write_line(
                "      [%s] %s" % (reason["severity"], reason["code"])
            )
    if cfg is not None:
        path = cfg.getoption("--benchguard-json", default=None)
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(records, fh, indent=2)
                fh.write("\n")
            terminalreporter.write_line("  envelopes written to %s" % path)
