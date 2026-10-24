"""Offline benchmarks against the installed recorder; see docs/benchmarks.md."""

from time import perf_counter

started = perf_counter()

from recorder_benchmarks.runner import main


if __name__ == "__main__":
    raise SystemExit(main(started_at=started))
