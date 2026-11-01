"""pytest plugin: fail a benchmark test because it was invalid, not because it was slow.

A timing assertion that fails under a CPU quota tells you the wrong thing: it
reports a slow function when what happened was that the kernel took the CPU
away. This plugin wraps the call phase of a test in a :func:`benchverdict.guard`
and, when the machine state invalidates the interval, fails the test with the
reason codes attached, so the report says INVALID rather than slow.

Enable it per test with the marker::

    @pytest.mark.benchverdict(min_duration=1.0, working_set_bytes=1_114_112_000)
    def test_decode_latency(benchmark):
        ...

or for every test in a run with ``--benchverdict-all``. Without either, the
plugin is inert.

Options
-------
``--benchverdict-all``
    Guard every test, not only marked ones.
``--benchverdict-strict``
    Fail a test whose measurement is INVALID or UNKNOWN. Without it the
    verdict is reported as a warning and a terminal summary section.
``--benchverdict-fail-on-warnings``
    With ``--benchverdict-strict``, also fail on warning-severity reasons.
``--benchverdict-min-duration SECONDS``
    Default minimum measured interval (default 1.0).
``--benchverdict-working-set-bytes BYTES``
    Default declared working set.
``--benchverdict-json FILE``
    Write one measurement-provenance/1 envelope per guarded test to FILE as a
    JSON array.
``--benchverdict-root PATH``
    Filesystem root for the state readers. For the plugin's own tests.
"""

from __future__ import annotations

import json
import warnings
from typing import Any, Dict, List, Optional

import pytest

from . import Guard, Verdict, guard
from ._version import SCHEMA_ID, __version__

MARKER = "benchverdict"

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


class BenchverdictWarning(UserWarning):
    """Issued when a guarded test was not valid and strict mode is off."""


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("benchverdict", "machine-state validity for benchmarks")
    group.addoption(
        "--benchverdict-all",
        action="store_true",
        default=False,
        help="Guard every test, not only those marked with @pytest.mark.benchverdict.",
    )
    group.addoption(
        "--benchverdict-strict",
        action="store_true",
        default=False,
        help="Fail a test whose measurement is INVALID or UNKNOWN.",
    )
    group.addoption(
        "--benchverdict-fail-on-warnings",
        action="store_true",
        default=False,
        help="With --benchverdict-strict, also fail on warning-severity reasons.",
    )
    group.addoption(
        "--benchverdict-min-duration",
        action="store",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Default minimum measured interval below which the verdict is UNKNOWN.",
    )
    group.addoption(
        "--benchverdict-working-set-bytes",
        action="store",
        type=int,
        default=None,
        metavar="BYTES",
        help="Default declared resident working set.",
    )
    group.addoption(
        "--benchverdict-json",
        action="store",
        default=None,
        metavar="FILE",
        help="Write one envelope per guarded test to FILE as a JSON array.",
    )
    group.addoption(
        "--benchverdict-root",
        action="store",
        default="/",
        metavar="PATH",
        help="Filesystem root for the state readers (testing aid).",
    )
    group.addoption(
        "--benchverdict-assume-linux",
        action="store_true",
        default=False,
        help="Force the Linux state readers on. Only useful together with "
        "--benchverdict-root, to exercise the readers against a synthetic tree.",
    )


def pytest_configure(config: Any) -> None:
    config.addinivalue_line(
        "markers",
        "benchverdict(**thresholds): validate the machine state around this test and "
        "report the measurement as INVALID rather than slow when the state "
        "contaminated it. Accepts any benchverdict.Thresholds field, plus strict=True.",
    )
    config._benchverdict_records = []  # type: ignore[attr-defined]


def _options(config: Any, marker: Optional[Any]) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "min_duration": config.getoption("--benchverdict-min-duration"),
        "working_set_bytes": config.getoption("--benchverdict-working-set-bytes"),
        "root": config.getoption("--benchverdict-root"),
    }
    if config.getoption("--benchverdict-assume-linux"):
        opts["assume_linux"] = True
    strict = bool(config.getoption("--benchverdict-strict"))
    fail_on_warnings = bool(config.getoption("--benchverdict-fail-on-warnings"))
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
                    "unknown @pytest.mark.benchverdict keyword %r. Valid: %s, strict, "
                    "fail_on_warnings, root, assume_linux, vcgencmd, page_size"
                    % (key, ", ".join(_MARKER_KEYS))
                )
    return {"guard_kwargs": opts, "strict": strict, "fail_on_warnings": fail_on_warnings}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: Any) -> Any:
    marker = item.get_closest_marker(MARKER)
    if marker is None and not item.config.getoption("--benchverdict-all"):
        yield
        return

    opts = _options(item.config, marker)
    g: Guard = guard(label=item.nodeid, strict=False, **opts["guard_kwargs"])

    with g:
        outcome = yield

    envelope = g.to_envelope()
    item.config._benchverdict_records.append(envelope)  # type: ignore[attr-defined]
    item.user_properties.append(("benchverdict_verdict", g.verdict))
    item.user_properties.append(("benchverdict_codes", ",".join(g.codes)))
    item.user_properties.append(("benchverdict_envelope", json.dumps(envelope)))

    body_failed = outcome.excinfo is not None

    if body_failed:
        # Never mask the test's own failure with a verdict. Annotate instead, so
        # a timing assertion that failed on a throttled machine is readable as
        # such in the report.
        if g.verdict == Verdict.INVALID:
            item.add_report_section(
                "call",
                "benchverdict",
                "This test failed on a machine benchverdict judged INVALID, so the "
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
            "benchverdict: measurement %s, not merely slow.\n%s" % (g.verdict, g.summary()),
            pytrace=False,
        )
    elif g.verdict != Verdict.PASS or g.warnings:
        warnings.warn(BenchverdictWarning(g.summary()), stacklevel=1)
        item.add_report_section("call", "benchverdict", g.summary())


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int = 0, config: Any = None) -> None:
    cfg = config if config is not None else getattr(terminalreporter, "config", None)
    records: List[Dict[str, Any]] = list(getattr(cfg, "_benchverdict_records", []) or [])
    if not records:
        return
    counts: Dict[str, int] = {}
    for rec in records:
        counts[rec["verdict"]] = counts.get(rec["verdict"], 0) + 1
    terminalreporter.write_sep("=", "benchverdict %s (%s)" % (__version__, SCHEMA_ID))
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
        path = cfg.getoption("--benchverdict-json", default=None)
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(records, fh, indent=2)
                fh.write("\n")
            terminalreporter.write_line("  envelopes written to %s" % path)
