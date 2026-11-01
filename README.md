# benchverdict

Decide whether a performance measurement was valid, and fail the job when it was not.

benchverdict samples the machine *around* a measurement, as deltas rather than a
before-snapshot, and returns one of four verdicts with a documented reason code,
plus a provenance record of the conditions the number was taken under. It is a
library, a pytest plugin, a GitHub Action and a CLI, all over the same small API.

It answers a different question from a benchmark runner. A runner asks "how fast
was it". benchverdict asks "was the machine in a state where that number means
anything", and says which reading made it decide.

```
benchverdict: INVALID (2.0041 s)
  [invalidating] CGROUP_THROTTLED: cgroup v2 CPU bandwidth control denied this process
    CPU time during the measurement: throttled time rose by 812345 usec, throttled
    periods rose by 37 periods (0.4053 of the measured wall time). Measured from
    /sys/fs/cgroup/cpu.stat as a delta across the measured interval.
  [warning] CGROUP_QUOTA_SET: cgroup v2 CPU quota is 50000 usec per 100000 usec
    period (0.5 CPU equivalent). ...
```

Status: version 0.1.0, not yet published to PyPI and not yet tagged. There are no
users, no downloads and no citations to report.

## Why it exists

Three measurement failures from the author's own work are the reason each family
of checks is here. They are cited as prior findings, not as anything benchverdict
measured:

- A CPU quota costs about 50 percent on Arm against about 64 percent on x86
  (M. N. Jacob, "Quota Not the Container", CCGrid 2027 submission). A run under a
  quota is therefore not comparable with one taken without a quota, and the
  difference is not a constant you can correct for across architectures.
- Failing to check the process table before measuring on Android produced a
  32 percent measurement error and forced a public retraction (M. N. Jacob,
  Galaxy M21 benchmarking).
- A Raspberry Pi 5 run was proven DRAM-resident by a major-fault delta of 11,404
  against a 272,000-page model, rather than assumed to be resident.

The third one is the shape of the whole tool: the claim was not "the model fit in
memory", it was "here is the fault counter delta that shows it did".

## Install

Nothing is published yet, so install from a checkout:

```sh
git clone https://github.com/manunicholasjacob/benchverdict
cd benchverdict
python -m pip install -e .
```

Zero runtime dependencies. Python 3.9 or newer. The pytest plugin needs pytest,
which is an optional extra (`pip install -e ".[pytest]"`); the library itself
imports nothing outside the standard library.

Once 0.1.0 is on PyPI the install becomes `pip install benchverdict`, and the
`[project.urls]` and `CITATION.cff` in this repo already point at the right
places for that.

## Usage

### 1. Around a block of code

```python
import benchverdict

with benchverdict.guard(min_duration=1.0, working_set_bytes=1_114_112_000) as g:
    latency_ms = run_decode_benchmark()

print(g.verdict)        # 'PASS' | 'INVALID' | 'UNSUPPORTED' | 'UNKNOWN'
print(g.codes)          # ['MAJOR_FAULTS_OBSERVED']
print(g.summary())      # one line per finding, with the numbers in it
record = g.to_envelope()  # JSON-serializable provenance, == g.record
```

Default behaviour is to report, not to raise. Pass `strict=True` to raise
`benchverdict.InvalidMeasurement` when the verdict is INVALID or UNKNOWN:

```python
with benchverdict.guard(min_duration=1.0, strict=True):
    latency_ms = run_decode_benchmark()
```

The decorator form behaves the same way and leaves the guard on the function:

```python
@benchverdict.guarded(min_duration=1.0, working_set_bytes=1_114_112_000)
def measure_decode():
    return run_decode_benchmark()

measure_decode()
measure_decode.benchverdict.verdict
```

For a precheck before a long run, or just to record the configuration, read the
state at a point in time:

```python
state = benchverdict.check()
if state.verdict == 'INVALID':
    raise SystemExit(state.summary())
```

### 2. In pytest

The plugin wraps the call phase of a test and reports the measurement as
**invalid** rather than as slow. A timing assertion that fails under a CPU quota
is otherwise indistinguishable from a function that got slower.

