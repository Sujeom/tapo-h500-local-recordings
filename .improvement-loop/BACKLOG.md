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

## Iteration 2 close-out (2026-08-24)

**Stale paragraph flagged, not rewritten.** The paragraph above beginning "Iteration 1 is NOT closed
as done" is false as of now, in both halves: iteration 1's tree WAS committed, at `b30d1d9`, by an
explicit driver decision taken after the FAIL verdict and recorded in `.improvement-loop/LOG.md`
under `GreenCommit:`; and the test count it quotes (1147) has since moved to 1148. Left in place as
the record of what the close-out believed, superseded here — the same convention [B-012] and [B-014]
established.

**Board state for iteration 3.** `.improvement-loop/board-iter2.json` holds **29 live candidates,
0 dropped** — every entry carries `still_holds: true`, re-checked against HEAD by the iteration-2
validator. Iteration 2 consumed exactly one of them (`offline-6`), so 28 remain unmined and iteration
3 should draw from that file rather than paying for a fresh sweep. Dimensions represented include
correctness, offline-resilience, frontend-cards, security and tests. The debate council, skipped in
iteration 2 for the reason recorded in `.improvement-loop/LEDGER.md`, returns for iteration 3, which
faces a genuine choice among them.

### From the iteration-2 audit (2026-08-24) — new findings

- [B-015] [polish] The new comment's single line-cite covers the face proof but not the battery proof
  it also claims. `custom_components/tapo_h500/api.py:134-136` reads "docs/protocol-notes.md:131
  establishes -40209 as this H500's answer to a method that exists and was called with the wrong
  shape, and uses exactly that to prove the face and battery methods absent." Line 131 sits inside the
  face-detection section (`being called wrongly answers -40209 or -40211 — that is how the face`),
  with the face conclusion at `docs/protocol-notes.md:132-133`; the BATTERY application of the same
  rule is a separate section at `docs/protocol-notes.md:168-171` ("with none of the `-40209`/`-40211`
  replies that mean \"exists, wrong params\"") that the comment does not cite. Both halves of the
  claim are TRUE and both are in the file — the cite simply under-covers one of them, and a reader
  following the pointer lands on the correct rule. Not a miscite, not misleading, does not block. Fix
  if that comment is ever touched: cite `docs/protocol-notes.md:130-133 and :168-171`.

- [B-016] [polish] The comment justifies excluding -40401 as "invalid stok" only, but on THIS hub
  -40401 with an inner -60502 is also the documented wrong-USERNAME signature.
  `custom_components/tapo_h500/api.py:129-130` (pre-existing from `b30d1d9`, untouched by the
  iteration-2 diff) says "Deliberately not -40401 (invalid stok) or -40413 (invalid nonce), which
  pytapo already retries by itself". But `docs/protocol-notes.md:39` records `| -40401 | login |
  Refused; seen with inner -60502, undocumented |`, and `docs/protocol-notes.md:45-57` documents that
  a TP-Link cloud email in place of the `admin` camera account produces exactly `{'error_code':
  -40401, 'result': {'data': {'code': -60502}}}` and surfaces as a bare `Exception("Invalid
  authentication data")`. So -40401 is genuinely three-way ambiguous here: expired stok (pytapo
  retries it,
  `.venv/lib/python3.14/site-packages/pytapo/transport/pytapo/pytapo.py:256-263`), a wedge from
  repeated logins (`:822-829`), AND a wrong username. Excluding it remains the CORRECT call — the
  ambiguity is precisely why it must fail safe toward retry — but the stated reason is incomplete for
  this device, and a future reader could mistake "pytapo already retries it" for the whole story.
  Pre-existing and out of scope for the iteration-2 diff; its practical consequence (a wrong
  camera-account password still retries forever) is already [B-007]/[B-008].

### Closed during iteration 2 — do NOT re-do

- [B-013] RESOLVED (2026-08-24), applied BEFORE iteration 2's claim was written, which is the only
  point at which a gate may honestly change. The allow-list now greps out the whole
  `.improvement-loop/` prefix instead of enumerating files, so the LOG.md and BACKLOG.md writes every
  close-out performs no longer fail it. Re-run at close-out it exits 0 and prints `ITER2-GATE OK`; the
  command is recorded verbatim in `.improvement-loop/LEDGER.md` under "Green-tree gate — allow-list,
  carried from [B-013]". Its old citation (`.improvement-loop/LEDGER.md:161`) still names iteration
  1's narrower list and is left alone — that is iteration 1's record, not a live gate.

