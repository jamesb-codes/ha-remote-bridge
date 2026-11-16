# Protocol notes: acting as a Companion Link receiver

How the iPhone's Control Center remote finds, pairs with and drives a "TV" over
the Companion Link protocol, from the **receiver's** side.

## Sources

| Ref | Project | Revision | Licence |
|---|---|---|---|
| **P** | [postlund/pyatv](https://github.com/postlund/pyatv) | `b277a4c` (v0.18.0, 2026-06-19) | MIT |
| **A** | [corvofeng/atv-core](https://github.com/corvofeng/atv-core) | `8a4ada2` (2026-10-07) | MIT (`Cargo.toml:7`, workspace licence) |

Both licences are MIT, so porting logic is allowed with attribution (see README
credits). We **port** pyatv's OPACK codec (P `pyatv/support/opack.py`) and write
the rest ourselves. atv-core is the only one of the two that has been tested as
a receiver against a real iPhone, so its message handling is the reference for
anything iOS-specific. atv-core says it is itself a port of `fake_atv.py`
(A `crates/atv-core/src/lib.rs:6-8`), which isn't in either repo.

Below, `A server.rs:256` means `crates/atv-core/src/server.rs` line 256, and
`P companion/api.py:35` means `pyatv/protocols/companion/api.py` line 35.

## 1. Discovery (mDNS)

atv-core registers **three** services under the same instance name
(A `server.rs:256-299`):

| Service | Port | TXT keys |
|---|---|---|
| `_companion-link._tcp` | Companion TCP | `rpMac=1`, `rpHA`, `rpHN`, `rpVr=715.2`, `rpMd=AppleTV14,1`, `rpFl=0x36782`, `rpAD`, `rpHI`, `rpBA` |
| `_mediaremotetv._tcp` | dummy TCP (reads varint frames, never replies, A `server.rs:1098-1112`) | `Name`, `UniqueIdentifier`, `SystemBuildVersion=22K160`, `LocalAirPlayReceiverPairingIdentity`, `ModelName=Apple TV`, `AllowPairing=YES` |
| `_airplay._tcp` | dummy HTTP (`200 OK`, A `server.rs:1148-1157`) | `deviceid`, `features=0x5A7FFFF7,0x1E`, `flags=0x44`, `model`, `srcvers=715.2`, `vv=2`, `pi`, `pk`, `name` |

Derived values (A `server.rs:224-254`):
- `rpHA` = device id (MAC-like `AA:BB:..`) with colons stripped, upper case.
- `rpHN`/`rpAD`/`rpHI` = first 6 bytes of `SHA256("<key>:" + server_identifier)`, hex upper case. These are stable opaque ids; the real values are hashes Apple derives from account keys. pyatv's proxy uses fixed hex strings of the same length (P `scripts/atvproxy.py:1437-1441`).
- `rpBA` = the device id string.
- `pk` (AirPlay) = first 16 bytes of `SHA256("pk:" + server_identifier)` hex. `LocalAirPlayReceiverPairingIdentity` = first 8 bytes of `SHA256("airplay-identity:" + id)`.

`rpFl` flags as pyatv reads them (P `protocols/companion/__init__.py:60,79,655-660`):
`0x4000` = pairing with PIN supported, `0x04` = pairing disabled.
`0x36782` has `0x4000` set and `0x04` clear. atv-core's comment says the
`rpFl` value "gate[s] features like the volume keys" (A `server.rs:3-6`).

**Open question (section 6, item 2):** is the Companion advert enough on its own? We
**Answered on iOS 27.0.1: Companion alone is enough** (listed, verified, remote
session started). The dev CLI now advertises only Companion and keeps the
stubs behind `--with-stubs`. Downside of the `_airplay._tcp` advert: the device
will probably also show up as an AirPlay target on the phone.

## 2. Framing

Every frame: `[type:1][length:3, big-endian][payload]`
(P `companion/connection.py:98-119`; A `server.rs:649-690`).

Frame types (P `companion/connection.py:21-40`; A `lib.rs:36-47`):
`1 NoOp, 3 PS_Start, 4 PS_Next, 5 PV_Start, 6 PV_Next, 7 U_OPACK, 8 E_OPACK, 9 P_OPACK`.

Auth frames carry an OPACK dict `{"_pd": <TLV8 bytes>, ...}`. TLV8 tags
(P `auth/hap_tlv8.py:13-33`): `0 Method, 1 Identifier, 2 Salt, 3 PublicKey,
4 Proof, 5 EncryptedData, 6 SeqNo, 7 Error, 10 Signature, 0x11 Name/extra-info`.
Values longer than 255 bytes are split into repeated tags and concatenated on
read (P `auth/hap_tlv8.py:83-104`; A `tlv.rs:20-50`). Error codes
(P `auth/hap_tlv8.py:43-52`): `1 Unknown, 2 Authentication, 3 BackOff,
4 MaxPeers, 5 MaxTries, 6 Unavailable, 7 Busy`.

After pair-verify, `E_OPACK` payloads are ChaCha20-Poly1305 encrypted:
- receiver output key = `HKDF-SHA512(salt="", info="ServerEncrypt-main", shared)`, input key = `...info="ClientEncrypt-main"` (P `companion/server_auth.py:131-132`; A `session.rs:527-531`; client mirror P `companion/protocol.py:40-42`)
- nonce = 64-bit little-endian counter, zero-padded to 12 bytes, separate counters per direction (P `support/chacha20.py:30-73` with `nonce_length=12`, P `companion/connection.py:92`)
- AAD = the 4-byte frame header, whose length **includes** the 16-byte tag (P `companion/connection.py:103-116`; A `session.rs:194-208`)
- empty payloads aren't encrypted (P `companion/connection.py:104,115,148`)

## 3. Pair-setup (SRP-6a, PIN shown on the receiver)

Parameters (P `companion/server_auth.py:47-71`; A `srp.rs:1-120`): username
`Pair-Setup`, password = the PIN as typed, RFC 5054 3072-bit group, g=5,
SHA-512, 128-bit salt. srptools conventions: `k = H(N | PAD(g))`,
`x = H(s | H("Pair-Setup:" + PIN))`, `u = H(PAD(A) | PAD(B))`, `K = H(S)`,
`M1 = H(H(N) xor H(g) | H(I) | s | A | B | K)`, `M2 = H(A | M1 | K)`, with
minimal big-endian integer encoding outside `PAD` (A `srp.rs:30-40,72-171`).

> **PIN encoding:** pyatv zero-pads to 4 digits (`str(pin).zfill(4)`,
> P `companion/pairing.py:77`). atv-core formats a `u32` with no padding
> (A `srp.rs:78`), which only works because it uses `1111`. To make both
> interpretations agree we generate PINs in **1000–9999**.

| Step | Direction | Content | Source |
|---|---|---|---|
| M1 | phone → `PS_Start` | `_pd={Method:0, SeqNo:1}`, `_pwTy:1` | P `companion/auth.py:54-62` |
| M2 | `PS_Next` → phone | `_pd={SeqNo:2, Salt, PublicKey:B, 27:0x01}`, `_pwTy:1` | P `companion/server_auth.py:144-153`; A `session.rs:371-391` |
| M3 | phone → `PS_Next` | `_pd={SeqNo:3, PublicKey:A, Proof:M1}` | P `companion/auth.py:82-94` |
| M4 | → phone | `_pd={SeqNo:4, Proof:M2}`, or `{SeqNo:4, Error:2}` if the proof is wrong | P `companion/server_auth.py:155-168`; A `session.rs:393-422` |
| M5 | phone → | `_pd={SeqNo:5, EncryptedData}` encrypted with `HKDF("Pair-Setup-Encrypt-Salt","Pair-Setup-Encrypt-Info",K)`, nonce `"PS-Msg05"`; inner TLV `{Identifier:pairing_id, PublicKey:LTPK, Signature, Name?}`, signature over `HKDF("Pair-Setup-Controller-Sign-Salt","...-Info",K) + id + LTPK` | P `auth/hap_srp.py:165-205` |
| M6 | → phone | `{SeqNo:6, EncryptedData}` (nonce `"PS-Msg06"`), inner TLV `{Identifier:server_id, PublicKey:our LTPK, Signature, 0x11: OPACK{altIRK, accountID, model, wifiMAC, name, mac}}`, signature over `HKDF("Pair-Setup-Accessory-Sign-Salt","...-Info",K) + server_id + LTPK` | P `companion/server_auth.py:170-217`; A `session.rs:424-488` |

Pair-setup ciphers use the 8-byte-nonce variant, with the ASCII label left-padded with zeros to 12 bytes
(P `support/chacha20.py:49-62,79-106`; A `crypto.rs:44-52`).

`server_id` is the receiver's UUID string, upper case, sent as UTF-8 bytes
(A `session.rs:133`; P `companion/server_auth.py:80`).

**Where we differ from both references (deliberately, for security):**
1. **We verify the client's M5 signature** and store `pairing_id → LTPK`. pyatv's fake and atv-core skip this.
2. **SRP `b` is random per attempt.** Both references reuse the long-term key seed as `b` (P `companion/server_auth.py:68`; A `srp.rs:92`).
3. **The PIN is random per attempt and only accepted while pairing is allowed.** Otherwise M2 is `{SeqNo:2, Error:6 (Unavailable)}`.

## 4. Pair-verify (X25519 + Ed25519)

| Step | Direction | Content | Source |
|---|---|---|---|
| M1 | phone → `PV_Start` | `_pd={SeqNo:1, PublicKey: client X25519}`, `_auTy:4` | P `companion/auth.py:136-144` |
| M2 | `PV_Next` → phone | `_pd={SeqNo:2, PublicKey: our X25519, EncryptedData}`; inner `{Identifier:server_id, Signature: Ed25519(our_pub + server_id + client_pub)}`, key `HKDF("Pair-Verify-Encrypt-Salt","Pair-Verify-Encrypt-Info", shared)`, nonce `"PV-Msg02"` | P `companion/server_auth.py:99-136`; A `session.rs:490-547` |
| M3 | phone → `PV_Next` | `_pd={SeqNo:3, EncryptedData}`; inner `{Identifier: pairing_id, Signature: Ed25519_client(client_pub + pairing_id + our_pub)}`, nonce `"PV-Msg03"` | P `auth/hap_srp.py:111-124` |
| M4 | → phone | `_pd={SeqNo:4}`, then turn on main-channel encryption | P `companion/server_auth.py:138-142`; A `session.rs:549-567` |

**Difference:** both references accept M3 without checking it, so *any*
device that runs pair-verify gets an encrypted session. **We decrypt M3, look
up `pairing_id`, and verify the signature with the stored LTPK.** If that fails
we reply `{SeqNo:4, Error:2}` and leave encryption off. We also use a fresh
ephemeral X25519 key per verify, which is standard HAP; the references use a fixed key.

## 5. Session messages (encrypted E_OPACK)

Message envelope (P `companion/protocol.py:54-60,143-153`): `{"_i": name,
"_t": 1 event | 2 request | 3 response, "_c": content, "_x": xid}`. A response
echoes `_x` and carries `_c` (A `session.rs:210-228`). We reply only when
`_t == 2`. atv-core replies whenever `_x` is present, but pyatv adds `_x` to
events too (P `companion/protocol.py:180-183`). Replying to an event is harmless (the
client logs "no receiver"), so it is safe either way.

Client connect sequence (pyatv, P `companion/api.py:153-159`): `_systemInfo` →
`_touchStart` → `_sessionStart` → `TVRCSessionStart` → `_tiStart` →
`_interest{_regEvents:["_iMC"]}`.

| `_i` | Our reply / action | Source |
|---|---|---|
| `_systemInfo` (iOS 27 may put the name in `model` rather than `_i`) | empty response, then push `SystemStatus`/`TVSystemStatus {state:3}` events | A `session.rs:572-579,774-810`; P `companion/api.py:187-211` |
| `_sessionStart {_srvT, _sid}` | response `{_sid: random u32}` | A `session.rs:584-610`; P `companion/api.py:213-225` |
| `TVRCSessionStart {ProtocolVersionKey}` | response echoing the version, **then immediately** `MediaControlStatus{MediaControlFlags:0x100}` + `_iMC{_mcF:0x100}` events: "Current iOS versions make the volume/mute decision immediately after session startup" | A `session.rs:611-636,247-269` |
| `_sessionStop` | empty response | A `session.rs:637-645` |
| `_touchStart` | response `{_i:1}` | A `session.rs:651-656` |
| `_touchStop`, `_tiStop` | empty response | A `session.rs:663-667` |
| `_tiStart` | response `{_tiE:false}` | A `session.rs:726-733` |
| `_interest {_regEvents:[...]}` | for each name push current state: `MediaControlStatus`, `_iMC`, `SystemStatus`/`TVSystemStatus`, `NowPlayingInfo` (empty) | A `session.rs:668-708` |
| `FetchMediaControlStatus` | response `{MediaControlFlags:0x100}` | A `session.rs:709-719` |
| `FetchAttentionState` | response `{state:3}` (Awake) | A `session.rs:750-760`; P `companion/api.py:77-85` |
| `FetchSiriRemoteInfo`, `FetchCurrentNowPlayingInfoEvent`, unknown requests | empty response | A `session.rs:720-722,811-818` |
| `_hidC {_hidC: code, _hBtS: 1 down / 2 up}` | empty response; emit RawInput | P `companion/api.py:305-309`; A `session.rs:823-906` |
| `_hidT {_tPh, _cx, _cy, _ns, _tFg}` | touch tracking (below) | P `companion/api.py:311-326`; A `session.rs:953-1052` |
| `_mcc` / `MediaControlCommand {_mcc: n, ...}` | `5 GetVolume` → `{_vol: float 0..1}`; `6 SetVolume {_vol}`; `7 SkipBy {_skpS}`; `1 Play`, `2 Pause`; `12 GetCaptionSettings` → `{_cse:false}` | P `companion/api.py:59-74,395-399`; A `session.rs:908-949` |

Volume capability: `MediaControlFlags.Volume = 0x0100` (P `companion/__init__.py:87-99`; A `session.rs:28`).
pyatv answers `_iMC` by sending GetVolume (P `companion/__init__.py:439-446`). atv-core
re-sends `MediaControlStatus`/`_iMC` after each volume key "so iOS refreshes its
volume HUD" (A `session.rs:271-292,850`).

### HID codes (`_hidC`)

From P `companion/api.py:35-56` and A `session.rs:41-66`:

| Code | pyatv name | iOS Control Center meaning (atv-core) |
|---|---|---|
| 1–4 | Up, Down, Left, Right | D-pad |
| 5 | Menu | Back |
| 6 | Select | Select / click |
| 7 | Home | TV button |
| 8 / 9 | VolumeUp / VolumeDown | phone volume keys |
| 10 | Siri | |
| 11 | Screensaver | |
| 12 / 13 | Sleep / Wake | |
| 14 | PlayPause | |
| 15 / 16 | ChannelIncrement / Decrement | |
| 17 | Guide | |
| 18 | PageUp | **Mute** on iOS 26/27 (A `session.rs:60-63`) |
| 19 | PageDown (pyatv: `control_center`) | **Power** on iOS 26/27 (A `session.rs:60-63`) |

`_hBtS`: 1 = button down, 2 = button up (P `companion/api.py:308`). atv-core
acts only on up (A `session.rs:829`). We forward both, because long-press detection needs them.

### Touchpad (`_hidT`)

`_tPh` phase (P `const.py:460-466`): `1 Press, 3 Hold(move), 4 Release, 5 Click`.
Coordinates run 0–1000 on both axes (P `companion/api.py:88-89`). atv-core
(A `session.rs:30-31,953-1052`) records the origin at phase 1 and decides at phase 4:
`max(|dx|,|dy|) < 60` → tap (select); `≥ 150` → swipe in the dominant direction
(`+dy` = down); in between → ignored. We use the same defaults and make the swipe threshold configurable.

pyatv's `click()` sends `_hidC 6` down/up **and** a `_hidT` phase 5
(P `companion/api.py:373-393`). If iOS does the same, a click would arrive as
both a HID select and a touch tap, so the gesture layer must de-duplicate. To be
confirmed on a real phone.

## 6. Known unknowns: status

| # | Question | Status |
|---|---|---|
| 1 | TXT flags for listing / power / volume | **Confirmed on iOS 27.0.1:** with `rpFl=0x36782` and the `0x100` volume flag, the phone lists the device and shows volume, mute (`_hidC 18`) and power (`_hidC 19`). |
| 2 | Does iOS need `_mediaremotetv` (and `_airplay`) too? | **No (iOS 27.0.1).** With only `_companion-link._tcp` the device is listed, verifies and starts a remote session. The stubs stay optional (`--with-stubs`) in case older iOS versions need them. |
| 3 | Swipe vs D-pad vs click; how a long TV press arrives | **Confirmed:** swipes = `_hidT` only; click = `_hidC 6` (+ an ignored `_hidT`); long TV press = `_hidC 7` down…up. iOS 27 sent no `_hidC 1-4`. |
| 4 | Can the receiver request keyboard input? | Not explored yet; `_tiStart` answered with `_tiE:false`. |
| 5 | Two phones connected | atv-core tracks one active connection per peer IP and supersedes old ones (A `server.rs:611-615,662-666`). We allow parallel sessions, each emitting inputs. **Confirm.** |

### Manual iPhone results (2026-11-16)

**Device:** iPhone17,1 on **iOS 27.0.1 (24A446)**, Control Center remote.
**Adverts:** first run Companion + MRP + AirPlay; second run Companion only, which also worked (listed and connected, buttons not re-tested).

- **Pairing:** listed as "Test TV", random PIN accepted, M5 signature verified;
  the phone closed the pairing connection, reconnected, and pair-verify passed.
- **Connect sequence (iOS 27):** `_systemInfo` (has `_i` *and* `model`) →
  `_sessionStart` → `TVRCSessionStart` → `FetchAttentionState` →
  `_interest[TVSystemStatus]` → `FetchSiriRemoteInfo` →
  `_interest[PushSiriRemoteInfo]` → `_interest[SupportedActions]` →
  `FetchSupportedActionsEvent` → `_interest[NowPlayingInfo]` →
  `FetchCurrentNowPlayingInfoEvent` → `_interest[TopShelfItems]` →
  `FetchCurrentTopShelfItemsEvent` → `_interest[MediaControlStatus]` →
  `FetchMediaControlStatus` → `_touchStart` → `_tiStart` →
  `PublishPresenceEvent`, `FetchUpNextInfoEvent`, `FetchLaunchableApplicationsEvent`.
  Empty replies to the Fetch* we don't implement were accepted.
- **Disconnect:** `TVRCSessionStop` → `_interest _deregEvents…` → `_touchStop` →
  `_tiStop` → `_sessionStop`.
- **Touchpad:** iOS 27 shows a touchpad, not a D-pad. Swipes arrive only as `_hidT` streams
  (~16 ms apart) and decoded correctly as up/down/left/right. **No `_hidC` 1–4 seen.**
- **Click:** `_hidC 6` down+up **and** one trailing `_hidT`. No duplicate tap was
  emitted (the tracker ignores it), so no de-dup is needed.
- **Buttons seen:** play_pause `14`, back `5`, TV/home `7`, **mute `18`**,
  **power `19`**, volume up/down `8`/`9` (phone hardware keys). This confirms
  atv-core's iOS 26/27 mapping and that volume, mute and power controls appear.
- **Long TV press:** a plain `_hidC 7` down … up held for 3.9 s; no separate
  command. Long-press detection belongs in the gesture layer, as planned.
- **Siri, skip ±10 s:** not exercised.
- **Privacy:** `_systemInfo`, `PublishPresenceEvent` and
  `SwitchActiveUserAccountEvent` carry the iCloud account, Siri profile and
  installed-app list. The receiver never logs their content (`_loggable`).
  `PublishPresenceEvent`/`SwitchActiveUserAccountEvent` accompany most presses.
