---
name: cpu-profiling
description: Collect continuous Linux CPU profiles and retrieve historical flamegraphs using the cpu-blackbox CLI. Use for retrospective on-CPU hotspots, bounded perf recording, and profiling diagnostics.
---

# Linux CPU blackbox

Use the cpu-blackbox project packaged with this skill. From the project root, run `python -m cpu_blackbox --help`; the installed entry point is `cpu-blackbox`. The skill directory is `skills/cpu-profiling`; project root is two directories above it. Read [the project README](../../README.md) for installation, configuration and examples; read [the design](../../docs/DESIGN.md) for clock and storage behavior.

## Operate

- Check Linux, target PID and perf access with `python -m cpu_blackbox doctor --pid PID`. Attach success alone does not establish usable stacks or business overhead.
- Choose the PID, retained history, disk budget, frequency and call-graph mode. Start with low-frequency frame-pointer sampling only if the target supports that unwind method. DWARF must be measured separately.
- Run `python -m cpu_blackbox --data-dir DATA --config CONFIG collect --pid PID`. For unattended operation use the supplied systemd template after adapting user, paths and perf permissions. Do not install or change host permissions without the applicable authorization.
- Retrieve history with `python -m cpu_blackbox --data-dir DATA --config CONFIG query --at TIME --before 30 --after 30 --output NEW_REPORT_DIR`. TIME must include a timezone. Open `index.html`; retain `report.json` and folded stacks as evidence.
- Review sample count, unknown symbols, clock uncertainty, warnings and gaps before interpreting hotspots. Normal flamegraph width represents aggregated sample count, not chronological duration.

## Invariants and limits

Queries use sealed retained files and may lag one rotation. Exit 3 indicates partial sample coverage with a generated report; exit 2 is an error, exit 75 stops collection for disk pressure. Clock-jump sessions are refused to avoid ambiguous wall-time interpretation. PID exit ends the session; recycled PIDs are not silently followed.

If intact recordings are UNINDEXED after decode timeout/resource limits, stop collection, adjust limits, then run `python -m cpu_blackbox --data-dir DATA --config CONFIG reindex`. Preserve the original files and distinguish this from CORRUPT.

Use status and stderr to diagnose failed perf, stale collection, disk pressure, unsupported clocks and symbol resolution. Preserve the workload binary/debug information when retrieving old profiles after upgrades. Never label synthetic reports or mocked tests as real perf evidence.

The tool finds on-CPU hotspots; blocked locks, JIT stacks and application-level causality need additional evidence. Validate the <3% requirement with the paired benchmark runner and an actual representative workload; a model opinion is not a measurement. Do not lower global kernel restrictions automatically or delete files outside the managed data directory.

## Linux unavailable

For a labelled workflow demonstration on Windows, run `python tools/run_simulation_demo.py --output-root NEW_DIRECTORY` from the project root. This runs synthetic capture/rotation and queries the completed hot phase. Report and test results must retain the synthetic provenance. Do not treat this as a Linux kernel emulator or perf permission/performance verification. For real Linux behavior, use an explicitly authorized Linux/WSL environment and rerun the real integration tests.
