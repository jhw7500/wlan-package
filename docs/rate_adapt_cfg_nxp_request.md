# NXP request: SD9098 firmware overwrites a configured `rate_adapt_cfg` static pair

> Supersedes the copies in `../rate-static-pair-20260914-0e769997/` and
> `../rate-load-boundary-20260914-0a2484b3/`, both left sealed in their campaigns. This version adds
> the traffic-profile dependence and the finding that the overwritten value is the **live** rate-
> selection state.

## Summary

On SD9098 with firmware `17.92.1.p149.115` (blob SHA256
`7c3ef6e12d3cfc9bd638d1571ccf6ddd2e96e0ed179ec70664ccb1df0ba29e57`, `sd9098_wlan_v1.bin`, 659428 B),
a static `rate_adapt_cfg` pair applied before association is silently replaced by `40/65` a few
seconds after aggregated Tx traffic starts, but only when the configured LOW threshold is at or above
a boundary we have bracketed at `58 < LOW <= 60`.

We can characterise the behaviour from the host but cannot identify the firmware code that performs
the write. This request asks for the specific artefacts that would close it.

## What we measured

Eight controlled arms, each on a fresh firmware download, with the nine background WLAN services on
our platform stopped, 60 s of traffic at 10 packets/s to the gateway, and `RATE_ADAPT_CFG` GET polled
every 50 ms. Command tracing was enabled for the trial window only and every trace was verified
gapless. Association was stable on one AP at 5180 MHz throughout; ping loss was zero in every arm.

| configured (`sr low high timer`) | endpoint | transition |
| --- | --- | --- |
| `1 70 90 10` | **40/65** | yes, in a 50 ms window 2.15 s after traffic start |
| `1 60 80 10` | **40/65** | yes, in a 60 ms window 3.02 s after traffic start |
| `1 58 90 10` | 58/90 | no |
| `1 55 90 10` | 55/90 | no |
| `1 50 90 10` | 50/90 | no |
| `1 50 70 10` (twice) | 50/70 | no |
| `1 0xff 0xff 10` | dynamic | no |

Observations:

1. **LOW governs; HIGH does not.** `HIGH=90` occurs in both a transitioning configuration (`70/90`)
   and three non-transitioning ones (`58/90`, `55/90`, `50/90`).
2. **The boundary depends on the traffic profile.** On the same link, same AP, same day:

   | profile | traffic | measured retries per MPDU | boundary |
   | --- | --- | ---: | --- |
   | light | 10 packets/s, 56 B | 0.676 | `58 < LOW <= 60` |
   | intermediate | 100 packets/s, 56 B | 0.512-0.534 | `LOW <= 65` |
   | heavy | 1000 packets/s, 1400 B | 0.392-0.410 | `75 < LOW <= 80` |

   With `70/90` the relaxation fired in 9 of 9 light-profile trials and in 0 of 2 heavy-profile
   trials. So the condition is not a fixed threshold on the configured value; it depends on the
   traffic or link regime as well. Aggregation depth is not the variable: MPDUs per A-MPDU stayed
   between 1.00 and 1.03 in every trial of both profiles.
3. **Dynamic mode is exempt.** `0xff/0xff` was never replaced.
4. **The change is a single discrete step**, not a gradual drift: it lands between two GETs 50-60 ms
   apart.
5. **No host command is involved.** In the transitioning arms the only host commands in the whole
   window were one `11N_ADDBA_RSP`, one `11N_ADDBA_REQ` and one `STA_CONFIGURE` channel-info GET, and
   the `STA_CONFIGURE` occurred 22.3 s and 18.9 s *after* the change. There was no additional
   `RATE_ADAPT_CFG` SET and no firmware lifecycle command.
6. **The change is in the command response payload, not host formatting.** The `0x8264` response
   carries `... 01 28 41 0a 00` — `sr=1`, `low=0x28` (40), `high=0x41` (65), `interval=0x0a`.
7. **A-MPDU participation is required.** In an earlier campaign, blocking TX A-MPDU on TID 0 kept the
   pair at `70/90`; restoring it reproduced `40/65`.
8. **AMSDU feedback is irrelevant.** The change reproduced with `amsduaggrctrl` both enabled and
   disabled.

## Questions

1. Which firmware component writes the `RATE_ADAPT_CFG` low/high fields after they have been set by
   `HostCmd_CMD_RATE_ADAPT_CFG`, and under what condition?
2. **What are the definitions and selection conditions of the landing pairs, and how is one chosen?**
   We previously reported that every transition landed on exactly `[40,65]` (18 of 18). That is no
   longer true. Across all campaigns we now count 21 transitions: 20 landed on `[40,65]` and one, from
   a configured `60/90`, landed on **`[30,50]`** - in a single step, with no intermediate value, under
   50 ms polling. `[30,50]` is also the pair recorded in a 2026-08-28 observation that we had wrongly
   dismissed as unreproducible. Notably the same `LOW=60` landed on `[40,65]` when paired with
   `HIGH=80` but on `[30,50]` when paired with `HIGH=90`, so `HIGH` participates in choosing the
   landing pair even though it does not affect whether a transition happens. Are these named static
   profiles, and what selects among them?
3. What exactly is the "aggregated data Tx success rate" that the LOW and HIGH thresholds are compared
   against — which counters, over what window, per peer or per TID?
