"""Estimate cost per run and per month from runs/log.jsonl (read-only, no API calls).

Usage (from the capstone folder):
    python cost.py --model claude-sonnet-5 --in-price 2 --out-price 10 --volume 1000

Prices are dollars per million tokens. Every logged run for the model counts,
including failed runs (they are logged with the tokens they used).
cost per run = (avg input_tokens * in-price + avg output_tokens * out-price) / 1,000,000
monthly cost = cost per run * volume
"""

import argparse
import json
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent / "runs" / "log.jsonl"


def main():
    p = argparse.ArgumentParser(description="Cost per run and per month from the run log")
    p.add_argument("--model", required=True)
    p.add_argument("--in-price", type=float, required=True, help="$ per 1M input tokens")
    p.add_argument("--out-price", type=float, required=True, help="$ per 1M output tokens")
    p.add_argument("--volume", type=int, required=True, help="runs per month")
    args = p.parse_args()

    with LOG_FILE.open(encoding="utf-8") as f:
        runs = [e for e in map(json.loads, f) if e.get("model") == args.model]
    if not runs:
        raise SystemExit(f"No runs for model {args.model!r} in {LOG_FILE}")

    avg_in = sum(e["input_tokens"] for e in runs) / len(runs)
    avg_out = sum(e["output_tokens"] for e in runs) / len(runs)
    per_run = (avg_in * args.in_price + avg_out * args.out_price) / 1_000_000

    print(f"Model: {args.model}")
    print(f"Runs: {len(runs)}")
    print(f"Average input tokens: {avg_in:,.0f}")
    print(f"Average output tokens: {avg_out:,.0f}")
    print(f"Cost per run: ${per_run:.4f}")
    print(f"Monthly cost ({args.volume:,} runs): ${per_run * args.volume:,.2f}")


if __name__ == "__main__":
    main()