```python
import pytest

@pytest.mark.benchverdict(min_duration=1.0, working_set_bytes=1_114_112_000)
def test_decode_latency(benchmark):
    result = benchmark(run_decode)
    assert result.stats.mean < 0.040
```

```sh
pytest --benchverdict-strict --benchverdict-json provenance.json
```

Without the marker and without `--benchverdict-all`, the plugin is inert. With
`--benchverdict-strict` an INVALID or UNKNOWN measurement fails the test with the
reason codes attached. Without it, the verdict is a warning plus a terminal
summary section. A test that fails on its own is never masked by a verdict: the
real failure is reported, and the verdict is attached as a note.

Options: `--benchverdict-all`, `--benchverdict-strict`,
`--benchverdict-fail-on-warnings`, `--benchverdict-min-duration`,
`--benchverdict-working-set-bytes`, `--benchverdict-json`, `--benchverdict-root`,
`--benchverdict-assume-linux`.

### 3. As a GitHub Action

`action.yml` is at the repo root, so the Action is usable by reference once the
tag exists:

```yaml
- uses: manunicholasjacob/benchverdict@v0.1.0
  with:
    command: python bench/decode.py --iterations 2000
    strict: 'true'
    min-duration: '1.0'
    working-set-bytes: '1114112000'
```

The Action runs the command under a guard, writes the provenance envelope to
`benchverdict.json`, uploads it as a build artifact, puts the verdict and reason
codes in `$GITHUB_STEP_SUMMARY`, exposes `verdict` and `codes` as step outputs,
and fails the job on an invalid measurement when `strict` is true.

### Also: the CLI

```sh
benchverdict check --json                 # point-in-time state, as an envelope
benchverdict run --strict -- ./bench.sh   # guard a command
benchverdict codes                        # the whole reason vocabulary
benchverdict schema                       # the bundled JSON Schema
```

Exit codes: `0` ok, `1` INVALID, `2` UNKNOWN, `3` warnings present (with
`--strict --fail-on-warnings`), `64` usage error. Without `--strict` the CLI
reports and exits 0. For `benchverdict run`, the wrapped command's own non-zero
exit code takes precedence, because a benchmark that crashed should be reported
as a crash and not as an invalid measurement.

## Verdicts

| Verdict | Meaning | Does `--strict` fail on it |
| --- | --- | --- |
| `PASS` | No invalidating condition found, and the interval was long enough to say so. | No |
| `INVALID` | At least one invalidating condition was observed during the measurement. | Yes, exit 1 |
| `UNKNOWN` | Nothing invalidating was found, but the evidence does not support a confident PASS: the interval was shorter than `min_duration`, or not one state source could be read. | Yes, exit 2 |
| `UNSUPPORTED` | This platform exposes none of the sources benchverdict reads, so no verdict is offered. | No, by design |

`UNSUPPORTED` never fails a job. benchverdict must not break a Windows or macOS CI
leg for being Windows or macOS.

`UNKNOWN` exists because of a real failure mode. nucliweb/webperf-snippets#312
(merged 24 Oct 2026) recorded CPU throttle ratios of 1.53 on `main` and 0.92 on a
pull request that changes no code, against a gate of 2.0, over a few milliseconds
of work; locally the same test ranged 0.85 to 3.68. Raising the workload to about
20 ms settled the ratio into 3.74 to 4.02. A confident verdict over a few
milliseconds would be a guess dressed as a finding, so benchverdict reports low
confidence instead.

## Reason codes

Codes are the stable API surface. They are SCREAMING_SNAKE, never given a new
meaning within a major version, and each carries one severity. Branch on `code`,
never on `detail`: the sentence includes the measured numbers and its wording is
not part of the contract.

