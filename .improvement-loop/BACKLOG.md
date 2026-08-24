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

Iteration 1 is NOT closed as done: its audit returned FAIL and nothing was committed. The tree is
green (`Ran 1147 tests / OK`) but uncommitted; see LOG.md's final iteration-1 entry and the close-out
supersession note at LEDGER.md:254+. B-001..B-004 are dx/frontend-cards items and iteration 1 ran
ha-conformance, so none of them were touched.

### From the iteration-1 audit (2026-08-24) — surviving findings, carried into iteration 2

- [B-007] [must-fix] The shipped reauth flow has no demonstrated real-device trigger — every pytapo
  login raise site strips the error code. `custom_components/tapo_h500/api.py:181-186` (the docstring
  conceding it) vs `.venv/lib/python3.14/site-packages/pytapo/transport/pytapo/pytapo.py:606, :693,
  :716, :764`. `H500AuthError` can only fire from `connect()`'s try
  (`custom_components/tapo_h500/api.py:229-244`), which wraps only the `Tapo(...)` construction at
  `:231-235`, whose only request is `getBasicInfo()`; every `_refreshStok` credential refusal raises a
  bare `Exception("Invalid authentication data")` with the code discarded. The ledger's -40414
  NEED_LOGIN_BY_LOCAL_PASSWORD arrival is asserted, not demonstrated, so the reauth screen, the
  `ConfigEntryAuthFailed` route (`custom_components/tapo_h500/__init__.py:200-206`) and the
  `invalid_auth` string (`custom_components/tapo_h500/strings.json:28`) may all be dead in the field.
  THIS IS THE HEADLINE CAVEAT of iteration 1. Needs evidence, not more code — and the evidence costs a
  real login against a hub that wedges under repeated auth, so it is the owner's call, not the loop's.

- [B-008] [must-fix] THE HONEST REMAINDER OF ITERATION 1 — a changed CAMERA-ACCOUNT password still
  retries forever and never offers reauth, and a changed CLOUD password cannot ever trigger it.
  Camera account: `custom_components/tapo_h500/api.py:188-196` correctly refuses to type a code-less
  exception, and all four refusal sites
  (`.venv/lib/python3.14/site-packages/pytapo/transport/pytapo/pytapo.py:606, :693, :716, :764`, plus
  `klap.py:53/66/73` and `kasa.py:107/331`) strip the code — so behaviour is unchanged from HEAD.
  Cloud password: this pytapo fork computes `hashedCloudPassword` at
  `.venv/lib/python3.14/site-packages/pytapo/transport/pytapo/pytapo.py:70` and never uses it in the
  control path, so a wrong cloud password produces no refusal at all. Widening the classifier is the
  WRONG fix — it would make the string "Invalid authentication data" load-bearing, and that string is
  what a wedged hub produces on the -40413 device_confirm path, the exact misclassification the
  BINDING constraint forbids by name (LEDGER.md:12-17). The remedy that classifies nothing:
  `async_step_reconfigure` reusing `_validate` (`custom_components/tapo_h500/config_flow.py:45-70`),
  letting the owner retype credentials on purpose. That is a new claim with its own audit.

- [B-009] [polish] A credential change on an already-loaded entry never reaches reauth; the
  coordinator only ever raises `UpdateFailed`. `custom_components/tapo_h500/coordinator.py:710` and
  `:736` — nothing in coordinator.py imports or raises `ConfigEntryAuthFailed`
  (`coordinator.py:12` imports only `DataUpdateCoordinator, UpdateFailed`), and `client.connect` is
  called only from `custom_components/tapo_h500/__init__.py:199` and
  `custom_components/tapo_h500/config_flow.py:58`. So reauth can only appear on a fresh setup attempt
  after a restart. This is also why the poll loop cannot be killed by the new exception type — worth
  NAMING in docs rather than changing.

- [B-010] [polish] `config.abort` now exists but omits `already_configured`, which the flow can
  actually abort with. `custom_components/tapo_h500/strings.json:30-32` (`config.abort`, holding only
  `reauth_successful`) and the same block at
  `custom_components/tapo_h500/translations/en.json:34-36`, vs
  `custom_components/tapo_h500/config_flow.py:78` (`_abort_if_unique_id_configured`). Re-adding a
  configured host shows the raw reason string. Pre-existing, but conspicuous now that an abort block
  exists.

