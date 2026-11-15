import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

# Every failure (test name, full traceback, captured output, environment) is appended to tests/.failures.log, so a
# failure that does not reproduce is still on record - nothing reruns or hides it. The file is git-ignored.
import os
import platform
import time

FAILURES = Path(__file__).resolve().parent / ".failures.log"


def pytest_runtest_logreport(report):
    if report.failed or (report.when != "call" and report.outcome == "error"):
        with FAILURES.open("a") as f:
            f.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} {report.nodeid} [{report.when}] {report.outcome} "
                    f"({report.duration:.2f}s) python {platform.python_version()} pid {os.getpid()}\n")
            f.write(f"{report.longreprtext}\n")
            for name, text in report.sections:
                f.write(f"--- {name}\n{text}\n")
            f.write("\n")