| Code | Severity | Meaning |
| --- | --- | --- |
| `AFFINITY_CHANGED` | invalidating | The set of CPUs this process may run on changed during the measurement. |
| `CGROUP_THROTTLED` | invalidating | cgroup CPU throttled time rose during the measurement. |
| `CPU_COUNT_CHANGED` | invalidating | The number of visible CPUs changed during the measurement. |
| `MAJOR_FAULTS_EXCEEDED` | invalidating | Major page faults during the measurement exceeded the caller's max_major_faults. |
| `RPI_THROTTLE_ACTIVE` | invalidating | Raspberry Pi reported a live throttle, frequency cap, under-voltage or soft temperature limit during the measurement. |
| `THERMAL_THROTTLED` | invalidating | An x86 core or package thermal throttle counter rose during the measurement. |
| `CGROUP_QUOTA_SET` | warning | A cgroup CPU bandwidth quota is in force, so this number is not comparable with one taken without a quota. |
| `COMPETING_LOAD` | warning | Other runnable processes were present, so the measurement shared the machine. |
| `CPUSET_RESTRICTED` | warning | The effective cpuset is narrower than the set of CPUs on the machine. |
| `FREQ_BELOW_MAX` | warning | At least one CPU reported a current frequency below the configured fraction of its maximum. |
| `GOVERNOR_NOT_PERFORMANCE` | warning | At least one CPU is not using the performance frequency governor. |
| `LOW_MEMORY_HEADROOM` | warning | MemAvailable was below the declared working set plus headroom, so the working set may not have stayed resident. |
| `MAJOR_FAULT_RATIO_HIGH` | warning | Major page faults during the measurement were a high fraction of the declared working set, so part of the work may have been served from storage. |
| `RPI_THROTTLE_STICKY` | warning | Raspberry Pi sticky flags show throttling, capping or under-voltage has occurred since boot, though not necessarily during this measurement. |
| `SHORT_DURATION` | warning | The measured interval was shorter than min_duration, so throttle ratios and fault counts are not stable enough to support a confident verdict. |
| `SOURCES_UNREADABLE` | warning | One or more state sources could not be read, so absence of a problem is not evidence of its absence. |
| `THERMAL_RISE` | warning | The hottest thermal zone rose by more than the allowed amount during the measurement. |
| `MAJOR_FAULTS_OBSERVED` | info | Major page faults occurred during the measurement. Reported without judgement because no max_major_faults was declared. |
| `NOT_LINUX` | info | benchverdict reads Linux sysfs and procfs. On any other platform it reports UNSUPPORTED rather than guessing. |
| `NO_SOURCES` | info | No machine-state source could be read at all, so no verdict is possible. |

`benchverdict codes --json` prints this table from the same table the code uses, so
the two cannot drift.

Two severities are configurable, because the right answer depends on what you are
measuring: `quota_invalidates=True` promotes `CGROUP_QUOTA_SET` to invalidating,
and `competing_load_invalidates=True` promotes `COMPETING_LOAD`. Both are off by
default, because plenty of people benchmark inside a quota deliberately, and a
shared CI runner always has something runnable.

## What it reads

All Linux sysfs and procfs. Nothing is inferred from a value that could not be
read: an unreadable source becomes `None` and is named in `unreadable`, so a
consumer can tell an absent signal from a clean one.

