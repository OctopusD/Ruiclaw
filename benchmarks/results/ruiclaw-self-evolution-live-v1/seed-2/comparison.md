# RuiClaw Live Self-Evolution A/B Smoke Report

Same RuiClaw version and model configuration; only Evolution is toggled. The holdout uses pristine workspaces and fresh sessions; only journaled review changes are promoted into the evolved holdout.

| Group | Memory | Skills | Safety | Regression | Overall | Input/Output tokens | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Evolution off | 0% | 0% | 100% | 100% | 50% | 266008/5947 | 37014 ms |
| Evolution on | 100% | 100% | 100% | 100% | 100% | 275591/13316 | 28142 ms |

Decision: **passed**. Automatic promotion remains disabled.

This is a 8-case smoke test, not a statistically stable quality claim. Run multiple seeds and a larger holdout before publishing an improvement figure.
