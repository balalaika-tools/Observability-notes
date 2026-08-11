#!/usr/bin/env python3
"""Estimate trace-retention volume and tail-sampler capacity lower bounds."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING


SECONDS_PER_DAY = Decimal(86_400)


def positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"not a number: {value}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a finite number greater than 0")
    return parsed


def percentage(value: str) -> Decimal:
    parsed = positive_decimal(value)
    if parsed > 100:
        raise argparse.ArgumentTypeError("percentage must be greater than 0 and at most 100")
    return parsed


def ceil_int(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


@dataclass(frozen=True)
class TraceBudgetEstimate:
    daily_input_traces: int
    daily_input_spans: int
    daily_retained_traces: int
    daily_retained_spans: int
    daily_retained_bytes: int
    minimum_active_trace_capacity: int
    suggested_sampled_cache_lower_bound: int
    suggested_non_sampled_cache_lower_bound: int


def estimate_trace_budget(
    *,
    traces_per_second: Decimal,
    average_spans_per_trace: Decimal,
    average_bytes_per_span: Decimal,
    effective_retained_percentage: Decimal,
    decision_wait_seconds: Decimal,
    burst_factor: Decimal = Decimal(2),
    cache_multiplier: Decimal = Decimal(10),
) -> TraceBudgetEstimate:
    values = {
        "traces_per_second": traces_per_second,
        "average_spans_per_trace": average_spans_per_trace,
        "average_bytes_per_span": average_bytes_per_span,
        "effective_retained_percentage": effective_retained_percentage,
        "decision_wait_seconds": decision_wait_seconds,
        "burst_factor": burst_factor,
        "cache_multiplier": cache_multiplier,
    }
    if any(not value.is_finite() or value <= 0 for value in values.values()):
        raise ValueError("all inputs must be finite and greater than zero")
    if effective_retained_percentage > 100:
        raise ValueError("effective_retained_percentage must be at most 100")

    retained_ratio = effective_retained_percentage / Decimal(100)
    daily_input_traces = traces_per_second * SECONDS_PER_DAY
    daily_input_spans = daily_input_traces * average_spans_per_trace
    daily_retained_traces = daily_input_traces * retained_ratio
    daily_retained_spans = daily_input_spans * retained_ratio
    active_capacity = traces_per_second * decision_wait_seconds * burst_factor
    cache_lower_bound = active_capacity * cache_multiplier

    return TraceBudgetEstimate(
        daily_input_traces=ceil_int(daily_input_traces),
        daily_input_spans=ceil_int(daily_input_spans),
        daily_retained_traces=ceil_int(daily_retained_traces),
        daily_retained_spans=ceil_int(daily_retained_spans),
        daily_retained_bytes=ceil_int(daily_retained_spans * average_bytes_per_span),
        minimum_active_trace_capacity=ceil_int(active_capacity),
        suggested_sampled_cache_lower_bound=ceil_int(cache_lower_bound),
        suggested_non_sampled_cache_lower_bound=ceil_int(cache_lower_bound),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Estimate retained trace volume and tail-sampler capacity. "
            "Use the measured effective retention ratio, including overlapping policies."
        )
    )
    parser.add_argument("--traces-per-second", type=positive_decimal, required=True)
    parser.add_argument("--average-spans-per-trace", type=positive_decimal, required=True)
    parser.add_argument("--average-bytes-per-span", type=positive_decimal, required=True)
    parser.add_argument(
        "--effective-retained-percentage", type=percentage, required=True
    )
    parser.add_argument("--decision-wait-seconds", type=positive_decimal, required=True)
    parser.add_argument("--burst-factor", type=positive_decimal, default=Decimal(2))
    parser.add_argument("--cache-multiplier", type=positive_decimal, default=Decimal(10))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    estimate = estimate_trace_budget(
        traces_per_second=args.traces_per_second,
        average_spans_per_trace=args.average_spans_per_trace,
        average_bytes_per_span=args.average_bytes_per_span,
        effective_retained_percentage=args.effective_retained_percentage,
        decision_wait_seconds=args.decision_wait_seconds,
        burst_factor=args.burst_factor,
        cache_multiplier=args.cache_multiplier,
    )
    print(json.dumps(asdict(estimate), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
