"""Forecast GTA VI launch-day Twitch viewers, hour by hour.

Model, in one paragraph: at run time the program fits the launch-day
shape from the case file's own comparables.  For every past launch it
takes the release day's hourly viewers, turns them into log-ratios
ln(v_h / v_1) for hours 2..24, and pools those ratios across launches,
weighting each launch by how close its hour-1 size is to this case's
hour-1 print (launches of a similar size behave more alike).  The
forecast median for an hour is the hour-1 print times exp() of the
weighted median ratio for that hour; the other quantiles are the
weighted empirical quantiles of the same pooled ratios, smoothed
across neighbouring hours and widened to a spread floor when the
pooled sample is too tight to be honest.  If the comparables are
missing or degenerate the program falls back to persistence (every
hour stays at the hour-1 print) with a widening spread.

Only the Python standard library is used.  No network, no randomness:
the same input always produces the same output bytes.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
HOURS = list(range(2, 25))

# How fast a comparable's weight decays with log-size distance from
# this case's hour-1 print.  A band of 1.5 log units means a launch
# ten times bigger or smaller still gets about half weight.
SIZE_BAND = 1.5
# Fewer usable comparables than this and the shape fit is not trusted.
MIN_COMPS = 5
# Minimum half-width of the 90% band, in log units, at the last hour;
# the floor grows with the square root of hours since hour 1, the way
# forecast uncertainty genuinely accumulates over the day.
FLOOR_SIGMA = 0.30
# Shrinkage of the fitted log-shape toward persistence (0 = flat at the
# hour-1 print, 1 = the pooled shape as fitted).  Launch-day shapes
# are noisy, so a little shrinkage usually pays.
MEDIAN_SHRINK = 1.0
# Multiplier on the width of the empirical quantile band around the
# median; 1.0 keeps the pooled spread as measured.
SPREAD_MULT = 1.0
# A comparable must cover at least this many of hours 1..24 to count.
MIN_HOURS = 18


def parse_time(text):
    """ISO-8601 timestamp (with a trailing Z) to epoch seconds."""
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def release_day_viewers(comp):
    """Hour -> viewers on the comparable's release day (hours 1..24).

    Returns an empty dict when the comparable has no usable day start
    or no positive hour-1 print.
    """
    day_start = parse_time(comp.get("day_start"))
    if day_start is None:
        return {}
    out = {}
    for row in comp.get("hourly") or []:
        at = parse_time(row.get("from"))
        if at is None:
            continue
        hour = int(round((at - day_start) / 3600.0)) + 1
        if 1 <= hour <= 24:
            viewers = row.get("viewers")
            if isinstance(viewers, (int, float)) and math.isfinite(viewers):
                if viewers > 0:
                    out[hour] = float(viewers)
    if out.get(1, 0) <= 0:
        return {}
    return out


def similarity_weight(comp_v1, target_v1):
    """Weight in (0, 1]: 1 for a same-size launch, decaying with distance."""
    gap = abs(math.log(comp_v1) - math.log(target_v1))
    return math.exp(-((gap / SIZE_BAND) ** 2))


def weighted_quantile(pairs, level):
    """Interpolated weighted quantile of [(value, weight), ...].

    Pairs are sorted by value; the cumulative weight is walked to the
    requested level and linearly interpolated between neighbours.
    """
    ordered = sorted(pairs, key=lambda p: p[0])
    total = sum(w for _, w in ordered)
    if total <= 0:
        return ordered[len(ordered) // 2][0] if ordered else 0.0
    target = level * total
    acc = 0.0
    for i, (value, weight) in enumerate(ordered):
        prev_acc = acc
        acc += weight
        if acc >= target:
            if i == 0 or weight <= 0:
                return value
            prev_value = ordered[i - 1][0]
            frac = (target - prev_acc) / weight if weight else 0.0
            return prev_value + frac * (value - prev_value)
    return ordered[-1][0]


def smooth(values):
    """Three-point running mean; the ends keep their own value blended
    with their single neighbour so no hour is left unsmoothed."""
    n = len(values)
    out = []
    for i, v in enumerate(values):
        if n == 1:
            out.append(v)
        elif i == 0:
            out.append(0.75 * v + 0.25 * values[1])
        elif i == n - 1:
            out.append(0.25 * values[i - 1] + 0.75 * v)
        else:
            out.append(0.25 * values[i - 1] + 0.5 * v + 0.25 * values[i + 1])
    return out


def round2(x):
    """Round to 2 decimals, half up, matching the house baseline's style."""
    scaled = x * 100.0
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100.0
    return int(value) if float(value).is_integer() else value


