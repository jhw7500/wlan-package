# wlan-opc / Protocol TODOs and Spec Ambiguities

Tracked spec questions, deferred decisions, and call-site back-references.
Each entry should be linked from a `// TODO(proto-todo:<id>): ...` comment in
the source so we can grep both ways.

## T1. Default UDP port

- **Spec**: TBD (section 3.1.2)
- **Our default**: `50607`, overridable via `/usr/local/opc/etc/opc.conf`
- **Call sites**: opcd config loader, vhlctl default `--port`
- **Resolve when**: vendor confirms a permanent value

## T2. List Boundary Flag values

- **Spec inconsistency**: page 22 says `start=0x0001 / continue=0x0000 / end=0x0002 / start+end=0x0003`; page 24 field description says `start=0x0000 / continue=0x0001 / end=0x0002`
- **Our choice**: page 22 wording (chosen by user in interview)
- **Call sites**: SetIpConfigList handler (`opcd/handler.c` once Phase 2), vhlctl `set-ip-list` packer
- **Resolve when**: vendor confirms which page is authoritative; flip values + test vectors if needed

## T3. Header `length` field vs UDP payload

- **Spec text**: "Length max 1416 B" AND "UDP payload max 1424 B" with 64-byte header — geometrically incompatible (64 + 1416 = 1480 > 1424)
- **Our choice**: effective payload max = 1360 B (1424 − 64); we keep 1416 as a constant in `proto.h` only for traceability
- **Call sites**: `protocol/proto.h::OPC_PAYLOAD_MAX`, recv loop in opcd
- **Resolve when**: vendor clarifies which figure is correct

## T4. Password storage format

- **Spec**: silent (only error codes for "invalid characters" and "NULL termination")
- **Our choice**: **plain text** in 1st stage with a `FIXME(proto-todo:T4)` boundary, file mode `0600`, owned by root (or the opcd service user)
- **Call sites**: opcd password store, SetPassword handler, vhlctl `set-password` packer
- **Resolve when**: security review answers "plain vs hashed (which algorithm) vs HSM-backed"

## T5. IEEE 802.11r / 11ai / 11k / 11v support flags

- **Spec**: GetDeviceInformation response carries one byte each (`0x00` unsupported / `0x01` supported)
- **1st-stage default**: `0` (unsupported) — emitted by the `platform_hook` stub
- **Call sites**: `platform_hook::caps`, GetDeviceInformation builder
- **Resolve when**: NXP88W9098 driver inquiry confirms which subset the silicon advertises

## T6. FaultDetect (0x0010) congestion thresholds

- **Spec**: enumerates `CPU / Memory / Disk-I/O / Network-I/O` congestion IDs but leaves thresholds open
- **1st-stage**: no-op probe; thresholds live in `opc.conf` with default values left as TBD
- **Call sites**: `opcd/indication.c::fault_detect_probe`, opc.conf loader
- **Resolve when**: vendor and/or system integration team agree on per-resource thresholds

## T7. Vendor maximum response time

- **Spec**: "If the regulation response time cannot be met, vendor provides the max response time per radio model"
- **1st-stage**: we use the regulation timers (1 s / 2 min) and a Result=NG fast-path on overload
- **Call sites**: handler timeout policy
- **Resolve when**: vendor publishes a max-response-time matrix

## T8. Reset (0x2001) re-init mechanism

- **Spec**: "after ack send, the device resets"
- **Our choice**: opcd `exit(0)` after sending the Acknowledgment; systemd `Restart=always` brings it back up
- **Call sites**: Reset handler, opcd.service
- **Resolve when**: never (this is a decided constraint, kept here for traceability)

## T9. ResetNotice (0x0020) trigger sources

- **Spec**: "emitted before the device autonomously resets"
- **1st-stage**: only emitted from the Reset command path (stub emitter). Watchdog or fault-driven autonomous reset paths are future work and must be hooked into this same emitter.
- **Call sites**: `opcd/indication.c::reset_notice_emit`
- **Resolve when**: watchdog / fault paths land

## T10. Header `Length` field — internally inconsistent across commands

- **Spec evidence**:
  - Login Req body 128 → Length 184 (i.e. `body + 56`)
  - Login Ack body 4 → Length 60 (`body + 56`)
  - Logout Req body 0 → Length 0 (NOT `body + 56`)
  - GetBasicInfo Req body 0 → Length 0 (NOT `body + 56`)
  - GetBasicInfo Ack body 16 → Length 72 (`body + 56`)
- **Our choice**: each per-command codec helper writes the spec-literal Length value, and the frame layer just transports it verbatim. Receivers do not strictly validate Length; they validate the **frame size** vs the expected-size table per command.
- **Call sites**: `protocol/commands.h` per-command `*_LENGTH` macros, `protocol/frame.c::opc_frame_build` `length_field` parameter
- **Resolve when**: vendor clarifies which row of the spec is authoritative

## T11. Reset Ack body present but `Length=0`

- **Spec text**: "응답 포맷 (Length=0 ※헤더상 Length=0, Result/Error Cause 포함)"
- **Reading**: the Acknowledgment carries a 4-byte Result+ErrorCause body in the same offsets as every other Ack, but the Length field on the wire is 0.
- **Our choice**: pack the 4-byte body in the body area as usual, but write Length=0 in the header per spec. Receivers parse the body from the frame regardless of Length.
- **Call sites**: future `opc_reset_ack_*` in `commands.h/.c`
- **Resolve when**: vendor confirms whether Length should be 60 like the other simple Acks

## T12. GetDeviceInfo Ack reserve area trailing length

- **Spec text**: shows the response payload ending at offset 412 with a reserve block "376 ┐ Reserve / 412 ┘"
- **Reverse-engineering Length=408**: frame size = 408 + 8 = 416, body = 416 - 64 = 352 → the trailing reserve must be 40 bytes (376..415), not the 36 bytes (376..411) the table suggests.
- **Our choice**: trust the Length=408 number, emit a 352-byte body with a 40-byte trailing reserve.
- **Call sites**: future `opc_get_device_info_ack_*` in `commands.h/.c`
- **Resolve when**: vendor confirms reserve area size
