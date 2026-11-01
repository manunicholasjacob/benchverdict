# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Within 0.x, the public API named in the `benchguard` package docstring and the
reason code vocabulary are treated as stable across minor releases; anything not
named there may change.

## [Unreleased]

## [0.1.0] - 2026-11-01

### Added

- `guard()` context manager that samples machine state before and after a
  measurement, judges the interval, and in strict mode raises
  `InvalidMeasurement` rather than letting a contaminated number pass.
- `guarded()` decorator, the same behaviour wrapped around a function, with the
  most recent `Guard` left on the function as a `benchguard` attribute.
- `check()` point-in-time read that judges machine configuration alone. It
  never returns UNKNOWN for a short duration, because it makes no claim about
  an interval, but it does return UNKNOWN when not one state source could be
  read.
- The `measurement-provenance/1` JSON envelope: a documented outer contract
  carrying the verdict, the reason list, the platform block, the measured
  duration and the list of sources that could not be read, with every
  tool-specific field under `measurement`. The authoritative JSON Schema is
  bundled in the package as `benchguard/schema/measurement-provenance-1.schema.json`
  and is reachable at runtime through `schema_path()` and `load_schema()`.
  `validate_envelope()` checks an envelope against the contract without
  requiring a JSON Schema library.
- The reason code vocabulary: SCREAMING_SNAKE codes, each with one of three
  severities (invalidating, warning, info) and a documented meaning, exposed as
  `REASON_CODES` and printable with `benchguard codes`.
- cgroup v2 and cgroup v1 CPU detection: throttled-time deltas over the
  measurement (`CGROUP_THROTTLED`) and the presence of a CPU bandwidth quota
  (`CGROUP_QUOTA_SET`), with the quota promoted from warning to invalidating on
  request.
- cpufreq checks: the scaling governor on every CPU
  (`GOVERNOR_NOT_PERFORMANCE`) and the ratio of current frequency to the
  advertised maximum (`FREQ_BELOW_MAX`).
- x86 thermal checks: core and package throttle counter deltas
  (`THERMAL_THROTTLED`) and thermal zone temperatures, including the rise of
  the hottest zone across the measurement (`THERMAL_RISE`).
- Raspberry Pi support: `vcgencmd get_throttled` bitmask decoding, separating
  live throttle, frequency cap, under-voltage and soft temperature limit bits
  (`RPI_THROTTLE_ACTIVE`) from the sticky since-boot bits
  (`RPI_THROTTLE_STICKY`).
- Paging checks: major page fault deltas over the measurement, reported as
  information when no ceiling was declared (`MAJOR_FAULTS_OBSERVED`), as a
  warning when they are a high fraction of a declared working set
  (`MAJOR_FAULT_RATIO_HIGH`), and as invalidating when they exceed a declared
  ceiling (`MAJOR_FAULTS_EXCEEDED`).
- Competing-load and memory checks: other runnable processes on the machine
  (`COMPETING_LOAD`) and `MemAvailable` against the declared working set plus a
  headroom multiplier (`LOW_MEMORY_HEADROOM`).
- cpuset and affinity checks: an effective cpuset narrower than the machine
  (`CPUSET_RESTRICTED`), a change to the set of CPUs the process may run on
  during the measurement (`AFFINITY_CHANGED`), and a change in the number of
  visible CPUs (`CPU_COUNT_CHANGED`).
- Short-interval handling: a measurement shorter than `min_duration` yields
  UNKNOWN with `SHORT_DURATION` instead of a confident PASS, and unreadable
  state sources yield `SOURCES_UNREADABLE` so that silence is never read as
  evidence of a clean machine.
- Blind-run handling: when not one state source was readable on a platform that
  reports itself as Linux, the verdict is UNKNOWN with `NO_SOURCES` rather than
  PASS, because no evidence of a problem is not evidence of no problem.
- `jsonschema` in the `dev` extra as a test-only dependency, so the bundled
  schema and the emitted envelopes are checked against each other by a real
  validator. benchguard itself imports nothing outside the standard library.
- Command line interface with four subcommands: `check` (read state now),
  `run -- <cmd>` (judge the interval around a command), `schema` (print the
  bundled JSON Schema) and `codes` (print the reason vocabulary). Exit codes
  are 0 for ok, 1 for INVALID, 2 for UNKNOWN, 3 for warnings under
  `--fail-on-warnings`, and 64 for a usage error; for `run`, the wrapped
  command's own non-zero exit code takes precedence, so a crashed benchmark is
  reported as a crash and not as an invalid measurement.
- pytest plugin, registered through the `pytest11` entry point, which guards
  marked tests, attaches the verdict, the reason codes and the envelope to the
  test's user properties, and can fail a test whose measurement was invalid.
- Composite GitHub Action at the repository root, so a workflow can run a
  benchmark command under benchguard, publish the verdict and reason codes as
  step outputs and a job summary, upload the provenance envelope as an
  artifact, and fail the job when the measurement was not valid.
- Zero runtime dependencies on Python 3.9 and newer. The core library imports
  with no third-party package installed; pytest is needed only for the plugin
  and the test suite.

[Unreleased]: https://github.com/manunicholasjacob/benchguard/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/manunicholasjacob/benchguard/releases/tag/v0.1.0

## Note on 0.1.0

0.1.0 is not yet published to PyPI, and the `v0.1.0` git tag does not yet
exist, so the compare and release links above will not resolve until the
release steps in `RELEASE_CHECKLIST.md` have been carried out. The date above
is the date the 0.1.0 contents were finalized, not a publication date.
