# RuiClaw GDPevo A/B Report

- Task group: `task_group_001`
- Protocol: baseline runs held-out test directly; evolved runs train, review, then a fresh held-out test.
- `self / evolved acc` is the average official `total_score` across held-out test tasks.

| Metric | Baseline | Self / evolved | Delta |
| --- | ---: | ---: | ---: |
| Held-out test acc | 48.75% | 53.11% | +4.36% |

## Evolution evidence

- Review ID: `evo_8b0d4218a0a54d40`
- Promoted Memory/Skill changes: memory/MEMORY.md
