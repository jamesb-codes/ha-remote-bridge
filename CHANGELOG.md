# Changelog

## Phase 0–1: protocol research and standalone receiver

- `docs/protocol-notes.md`: discovery → pairing → session → button press, with
  every TXT key and message sourced to pyatv / atv-core file and line.
- `protocol/` (no Home Assistant imports): OPACK, TLV8, SRP-6a server,
  HAP pair-setup/verify, Companion framing + encryption, HID/touch decoding,
  minimal state replies, mDNS TXT builders.
- Security beyond the references: phone signatures are verified in pair-setup
  M5 and pair-verify M3 (unpaired or forged keys are refused), random SRP `b`,
  ephemeral X25519 per verify, random PIN per attempt, pairing on/off switch,
  failed-attempt limit.
- Dev CLI: `python -m remote_bridge.protocol.dev`.
- Tests: pyatv as a real client (pair, every button, volume, hold, swipe,
  click, skip, power), plus refusal cases and codec cross-checks.

## Phase 2: gesture layer

- `gesture.py` (no Home Assistant imports): raw inputs → `(control, press_type)`
  with short / long / double / repeat, an injectable clock and scheduler, and
  per-phone state.
- Long press fires at the threshold and suppresses the short; a long TV press
  is `input/long` with no stray `home/short`. Only controls with a double
  mapping wait out the double-tap window, so navigation stays instant.
  Repeat can be switched on or off per control.
- Touchpad swipes map to directions and a tap to select. The trailing touch
  iOS sends after a click is de-duplicated. The volume slider becomes relative
  steps, and skip becomes skip_forward/skip_back.
- 43 table-driven tests, 100% branch coverage, strict mypy.