- [B-011] [polish] `tests/test_config_flow.py:19` imports `requests` unguarded while `:38-57`
  carefully installs hollow stand-ins when `import pytapo` fails. `requests` is only present
  transitively via pytapo, and the file uses it in exactly three places
  (`tests/test_config_flow.py:208, :209, :211`), so on an interpreter with neither, this file raises
  ImportError at collection and takes all 1147 tests down instead of the handful that need it. Both
  interpreters here have it, so it does not bite today.

- [B-013] [must-fix] (blocks iteration 2's own gate) C6's allow-list was never widened to
  `.improvement-loop/`, so C6's verify_command still exits 1 on a clean close-out.
  `.improvement-loop/LEDGER.md:161` names the six code/test paths plus `.improvement-loop/LEDGER.md`
  and `.improvement-loop/STATE.json` only; every close-out must also write
  `.improvement-loop/LOG.md` and `.improvement-loop/BACKLOG.md`, and `.improvement-loop/PROFILE.md`
  now carries the driver-notes appendix. Re-run at close-out, the command exits 1 naming those three.
  Fix: grep out the whole `.improvement-loop/` prefix instead of listing files. Apply it BEFORE
  iteration 2's claims are written — changing a gate after an audit has recorded it failing rewrites
  the audit's own evidence, which is why it was NOT done at close-out.
  `.improvement-loop/PROFILE.md:110-113` already records the underlying cause: the owner_glob named code files only, a driver
  mis-specification, and future iterations must name `.improvement-loop/**` explicitly.

### Closed during iteration 1 — do NOT re-do, do NOT chase the old citations

- [B-005] RESOLVED (2026-08-24). Was: "the ledger asserts a PROFILE.md revert that did not happen,
  and the un-reverted file now legislates against its own audit." The revert is now real and was
  re-checked at close-out, not taken from the ledger's account of itself:
  `git diff HEAD --numstat -- .improvement-loop/PROFILE.md` -> `16	0` (purely additive, zero deleted
  lines, HEAD's blob a byte-exact prefix); `.improvement-loop/PROFILE.md:78` carries the owner's
  guardrail verbatim; `grep -c OwnerGlobRule .improvement-loop/PROFILE.md` -> `0`. The only addition
  is the attributed appendix at `.improvement-loop/PROFILE.md:100-113`. Its old citations
  (PROFILE.md:79, :85-88) no longer resolve to anything.

- [B-006] RESOLVED (2026-08-24). Was: "`is_auth_failure` reads the FIRST `error_code` in the response
  body, not the one pytapo raised on." Replaced by `_refused_code` at
  `custom_components/tapo_h500/api.py:135-156`, which partitions on `"Response: "` and `json.loads`
  the body to read the TOP-LEVEL `error_code`, called from
  `custom_components/tapo_h500/api.py:192`. The regex and the `re` import are gone: `_ERROR_CODE`
  appears nowhere in api.py and an AST walk finds zero `re` imports, zero `re` Name nodes and zero
  attribute accesses on it. Two regression tests cover it —
  `tests/test_config_flow.py:248` (`test_a_nested_error_code_is_not_mistaken_for_the_refusal`) and
  `:268` (`test_an_unreadable_body_retries`); the auditor reconstructed the old regex and confirmed it
  returns True where the new test asserts False, i.e. the test genuinely fails on the old code. Its
  old citations (api.py:136, :172) no longer resolve.

- [B-012] RESOLVED at close-out (2026-08-24). Was: the ledger's tail asserted a PROFILE.md state that
  no longer existed (`.improvement-loop/LEDGER.md:237-239` and `:246`), and because the driver
  retro-inserted its corrections ~60 lines upstream, the newest text in an append-only file was the
  stale, false version with no forward pointer. Fixed by appending a dated supersession note at
  `.improvement-loop/LEDGER.md:254+` that states what is on disk, names both false statements
  explicitly, and leaves them in place as the record of what was claimed. Noted for the direction it
  erred in: those statements OVER-reported breakage, which is the opposite of a false progress claim,
  but still false about the filesystem.

- [B-014] RESOLVED at close-out (2026-08-24). Was: LOG.md's mid-iteration entry quoted a test count
  (1145) and C6 evidence the fix pass had since invalidated. Fixed by a SUPERSEDED pointer at
  `.improvement-loop/LOG.md:6-9` naming both staleness points, with the entry kept verbatim below it
  as the record, and by the final iteration-1 entry appended at the foot with the measured 1147.
