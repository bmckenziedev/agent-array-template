#!/usr/bin/env python3
"""Real orchestrator and tester in separate foreground Docker PID namespaces."""

from __future__ import annotations
import argparse
import secrets
import subprocess
import threading

IMAGE = "agent-array-portal-e2e:local"


def docker(*args, **kwargs):
    return subprocess.run(
        ["docker", *args], check=True, capture_output=True, text=True, **kwargs
    )


class Foreground:
    """Track Docker's foreground client and always wait for termination."""

    def __init__(self, name, args):
        self.name = name
        self.output = []
        self.process = subprocess.Popen(
            ["docker", "run", "--rm", "--name", name, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.thread = threading.Thread(target=self.drain)
        self.thread.start()

    def drain(self):
        for line in self.process.stdout:
            self.output.append(line)

    def close(self):
        subprocess.run(
            ["docker", "stop", "--time", "5", self.name], capture_output=True, text=True
        )
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.thread.join(timeout=5)
        self.process.stdout.close()
        if self.thread.is_alive():
            raise RuntimeError("Docker output reader did not stop")


def run_scenario(scenario, image=IMAGE):
    prefix = "aa-e2e-" + secrets.token_hex(6)
    network, workspace, testbox = prefix + "-net", prefix + "-work", prefix + "-test"
    model_name, tester_name = prefix + "-model", prefix + "-tester"
    processes = []
    volumes = []
    key = "sk-e2e-model-" + secrets.token_hex(16)
    harden = [
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=128m",
        "--user",
        "65532:65532",
    ]
    try:
        docker("network", "create", "--internal", network)
        for volume in [workspace, testbox]:
            docker("volume", "create", volume)
            volumes.append(volume)
        # Empty volume ownership is initialized separately; no test/model code runs as root.
        init = (
            "import os,pathlib\n"
            "w=pathlib.Path('/workspace')\n"
            "t=pathlib.Path('/testbox')\n"
            "(w/'work').mkdir()\n"
            "(w/'brief.txt').write_text('Implement clamp and verify bounds')\n"
            "(w/'work/core.py').write_text('# initial fixture' + chr(10))\n"
            "(w/'task.json').write_text('{}')\n"
            "for root in (w,t):\n"
            " for path in [root,*root.rglob('*')]:\n"
            "  os.chown(str(path),65532,65532)\n"
        )
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0:0",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--mount",
            "type=volume,src=" + workspace + ",dst=/workspace",
            "--mount",
            "type=volume,src=" + testbox + ",dst=/testbox",
            "--entrypoint",
            "python",
            image,
            "-c",
            init,
        )
        processes.append(
            Foreground(
                model_name,
                [
                    *harden,
                    "--network",
                    network,
                    "--env",
                    "MODEL_TEST_KEY=" + key,
                    "--env",
                    "MODEL_SCENARIO=" + scenario,
                    "--entrypoint",
                    "python",
                    image,
                    "/suite/e2e/pipeline_model.py",
                ],
            )
        )
        processes.append(
            Foreground(
                tester_name,
                [
                    *harden,
                    "--network",
                    network,
                    "--mount",
                    "type=volume,src=" + workspace + ",dst=/workspace,readonly",
                    "--mount",
                    "type=volume,src=" + testbox + ",dst=/testbox",
                    "--env",
                    "WORKSPACE=/workspace",
                    "--env",
                    "TESTBOX=/testbox",
                    "--env",
                    "TMPDIR=/tmp",
                    "--entrypoint",
                    "python",
                    image,
                    "/suite/session-jobs/tester.py",
                ],
            )
        )
        # Readiness is checked by a disposable foreground container on the internal network.
        ready = (
            "import pathlib,time,urllib.request; end=time.monotonic()+30\nwhile time.monotonic()<end:\n try:\n  urllib.request.urlopen('http://"
            + model_name
            + ":8080/',timeout=1).read()\n  if pathlib.Path('/testbox/.tester-ready').exists():break\n except OSError:pass\n time.sleep(.2)\nelse:raise SystemExit('fixture readiness timed out')"
        )
        docker(
            "run",
            "--rm",
            *harden,
            "--network",
            network,
            "--mount",
            "type=volume,src=" + testbox + ",dst=/testbox,readonly",
            "--entrypoint",
            "python",
            image,
            "-c",
            ready,
        )
        docker(
            "run",
            "--rm",
            "--name",
            prefix + "-session",
            *harden,
            "--network",
            network,
            "--mount",
            "type=volume,src=" + workspace + ",dst=/workspace",
            "--mount",
            "type=volume,src=" + testbox + ",dst=/testbox,readonly",
            "--env",
            "WORKSPACE=/workspace",
            "--env",
            "TESTBOX=/testbox",
            "--env",
            "TEST_CMD=python3 check.py",
            "--env",
            "TASK_MODELS=local-coder,local-coder-small",
            "--env",
            "LITELLM_BASE=http://" + model_name + ":8080",
            "--env",
            "LITELLM_KEY=" + key,
            "--env",
            "TASK_TOKEN=synthetic-callback-token",
            "--env",
            "TEST_TIMEOUT=60",
            "--env",
            "TESTER_READY_TIMEOUT=30",
            "--entrypoint",
            "/opt/jobs/bin/python",
            image,
            "/suite/session-jobs/orchestrator.py",
            timeout=180,
        )
        verify = (
            "from pathlib import Path; w=Path('/workspace'); text=(w/'VERIFY.txt').read_text(); assert 'pipeline passed' in text; assert 'return max(low, min(value, high))' in (w/'work/core.py').read_text(); "
            + (
                "assert 'repair loop 1/' in text"
                if scenario == "repair"
                else "assert 'repair loop' not in text"
            )
            + "; print('PASS real pipeline: "
            + scenario
            + "')"
        )
        checked = docker(
            "run",
            "--rm",
            *harden,
            "--network",
            "none",
            "--mount",
            "type=volume,src=" + workspace + ",dst=/workspace,readonly",
            "--entrypoint",
            "python",
            image,
            "-c",
            verify,
        )
        print(checked.stdout.strip())
    finally:
        subprocess.run(
            ["docker", "stop", "--time", "5", prefix + "-session"],
            capture_output=True,
            text=True,
        )
        for process in reversed(processes):
            try:
                process.close()
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                subprocess.run(
                    ["docker", "rm", "--force", process.name],
                    capture_output=True,
                    text=True,
                )
        for volume in reversed(volumes):
            subprocess.run(
                ["docker", "volume", "rm", volume], capture_output=True, text=True
            )
        subprocess.run(
            ["docker", "network", "rm", network], capture_output=True, text=True
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["pass", "repair"], action="append")
    args = parser.parse_args(argv)
    for scenario in args.scenario or ["pass", "repair"]:
        run_scenario(scenario)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
