"""Build and run disposable Docker distro tests; logs go to .artifacts/docker."""

import argparse
import concurrent.futures
import fcntl
import json
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "debian13": "debian:trixie",
    "sid": "debian:sid",
    "ubuntu2404": "ubuntu:24.04",
    "mint223": "linuxmintd/mint22.3-amd64:latest",
    "kali": "kalilinux/kali-rolling:latest",
}

EXPECTED_DISTROS = {
    "debian13": "debian",
    "sid": "debian",
    "ubuntu2404": "ubuntu",
    "mint223": "linuxmint",
    "kali": "kali",
}


def run(target):
    directory = ROOT / ".artifacts/docker" / target
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"{target}: another test run is active; preserving its logs", flush=True)
            return {"target": target, "exit": 1}
        return run_locked(target, directory)


def run_locked(target, directory):
    # An interrupted rerun must not leave a previous success beside partial logs.
    (directory / "result.json").unlink(missing_ok=True)
    (directory / "test.log").unlink(missing_ok=True)
    tag = f"orbit-distro-test:{target}"
    print(f"Building {target} ({TARGETS[target]})", flush=True)
    with (directory / "build.log").open("w") as log:
        try:
            build = subprocess.run(
                [
                    "docker",
                    "build",
                    "--build-arg",
                    f"BASE_IMAGE={TARGETS[target]}",
                    "--build-arg",
                    f"EXPECTED_DISTRO={EXPECTED_DISTROS[target]}",
                    "-f",
                    "tests/docker/Dockerfile",
                    "-t",
                    tag,
                    ".",
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=1800,
            )
            build_code = build.returncode
        except subprocess.TimeoutExpired:
            log.write("\nFAILED: image build exceeded 1800 seconds\n")
            build_code = 124
    code = build_code
    if code == 0:
        print(f"Testing {target}", flush=True)
        name = f"orbit-test-{target}-{uuid.uuid4().hex[:8]}"
        with (directory / "test.log").open("w") as log:
            try:
                test = subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--init",
                        "--name",
                        name,
                        "--security-opt=no-new-privileges",
                        "--memory=3g",
                        "--cpus=2",
                        "--pids-limit=512",
                        tag,
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=900,
                )
                code = test.returncode
            except subprocess.TimeoutExpired:
                log.write("\nFAILED: test container exceeded 900 seconds\n")
                code = 124
            finally:
                # Only this invocation's disposable container; never prune others.
                subprocess.run(
                    ["docker", "rm", "-f", name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
    result = {
        "target": target,
        "image": TARGETS[target],
        "build_exit": build_code,
        "exit": code,
    }
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "targets", nargs="*", metavar="TARGET", help="Choices: " + ", ".join(TARGETS)
    )
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if args.jobs < 1 or any(target not in TARGETS for target in args.targets):
        parser.error("Use a positive --jobs count and known target names")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        results = list(executor.map(run, args.targets or TARGETS))
    raise SystemExit(any(result["exit"] for result in results))
