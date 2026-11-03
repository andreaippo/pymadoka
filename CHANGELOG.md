# Changelog

## Unreleased

- **Opt-in command retry with short attempts.** A command used to wait once,
  10 s, for its reply and then fail. `Connection.command_timeout` and
  `Connection.command_max_tries` (public and mutable, like `pair_timeout`)
  now set the length and number of attempts; only a timeout is retried, a
  dropped link still fails at once. Defaults keep the old single 10 s
  attempt. A reply to an attempt that timed out can still arrive; the next
  that many replies for that command (within `stale_response_window`, 5 s)
  are dropped so a straggler cannot answer the next request. The guard is
  cleared when the link drops.

- **The owner of reconnects can learn about a link drop at once.**
  `Connection` and `Controller` take an optional `disconnected_callback`, a
  zero-argument callable invoked when the live link drops (not on
  `cleanup()`/`stop()`, and not for a failed candidate's client). A caller
  that runs with `reconnect=False`, such as Home Assistant's coordinator,
  used to notice the drop only on its next poll, up to a whole scan interval
  later, and every command in between failed with the link down. An
  exception raised by the callback is logged and does not affect the
  library's own disconnect handling.

- **Feature and command logs name the device.** With more than one
  thermostat on the same Home Assistant, the debug lines of each device
  interleaved and could not be told apart. Feature logs now start with
  `[address] ClassName` (the new `Feature.log_id`), and the payload and chunk
  lines in `Connection.send` start with `[address]`. The line logged after a
  successful update now shows the status written instead of the parsed
  response, whose contents do not reflect the device state. Messages and log
  levels are otherwise unchanged.

- **A reply left incomplete no longer fails the next request.** When a reply
  lost its last chunk (a query that timed out, or a link that dropped mid-
  reply), its first chunks stayed in the transport. The next reply for the
  same command flushed them as a failed message, and that failure cancelled
  the head pending request of the command: the new one, which was about to
  succeed. The caller got "connection lost" with the link up, and the real
  reply then arrived with nobody waiting for it. A leftover partial is now
  dropped when a command is sent and when the link drops, and an incomplete
  message is logged and discarded instead of cancelling a request. Chunks are
  also checked in sequence: only chunk 0 starts a message and only the next id
  continues it, so a repeated or skipped chunk is dropped instead of restarting
  the message.
- **A command whose link is gone fails at once instead of after 10 s.** A
  request waiting for its response when the link dropped, or when the
  connection was cleaned up, was never failed: the caller held the operation
  lock for the full 10 s response timeout and got `TimeoutError`. Pending
  requests are now cancelled on disconnect and on cleanup, so the caller gets
  a `ConnectionException` right away.
- **A command whose chunks could not be written no longer breaks the next
  query.** `send()` raised `ConnectionException` but left the request queued,
  and the caller had no way to remove it. The next reply for that command
  resolved the orphan, and the next query timed out after 10 s although the
  thermostat had answered it. The request is now dropped, and sending stops
  at the first chunk that cannot be written instead of going on to deliver a
  truncated message. A reply also skips requests that are already finished
  instead of being lost on them.

## v0.5.0

- **Display brightness and contrast, and the eye is the status ring.** A new
  `Display` feature (function 0x0302, write 0x4302) reads and writes the levels
  of the on-board display, each one byte on a 0-19 scale, as recovered from a
  Bluetooth HCI capture of the official app:
  - 0x32 `brightness`: the display
  - 0x31 `contrast`: the display
  - 0x33 `ring_brightness`: the status ring around the display

  0x33 is the parameter `EyeBrightness` wrote. It dims the ring, not the
  screen, so it now lives on `Display` under that name.

  A query carries no argument and the device answers the whole block; the
  arguments not modelled are kept in `DisplayStatus.other`. A write carries
  only the levels that are set, one at a time as the app does, and `update()`
  merges it into the cached status so the other two levels do not flap to
  unknown until the next poll.

  **Breaking:** `Controller.eye_brightness` is replaced by
  `Controller.display`, so the same function is not polled twice. Read and
  write `controller.display.status.ring_brightness` /
  `controller.display.update(DisplayStatus(ring_brightness=n))` instead.
  `EyeBrightness` and `EyeBrightnessStatus` are still importable, deprecated.

## v0.4.3

- **The fan speeds a unit accepts are read from the device.** `FanSpeed` now
  queries function 0x0050 with no argument, the way the official app does, so
  the device answers the whole block instead of the two speeds the query used
  to carry. Arguments 0x12 (cooling) and 0x13 (heating) are a mask of the
  accepted speeds, exposed as `FanSpeedStatus.cooling_speeds` and
  `heating_speeds`; arguments not modelled are kept in `other`.

  Only bit 0 is mapped, and it means the automatic speed:
  `FanSpeedStatus.supports_auto` is false when either mask lacks it. Read off
  four indoor units: the three that take the automatic speed report 0x0d for
  heating, the one that refuses it (from the official app too) reports 0x0c.
  The cooling mask is 0x1d on all four. The other bits do not line up with
  low/mid/high in any obvious way, so nothing is claimed about them.

  `supports_auto` stays true until the masks have been read, and `update()`
  carries the masks (and a speed it did not write) over from the last read,
  so a write does not bring the automatic speed back until the next poll.
  `FanSpeedStatus()` now takes both speeds as optional; positional calls are
  unchanged.

## v0.4.2

- **A negative outdoor temperature is no longer read as 128 or more.** The
  outdoor value (function 0x0110, argument 0x41) is one byte, and it was read
  as an unsigned integer: -5 C would have come out as 133 C. It is now decoded
  as sign and magnitude (bit 7 set means below zero, the low seven bits are the
  degrees), the way the protocol's reverse engineer decodes it in the OpenHAB
  binding. 0xFF still means the unit reports no outdoor sensor. No capture
  below 0 C has confirmed the rule yet; positive values are unchanged.

## v0.4.1

- **A round that ends connected no longer hides the paths that failed before
  it.** `Connection.last_round_evidence` exposes the per-path verdicts of the
  most recent candidates round, in the same shape and with the same
  attribution rule as `PairingRequiredError.evidence`, and it is set on every
  round, successful ones included. Until now those verdicts only left the
  library inside the error, so a path that timed out while pairing, followed
  by one that authenticated, was invisible to the caller.

  Seen on a Home Assistant installation on 2026-09-18: HA kept routing a
  thermostat through a proxy whose bond was dead. Every attempt there put a
  pairing prompt on the thermostat screen, and every round still ended
  connected through another proxy, so the caller never learnt which proxy had
  timed out and could never stop using it.

  The path that connected is not listed (read `connected_source`). A round
  that authenticates on its first path leaves the mapping empty. Nothing else
  changes: no verdict, streak or exception is computed differently.

## v0.4.0

- **VAM ventilation units are modelled by the library, not by its callers.**
  Function `0x0031` — how a VAM (Ventilation Air Management / HRV) keeps its
  ventilation mode and fan speed — now has a `Ventilation` feature, with
  `VentilationStatus` and `VentilationModeEnum` beside it. It was written and
  measured by @Frank802 on a VAM350J8VEB behind a BRC1H (firmware 1.10.3) and
  lived, until now, inside the Home Assistant integration, which had to reach
  into the controller to attach it. Protocol knowledge belongs here.

  A write serializes **only the argument it sets**, and `update()` merges the
  result back over the previous status rather than replacing it. Both matter
  for the same reason: the unit applies whatever it is sent and never reports a
  rejection, so a stale companion argument would silently overwrite a value the
  caller never meant to touch.

- **`Controller` takes a `device_type`, and a VAM stops polling function
  `0x0050`.** `DEVICE_TYPE_THERMOSTAT` (the default, and what every earlier
  release assumed) gives the controller `fan_speed`; `DEVICE_TYPE_VENTILATION`
  gives it `ventilation` instead. Never both.

  A VAM does answer `0x0050`, which is why this went unnoticed — but every
  argument comes back with length 0 and none of them ever change, so `FanSpeed`
  there could neither read nor write anything while still costing a query round
  trip on every poll. An unknown `device_type` is treated as a thermostat.

  **Not hardware-validated.** No VAM was available to the maintainer, then or
  since. What is verified is that a thermostat controller is unchanged.

- `Controller.update()` now documents that its walk over `vars(self)` is a
  supported extension point rather than an oversight: a caller may attach its
  own `Feature` and have it polled with the rest, and the Home Assistant
  integration relies on this for its energy-consumption feature. Replacing it
  with a fixed list of attribute names would silently stop polling that.

## v0.3.13

- **A path proven bondless keeps that verdict when it later merely times out.**
  The retained-refusal branch documents itself as applying "whatever it failed
  with this time", but it was tested one branch below `pair_timed_out` — and a
  keyless proxy's normal failure IS a timeout, because the prompt goes up on
  the thermostat and nobody answers it. So the branch could only ever see the
  rarer failures, and the proof was discarded on exactly the rounds it was
  written for.

  The verdict therefore decayed from `"rejected"` to `"timeout"` from the
  second round onward, and a consumer that acts only on proven refusals — as
  it must, since congestion times out on healthy bonds too — charged nobody.
  The proxy kept its place in the caller's bonded list, which is precisely the
  list the pairing veto trusts, so every reconnect put a fresh six-digit code
  on the thermostat screen with nothing able to conclude otherwise.

  Measured (2026-08-28): one proxy refused a thermostat's bond once with
  "Insufficient authentication", then timed out on every round for the next
  eleven hours. A second, independent tool had already named it as holding no
  key for that device; this library could not, and its own retained proof was
  sitting unused the whole time.

  Ordering is the entire fix: retained proof now sits directly below
  `_UnbondedPath` — a round that never called `pair()` still says nothing
  about any bond — and above every failure classifier. Nothing else changes:
  only a PROVEN refusal is ever retained, never a timeout, and a successful
  authenticated connect through that path still discards it.

  This also settles a latent hazard in the same round: per-path verdicts are
  collapsed with `dict(zip(...))`, where later duplicates win. That is safe
  only while "proven sources are unique per round", which HA's re-scoring
  breaks routinely — it elects whichever proxy is free, so several attempts in
  one round land on the same one. A refusal followed by a timeout on that path
  used to be recorded as a timeout; both are now `"rejected"`, so the collapse
  can no longer lose the stronger evidence.


## v0.3.12

- **Pairing is no longer attempted on a path the caller did not sanction.**
  New `allowed_sources_callback` on `Controller`/`Connection`: it returns the
  proxy sources the device may pair through, and it is checked against the
  path the backend *actually* used, read after `establish_connection` and
  before `pair()`. This closes the hole v0.3.11 could only report: filtering
  the candidate list cannot control where a connect lands, because habluetooth
  keeps just the address and re-scores every path itself. A connection routed
  somewhere unsanctioned is now dropped without pairing, so no
  numeric-comparison prompt ever appears on the thermostat screen — the
  failure mode that put eight prompts on one BRC1H in an hour, through a proxy
  that was not in its allowed list at all.

  Fails open at every step, deliberately: no callback, a callback that raises,
  an empty allowed set, or a path the backend cannot name all pair as before.
  The guard can only ever remove a pairing opportunity, so a broken policy
  must never be the reason a device cannot connect.

- **New `PairingRequiredError` reason: `"unbonded_path"`.** Raised when every
  path chosen was unsanctioned for `UNBONDED_PATH_ROUNDS` (3) consecutive
  rounds, with `evidence` naming the proxy the connection keeps landing on. It
  is a statement about ROUTING, not about a bond: nothing was refused and
  nothing timed out, because nothing was attempted. Consumers must not evict a
  bond on it — the remedy is to pair with the named proxy deliberately.
  `PathVerdict` gains the matching `"unbonded"` value.

## v0.3.11

- **`connected_source` and the per-path evidence now name the path Home
  Assistant actually used, not the candidate we offered.** habluetooth's
  `HaBleakClientWrapper` keeps only the *address* of the `BLEDevice` handed to
  `establish_connection` and re-picks a scanner by RSSI on every connect, so
  the candidate has always been an intention rather than a fact — and the two
  disagree in practice (a thermostat was observed pairing through a proxy that
  had been filtered out of its candidate list entirely). Recording the
  intention as fact is what let a proxy that never carried a session be marked
  as holding a bond, and a refusal be charged to a proxy that was never in the
  conversation. The wrapper publishes the winning scanner once the link is up,
  which is exactly when a bond rejection is raised — by `client.pair()`, after
  `establish_connection` has already returned — so the case that matters is
  attributable.

- **A failure to connect at all is now charged to nobody.** Before a link
  exists nothing names the path, so `PairingRequiredError.evidence` carries a
  `None` key for that attempt and `_rejected_sources` records nothing.
  `tried_sources` still lists what was aimed at, because that is the only thing
  worth telling a human. Consumers already skip falsy sources — but one that
  falls back to `tried_sources` when `evidence` names no proven path must stop
  doing so, or it will re-introduce the guess.

  Deliberate consequence: retained refusal proof can only be applied to a path
  that produced a link, so a device whose connections fail *before*
  establishing reaches a rejection verdict more slowly. Consumers are expected
  to carry a verdict-independent brake for that case.

## v0.3.10

- **`PairingRequiredError` now says WHY it was raised.** The same class was
  raised from two epistemically different places with the same attributes and
  a byte-identical message claiming the device *"refused the authenticated
  bond"*: a path that explicitly rejected the bond (proof — a human must
  re-pair), and every path merely timing out for `PAIRING_TIMEOUT_ROUNDS`
  rounds (an inference — on a bonded path a pairing timeout means congestion).
  Consumers could only tell them apart through an undocumented side channel.
  New attributes:
  - `reason`: `"rejected"` (default, so existing constructor calls keep their
    meaning) or `"timeout_streak"`.
  - `timeout_rounds`: consecutive all-timed-out rounds behind the verdict;
    `0` for a rejection.
  - `evidence`: per-source verdict (`"rejected"` / `"timeout"` /
    `"transient"`), so a consumer can attribute a refusal to a SPECIFIC proxy
    even when several paths were tried.

  The `"timeout_streak"` message no longer claims a refusal: it reports that
  pairing did not complete within the budget on any path and suggests a
  re-pair only if it persists.