4. **What quantity is `LOW` compared against?** We have established it is not a fixed constant: the
   boundary is `58 < LOW <= 60` under a light profile and `75 < LOW <= 80` under a heavy one. The
   direction is what a success-rate comparison would predict (the heavy profile has the lower
   per-MPDU failure rate and needs a higher `LOW`), but we could not identify the quantity. We tested
   and rejected two candidates, each registered before the trials that tested it:

   - a success rate of `1/(1 + retries_per_mpdu)` fits the light boundary at 59.7 % but predicts about
     71.5 % for the heavy profile, where `72/90` and `75/90` both failed to relax and only `80/90` did;
   - a boundary linear in measured `retries_per_mpdu` correctly predicted that `70/90` relaxes under
     the intermediate profile, then wrongly predicted that `65/90` would not — it did.

   We also note the proxy is noisy: the same intermediate profile gave `retries_per_mpdu` of 0.5341
   and 0.5122 on consecutive fresh-FW boots. Please tell us which counters, over which window, and per
   which peer or TID the firmware actually uses.

   **New controlled evidence (2026-09-14).** We have now moved this from correlation to intervention.
   Holding the traffic profile and the configured pair fixed at `60/90` and changing only the STA Tx
   power pre-association, the transition happened at default power (`retries_per_mpdu` 0.7225) and did
   not happen at 2 dBm (0.0050, about 145x better). The boundary therefore rose from below 60 to
   `60 < LOW <= 70`. This is consistent with a success-rate comparison, but it does not explain
   item 9 below.
5. Is the `0xff/0xff` exemption intentional?
6. ~~Does the GET response report the thresholds actually used by live rate selection?~~
   **We have now measured this: it does.** An interface configured `1 70 90 10` and overwritten to
   `40/65` selected transmit rates indistinguishable from a genuine `1 40 65 10` configuration (mean
   58.66 versus 58.11 Mbps) and clearly different from `1 40 90 10` (53.95 Mbps), with matching MCS
   distributions. So the overwrite changes effective behaviour, not just the readback. Please confirm
   whether that is the intended design.
6b. **The power-on default is exempt.** With no `rate_adapt_cfg` SET since the firmware download, the
   interface reports dynamic (`0xff/0xff`) and stays dynamic through the same traffic that overwrites a
   static pair - 967 of 967 samples, zero transitions. An explicit `1 0xff 0xff 10` SET behaves the
   same. Only *static* pairs are overwritten.
6c. **The landing value is invariant.** All 18 transitions we have observed, from five different
   starting pairs (70/90 x14, 58/90, 60/80, 65/90, 80/90), landed on exactly `40/65`. Is `40/65` a
   named profile in firmware, and under what conditions would a different one be selected? We have one
   historical observation of `30/50` from 2026-08-28, but it was confounded by an abnormal SDIO reset
   and a failed load from a wrong firmware path, and it never reproduced.
7. Given that, **is a configured static pair intended to be overridable at all?** If the override is
   deliberate, we need the conditions documented so we can choose a configuration that is stable. If
   it is not deliberate, we need a firmware fix.
8. **Is there any supported way to re-apply the pair on an associated interface?** We observe that an
   associated SET prints the requested value while the immediate GET still returns the overwritten
   one, consistent with your documented pre-association-only restriction. If that is correct, the only
   correction path available to us is a disconnect and reconnect, which the value then drifts away from
   within 2-3 seconds of traffic - so no monitoring-based correction is viable.

9. **Why does a lower `LOW` relax sooner?** With the same light traffic profile and the same fresh-FW
   boot procedure, a configured `70/90` relaxed 2.90 s after traffic started, while `60/90` relaxed
   after only 0.24 s. A lower threshold is easier to satisfy, so we would expect it to relax later or
   not at all. The observed direction is the opposite and we have no mechanism for it.

10. **Is the relaxation ever multi-step?** All 21 transitions we have recorded are single-step at 50 ms
    polling resolution. If firmware can step through intermediate pairs under conditions we have not
    produced, we need to know, because our monitoring assumes one discrete change.

## Artefacts requested

1. The source commit, ELF/MAP, or symbol file matching the loaded firmware blob SHA256 above.
2. A matching diagnostic firmware, or a patch, that traces rate-adaptation threshold writes, together
   with a documented trace-only activation procedure. `MFW_D` is not sufficient: the host driver only
   hexdumps what the firmware elects to send under `PKT_TYPE_DEBUG`
   (`mlan/mlan_sta_rx.c:474-493`) and there is no rate-control debug category.
3. Documentation of the static profile set and its selection logic.
4. Confirmation of whether a configured static pair is intended to be durable for the life of the
   association, since `README_MLAN` states the value can be set only before associating and our
   measurements show it does not survive traffic at `LOW >= 60`.

## Why we cannot answer these ourselves

We hold only the firmware binary. There is no ELF, MAP or symbol file in our driver tree, and the
driver source we do have proves only that a host command crossed the host-to-firmware boundary — it
cannot observe a write performed inside the firmware.

## Host platform details

- Driver: `wlan-driver-v2` at `d877d7036f59ec539d02c8affe1a3113b419ac08`
- Utility: `/usr/local/bin/mlanutl`
- Interface: `mlan0`, STA mode, WPA2-PSK, 5180 MHz, 20 MHz, MCS 7 at about -52 dBm
- Firmware load: fresh download each boot, confirmed via `Wlan: FW download over, firmwarelen=659428`
- Evidence: raw per-arm JSONL traces, per-arm audits and a cross-arm comparison are available on
  request
