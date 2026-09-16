# RuiClaw Self-Evolution A/B Report

This deterministic fixture validates the isolated evolution pipeline. It does not
measure live-model learning quality and must not be reported as a model benchmark.

| Group | Learning holdout | Safety | Regression | Overall |
| --- | ---: | ---: | ---: | ---: |
| baseline (Evolution off) | 0% | 100% | 100% | 43% |
| evolved (Evolution on) | 100% | 100% | 100% | 100% |

Learning-evidence availability delta: +100%
Reviewer: `scripted_deterministic` (no model calls or token-cost claim)
Automatic promotion: `false`