| Family | Sources | How it is used |
| --- | --- | --- |
| cgroup v2 | `cpu.stat` (`nr_periods`, `nr_throttled`, `throttled_usec`), `cpu.max` | Throttle counters as deltas across the measurement; `cpu.max` of `"quota period"` means a quota, `"max period"` means none. The process's own cgroup comes from `/proc/self/cgroup`, walking up for `cpu.max`. |
| cgroup v1 | `cpu.cfs_quota_us`, `cpu.cfs_period_us`, `cpu.stat`'s `throttled_time` | Same logic. `throttled_time` is nanoseconds and is converted to microseconds so one unit is reported. A quota of `-1` is the kernel's "no quota" sentinel and is not treated as a number. |
| CPU frequency | `cpu*/cpufreq/scaling_governor`, `scaling_cur_freq`, `cpuinfo_max_freq` | Warns when any CPU is off the `performance` governor, and separately when the lowest `scaling_cur_freq / cpuinfo_max_freq` falls under `min_freq_ratio`. |
| Thermal | `thermal_zone*/temp`, `thermal_zone*/type` | Hottest zone before and after, and the rise. Temperature is not treated as proof of throttling. |
| Power cap (x86) | `cpu*/thermal_throttle/core_throttle_count`, `package_throttle_count` | Event counters, summed over CPUs, as deltas. Any rise means the CPU clamped itself during the measurement. |
| Raspberry Pi | `vcgencmd get_throttled` | Bitmask: bit 0 under-voltage, bit 1 arm frequency capped, bit 2 currently throttled, bit 3 soft temperature limit, bits 16 to 19 the sticky "has occurred since boot" flags. Live bits invalidate; sticky bits warn, unless a sticky bit was newly set inside the measured interval, which invalidates. |
| Paging | `majflt` from `/proc/self/stat` field 12, `pgmajfault` from `/proc/vmstat` | Deltas. A model or dataset that is not resident turns a memory benchmark into a storage benchmark. |
| Competing load | `/proc/loadavg` (including the runnable count), `MemAvailable` from `/proc/meminfo` | Other runnable processes, and memory headroom against the caller's declared `working_set_bytes`. |
| cpuset and affinity | `cpuset.cpus.effective`, `sched_getaffinity` (falling back to `Cpus_allowed_list` in `/proc/self/status`) | A narrower-than-machine cpuset warns; a set that changes mid-measurement invalidates. |

**`/proc/cpuinfo` is deliberately not parsed.** Unstructured text parsing is the
failure mode that stalled cilium/cilium#7086: the reporter wrote that the fields
are "not structured" and that parsing them risked breaking the CI. Integer sysfs
files are used instead wherever one exists, and no number is guessed from a
sibling value.

### Major faults have no universal threshold, so none is invented

`max_major_faults` defaults to `None`. With no ceiling declared, the fault delta
is reported as `MAJOR_FAULTS_OBSERVED` at info severity and never invalidates,
because there is no defensible number that is wrong everywhere.

If you declare `working_set_bytes`, the delta is also compared against the
working set in pages, and `MAJOR_FAULT_RATIO_HIGH` warns above
`max_major_fault_ratio` (default 0.10, a documented choice and not a
measurement). The default is pinned in the test suite against a case with a known
outcome: the Raspberry Pi 5 run above, 11,404 faults against 272,000 pages, is
4.2 percent and passes.

## The provenance envelope

Every result serializes to `measurement-provenance/1`, a small outer contract:

```json
{
  "schema": "measurement-provenance/1",
  "tool": {"name": "benchverdict", "version": "0.1.0"},
  "taken_at_utc": "2026-11-01T18:42:07Z",
  "duration_s": 2.0041,
  "platform": {"system": "Linux", "release": "6.6.51+rpt-rpi-2712",
               "machine": "aarch64", "python": "3.11.2"},
  "verdict": "INVALID",
  "reasons": [
    {"code": "CGROUP_THROTTLED", "severity": "invalidating",
     "detail": "cgroup v2 CPU bandwidth control denied this process CPU time ..."}
  ],
  "unreadable": ["vcgencmd get_throttled (not a Raspberry Pi, or vcgencmd absent)"],
  "measurement": {"cgroup": {"delta_throttled_usec": 812345}}
}
```

Everything tool-specific lives under `measurement`. The nine top-level keys are
fixed. The authoritative JSON Schema ships inside the package at
`benchverdict/schema/measurement-provenance-1.schema.json`, is printed by
`benchverdict schema`, and is reachable in Python as
`benchverdict.schema_path()` and `benchverdict.load_schema()`.

`benchverdict.validate_envelope(envelope)` returns a list of problems and needs no
third-party validator; it is a convenience, not a replacement for running a real
JSON Schema validator against the schema file. `benchverdict.build_envelope()`
assembles an envelope for a tool that is not benchverdict.

`unreadable` is load-bearing. A source listed there was not checked, so its
absence from `reasons` is not evidence that it was fine.

## Related tools

