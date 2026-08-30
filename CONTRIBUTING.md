# Contributing

## Before anything else: the hub is fragile

**It wedges under repeated authentication**, and recovers on a timeout rather
than on a retry. Everything about this codebase follows from that — one login
per setup, a lock around every hub call, a lock around every media session,
`PARALLEL_UPDATES = 1` on the platforms that write.

So: **no test may talk to real hardware.** The suite stubs `pytapo` entirely
and always will. If a change seems to need a live hub to verify, say so in the
pull request and describe what you observed; do not add something that
connects.

Nothing may make the hub or its cameras contact TP-Link. No cloud check, no
telemetry, no update ping. That is the point of the project.

## The gate

One command, and it must pass before anything is committed:

```
bash tools/verify.sh
```

It runs the whole suite, the card suite, lint, an AST parse of every module,
a YAML/JSON sweep, the translation-key check, an audit for filesystem work on
the event loop, and a check that no test file has drifted past its own
`unittest.main()` guard. It needs `pyyaml`, `jinja2`, `node` and `ffmpeg`.

Coverage is a ratchet, not a target:

```
python -B tools/coverage.py --gate
python -B tools/card_coverage.py --gate
```

Neither floor may fall. If a change adds uncovered lines, cover them — do not
lower a floor.

## Tests

- **Stdlib `unittest` only.** No pytest, no fixtures library, no mock
  framework beyond `unittest.mock`.
- **Drive the code, do not read it.** A test that asserts a string appears in
  a source file proves the code was written, not that it works. Where the
  behaviour can be run, run it.
- **Mutation-check anything non-trivial.** Break the new behaviour on purpose,
  confirm a test fails, restore. `tools/mutate.py` holds the standing map of
  paths worth protecting; add to it when you add one.
- **Say why in the test.** A docstring explaining what breaks in the real
  world if this stops holding is worth more than the assertion.

## Dependencies

**Do not add to `manifest.json` `requirements`.** The integration depends on
`pytapo` and nothing else, deliberately. Tools and tests may use what is
already installed; a new one has to be justified in the pull request.

## Commits

One change per commit, with a message that says what changed and why. No
attribution trailers of any kind.

Do not bump the version in `manifest.json` or author a release commit —
releases are tagged separately.
