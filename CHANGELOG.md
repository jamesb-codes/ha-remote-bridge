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

## Phase 3: Home Assistant integration skeleton

- Config flow: name plus port (0 = auto). One entry is one virtual TV and gets a
  device in the device registry.
- Advertised through HA's shared zeroconf instance (IPv4 announce addresses
  only) and unregistered on unload. No second Zeroconf instance is created.
- Identity, port and paired-phone keys are kept in `.storage/remote_bridge.<entry>`
  (private, 0600) and flushed on unload. The last port is reused so phones
  reconnect.
- Pairing: the PIN appears as a persistent notification. "Allow new pairings"
  defaults on and switches itself off after the first successful pairing;
  turning it back on clears the failed-PIN lockout. A blocked attempt raises a
  notification explaining how to allow it.
- Entities: `binary_sensor.<tv>_connected`, `sensor.<tv>_last_button`, and a
  `button.<tv>_forget_paired_devices` (which also re-opens pairing).
- Every press fires `remote_bridge_button`
  `{entry_id, device_id, name, control, press_type, client, value|text}`.
  There are 27 device triggers, with labels generated from one control table.
- Requires Python 3.14 (HA 2026.9+). Tests pass on HA 2026.9.2 and 2026.10.4,
  including a real pyatv phone pairing, pressing buttons and firing a
  device-trigger automation.
- The dev CLI now runs without Home Assistant:
  `PYTHONPATH=custom_components/remote_bridge python -m protocol.dev`.

## Phase 4: mapping UI (v0.2.0)

- Options are now a menu: Navigation, Volume & power, Media, Input / source,
  Timing and Pairing. Each page holds a few action pickers (any Home Assistant
  action sequence), validated before saving.
- `mapper.py` runs the mapped actions as parallel scripts with the variables
  `control`, `press_type`, `virtual_tv`, `client`, `value` and `text`. A failing
  action is logged and never drops the phone's session.
- Holding a direction or volume key repeats its short-press action, so there is
  no separate repeat mapping.
- Input / source: **single** runs one action (e.g. an IR "Input" code);
  **cycle** steps through up to 6 named inputs on each long press of the TV
  button, wrapping round. `select.<tv>_input_source` shows the current input,
  can be corrected by hand without sending anything, and survives restarts.
- Timing page: long press, double-press window, repeat delay and interval,
  swipe distance, which controls repeat, and TV double-press detection.
- All option changes apply live, without a reload, so phones stay connected.
- Behaviour change: the TV button's single press is now instant. It only waits
  for a possible double press when a double-press action is mapped or
  detection is turned on.

## v0.2.1

- Links and code owner updated for the renamed GitHub account (jamesb-codes).