def persistence_quantiles(p1, hour):
    """Fallback forecast: flat median, spread widening like a random walk."""
    z = [-1.645, -0.674, 0.0, 0.674, 1.645]
    sigma = 0.15 * math.sqrt(hour - 1)
    return [round2(p1 * math.exp(zi * sigma)) for zi in z]


def shape_forecast(p1, comparables):
    """Per-hour empirical quantiles of log-ratios, or None to fall back.

    Returns {hour: [q05, q25, q50, q75, q95]} in viewer counts.
    """
    # Pool log-ratios per hour, with a similarity weight per launch.
    pooled = {h: [] for h in HOURS}
    usable = 0
    for comp in comparables or []:
        day = release_day_viewers(comp)
        if len(day) < MIN_HOURS or day.get(1, 0) <= 0:
            continue
        usable += 1
        weight = similarity_weight(day[1], p1)
        base = math.log(day[1])
        for h in HOURS:
            if h in day:
                pooled[h].append((math.log(day[h]) - base, weight))
    if usable < MIN_COMPS:
        return None

    # Raw empirical quantiles per hour, in log-ratio space.
    raw = {level: [] for level in LEVELS}
    for h in HOURS:
        pairs = pooled[h]
        if len(pairs) < MIN_COMPS:
            # Too thin for this hour: borrow the neighbouring hours.
            neighbours = []
            for hh in (h - 1, h + 1):
                if hh in pooled:
                    neighbours.extend(pooled[hh])
            pairs = pairs + neighbours
        if not pairs:
            return None
        for level in LEVELS:
            raw[level].append(weighted_quantile(pairs, level))

    # Smooth each quantile curve across hours; launch-day shapes move
    # gradually (ramps at regional unlocks), they do not zig-zag.
    curves = {level: smooth(raw[level]) for level in LEVELS}

    out = {}
    for i, h in enumerate(HOURS):
        med = curves[0.5][i] * MEDIAN_SHRINK
        lo = med + (curves[0.05][i] - curves[0.5][i]) * SPREAD_MULT
        hi = med + (curves[0.95][i] - curves[0.5][i]) * SPREAD_MULT
        # Spread floor: the 90% band must be at least this wide in log
        # units, widening over the day; expand symmetrically about the
        # median when the pooled sample claims to know too much.
        floor_half = 1.645 * FLOOR_SIGMA * math.sqrt((h - 1) / 23.0) + 0.05
        if hi - lo < 2.0 * floor_half:
            mid = 0.5 * (lo + hi)
            lo = mid - floor_half
            hi = mid + floor_half
        # Keep the inner quantiles inside the widened band and ordered.
        q25 = med + (curves[0.25][i] - curves[0.5][i]) * SPREAD_MULT
        q75 = med + (curves[0.75][i] - curves[0.5][i]) * SPREAD_MULT
        q25 = min(max(q25, lo), med)
        q75 = min(max(q75, med), hi)
        logs = [lo, q25, med, q75, hi]
        row = []
        for value in logs:
            q = p1 * math.exp(value)
            if not math.isfinite(q) or q <= 0:
                q = p1
            row.append(round2(min(q, 1_000_000_000.0)))
        # Enforce non-decreasing after rounding.
        for j in range(1, 5):
            if row[j] < row[j - 1]:
                row[j] = row[j - 1]
        out[h] = row
    return out


def forecast_case(case, name):
    """Forecast one case dict; returns the {"hours": [...]} object."""
    first = next((k for k in case.get("known") or [] if k.get("hour") == 1), None)
    if first is None:
        raise ValueError(f"{name} has no hour-1 print")
    viewers = first.get("viewers")
    if not isinstance(viewers, (int, float)) or not math.isfinite(viewers):
        raise ValueError(f"{name} has no usable hour-1 print")
    p1 = max(1.0, float(viewers))

    shaped = None
    try:
        shaped = shape_forecast(p1, case.get("comparables"))
    except Exception:
        shaped = None

    hours = []
    for row in case.get("forecast") or []:
        h = row.get("hour")
        if not isinstance(h, int) or h < 2 or h > 24:
            continue
        if shaped is not None and h in shaped:
            q = shaped[h]
        else:
            q = persistence_quantiles(p1, h)
        hours.append({"hour": h, "q": q})
    hours.sort(key=lambda r: r["hour"])
    if len(hours) != 23:
        raise ValueError(f"{name} forecast does not cover hours 2..24")
    return {"hours": hours}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        text = json.dumps(forecast, separators=(",", ":")) + "\n"
        (OUTPUT_DIR / f"{path.stem}.out").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
