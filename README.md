# Remote Bridge

A Home Assistant custom integration (work in progress) that lets the **Remote in
iPhone/iPad Control Center** drive anything in Home Assistant. It advertises
"virtual TVs" on your network. The phone pairs with one, and each button press
becomes a Home Assistant event/action.

Compatible with the iPhone's Apple TV Remote. Unofficial; not affiliated with
Apple. An iOS update could break it.

## Status

Phases 0–3 are done: protocol research ([docs/protocol-notes.md](docs/protocol-notes.md)),
a standalone receiver, the gesture layer, and the Home Assistant integration skeleton
(events, device triggers, pairing UX). The mapping UI comes next.

## Try the standalone receiver

Needs Python 3.14 and a machine on the same network/VLAN as the phone.

```bash
python3.14 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
PYTHONPATH=custom_components/remote_bridge .venv/bin/python -m protocol.dev --name "Test TV"
```

Open Control Center → Remote, pick **Test TV**, type the PIN the terminal
prints, and press buttons. Each decoded input is printed. Options:
`--with-stubs` (also advertise MRP/AirPlay discovery stubs), `--no-pairing`,
`--swipe-threshold`, `-v` (log every protocol message).

## Development

```bash
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/mypy custom_components/remote_bridge/protocol
```

Tests use [pyatv](https://github.com/postlund/pyatv) as a real Companion client.

## Credits

- [pyatv](https://github.com/postlund/pyatv) (MIT): protocol reference; the
  OPACK codec is ported from it.
- [atv-core](https://github.com/corvofeng/atv-core) (MIT): the receiver-side
  reference that showed the iPhone remote works against a non-Apple device.