- **The ambiguity streak is reset at the streak verdict too.** Only the
  rejection branch reset it, so after an accusation the counter stayed past
  the threshold and the very next classified-timeout round re-raised
  immediately (`4 >= 3`) — including the user-initiated retry that follows,
  which deserves a fresh budget.
- **The fallback single-device path forgives the streak on success.** Only the
  candidate loop did, so a device that recovered through the fallback kept a
  stale streak armed.
- **Proven rejections survive a mixed round.** `every_path_failed_auth` was
  computed from this round alone, so two proven rejections plus one
  transiently-failing third proxy took the transient branch and the proof was
  discarded — one flapping proxy could postpone a legitimate conclusion
  forever. A source that explicitly refused the bond is now remembered until
  it authenticates again. Conservative by construction: only PROVEN
  rejections are retained, never timeouts and never plain transient failures,
  so it never becomes easier to convict a healthy device.
- **Public streak API**, so consumers stop reaching for
  `_pairing_timeout_rounds`: `reset_pairing_timeout_rounds()`,
  `resume_pairing_timeout_rounds(rounds)` (restore a streak carried across
  Connection rebuilds, clamped to >= 0) alongside the existing
  `pairing_timeout_rounds` property, plus a `rejected_sources` view.

## v0.3.7

- **One path decision per connection attempt**: the candidate loop now calls
  `establish_connection` with `max_attempts=1`. Under Home Assistant, the
  bluetooth client wrapper rescores every available path (RSSI + failure
  counts) on each connect attempt, so a multi-attempt call could silently
  fail over to a stronger-signal — possibly unbonded — proxy mid-call
  (observed in the field: an unbonded proxy captured the BRC1H's single
  central slot through SMP auth timeouts, keeping the thermostat unreachable
  for over 20 minutes). Retries and the failover decision now stay with the
  candidate loop. Note: `bleak-retry-connector`'s `ble_device_callback`
  parameter is vestigial in 4.6.0 (declared but never read) and cannot pin
  the path.
