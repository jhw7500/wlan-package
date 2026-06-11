# wlan-opc / Protocol TODOs and Spec Ambiguities

Tracked spec questions, deferred decisions, and call-site back-references.
Each entry should be linked from a `// TODO(proto-todo:<id>): ...` comment in
the source so we can grep both ways.

## T1. Default UDP port

- **Spec**: TBD (section 3.1.2)
- **Our default**: `50607`, overridable via `/usr/local/opc/etc/opc.conf`
- **Call sites**: opcd config loader, vhlctl default `--port`
- **Resolve when**: vendor confirms a permanent value

## T2. List Boundary Flag values — RESOLVED (page-24: start=0x0000)

- **Spec inconsistency**: page 22 body says `start=0x0001 / continue=0x0000 / end=0x0002 / start+end=0x0003`; page 24 field description says `start=0x0000 / continue=0x0001 / end=0x0002`. The inconsistency is real in the **original docx** (not a transcription artifact).
- **Resolution (2026-06-11)**: re-checked the original docx — a body sentence that had been
  dropped from the markdown transcription reads "시작 리스트(0x0000)와 계속 리스트(0x0001)를
  수신하면…", i.e. **2 of 3 mentions in the original support start=0x0000**. Combined with the
  vendor confirmation recorded at `protocol/commands.h` (page-24 field description authoritative),
  **start=0x0000 / continue=0x0001 / end=0x0002 stands; START_END(0x0003) does not exist**
  (atomic single-frame commit unsupported — callers send START then END).
- **Code**: `protocol/commands.h:270-272` already implements page-24 values — correct as-is.
- **Historical note**: this entry previously said "Our choice: page 22 (chosen by user in
  interview)" and `seed.yaml:36` still records the page-22 values — both were superseded by the
  vendor confirmation; seed.yaml is a historical scaffold record and is left unmodified.

## T3. Header `length` field vs UDP payload — RESOLVED (original docx figures)