**Still open and untouched by iteration 2:** [B-001] through [B-004] (dx and frontend-cards),
[B-007] through [B-011]. [B-007] is the one to read alongside iteration 2's work — no member of
`AUTH_ERROR_CODES` has a demonstrated real-hardware arrival on this hub, which is a reachability gap
rather than a fail-danger, and the evidence costs a real login against hardware that wedges under
repeated auth. That is the owner's call, not the loop's.

## Iteration 3 close-out (2026-08-24)

**Consumed: `correctness-2`** — "Writing a hub setting refreshes the coordinator but not readings,
so every control snaps back to its old value". Shipped and audited PASS; do NOT re-mine it. It was
never a `[B-xxx]` line in this file — it lived on `.improvement-loop/board-iter3.json`, which is the
candidate pool, so there was nothing here to delete. It has no duplicate id on that board: no other
entry describes a write that fails to confirm. Two OTHER entries there ARE duplicates of each other
— `correctness-i2-1` and `offline-7` are the same finding ("a failed automatic download is never
retried") under two ids, and iteration 4 should score them once, not twice.

**Board state for iteration 4.** `.improvement-loop/board-iter3.json` holds 28 entries; iteration 3
consumed exactly one (`correctness-2`), leaving 27, or 26 distinct after the duplicate above. The
board file is left byte-intact as the record of what the council scored, following iteration 2's
convention.

**`security-2` is disqualified as an ARGUMENT, not as a defect.** It led the raw board at 38.2 and
was struck premise-false in the iteration-3 premise check: its round-2 claim that preview.py's
handlers keep a wild `start_time` from producing a user-visible 500 was checked and found false. The
idea must not be re-crowned on that reasoning. The real defect the check surfaced is filed as [B-020]
below, and it is a plain correctness bug, not the session-churn hazard security-2 argued for.

### From the iteration-3 audit (2026-08-24) — new findings

- [B-017] [must-fix] No test pins the one-shot clear to its position BEFORE the `try`, so a refactor
  that moves it inside latches the status read permanently on while all four new tests stay green.
  `custom_components/tapo_h500/coordinator.py:768` (the clear) vs `:769` (the `try:`);
  `tests/test_coordinator.py:472-481` is the one-shot test and it does not exercise a raising read.
  The auditor built the variant: with the clear moved inside the `try` after the `hub_status` call,
  C1, C3 and C4 all still pass — then with `client.hub_status` raising and the modulo due to skip,
  four consecutive polls gave `_force_status=True, hub_status called 4 times over 4 skipping polls`,
  and it never stops. That is continuous extra traffic against hardware this ledger documents as
  wedging under repeated sessions (`.improvement-loop/LEDGER.md:452-453`), reachable by a refactor
  the suite cannot see. Today the position is guarded only by a `grep -A1` inside C4's
  verify_command, which does not survive into the test suite. Three lines close it: set
  `client.hub_status` to raise, run three skipping polls with the flag set, assert
  `client.calls.count("hub_status") == 1`. Shipped behaviour is CORRECT — this is a coverage gap.

- [B-018] [polish] Forced status reads append off-cadence `storage_trend` samples, and sustained
  writes can evict the 24h history until the fill forecast goes unavailable.
  `custom_components/tapo_h500/coordinator.py:775-778` takes the storage sample inside the branch the
  new flag can force, and its own comment at `:772-774` says "One sample per status refresh, which is
  once a minute" — an assumption the write rate now breaks. `const.py:415-429` documents the same
  cadence and sets `STORAGE_SAMPLES = 1440` / `MIN_TREND_SECONDS = 3600`. Measured against the real
  `trend_samples`/`fill_rate`/`hours_until_full`: 1440 samples at the documented cadence span 86340s
  and forecast 99.995 hours; an automation writing a hub setting every 2s for 48 minutes fills the
  whole cap, the span collapses to 2878s — under `MIN_TREND_SECONDS` — and `hours_until_full`
  returns None, so `sensor.py:493`'s "full in" sensor goes unavailable and stays that way for an hour
  after the writes stop. A modest burst is harmless (200 writes moved the fill rate by -0.0%), so
  this needs a write-heavy automation to bite. One-line fix: sample the trend only when the modulo
  was actually due.

- [B-019] [future] `format_hub_storage` writes to the hub and never refreshes at all — the same
  defect class iteration 3 fixed, at a site it did not touch.
  `custom_components/tapo_h500/__init__.py:472-481`: the service erases every recording
  (`await hass.async_add_executor_job(coordinator.client.format_storage)`, `:477`) and returns with
  no refresh, so the storage sensors keep reporting the pre-format used-percent and free-space until
  the next scheduled status read, up to 60s at the default 2s interval — and after a format that
  stale figure is maximally wrong. Outside C2's scope, which is explicitly the three callers that
  already refreshed, so it failed no claim. The new `coordinator.async_refresh_after_write()` is a
  one-line fix here.

### Surfaced by the iteration-3 premise check — filed here because nobody had

- [B-020] [must-fix] `custom_components/tapo_h500/preview.py:67`
  (`path = await async_preview_clip(hass, coordinator.client, camera, start)`) sits outside every
  `try`, so an out-of-range `start_time` raises through the view and returns a 500 with a traceback;
  `custom_components/tapo_h500/media.py:172-177` also runs above the `try` at `media.py:179`.
  Surfaced by the iteration-3 premise check that disqualified `security-2` — the disqualification was
  of the ARGUMENT, not of this defect. Confirmed on disk at close-out: preview.py's only `try` blocks
  in `get` are at `:51` (the int casts) and `:61-64` (`camera_at`), and `:67` is below both.

## Iteration 4 close-out (2026-08-24) — FINAL

**Consumed: `correctness-1`** — "The storage-nearly-full repair reads two keys `hub_readings` never
emits, so it can never fire". Shipped and audited PASS; do NOT re-mine it. Like `correctness-2`
before it, it was never a `[B-xxx]` line in this file — it lived on `.improvement-loop/board-iter4.json`,
which is the candidate pool, so there was nothing here to delete. The board file is left byte-intact
as the record of what the council scored, following the convention iterations 2 and 3 set.

**Board state at run end.** `.improvement-loop/board-iter4.json` holds 27 entries; iteration 4
consumed exactly one (`correctness-1`), leaving 26, or 25 distinct — `correctness-i2-1` and
`offline-7` are still the same finding under two ids ("a failed automatic download is never
retried"), flagged at the iteration-3 close-out and never merged, because merging them is a board
edit and the board is kept as the record of what was scored. Score it once, not twice.

### From the iteration-4 audit (2026-08-24) — new findings

- [B-021] [polish] `.gitignore` rides along in the tracked diff, outside the declared owner_glob.
  `git diff --name-only` at close-out listed `.gitignore` alongside the three owned paths, and
  `git diff -- .gitignore` adds six lines under "# Claude Code session-recovery runtime state". It is
  NOT this iteration's work: `stat` puts `.gitignore` and the untracked `session-recover.yaml` both at
  2026-08-24 05:02:42, roughly fifteen hours before this iteration's edits (LEDGER.md 19:56:36,
  repairs.py 19:58:57, test_platforms.py 20:00:58), and the content is harness state with no bearing
  on the integration. Pre-declared in C5's allow-list so the claim was passable rather than silently
  defeated by unrelated noise. Failure mode if ignored: whoever runs `git commit -a` sweeps another
  tool's config into the integration's history under a "correctness" subject line. Stage the owned
  paths explicitly, which is what this close-out did.

- [B-022] [polish] The placeholder assertion pins a value shape production never emits.
  `tests/test_platforms.py:184` asserts `translation_placeholders == {"used": "96"}` because the test
  feeds an int; `status.py:222` is `used_percent = round((total - free) / total * 100, 1)`, so a real
  hub carries e.g. `96.2` and the rendered title reads "H500 storage is 96.2% full". No functional
  defect — the float compares correctly against `STORAGE_WARN_PERCENT` and the sentence reads right
  either way, and LEDGER.md pre-declares it as a deliberate non-change. But the test's fidelity to
  `status.hub_readings` covers the key NAME only, not the value TYPE, so any future formatting work
  on `{used}` has no test standing behind it.

- [B-023] [future] Repair-check failures are swallowed at debug level — the same silent-death class,
  one level up. `custom_components/tapo_h500/coordinator.py:911-915`: `try: async_check(...)` /
  `except Exception as err: _LOGGER.debug("Could not update repair issues: %s", err)`. The broad
  except is correct and load-bearing — it is what stops a raise in `_storage` from killing the poll,
  and `tests/test_platforms.py:148` pins it. The residual risk is the LEVEL. If
  `coordinator.readings` ever carried a non-numeric `storage_used_percent`, the
  `used_percent < STORAGE_WARN_PERCENT` comparison raises TypeError, every check after `_storage` in
  `async_check` is skipped for that poll, and the only trace is a debug line nobody has enabled — the
  storage warning silently stops firing again, which is precisely the failure this iteration exists
  to fix. Not introduced by this diff and not reachable today (`hub_readings` only ever yields float
  or None out of `round()`). Worth a warning-level log or a one-shot counter if the checks grow.

- [B-024] [future] The other eight repair checks are still guarded only by a source-text match.
  `tests/test_platforms.py:130-133` splits `REPAIRS` on `def <name>` and `assertIn`s the call names
  for `_storage`, `_reachable` and `_unnamed_faces`. `_storage` is now behaviourally covered, but
  `async_check` (`repairs.py:62-70`) runs nine checks and the remaining eight are protected by the
  same technique that let this defect live behind 1152 green tests. The auditor checked each one's
  input for the same dead-key class and found none live: `_tampered` / `_unnamed_faces` /
  `_silent_cameras` use coordinator methods that exist, `_media` / `_downloads_failing` /
  `_restart_ineffective` use `getattr` with defaults over attributes that exist, and `_reachable`'s
  `last_update_success` is inherited from `DataUpdateCoordinator` (`coordinator.py:71`). So there is
  no second dead branch today — but the `StorageWarning` recorder now makes a behavioural test for
  the other eight nearly free, and the guard against the next one is still a string.

- [B-025] [future] All nine repair issue strings live only in `translations/en.json`, never in
  `strings.json`. `strings.json`'s top-level keys are `title`, `config`, `options`, `selector`,
  `entity` with no `issues` block at all; `translations/en.json:372-374` carries
  `storage_nearly_full` with the title "H500 storage is {used}% full". Nothing is broken now that the
  issue can finally fire — custom integrations load `translations/<lang>.json` at runtime rather than
  `strings.json`, and the placeholder was confirmed to resolve at en.json:373. The gap is that
  `strings.json`, the file core tooling reads, has no issues section, and
  `tests/test_platforms.py:142-146` checks `STRINGS`, which is bound to en.json
  (`test_platforms.py:24`), so no test would notice. Pre-existing across all nine issues; it becomes
  worth closing the day this integration is submitted to core or gains a second language.

## OPEN AT RUN END

The loop stopped here because the owner asked it to finish the checklist and stop, not because the
backlog ran out. Everything above stays valid. If a future run picks this up, these four are the
front of the queue, in this order — the first two are defects in shipped behaviour, the third is the
reason defects survive, and the fourth is a promise the repo has not yet kept.

1. **[B-020] `preview.py:67` raises through the view and returns a 500 with a traceback.** The
   `await async_preview_clip(hass, coordinator.client, camera, start)` call sits outside every `try`
   in `get` — the only ones are at `:51` (the int casts) and `:61-64` (`camera_at`), both above it —
   so an out-of-range `start_time` reaches the user as a stack trace. `media.py:172-177` has the same
   shape above its `try` at `:179`. Confirmed on disk at the iteration-3 close-out. This is the
   highest-value untouched must-fix in the file and it has now sat through a full iteration.

2. **A failed automatic download is never retried, so footage the hub still holds is lost for good.**
   Filed twice on `.improvement-loop/board-iter4.json` as `correctness-i2-1` and `offline-7` — the
   same finding, and a future run must score it once. It bites exactly where this hub is documented
   to be weakest: a clip recorded during a media-session wedge or a network outage is gone once the
   hub's own 24h window closes, even though the hub was holding it the whole time.

3. **[B-001] `tools/verify.sh` is a real gate that nothing automated runs.** There is no `.github/`
   workflow anywhere in the repo. Every green result in this log was produced by a human or an agent
   remembering to run it. Related and cheap alongside it: [B-002], zero static analysis — no ruff,
   flake8 or mypy config exists, so the test suite is the only signal. Iteration 4 is the argument
   for both: a defect lived behind 1152 passing tests because its only coverage was a string match,
   and nothing outside a person's habit was checking even that.

4. **[B-007] The reauth flow iteration 1 shipped has no demonstrated real-hardware trigger.** It is
   correct and fail-safe — it errs toward retry, which is the right direction against a hub that
   wedges — but the reauth screen, the `ConfigEntryAuthFailed` route and the `invalid_auth` string
   may all be dead in the field. Every pytapo login raise site strips the error code
   (`pytapo/transport/pytapo/pytapo.py:606, :693, :716, :764`), so the only live route is a coded
   refusal out of `Tapo.__init__`'s own `getBasicInfo()`, with `-40414 NEED_LOGIN_BY_LOCAL_PASSWORD`
   the realistic arrival — asserted, never observed. Proving it costs a real login against hardware
   this repo documents as wedging under repeated auth, which is the owner's call to make and not the
   loop's. Recorded here so it is not mistaken for settled.
