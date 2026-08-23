# Backlog

## Active candidates
(seeded from Stage A recon; scouts refresh this every iteration)
- [B-001] (dx, value ~70) `tools/verify.sh` is a real gate nothing automated runs — no `.github/`
  workflow exists anywhere in the repo.
- [B-002] (dx, value ~60) Zero static analysis: no ruff/flake8/mypy config in the repo root, so the
  only signal is the test suite.
- [B-003] (dx, value ~55) `custom_components/tapo_h500/coordinator.py` is 1136 lines — the largest
  module by 200+ lines over `clips.py` (921).
- [B-004] (frontend-cards, value ~55) `custom_components/tapo_h500/www/tapo-h500-card.js` is 1350
  lines / 56KB of eight cards with a single `tests/test_cards.mjs` covering it.

## Deepening moves
(see PROFILE.md DeepeningBacklog — used when nothing clears MinValueFloor 55)

## Deferred ([future] findings from audits)
