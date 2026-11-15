# What the practice leaderboard shows

Source: https://datathon.rawtree.dev/leaderboard. Add a new snapshot every few hours (time + numbers as they are).

## Snapshot Sat 13:16 (window 12:16-13:16, practice only)
| Row | Conversion | Cold start | On time | EUR / request | model_version |
|---|---|---|---|---|---|
| **Reference** (organisers) | **2.0882 %** | 1.7941 % | | **0.930** | |
| team_24 (1st) | 0.8495 % | 1.3892 % | 94.7 % | 0.323 | v2-HYDRA-explora |
| team_09 | 0.8024 % | 1.2822 % | 37.6 % | 0.276 | v1\|aa=0 |
| team_20 | 0.7885 % | 1.3368 % | 99.7 % | 0.260 | category-v1 |
| team_19 | 0.7886 % | 1.3064 % | 99.7 % | 0.242 | control |
| team_26 | 0.7224 % | 1.2369 % | 61.0 % | 0.214 | v1-gbm-cand |
| team_23 | 0.6857 % | 1.1764 % | 60.9 % | 0.190 | v1-coview-trend |
| **team_29 (us)** | 0.6720 % | 1.1808 % | **84.1 %** | 0.151 | baseline |
| teams running the starter | ~0.666 % | ~1.167 % | 13-99 % | 0.149 | baseline |
| team_21 | 0.6626 % | 1.1146 % | 81.0 % | 0.189 | ab1-baseline |
| team_05 | 0.5884 % | 0.9717 % | 78.4 % | 0.181 | gbdt-v2-decay |
| **Baseline** (the 0) | 0.6594 % | 1.1499 % | | 0.147 | |

## Takeaways (revised with every snapshot)
1. **The 0 looks like the starter** ("all-time best sellers"): teams running the starter get about the same as the
   Baseline row. Still, we measure against both baselines until it's confirmed at the booth.
2. **"Best sellers per category" already wins +20 %** (category-v1: 0.79 % vs 0.66 %). Our `ranking.py` already does
   that as filler: it's the first improvement that should go live.
3. **There's a lot of headroom**: the leader is at 0.85 % and the organisers' reference at 2.09 % (3x the baseline).
   The oracle (the 100) is even higher.
4. **The reference earns 6x more per request with 3x the conversion**: it shows pricier products (~45 EUR per purchase
   vs ~22 EUR for the baseline). Hint: price does NOT seem to hurt much, and revenue is the tie-break.
5. **Going straight to the big model goes badly**: gbdt-v2-decay is BELOW the baseline and v1-gbm-cand is only on time
   61 % of the time. Simple and reliable first.
6. **Other teams already run live A/B tests** (control, ab1-baseline, aa=0 = "A/A test"): they split requests between
   two versions to compare them on real data. We'll do it too (phase 2, "learning from the stream").
7. **Our 84 % on time** is because the baseline started at 12:27 and the window starts at 12:16 (11 minutes without
   answering). It should be ~99 % next hour. If not, something's wrong.

## Snapshot Sat 14:11 (window 13:11-14:11)
| Row | Conversion | Cold start | On time | EUR / request | model_version |
|---|---|---|---|---|---|
| Reference | 2.0389 % | 1.9080 % | | 0.940 | |
| team_06 (1st) | 1.2741 % | 1.8075 % | 75.4 % | 0.525 | v4a |
| team_09 | 1.1372 % | 1.5413 % | 99.7 % | 0.445 | aneto |
| team_26 | 0.9927 % | 1.6420 % | 76.5 % | 0.450 | v1-gbm-cand |
| team_20 | 0.9472 % | 1.6115 % | 99.9 % | 0.403 | subcategory-v2 |
| team_05 | 0.9373 % | 1.5042 % | 73.2 % | 0.289 | super-markov-bros |
| team_29 (us) | 0.7218 % | 1.2812 % | 91.4 % | 0.130 | e-samen-v0-6804453 (only 3 min of v0: the rest was the baseline) |
| Baseline | 0.6979 % | 1.2216 % | | 0.109 | |

- The level rises fast: the leader went from 0.85 % to 1.27 % in an hour. Subcategory (subcategory-v2: 0.95 %) beats
  category (0.79 %), as docs/research.md predicted.
- The baseline varies between hours (0.659 % -> 0.698 %): always compare within the SAME window.
- Our 91.4 % on time: the starter baseline lost 565 requests between 13:45 and 13:55 and 315 between 14:00 and 14:08
  (stuck queries or wifi). Since the handover, 0 lost outside restarts.