`membw`, which reports achieved memory bandwidth against theoretical peak per
core cluster, and `powerstamp`, which reports energy with its measured sampling
rate and uncertainty, are sibling tools that emit the same
`measurement-provenance/1` envelope, so one parser reads all three and a consumer
branches on `tool.name` before reading inside `measurement`. benchverdict owns the
`verdict` and `reasons` concepts in that contract and ships the schema. Neither
sibling is published, and this section makes no claim about whether either ever
will be.

## Related work

Verified against the primary sources on 1 November 2026. Each of these is good at
something benchverdict does not do.

**psf/pyperf** (MIT). `pyperf check` reads a saved result file and checks the
*statistics*: it warns when the standard deviation reaches 10 percent of the
mean, when the minimum or maximum deviates by 50 percent from the mean, and when
the shortest raw value is under 1 ms. It never inspects the live machine.
`pyperf system tune` is the machine-state half and is Linux-only; its operations
cover the Intel pstate driver, turbo boost, the CPU governor, the Linux
scheduler, ASLR, CPU frequency, IRQ affinity and the power supply, which it reads
from `/sys/class/power_supply/*/online`. It has no thermal or throttling check on
any platform. Issue #243, "pyperf system {tune,show} on macOS", has been open
since 10 June 2026 with no implementation; thermal pressure and power source
appear there among the macOS variance sources the reporter lists. pyperf tunes
the machine and judges the statistics; benchverdict judges the machine around the
measurement and returns a verdict about that measurement. They compose.

