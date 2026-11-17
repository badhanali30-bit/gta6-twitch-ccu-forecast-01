# GTA VI launch-day Twitch viewers forecast

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/badhanali30-bit/gta6-twitch-ccu-forecast-01/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/badhanali30-bit/gta6-twitch-ccu-forecast-01/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Entry for the [make.fun bounty](https://make.fun/bounty/gta6-twitch-ccu-forecast-01): forecasts the Twitch concurrent
viewers in the Grand Theft Auto VI category for hours 2–24 of release
day 2026-11-19 (UTC), as five quantiles (0.05 / 0.25 / 0.5 / 0.75 / 0.95)
per hour. Python 3.13, standard library only, no network, deterministic.

## Model

At run time the program fits the launch-day shape from the case file's
own comparables (38 past big launches in the practice cases, 42 in the
final):

1. For each comparable launch, take its release-day hourly viewers and
   form log-ratios `ln(v_h / v_1)` for hours 2–24.
2. Pool those ratios per hour, weighting each launch by similarity to
   the target: weight decays with the log-distance between its hour-1
   print and this case's hour-1 print (Gaussian kernel, band 1.5 log
   units), so similar-sized launches count most.
3. The median for an hour is the hour-1 print times `exp()` of the
   weighted median ratio; the other quantiles are the weighted empirical
   quantiles of the same pooled ratios, smoothed across neighbouring
   hours and widened to a spread floor when the sample is too tight.
4. If the comparables are missing or degenerate, it falls back to
   persistence (flat at the hour-1 print) with a widening spread.

The target's channels and pre-launch history are not needed for the
shape fit and are only used implicitly through the hour-1 print; the
model is deliberately simple and robust. Every edge is guarded: zero
or missing hour-1 prints, missing hours, zero viewers in a comparable,
and non-finite values can never reach the output.

## How to run locally

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Reads every `*.in` file in the input directory (in name order) and
writes one `<id>.out` per case. Runs in ~0.05 s per case.

## Backtest (leave-one-out over the 38 comparables)

Each comparable is treated as the target (its hour-1 print, the other
37 as its comparables) and scored by mean pinball loss on the log
scale over hours 2–24 and all five levels:

| model | mean pinball loss | typical miss | 90% coverage |
| --- | --- | --- | --- |
| this entry | **0.409** | 84.4% | 0.866 |
| starter (persistence) | 0.930 | 213.3% | 0.624 |

Tuning tried: size band 0.8 / 1.2 / 1.5 / 1.8 / 3.0 / unweighted
(1.5 best), spread floor sigma 0.15–0.60 (negligible effect — the
empirical spread is already wide), median shrinkage 0.7–1.0 and spread
multiplier 1.0–1.3 (both hurt; the fitted shape as-is wins, so shrink
and spread multiplier stay at 1.0).

## Practice-case validation

All 4 practice cases (p00–p03) produce valid outputs: 23 rows, hours
2–24, monotone quantiles, every value in (0, 1e9], files ≤ 1,546 bytes.
Two runs are byte-identical.

## License

MIT — see `LICENSE`.
