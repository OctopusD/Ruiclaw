# RuiClaw Evolver Lite Report

This deterministic offline experiment compares one isolated candidate with the baseline.
It does not modify runtime configuration or deploy the candidate.

| Policy | Train pass | Holdout pass | Stale safety | Working-memory chars |
| --- | ---: | ---: | ---: | ---: |
| baseline (Top-K 3) | 100% | 100% | 100% | 1136 |
| candidate (Top-K 1) | 100% | 100% | 100% | 490 |

Context reduction: 56.87%
Decision: `recommended_for_manual_review`
Automatic promotion: `false`
Statistical confidence: `insufficient_small_holdout`
