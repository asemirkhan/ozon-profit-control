from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PIPELINE = [
    "build_profit_control_daily_fact_layer_v1.py",
    "build_profit_control_cabinet_human_pack_v1.py",
]


def run_script(name: str) -> None:
    env = dict(os.environ)
    subprocess.run([sys.executable, str(ROOT / "scripts" / name)], cwd=ROOT, check=True, env=env)


def run_node_script(script_name: str) -> None:
    env = dict(os.environ)
    subprocess.run(["node", str(ROOT / "scripts" / script_name)], cwd=ROOT, check=True, env=env)


def run_script_best_effort(name: str, *, timeout_sec: int) -> None:
    env = dict(os.environ)
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / name)],
            cwd=ROOT,
            check=True,
            env=env,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        print(f"warning={name} timed_out_after={timeout_sec}s")
    except subprocess.CalledProcessError as exc:
        print(f"warning={name} failed_exit_code={exc.returncode}")


def main() -> None:
    for script_name in PIPELINE:
        print(f"running={script_name}")
        run_script(script_name)
    print("running=build_wb_competitor_snapshot_v1.py")
    run_script_best_effort(
        "build_wb_competitor_snapshot_v1.py",
        timeout_sec=int(os.getenv("WB_COMPETITOR_SNAPSHOT_TIMEOUT_SEC", "300")),
    )
    print("running=build_profit_control_cabinet_human_pack_v1.py")
    run_script("build_profit_control_cabinet_human_pack_v1.py")
    print("running=build_profit_control_recommendations_v2.py")
    run_script("build_profit_control_recommendations_v2.py")
    print("running=cache_wb_competitor_images_v1.mjs")
    run_node_script("cache_wb_competitor_images_v1.mjs")
    print("built=profit_control_cabinet_human_pack_v1")


if __name__ == "__main__":
    main()
