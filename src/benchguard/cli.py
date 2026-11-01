"""Command line interface.

Subcommands
-----------
``check``   read machine state now and print a verdict
``run``     run a command under a guard and judge the interval around it
``schema``  print the bundled measurement-provenance/1 JSON Schema
``codes``   print the whole reason code vocabulary

Exit codes
----------
0   PASS, or UNSUPPORTED, or any verdict at all when --strict is absent
1   INVALID, with --strict
2   UNKNOWN, with --strict
3   warnings present, with --strict and --fail-on-warnings
64  usage error
Any other code from ``run`` is the wrapped command's own exit code, which
always takes precedence: a benchmark that crashed is reported as a crash, not
as an invalid measurement.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence

from . import check as _check
from . import guard as _guard
from ._version import SCHEMA_ID, __version__
from .checks import REASON_CODES, Severity, Verdict
from .envelope import load_schema

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_UNKNOWN = 2
EXIT_WARNINGS = 3
EXIT_USAGE = 64


def _add_threshold_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--min-duration",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Minimum measured interval. Below this benchguard reports UNKNOWN "
        "rather than a confident PASS (default: 1.0).",
    )
    p.add_argument(
        "--working-set-bytes",
        type=int,
        default=None,
        metavar="BYTES",
        help="Declared resident working set. Enables the memory-headroom and "
        "major-fault-ratio checks.",
    )
    p.add_argument(
        "--max-major-faults",
        type=int,
        default=None,
        metavar="N",
        help="Hard ceiling on major page faults during the measurement. Without "
        "it the fault delta is reported but never invalidates.",
    )
    p.add_argument(
        "--max-major-fault-ratio",
        type=float,
        default=0.10,
        metavar="FRACTION",
        help="Major faults as a fraction of the declared working set above which "
        "a warning fires (default: 0.10).",
    )
    p.add_argument(
        "--min-freq-ratio",
        type=float,
        default=0.90,
        metavar="FRACTION",
        help="scaling_cur_freq / cpuinfo_max_freq below which a warning fires "
        "(default: 0.90).",
    )
    p.add_argument(
        "--max-other-runnable",
        type=int,
        default=0,
        metavar="N",
        help="Other runnable processes tolerated before COMPETING_LOAD fires "
        "(default: 0).",
    )
    p.add_argument(
        "--max-thermal-rise-c",
        type=float,
        default=10.0,
        metavar="CELSIUS",
        help="Allowed rise in the hottest thermal zone (default: 10.0).",
    )
    p.add_argument(
        "--memory-headroom",
        type=float,
        default=1.10,
        metavar="MULTIPLIER",
        help="Headroom multiplier applied to --working-set-bytes (default: 1.10).",
    )
    p.add_argument(
        "--competing-load-invalidates",
        action="store_true",
        help="Treat competing runnable processes as invalidating rather than a warning.",
    )
    p.add_argument(
        "--quota-invalidates",
        action="store_true",
        help="Treat the presence of a cgroup CPU quota as invalidating rather than "
        "a warning.",
    )
    p.add_argument(
        "--root",
        default="/",
        metavar="PATH",
        help="Filesystem root to read sysfs and procfs under. For testing against "
        "a synthetic tree (default: /).",
    )
    p.add_argument(
        "--assume-linux",
        action="store_true",
        help="Force the Linux readers on. Only useful together with --root.",
    )


def _add_output_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--json",
        action="store_true",
        help="Print the measurement-provenance/1 envelope as JSON on stdout "
        "instead of the human summary.",
    )
    p.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help="Also write the envelope as JSON to FILE.",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on INVALID (1) or UNKNOWN (2). Default is to report "
        "and exit 0.",
    )
    p.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help="With --strict, also exit 3 when any warning-severity reason is present.",
    )
    p.add_argument(
        "--label",
        default=None,
        metavar="TEXT",
        help="Free-text label recorded in the provenance.",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the human summary. The envelope is still written by "
        "--json and --output.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchguard",
        description=(
            "Decide whether a performance measurement was valid, and say why it "
            "was not. benchguard samples Linux cgroup, cpufreq, thermal, paging "
            "and load state around a measurement and emits a "
            "measurement-provenance/1 envelope carrying the verdict."
        ),
        epilog="Exit codes: 0 ok, 1 INVALID, 2 UNKNOWN, 3 warnings, 64 usage.",
    )
    parser.add_argument("--version", action="version", version="benchguard %s" % __version__)
    sub = parser.add_subparsers(dest="command")

    p_check = sub.add_parser(
        "check",
        help="Read machine state now and judge the state alone.",
        description="Point-in-time read. Returns PASS, INVALID or UNSUPPORTED, "
        "never UNKNOWN, because it makes no claim about an interval.",
    )
    _add_threshold_args(p_check)
    _add_output_args(p_check)

    p_run = sub.add_parser(
        "run",
        help="Run a command under a guard and judge the interval around it.",
        description="Everything after -- is the command to run. Its own exit code "
        "takes precedence over the verdict exit code.",
    )
    _add_threshold_args(p_run)
    _add_output_args(p_run)
    p_run.add_argument(
        "argv",
        nargs=argparse.REMAINDER,
        help="The command to run, after --.",
    )

    sub.add_parser(
        "schema",
        help="Print the bundled measurement-provenance/1 JSON Schema.",
    )
    p_codes = sub.add_parser(
        "codes",
        help="Print the reason code vocabulary.",
    )
    p_codes.add_argument("--json", action="store_true", help="Print as JSON.")
    return parser


def _threshold_kwargs(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "min_duration": args.min_duration,
        "working_set_bytes": args.working_set_bytes,
        "max_major_faults": args.max_major_faults,
        "max_major_fault_ratio": args.max_major_fault_ratio,
        "min_freq_ratio": args.min_freq_ratio,
        "max_other_runnable": args.max_other_runnable,
        "max_thermal_rise_c": args.max_thermal_rise_c,
        "memory_headroom": args.memory_headroom,
        "competing_load_invalidates": args.competing_load_invalidates,
        "quota_invalidates": args.quota_invalidates,
    }


def _emit(result: Any, args: argparse.Namespace, stream: Any) -> None:
    envelope = result.to_envelope()
    if args.json:
        stream.write(json.dumps(envelope, indent=2) + "\n")
    elif not args.quiet:
        stream.write(result.summary() + "\n")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(envelope, fh, indent=2)
            fh.write("\n")


def _verdict_exit(result: Any, args: argparse.Namespace) -> int:
    if not args.strict:
        return EXIT_OK
    if result.verdict == Verdict.INVALID:
        return EXIT_INVALID
    if result.verdict == Verdict.UNKNOWN:
        return EXIT_UNKNOWN
    if args.fail_on_warnings and result.warnings:
        return EXIT_WARNINGS
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None, stream: Any = None) -> int:
    parser = build_parser()
    raw = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(raw)
    if args.command is None:
        # No subcommand given: "benchguard" means "benchguard check". Re-parse
        # under the check subparser so its defaults are populated rather than
        # absent from the namespace.
        args = parser.parse_args(["check"] + raw)
    out = stream if stream is not None else sys.stdout
    command = args.command

    if command == "schema":
        out.write(json.dumps(load_schema(), indent=2) + "\n")
        return EXIT_OK

    if command == "codes":
        rows = [
            {"code": code, "severity": sev, "meaning": doc}
            for code, (sev, doc) in sorted(
                REASON_CODES.items(),
                key=lambda kv: (
                    {Severity.INVALIDATING: 0, Severity.WARNING: 1, Severity.INFO: 2}[kv[1][0]],
                    kv[0],
                ),
            )
        ]
        if getattr(args, "json", False):
            out.write(json.dumps({"schema": SCHEMA_ID, "codes": rows}, indent=2) + "\n")
        else:
            width = max(len(r["code"]) for r in rows)
            for r in rows:
                out.write(
                    "%-*s  %-13s  %s\n" % (width, r["code"], r["severity"], r["meaning"])
                )
        return EXIT_OK

    kwargs = _threshold_kwargs(args)

    if command == "check":
        result = _check(
            label=args.label,
            root=args.root,
            assume_linux=True if args.assume_linux else None,
            **kwargs
        )
        _emit(result, args, out)
        return _verdict_exit(result, args)

    if command == "run":
        # argparse.REMAINDER keeps the separating "--" as the first token. Strip
        # only that one, so a command that legitimately contains "--" survives.
        cmd: List[str] = list(args.argv or [])
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        if not cmd:
            parser.error("run needs a command, for example: benchguard run --strict -- pytest")
            return EXIT_USAGE
        g = _guard(
            label=args.label or " ".join(cmd),
            root=args.root,
            assume_linux=True if args.assume_linux else None,
            **kwargs
        )
        with g:
            try:
                proc = subprocess.run(cmd, check=False)
                child_rc = proc.returncode
            except OSError as exc:
                sys.stderr.write("benchguard: could not run %r: %s\n" % (cmd[0], exc))
                child_rc = 127
        _emit(g, args, out)
        if child_rc != 0:
            # The command's own failure is the more important fact.
            return child_rc
        return _verdict_exit(g, args)

    parser.print_help(out)
    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
