# Improvement Loop — Operating Profile
RunId: ril-20260823-215339    Started: 2026-08-23T21:53:39+00:00
ProjectRoot: /home/sujeo/Development/tapo-h500-for-home-assistant
Branch: improvments  (cut from main @ 340260b "release: 0.123.0")

ProjectType: Home Assistant custom integration (HACS), domain `tapo_h500`
Stack: Python 3.14 / stdlib `unittest` / node (card tests) / vanilla-JS Lovelace card
Size: 31 python modules, ~9.1k LOC + 1.35k LOC single-file card JS; 56 test files, 1134 tests
Maturity: mature — deep test suite, no CI, no static analysis
Concept: n/a (existing project, not a greenfield bootstrap)

AuditorPersona: "senior Home Assistant integration engineer" — reflexes: blocking I/O inside the
  event loop, un-awaited coroutines, DataUpdateCoordinator refresh/backoff semantics, config-entry
  and unload lifecycle, unique_id / translation_key / device-registry correctness, entity property
  purity (no I/O in properties), exception handling that must not kill the update loop, and — first
  among equals — that no change can make the hub reach the internet.

ScoutDimensions: [correctness, tests, ha-conformance, offline-resilience, security, performance,
                  frontend-cards, docs, dx]

SelectionDebate:            # scale for the debate-council skill invoked at Act 2 SELECT
  rounds: 3                 # propose -> contest -> converge
  verifyTop: 2              # finalists whose load-bearing premises get fact-checked against real files
  auditor: { name: "Jordan Ruiz", role: "neutral judge — scores, never argues" }
  personas:
    - { name: "Ada Okonkwo",  discipline: "reliability engineer",
        lens: "the hub is fragile hardware that wedges; does this survive a wedged, rebooting, or clock-drifted hub?" }
    - { name: "Priya Raman",  discipline: "Home Assistant core reviewer",
        lens: "integration quality scale — async purity, entity/device registry, config flow, translations, unload" }
    - { name: "Tomas Lindqvist", discipline: "test engineer",
        lens: "what does the 1134-test suite NOT cover, and which claim here is actually falsifiable?" }
    - { name: "Mei Sato",     discipline: "frontend engineer",
        lens: "the 1350-line card is a product surface with one test file; what breaks in a real dashboard?" }
    - { name: "Idris Bello",  discipline: "security & privacy engineer",
        lens: "credentials, the derived media key, and the no-internet mission — does this leak or phone home?" }

ExtraWorkflowSteps:
  - name: no-cloud-guard
    readonly: true
    slot: before audit
    purpose: >
      Diff-scoped grep proving the iteration adds NO code path that can make the hub or the
      integration contact TP-Link or any WAN host. This project's whole premise is a hub with no
      internet access (README "Local only, by design"; commit aa3612b "never command the hub to
      contact TP-Link"). A regression here is a [blocker], not a [must-fix].
  - name: hub-safety-guard
    readonly: true
    slot: before audit
    purpose: >
      Prove the iteration adds no new authentication attempt, extra concurrent login, or new port-8800
      media session against the live hub. The real H500 wedges under repeated auth and only recovers on
      a timeout; port 8800 has been observed refusing connections after a rejected session. Also assert
      the test suite stayed fully offline (no live-hub sockets) and that `tools/probe_live.py` was
      neither run nor invoked by any test.

VerificationSurface:
  tests:     bash tools/verify.sh          # 1134 unittest + node card tests + AST/YAML/JSON/translation-key checks
  smoke:     python3 -B -m unittest discover -s tests -p 'test_*.py'
  lint:      none                          # no ruff/flake8 configured — see DeepeningBacklog
  typecheck: none                          # no mypy configured — see DeepeningBacklog
  build:     none                          # HACS integration; no build step
  BaselineGreen: "Ran 1134 tests in 14.511s / OK; card tests OK; 31 modules parse; 9 yaml files valid;
                  json valid; translation keys resolve"

Guardrails:
  - scoped+reversible changes only; `bash tools/verify.sh` must exit 0 before any commit
  - NEVER add a code path that commands the hub, a camera, or the integration to contact TP-Link or
    any internet host. This is the product's reason to exist. Violation = [blocker], revert immediately.
  - NEVER make a live network call to the hub during the loop: do not run tools/probe_live.py, do not
    authenticate, do not open a port-8800 session. The hardware wedges under repeated auth. All
    verification is offline and mocked.
  - NEVER add a new entry to manifest.json `requirements` (HA/HACS ships these to real installs)
  - NEVER bump manifest.json `version` and never author a `release: X.Y.Z` commit — releases are the
    owner's call, not the loop's
  - NEVER read, print, edit or commit `.env`, or any credential/password value
  - NEVER delete or edit files the loop did not create; specifically leave the untracked
    `tapo-h500-media-session-wedge-development-plan.md` alone (owner's working doc)
  - stay on branch `improvments`; never push, never open a PR, never touch `main`
  - no attribution trailers in any commit (no Co-Authored-By, no "Generated with") — owner's global rule
  - commit each green iteration so every change is individually revertable

MinValueFloor: 55
  # Deliberately above the 40 default: 1134 tests and a careful owner mean the cheap wins are gone and
  # churn is the real risk. An idea has to earn its diff here.

DeepeningBacklog:
  - Add CI: a GitHub Actions workflow running `tools/verify.sh` on push/PR. `tools/verify.sh` is a
    real gate that nothing automated ever runs (no .github/ exists at all).
  - Add a static-analysis surface (ruff config + clean pass) — the project currently has zero lint or
    typecheck, so the auditor has only tests to lean on.
  - Split the two oversized modules if the auditor flags them: coordinator.py (1136 lines),
    clips.py (921 lines).
  - Raise coverage on the least-tested module (measure first; do not guess).
  - Give the 1350-line card a real test harness — today `tests/test_cards.mjs` is its only check.
  - docs/: an architecture map of the 31 modules and how coordinator/clips/media relate.

StopChannels: ["touch .improvement-loop/STOP", "say 'stop' in chat"]
