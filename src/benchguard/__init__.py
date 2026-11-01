"""benchguard: decide whether a performance measurement was valid.

benchguard samples the machine *around* a measurement and returns a verdict
with a documented reason, plus a provenance record of the conditions the number
was taken under. It is a precheck and a postcheck on the machine, not a
comparison of results.

Public API, stable within 0.x minor releases and within 1.x:

    benchguard.guard(...)         context manager, verdict over an interval
    benchguard.guarded(...)       the same thing as a decorator
    benchguard.check(...)         point-in-time machine state read
    benchguard.Guard              what guard() yields
    benchguard.CheckResult        what check() returns
    benchguard.Verdict            PASS / INVALID / UNSUPPORTED / UNKNOWN
    benchguard.Severity           invalidating / warning / info
    benchguard.Reason             code + severity + detail
    benchguard.Thresholds         every tunable with its default
    benchguard.REASON_CODES       the whole reason vocabulary
    benchguard.InvalidMeasurement raised by strict mode
    benchguard.SCHEMA_ID          "measurement-provenance/1"
    benchguard.schema_path()      path to the bundled JSON Schema
    benchguard.build_envelope()   assemble an envelope by hand
    benchguard.validate_envelope() dependency-free envelope check

Example
-------
>>> import benchguard
>>> with benchguard.guard(min_duration=1.0) as g:   # doctest: +SKIP
...     run_the_benchmark()
>>> g.verdict                                        # doctest: +SKIP
'PASS'
"""

from __future__ import annotations

import functools
import json
import time
from typing import Any, Callable, Dict, List, Optional, TypeVar

from ._version import SCHEMA_ID, __version__
from .checks import (
    REASON_CODES,
    Reason,
    Severity,
    Thresholds,
    Verdict,
    evaluate,
    evaluate_point_in_time,
)
from .envelope import (
    build_envelope,
    load_schema,
    platform_block,
    schema_path,
    validate_envelope,
)
from .sources import Probe, is_linux

__all__ = [
    "__version__",
    "SCHEMA_ID",
    "guard",
    "guarded",
    "check",
    "Guard",
    "CheckResult",
    "Verdict",
    "Severity",
    "Reason",
    "Thresholds",
    "REASON_CODES",
    "InvalidMeasurement",
    "Probe",
    "is_linux",
    "build_envelope",
    "validate_envelope",
    "platform_block",
    "schema_path",
    "load_schema",
]

F = TypeVar("F", bound=Callable[..., Any])

