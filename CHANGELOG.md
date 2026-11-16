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