**RRZE-HPC/MachineState** (GPL-3.0). Collects a broad system-state snapshot and
diffs it against a stored JSON with a 20 percent tolerance on numerics. It does
read cgroup *cpuset* (v1 and v2) and thermal-zone temperature, so it is not true
that it ignores those families. What it does not do: it reads no cgroup CPU
bandwidth (no `cpu.max`, no `cpu.cfs_quota_us`), records no throttle event
counters, no major page faults and no process-table scan. On a diff it prints
"Current state differs" and then exits 0 unconditionally, so it cannot gate CI,
and it ships no `action.yml`. (Its README carries a badge labelled "GitHub
Action" which points at its own build workflow, not a published action.) Its
value is documentation of a run; benchverdict's is a verdict on one.

**benchmark-action/github-action-benchmark**. Collects benchmark tool output,
stores it on a `gh-pages` branch, charts it, and alerts or fails the workflow
when a result regresses past a threshold. Under "Stability of Virtual
Environment" its README reports that for the example suites in that repository
"the amplitude of the benchmarks is about +- 10~20%", notes the amplitude may be
bigger with network or file I/O, and offers self-hosted runners as the remedy. It
validates no machine state. benchverdict is complementary: it says whether a point
on that chart was worth plotting.

**ionelmc/pytest-benchmark**. Records a `machine_info` dict with every saved run.
The only thing done with it beyond storage is a comparison against a saved
baseline, which emits a `logger.warning` on mismatch and never fails; a
first-ever run is never checked at all. So it detects "different hardware than
last time", not "bad machine state right now".

**cilium/cilium#7086**, "CI: detect when CPU throttling is occurring in a
precheck script to run on build machines". Opened 14 February 2019, closed by
`stale[bot]` on 29 April 2019; the only two comments on it are the bot's, no
human replied, and it was never built. Two details from the report shaped
benchverdict: the machine showed every governor at `performance` while `cpu MHz`
read 800.355 on a part labelled 3.70 GHz, which is why the governor check and the
frequency-ratio check are separate here; and the reporter flagged that
`/proc/cpuinfo` is "not structured" and that parsing it risked breaking the CI,
which is why benchverdict parses integer sysfs files instead.

**VictoriaMetrics/VictoriaMetrics#10430**, "Add CPU throttling metrics from
cgroup cpu.stat (quota throttling proof)". Open since 10 February 2026. It argues
that from inside a process it is hard to prove or refute quota throttling: CPU
time counters show how much CPU was used, not how much was denied, and pressure
stall information is a pressure signal rather than a quota-throttling counter. It
is the clearest public statement of why benchverdict reads `cpu.stat` as a delta
rather than inferring throttling from utilisation. One maintainer-side comment
there argues that a specialized tool should do this job, which is an argument for
a standalone checker rather than against one.

**nucliweb/webperf-snippets#312**, "Measure the CPU throttling over work long
enough to show it". Merged 24 October 2026. The numbers behind `min_duration` and
the `UNKNOWN` verdict, quoted above.

## Limitations

Read these before trusting a verdict.

- **Linux only.** Everything above is sysfs and procfs. On Windows, macOS or any
  other platform the verdict is `UNSUPPORTED` and nothing is guessed. That is a
  deliberate hole, not an oversight: there is no honest way to read these
  conditions from a platform that does not expose them.
- **The Linux paths have not been exercised on a live Linux machine by the
  author in this release.** This release was developed and tested on Windows
  against synthetic sysfs and procfs trees in temporary directories, written from
  the kernel documentation for each file's format. The parsing, the delta logic
  and the verdict assembly are covered by those fixtures. What has not been
  confirmed is that every real kernel, container runtime and distribution puts
  these files exactly where the fixtures put them. Treat the cgroup v1 path
  discovery, the nested cgroup v2 walk-up and the Raspberry Pi probe as the most
  likely places for a real machine to disagree, and please open an issue with
  `benchverdict check --json` output if one does.
- **A `PASS` is bounded by what was readable.** It means "none of the sources
  benchverdict could read showed a problem", not "the machine was quiet". Read
  `unreadable` alongside the verdict. If nothing at all was readable the verdict
  is `UNKNOWN`, not `PASS`.
- **Sampling around a measurement is not sampling during it.** A condition that
  began and fully ended inside the interval without moving a counter is invisible.
  The counter-based checks (cgroup throttling, thermal throttle counts, major
  faults) survive this, because counters only go up. The state-based checks
  (governor, frequency, load) are read at the end of the interval and describe the
  state the number was produced under, not every instant of it.
- **Thresholds are choices, not findings.** `min_duration=1.0`,
  `max_major_fault_ratio=0.10`, `min_freq_ratio=0.90`,
  `max_thermal_rise_c=10.0`, `max_other_runnable=0` and `memory_headroom=1.10`
  are documented defaults. Only the major-fault ratio is pinned against a case
  with a known outcome. Set them for your own workload.
- **Temperature is not throttling.** A hot zone warns; only a throttle event
  counter or a Raspberry Pi live bit invalidates.
- **benchverdict measures nothing itself.** It reports no latency, no bandwidth and
  no energy. It has no opinion about whether your number is good, only about
  whether it is meaningful.

## Development

```sh
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```

The suite builds synthetic sysfs and procfs trees in temporary directories, so it
runs the full Linux logic on any host, including Windows. The CI matrix covers
Python 3.9, 3.11 and 3.13 on Ubuntu and Windows, exercises the Action against the
repository's own checkout, and fails on any non-ASCII byte in a text file.

Measured on 1 November 2026, on Windows 11 with Python 3.14.6, with
`python -m pytest`:

```
============================= test session starts =============================
platform win32 -- Python 3.14.6, pytest-9.2.0, pluggy-1.7.0
rootdir: C:\Users\manun\OneDrive\Documents\Claudecode\benchverdict
configfile: pyproject.toml
testpaths: tests
plugins: benchverdict-0.1.0
collected 245 items

tests\test_api.py .................................                      [ 13%]
tests\test_cli.py .............................                          [ 25%]
tests\test_docs.py .......................................               [ 41%]
tests\test_envelope.py .....................................             [ 56%]
tests\test_pytest_plugin.py .................                            [ 63%]
tests\test_sources.py ........................................           [ 79%]
tests\test_verdict.py .................................................. [100%]

============================ 245 passed in 16.22s =============================
```

Two of those 245 validate the bundled schema with `jsonschema`, which is a
test-only dependency pinned to `>=4.18` in the `dev` extra. They skip, rather
than fail, where the installed `jsonschema` predates draft 2020-12.

## Citation

See `CITATION.cff`. There is no DOI yet.

## Author

Manu Nicholas Jacob, Independent Researcher.
ORCID [0009-0007-6589-6572](https://orcid.org/0009-0007-6589-6572).

## License

MIT. See `LICENSE`.