_THRESHOLD_KEYS = (
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


class InvalidMeasurement(RuntimeError):
    """Raised in strict mode when a measurement is not valid.

    The offending result is attached as ``.result`` so a caller can inspect the
    verdict, the reasons and the full provenance record.
    """

    def __init__(self, message: str, result: "_ResultBase") -> None:
        super().__init__(message)
        self.result = result

    @property
    def verdict(self) -> str:
        return self.result.verdict

    @property
    def reasons(self) -> List[Reason]:
        return self.result.reasons


class _ResultBase:
    """Shared surface of a guard result and a point-in-time check result."""

    def __init__(
        self,
        verdict: str,
        reasons: List[Reason],
        measurement: Dict[str, Any],
        duration_s: float,
        unreadable: List[str],
        label: Optional[str] = None,
    ) -> None:
        self.verdict = verdict
        self.reasons = reasons
        self.duration_s = duration_s
        self.unreadable = unreadable
        self.label = label
        if label is not None:
            measurement = dict(measurement)
            measurement["label"] = label
        self._measurement = measurement
        self.record: Dict[str, Any] = build_envelope(
            verdict=verdict,
            reasons=reasons,
            measurement=measurement,
            duration_s=duration_s,
            unreadable=unreadable,
        )

    # -- the envelope -----------------------------------------------------
    def to_envelope(self) -> Dict[str, Any]:
        """The ``measurement-provenance/1`` envelope for this result.

        ``result.record`` is the same dict. Both names are supported: ``record``
        reads naturally as provenance, ``to_envelope()`` reads naturally as a
        contract boundary.
        """
        return self.record

    #: Alias, for callers who prefer a noun.
    def envelope(self) -> Dict[str, Any]:
        return self.record

    def to_json(self, indent: Optional[int] = 2) -> str:
        """The envelope as JSON text."""
        return json.dumps(self.record, indent=indent, sort_keys=False)

    # -- convenience ------------------------------------------------------
    @property
    def ok(self) -> bool:
        """True when the verdict is PASS or UNSUPPORTED.

        UNSUPPORTED counts as ok on purpose: benchguard must not fail a job for
        running on a platform it cannot read.
        """
        return self.verdict in (Verdict.PASS, Verdict.UNSUPPORTED)

    @property
    def codes(self) -> List[str]:
        """Just the reason codes, in severity order."""
        return [r.code for r in self.reasons]

    def reasons_by_severity(self, severity: str) -> List[Reason]:
        return [r for r in self.reasons if r.severity == severity]

    @property
    def invalidating(self) -> List[Reason]:
        return self.reasons_by_severity(Severity.INVALIDATING)

    @property
    def warnings(self) -> List[Reason]:
        return self.reasons_by_severity(Severity.WARNING)

    def summary(self) -> str:
        """One line per reason, prefixed by the verdict."""
        head = "benchguard: %s (%.4f s)" % (self.verdict, self.duration_s)
        if not self.reasons:
            return head + "\n  no findings"
        return "\n".join([head] + ["  " + str(r) for r in self.reasons])

    def raise_if_invalid(self, fail_on_warnings: bool = False) -> None:
        """Raise :class:`InvalidMeasurement` when this result should fail a job.

        INVALID always raises. UNKNOWN raises, because benchguard could not
        establish validity and a silent pass would be a false assurance.
        UNSUPPORTED never raises. Warnings raise only when asked.
        """
        if self.verdict == Verdict.INVALID:
            raise InvalidMeasurement(
                "measurement is INVALID: %s"
                % "; ".join("%s (%s)" % (r.code, r.detail) for r in self.invalidating),
                self,
            )
        if self.verdict == Verdict.UNKNOWN:
            raise InvalidMeasurement(
                "measurement validity is UNKNOWN: %s"
                % "; ".join("%s (%s)" % (r.code, r.detail) for r in self.reasons),
                self,
            )
        if fail_on_warnings and self.warnings:
            raise InvalidMeasurement(
                "measurement carries warnings and fail_on_warnings is set: %s"
                % "; ".join("%s (%s)" % (r.code, r.detail) for r in self.warnings),
                self,
            )

    def __repr__(self) -> str:
        return "<%s verdict=%s duration_s=%.4f reasons=%s>" % (
            type(self).__name__,
            self.verdict,
            self.duration_s,
            self.codes,
        )


class CheckResult(_ResultBase):
    """What :func:`check` returns: a point-in-time read of machine state."""


class Guard(_ResultBase):
    """Context manager that samples around a measurement and judges it.

    Before the body runs, nothing is known. Inside the body the attributes are
    not yet populated. After the body, ``verdict``, ``reasons``, ``record`` and
    ``duration_s`` are set.

    The guard deliberately does not suppress an exception raised by the body,
    and does not raise its own strict error on top of one, so an exception from
    the benchmark is never masked by a verdict.
    """

    def __init__(
        self,
        thresholds: Thresholds,
        strict: bool = False,
        fail_on_warnings: bool = False,
        probe: Optional[Probe] = None,
        label: Optional[str] = None,
    ) -> None:
        self._thresholds = thresholds
        self._strict = strict
        self._fail_on_warnings = fail_on_warnings
        self._probe = probe or Probe()
        self._label = label
        self._entered = False
        self.before: Optional[Dict[str, Any]] = None
        self.after: Optional[Dict[str, Any]] = None
        self.verdict = Verdict.UNKNOWN
        self.reasons: List[Reason] = []
        self.duration_s = 0.0
        self.unreadable: List[str] = []
        self.record: Dict[str, Any] = {}
        self.label = label

    @property
    def confidence(self) -> str:
        """``high``, ``low`` or ``unknown``.

        ``low`` means the measured interval was shorter than ``min_duration``,
        so a PASS would not have been well founded. benchguard reports UNKNOWN
        in that case instead of a confident verdict.
        """
        if not self.record:
            return "unknown"
        return (self.record.get("measurement") or {}).get("confidence", "unknown")

    def __enter__(self) -> "Guard":
        if self._entered:
            raise RuntimeError("a benchguard Guard cannot be re-entered")
        self._entered = True
        self.before = self._probe.sample()
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration = time.perf_counter() - getattr(self, "_t0", time.perf_counter())
        self.after = self._probe.sample()
        result = evaluate(self.before or {}, self.after, duration, self._thresholds)
        _ResultBase.__init__(
            self,
            verdict=result["verdict"],
            reasons=result["reasons"],
            measurement=result["measurement"],
            duration_s=duration,
            unreadable=result.get("unreadable") or [],
            label=self._label,
        )
        if self._strict and exc_type is None:
            self.raise_if_invalid(fail_on_warnings=self._fail_on_warnings)
        return False


def _thresholds_from(kwargs: Dict[str, Any]) -> Thresholds:
    known = {k: kwargs.pop(k) for k in list(kwargs) if k in _THRESHOLD_KEYS}
    return Thresholds(**known)


def guard(
    strict: bool = False,
    fail_on_warnings: bool = False,
    label: Optional[str] = None,
    root: str = "/",
    assume_linux: Optional[bool] = None,
    vcgencmd: Optional[Callable[[], Optional[str]]] = None,
    page_size: Optional[int] = None,
    **threshold_kwargs: Any,
) -> Guard:
    """Return a context manager that judges the measurement inside it.

    Parameters
    ----------
    strict:
        Raise :class:`InvalidMeasurement` on exit when the verdict is INVALID
        or UNKNOWN. Default False, which reports and does not raise.
    fail_on_warnings:
        With ``strict``, also raise when any warning-severity reason is present.
    label:
        Free-text name recorded in the provenance under ``measurement.label``.
    root, assume_linux, vcgencmd, page_size:
        Passed to :class:`benchguard.sources.Probe`. ``root`` exists so the
        library can be tested against a synthetic sysfs tree; production callers
        leave it alone.
    **threshold_kwargs:
        Any field of :class:`Thresholds`: ``min_duration``,
        ``working_set_bytes``, ``max_major_faults``, ``max_major_fault_ratio``,
        ``min_freq_ratio``, ``max_other_runnable``, ``max_thermal_rise_c``,
        ``memory_headroom``, ``competing_load_invalidates``,
        ``quota_invalidates``.

    Examples
    --------
    >>> import benchguard
    >>> with benchguard.guard(min_duration=1.0,
    ...                       working_set_bytes=1_114_112_000) as g:  # doctest: +SKIP
    ...     latency = run_model()
    >>> g.verdict, g.codes                                            # doctest: +SKIP
    ('PASS', ['MAJOR_FAULTS_OBSERVED'])
    """
    unknown = set(threshold_kwargs) - set(_THRESHOLD_KEYS)
    if unknown:
        raise TypeError(
            "guard() got unexpected keyword argument(s): %s. Valid thresholds: %s"
            % (", ".join(sorted(unknown)), ", ".join(_THRESHOLD_KEYS))
        )
    thresholds = _thresholds_from(dict(threshold_kwargs))
    probe = Probe(root=root, assume_linux=assume_linux, vcgencmd=vcgencmd, page_size=page_size)
    return Guard(
        thresholds=thresholds,
        strict=strict,
        fail_on_warnings=fail_on_warnings,
        probe=probe,
        label=label,
    )


def guarded(
    strict: bool = False,
    fail_on_warnings: bool = False,
    label: Optional[str] = None,
    root: str = "/",
    assume_linux: Optional[bool] = None,
    vcgencmd: Optional[Callable[[], Optional[str]]] = None,
    page_size: Optional[int] = None,
    **threshold_kwargs: Any,
) -> Callable[[F], F]:
    """Decorator form of :func:`guard`.

    The wrapped function gains a ``benchguard`` attribute holding the
    :class:`Guard` from its most recent call, so a non-strict caller can inspect
    the verdict after the fact without changing the function's return value.

    >>> import benchguard
    >>> @benchguard.guarded(min_duration=1.0, strict=True)             # doctest: +SKIP
    ... def measure_decode():
    ...     return run_model()
    >>> measure_decode()                                               # doctest: +SKIP
    >>> measure_decode.benchguard.verdict                              # doctest: +SKIP
    'PASS'
    """

    def decorate(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            g = guard(
                strict=strict,
                fail_on_warnings=fail_on_warnings,
                label=label if label is not None else getattr(fn, "__qualname__", None),
                root=root,
                assume_linux=assume_linux,
                vcgencmd=vcgencmd,
                page_size=page_size,
                **threshold_kwargs,
            )
            with g:
                out = fn(*args, **kwargs)
            wrapper.benchguard = g  # type: ignore[attr-defined]
            return out

        wrapper.benchguard = None  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorate


def check(
    strict: bool = False,
    fail_on_warnings: bool = False,
    label: Optional[str] = None,
    root: str = "/",
    assume_linux: Optional[bool] = None,
    vcgencmd: Optional[Callable[[], Optional[str]]] = None,
    page_size: Optional[int] = None,
    **threshold_kwargs: Any,
) -> CheckResult:
    """Read machine state right now and judge the state alone.

    A point-in-time check cannot see a delta, so it never reports
    CGROUP_THROTTLED, THERMAL_THROTTLED or a fault count for an interval, and
    it never returns UNKNOWN for a short duration, because it makes no claim
    about any interval. It does return UNKNOWN when not one state source could
    be read, since no evidence of a problem is not evidence of no problem. Use
    it as a precheck before a long run, or to record the machine's
    configuration.

    >>> import benchguard
    >>> r = benchguard.check()
    >>> r.verdict in ('PASS', 'INVALID', 'UNSUPPORTED')
    True
    """
    unknown = set(threshold_kwargs) - set(_THRESHOLD_KEYS)
    if unknown:
        raise TypeError(
            "check() got unexpected keyword argument(s): %s. Valid thresholds: %s"
            % (", ".join(sorted(unknown)), ", ".join(_THRESHOLD_KEYS))
        )
    thresholds = _thresholds_from(dict(threshold_kwargs))
    probe = Probe(root=root, assume_linux=assume_linux, vcgencmd=vcgencmd, page_size=page_size)
    sample = probe.sample()
    result = evaluate_point_in_time(sample, thresholds)
    out = CheckResult(
        verdict=result["verdict"],
        reasons=result["reasons"],
        measurement=result["measurement"],
        duration_s=0.0,
        unreadable=result.get("unreadable") or [],
        label=label,
    )
    if strict:
        out.raise_if_invalid(fail_on_warnings=fail_on_warnings)
    return out