- **`connected_source` is reset on disconnect and cleanup** so callers never
  read a stale path after the connection that used it is gone.

## v0.3.6

- **Typed errors**: new `MadokaError` base class; `PairingRequiredError`
  (carries the attempted proxy sources) raised when every path refuses the
  authenticated bond; `DeviceUnreachableError` when no BLE path sees the
  device. `ConnectionException` now subclasses `MadokaError`.
  `is_pairing_error()` classifies auth/bond failures from error text
  (ATT error 0x05 "Insufficient authentication", insufficient encryption,
  pairing failed, BlueZ authentication errors); a `pair()` timeout is treated
  as a pairing failure at the call site (unanswered prompt on the thermostat
  screen). Typed errors propagate out of `start()` so callers can react;
  background reconnects record them in `connection.last_error` instead of
  crashing.
- **Candidate-list connect API**: `Controller(..., candidates_callback=...)` —
  the caller supplies an ordered list of `BLEDevice` paths (preferred proxy
  first); each is tried in order with per-path pairing classification, and
  `connection.connected_source` reports the proxy that served the winning
  connection. Enables sticky-proxy behavior in Home Assistant. Without the
  callback, behavior is unchanged (legacy single-device path).
- **Connection robustness**: a failed candidate's late disconnect callback can
  no longer clobber a live connection or spawn a competing reconnect (the
  BRC1H accepts a single central); failed paths are disconnected before trying
  the next one; background tasks are tracked and quiesced by `cleanup()`
  (no reconnect racing an unload); `last_error` is cleared on any successful
  connect.
- **Explicit pairing-timeout message** (#3): a `pair()` timeout now says
  "confirm the pairing prompt on the thermostat screen (required once per
  Bluetooth proxy)".
- `ConnectionStatus` is now exported at package level.
- Test suite: 16 → 46 tests (error taxonomy, classifier, candidate loop,
  propagation, cleanup quiescing).

## v0.3.5

- Re-pair on every reconnect: `_paired` is reset on disconnect/cleanup so a
  dropped link (or the HA Reconnect button) recovers cleanly instead of
  failing with "Insufficient authentication" (the bond is stored per
  Bluetooth proxy). Validated on hardware.

## v0.3.4

- PyPI-ready metadata; first release published as `pymadoka-ng`.

## v0.3.0–0.3.3

- HA-native BLE path (bleak + bleak-retry-connector), explicit `pair()`,
  retries, cancellation fixes, lean packaging, first tests + CI.
  See release notes for details.