- **Spec text**: "Length max 1416 B" AND "UDP payload max 1424 B" with a 64-byte-header diagram (그림 3-6).
- **Resolution**: the original docx byte-map figures are authoritative and self-consistent. Common header = **64 bytes** (8-byte fixed part + 56-byte Reserve at bytes 8..63); body starts at **offset 64**; `Length = total_frame - 8` (= Reserve 56 + payload). Then payload max 1360 -> frame/UDP max 1424 -> Length max 1416, all matching the spec text. The earlier "64 + 1416 = 1480 > 1424" contradiction was a misreading: Length is `total - 8` (reserve + payload), NOT the body length.
- **Our choice**: `OPC_FIXED_HEADER_SIZE = 8`, `OPC_HEADER_SIZE = 64`, `OPC_PAYLOAD_MAX = 1360`, `OPC_FRAME_MAX = 1424` (`protocol/proto.h`, static_assert-pinned).
- **Call sites**: `protocol/proto.h`, `protocol/frame.c`, recv loop in opcd
- **Status**: resolved — header 64 B / `Length = total - 8` confirmed by the original docx figures (그림 3-6 + per-command formats). Code refactored M1->M2; spec.md / seed.yaml updated to match.
- **History**: a transient 60-byte / `total - 4` reading (the `.md` reconstruction's "60 ┘" reserve marker mistaken for the body offset) was implemented and then reverted once the docx figures showed the Reserve ending at byte 63 (its last row labelled 60) with the body at 64.

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

## T10. Header `Length` field — per-command values — RESOLVED (original docx figures)

- **Rule**: `Length = total_frame - 8` (8-byte fixed header excluded; counts Reserve 56 + payload). Equivalently `Length = payload + 56`. Worked examples:
  - Login Req payload 128 -> frame 192 -> Length 184
  - Login Ack payload 4 -> frame 68 -> Length 60
  - GetBasicInfo Ack payload 16 -> frame 80 -> Length 72
  - GetDeviceInfo Ack payload 352 -> frame 416 -> Length 408
- **Empty-body requests**: Logout / GetBasicInfo / GetDeviceInfo / Reset transmit ONLY the 8-byte fixed header (no Reserve), so `Length = 8 - 8 = 0` falls straight out of the rule — NOT an exception. (Reset Ack's spec "0" was a separate typo; see T11, now 60.)
- **Our choice**: body-bearing frames use `opc_frame_build` (64-byte header + payload at offset 64); empty requests use `opc_empty_frame_build` (8 bytes). Per-command `OPC_*_LENGTH` macros carry the literal value. Receivers validate **frame size**, not Length (`opc_frame_parse` requires >= 8 B and slices body = frame_len - 64 when present).
- **Call sites**: `protocol/commands.h` per-command `*_LENGTH` macros, `protocol/frame.c::opc_frame_build` / `opc_empty_frame_build`
- **Status**: resolved — `total - 8` with empty requests = 8-byte frames, confirmed by the docx figures. With T11 (Reset Ack = 60) the rule has **zero exceptions**.

## T11. Reset Ack `Length` — RESOLVED (spec typo, 60 adopted)

- **Spec text**: "응답 포맷 (Length=0 ※헤더상 Length=0, Result/Error Cause 포함)" — the spec writes Length=0 while explicitly stating the Result+ErrorCause body IS present.
- **Why it is a typo**: (1) the line directly above is the Reset *Request* `(Length=0)`, legitimately empty — the `0` looks copy-pasted into the response box; (2) every other Ack with the same 4-byte Result/ErrorCause body uses Length 60; (3) setting it to 60 removes the **only** remaining exception, making the Length rule fully uniform (`no body -> 0`, `body -> payload+56`).
- **Our choice**: treat the spec `0` as a typo and emit **Length=60** like every other simple Ack (`OPC_RESET_ACK_LENGTH = 60`). Parsing is unaffected either way (receivers slice the body by frame size, not by Length).
- **Call sites**: `protocol/commands.h::OPC_RESET_ACK_LENGTH`, `protocol/commands.c::opc_reset_ack_pack`, `protocol/tests/test_codec.c::test_reset`
- **Status**: resolved — 60 adopted (user decision). If a real device is ever observed emitting 0, revert this single macro.

## T12. GetDeviceInfo Ack reserve area trailing length — RESOLVED

- **Spec figure**: the response payload ends around offset 412 with a trailing reserve block.
- **Under M2 (64-byte header, `Length = total - 8`)**: Length 408 -> frame 416 -> payload = 416 - 64 = 352 (offset 64..415). The trailing reserve runs to byte 415 (40 bytes); the figure's "412" label is one row short.
- **Our choice**: trust Length=408, emit a 352-byte payload (`OPC_GET_DEVICE_INFO_ACK_BODY_LEN`) with a 40-byte trailing reserve.
- **Call sites**: `protocol/commands.c::opc_get_device_info_ack_pack`
- **Status**: resolved under the 64-byte-header / `total - 8` model; payload = 352 B.

## T13. SetRadioConfig (0x1004) — WLAN#2 FREQ/CH order reversed in spec — RESOLVED

- **Spec text**: the Korean markdown transcription (`opc_vhl_protocol_Rev1.00_KO.md`) showed
  WLAN#1 "FREQ then CH" but WLAN#2 "CH then FREQ".
- **Resolution (2026-06-11)**: checked the original docx
  (`무선기판공통제어통신사양서_Rev1.00_KO.docx`) — the §3.3.8 request-format figure
  (image33.emf) shows **WLAN#2 FREQ then CH**, identical to WLAN#1 and to the
  GetDeviceInfo layout. The reversal was a transcription error introduced while
  converting the docx to markdown, not a spec inconsistency. The markdown has been
  corrected in place (76-byte row + correction note).
- **Code**: already correct — `opc_set_radio_config_req_pack/unpack` packs both WLANs
  uniformly FREQ→CH (`protocol/commands.c:541-543`). No code change needed.
  (Historical note: an earlier revision of this entry said "swap inside the codec";
  the code was later unified to FREQ→CH and this entry had gone stale.)

## T14. Roaming Indication (0x0004) — CH Number offset row table mis-aligned — RESOLVED

- **Spec text (as transcribed)**: "64 SNR|RSSI / 68 Connect AP MAC (6B) / 72 CH Number" — read
  literally, the "72 CH Number" row appeared to overlap the 6-byte MAC.
- **Resolution (2026-06-11)**: checked the original docx figure (image42.emf) — the MAC(6Byte)
  cell at row 68 is vertically merged into the **left half of row 72 (bytes 72..73)**, and
  CH Number is an independent cell in the **right half (bytes 74..75)**. No overlap exists in
  the original; the apparent mis-alignment was an artifact of flattening the merged-cell figure
  into one text row per offset. The markdown transcription has been corrected
  ("72 (MAC 계속) | CH Number (74~75)"). Same pattern fixed in §3.4.4 ApDisconnect (image44).
- **Code**: already correct — SNR(1)+RSSI(1)+reserve(2) at body 0..3, MAC(6) at 4..9,
  CH Number(2) at body 10..11 (= frame 74..75), Length=68
  (`protocol/indications.c::opc_ind_roaming_pack/unpack`). No change needed.

## T15. Reset (0x2001) Ack — figure says Length=0 but the frame carries Reserve+Result (68B)

- **Spec figure (original docx, image38.emf — confirmed 2026-06-11)**: the Reset response
  figure marks **Length=0** while simultaneously drawing Reserve 8..63 and Result|Error Cause
  64..67 — i.e. a 68-byte frame whose Length field would be 60 under the universal
  Length = total − 8 rule (T3/T10). The figure is self-contradictory.
- **Our choice**: follow the universal rule — emit Length=60 (`OPC_RESET_ACK_LENGTH`,
  `protocol/commands.h:406`). Consistent with every other simple ack on the wire.
- **Risk**: if the vendor's VHL implements the figure literally (expects Length=0 in the Reset
  ack), it may reject our ack. Conversely our SEC-003 length gate would reject a vendor frame
  that declares Length=0 while carrying 68 bytes.
- **Resolve when**: vendor confirms the intended Length for the Reset ack (0 vs 60).
