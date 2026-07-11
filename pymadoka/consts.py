SERVICE_UUID = "2141E110-213A-11E6-B67B-9E71128CAE77"
NOTIFY_CHAR_UUID = "2141E111-213A-11E6-B67B-9E71128CAE77"
WRITE_CHAR_UUID = "2141E112-213A-11E6-B67B-9E71128CAE77"

# Max GATT write retries for a single chunk.
SEND_MAX_TRIES = 5

# --- Resilience / latency tuning ---------------------------------------------
# Per-attempt wait for the device response. Kept short on purpose: a BLE
# notification roundtrip on this device is ~0.1-0.8s, so a short timeout
# detects a lost response fast and lets us retry instead of blocking.
COMMAND_TIMEOUT = 2.0
# How many times a full command roundtrip (write + await response) is retried.
COMMAND_MAX_TRIES = 3
# Base backoff between command retries (multiplied by attempt number).
COMMAND_RETRY_DELAY = 0.15
# Base backoff between GATT chunk-write retries (multiplied by attempt number).
WRITE_RETRY_DELAY = 0.2
# Reconnect backoff bounds (exponential, jitter-free, capped).
RECONNECT_BASE_DELAY = 2.0
RECONNECT_MAX_DELAY = 60.0
# How long (seconds) a delayed response to a timed-out command may still arrive.
# Within this window such stragglers are dropped so they cannot be mis-matched
# to a later command sharing the same command id.
STALE_RESPONSE_WINDOW = 5.0
