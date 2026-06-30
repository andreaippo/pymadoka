"""BLE connection manager for Madoka thermostats.

Design principles:
- A single asyncio.Lock (_operation_lock) serialises all BLE operations so that
  only one request/response round-trip is in flight at a time.
- Notifications are delivered into an asyncio.Queue by the bleak callback.
  send() drains any stale items, writes all chunks, then reads from the queue
  until the complete response has been reassembled.
- Disconnection puts a None sentinel into the queue so that a blocked send()
  fails fast instead of waiting for the full timeout.
- Reconnection is handled by a background task started from on_disconnect.
"""

import asyncio
import logging
import math
from enum import Enum
from typing import Dict, List, Optional

from bleak import BleakClient, BleakScanner

from pymadoka.transport import Transport, TransportDelegate
from pymadoka.consts import NOTIFY_CHAR_UUID, WRITE_CHAR_UUID, SEND_MAX_TRIES

logger = logging.getLogger(__name__)

_CMD_TIMEOUT = 10.0
_PAYLOAD_PER_CHUNK = 19
_RECONNECT_DELAY_INIT = 5.0
_RECONNECT_DELAY_MAX = 60.0


class ConnectionException(Exception):
    pass


class ConnectionStatus(Enum):
    DISCONNECTED = 0
    CONNECTING = 1
    CONNECTED = 2
    ABORTED = 3


async def discover_devices(timeout=5, adapter="hci0", force_disconnect=True):
    """Scan for BLE devices on the given adapter for *timeout* seconds."""
    scanner = BleakScanner(adapter=adapter)
    await scanner.start()
    await asyncio.sleep(timeout)
    await scanner.stop()
    return scanner.discovered_devices


async def force_device_disconnect(address):
    """Ask bluetoothctl to drop an existing connection so the device is scannable."""
    logger.debug("Forcing disconnect from %s", address)
    process = await asyncio.create_subprocess_exec(
        "bluetoothctl", "disconnect", address,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        logger.debug("Forced disconnect failed: %s", stderr.decode().strip())


# ---------------------------------------------------------------------------
# Internal chunk reassembler (used only inside Connection.send)
# ---------------------------------------------------------------------------

class _ChunkReassembler:
    """Stateful accumulator for multi-chunk BLE notifications."""

    def __init__(self):
        self._chunks: List[bytes] = []
        self._last_id: Optional[int] = None

    def reset(self):
        self._chunks.clear()
        self._last_id = None

    def feed(self, raw_chunk: bytes) -> Optional[bytearray]:
        """Process one incoming chunk.  Returns the assembled payload when
        complete, or None if more chunks are still needed."""
        if len(raw_chunk) < 2:
            logger.debug("Ignoring chunk that is too short (%d bytes)", len(raw_chunk))
            return None

        chunk_id = raw_chunk[0]

        # A chunk_id that is not strictly greater than the last one means a
        # new message has started before the previous one was complete.
        if self._last_id is not None and chunk_id <= self._last_id:
            logger.debug(
                "New message detected mid-reassembly (got id %d after %d), resetting",
                chunk_id, self._last_id,
            )
            self._chunks.clear()

        self._last_id = chunk_id
        self._chunks.append(raw_chunk)

        if self._is_complete():
            return self._collect()

        return None

    def _is_complete(self) -> bool:
        if not self._chunks:
            return False
        total_payload_size = self._chunks[0][1]
        expected = math.ceil(total_payload_size / _PAYLOAD_PER_CHUNK)
        return len(self._chunks) == expected

    def _collect(self) -> bytearray:
        out = bytearray()
        for c in self._chunks:
            out.extend(c[1:])
        self._chunks.clear()
        self._last_id = None
        return out


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

class Connection(TransportDelegate):
    """Manages a BLE connection to a single Madoka thermostat.

    Public attributes accessed by the HA integration:
        address, name, adapter, reconnect, hass, connection_status, client
    """

    client: Optional[BleakClient] = None

    def __init__(
        self,
        address: str,
        adapter: str,
        reconnect: bool = True,
        hass=None,
        name: str = None,
    ):
        self.address = address
        self.adapter = adapter
        self.reconnect = reconnect
        self.hass = hass
        self.name = name or address

        self.connection_status = ConnectionStatus.DISCONNECTED
        self.last_info: Optional[Dict] = None

        # Kept for API / TransportDelegate compatibility; not used in the
        # core send/receive path.
        self.transport = Transport(self)
        self.requests: Dict = {}
        self.current_cmd_id: Optional[int] = None

        # Internals
        self._operation_lock = asyncio.Lock()
        self._notify_queue: asyncio.Queue = asyncio.Queue()
        self._is_starting = False
        self._retry_delay = _RECONNECT_DELAY_INIT

    # ------------------------------------------------------------------
    # TransportDelegate (kept for API compat; not called in new code path)
    # ------------------------------------------------------------------

    def response_rebuilt(self, data: bytearray):
        pass

    def response_failed(self, data: bytearray):
        pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_disconnect(self, client: BleakClient):
        self.connection_status = ConnectionStatus.DISCONNECTED
        logger.info("Disconnected from %s", self.address)
        # Wake any coroutine blocked on _notify_queue.get() so it fails fast.
        self._notify_queue.put_nowait(None)
        if self.reconnect and not self._is_starting:
            asyncio.create_task(self.start())

    async def cleanup(self):
        """Disconnect and suppress further reconnection attempts."""
        self.reconnect = False
        if self.client:
            try:
                await self.client.stop_notify(NOTIFY_CHAR_UUID)
            except Exception:
                pass
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.connection_status = ConnectionStatus.DISCONNECTED

    async def start(self):
        """Connect to the device, retrying with exponential back-off until
        connected or aborted.  Called directly by the user and again from
        on_disconnect when reconnect=True."""
        if self._is_starting:
            logger.debug("start() already running for %s, skipping", self.address)
            return
        self._is_starting = True
        self.connection_status = ConnectionStatus.CONNECTING
        logger.debug("Starting connection manager for %s", self.address)
        try:
            while self.connection_status not in (
                ConnectionStatus.CONNECTED,
                ConnectionStatus.ABORTED,
            ):
                try:
                    if self.hass is not None:
                        await self._connect_via_ha()
                    else:
                        if self.client is None:
                            await self._select_device()
                        await self._connect()
                except ConnectionAbortedError:
                    self.connection_status = ConnectionStatus.ABORTED
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error("Connection attempt failed for %s: %s", self.address, e)
                    if self.connection_status != ConnectionStatus.CONNECTED:
                        logger.info(
                            "Retrying %s in %.0fs", self.address, self._retry_delay
                        )
                        await asyncio.sleep(self._retry_delay)
                        self._retry_delay = min(
                            self._retry_delay * 2, _RECONNECT_DELAY_MAX
                        )
        finally:
            self._is_starting = False

    async def _select_device(self):
        """Create the BleakClient for the standalone (non-HA) code path."""
        logger.debug("Creating BleakClient for %s", self.address)
        self.client = BleakClient(
            self.address,
            adapter=self.adapter,
            disconnected_callback=self.on_disconnect,
        )

    async def _connect(self):
        """Attempt a single connection on an existing BleakClient."""
        if not self.client.is_connected:
            await self.client.connect()

        if self.client.is_connected:
            self._on_connected()
            await self.client.start_notify(NOTIFY_CHAR_UUID, self.notification_handler)
            logger.info("Connected to %s", self.address)
        else:
            logger.warning("Could not connect to %s, will retry", self.address)
            await asyncio.sleep(2.0)

    async def _connect_via_ha(self):
        """Connect using HA's BLE device registry and bleak_retry_connector."""
        from homeassistant.components.bluetooth import async_ble_device_from_address

        try:
            from bleak_retry_connector import establish_connection
        except ImportError:
            logger.warning(
                "bleak_retry_connector not available, falling back to direct connect"
            )
            if self.client is None:
                await self._select_device()
            await self._connect()
            return

        ble_device = async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            logger.warning(
                "Device %s not found in HA BLE registry, will retry", self.address
            )
            await asyncio.sleep(5.0)
            return

        if ble_device.name:
            self.name = ble_device.name

        try:
            self.client = await establish_connection(
                BleakClient,
                ble_device,
                self.address,
                disconnected_callback=self.on_disconnect,
                max_attempts=3,
            )
            self._on_connected()
            await self.client.start_notify(NOTIFY_CHAR_UUID, self.notification_handler)
            logger.info(
                "Connected to %s (%s) via bleak_retry_connector",
                self.address,
                self.name,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("bleak_retry_connector failed for %s: %s", self.address, e)
            logger.info("Retrying %s in %.0fs", self.address, self._retry_delay)
            await asyncio.sleep(self._retry_delay)
            self._retry_delay = min(self._retry_delay * 2, _RECONNECT_DELAY_MAX)

    def _on_connected(self):
        self.connection_status = ConnectionStatus.CONNECTED
        self._retry_delay = _RECONNECT_DELAY_INIT

    # ------------------------------------------------------------------
    # Notification handler
    # ------------------------------------------------------------------

    def notification_handler(self, sender: str, data: bytearray):
        """Called by bleak on every GATT notification.  Enqueues raw chunk
        bytes for consumption by the pending send() call."""
        self._notify_queue.put_nowait(bytes(data))

    # ------------------------------------------------------------------
    # Send / receive
    # ------------------------------------------------------------------

    def cmd_id_to_bytes(self, cmd_id: int) -> bytearray:
        return bytearray([0x00]) + cmd_id.to_bytes(2, "big")

    def bytes_to_cmd_id(self, data: bytes) -> int:
        return int.from_bytes(data[2:4], "big")

    def _build_payload(self, cmd_id: int, data: bytearray) -> bytearray:
        # Layout: [total_len (1)] [0x00 (1)] [cmd_id (2)] [params ...]
        payload = bytearray([0x00, 0x00]) + cmd_id.to_bytes(2, "big") + data
        payload[0] = len(payload)
        return payload

    async def send(self, cmd_id: int, data: bytearray) -> bytearray:
        """Send *cmd_id* with *data* and return the assembled response payload.

        Acquires _operation_lock for the full round-trip so that no two
        operations are in flight simultaneously.  Raises:
            ConnectionAbortedError – if the connection is permanently gone.
            ConnectionException    – on send failure or mid-operation disconnect.
            asyncio.TimeoutError   – if no complete response arrives within
                                     _CMD_TIMEOUT seconds.
        """
        if self.connection_status == ConnectionStatus.ABORTED:
            raise ConnectionAbortedError(
                f"Cannot send cmd {cmd_id}: connection is aborted"
            )
        if self.connection_status != ConnectionStatus.CONNECTED:
            raise ConnectionException(
                f"Cannot send cmd {cmd_id}: not connected"
            )

        payload = self._build_payload(cmd_id, data)
        chunks = self.transport.split_in_chunks(payload)

        async with self._operation_lock:
            # Re-check inside the lock: the connection may have dropped while
            # we were waiting to acquire it.
            if self.connection_status == ConnectionStatus.ABORTED:
                raise ConnectionAbortedError(
                    f"Cannot send cmd {cmd_id}: connection is aborted"
                )
            if self.connection_status != ConnectionStatus.CONNECTED:
                raise ConnectionException(
                    f"Cannot send cmd {cmd_id}: not connected"
                )

            # Drain any stale notifications left over from a previous failed
            # or timed-out operation.
            while not self._notify_queue.empty():
                try:
                    self._notify_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # Write all chunks, retrying each one on transient errors.
            self.current_cmd_id = cmd_id
            for i, chunk in enumerate(chunks):
                for attempt in range(SEND_MAX_TRIES):
                    try:
                        await self.client.write_gatt_char(WRITE_CHAR_UUID, chunk)
                        logger.debug(
                            "cmd %d: sent chunk %d/%d", cmd_id, i + 1, len(chunks)
                        )
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.debug(
                            "cmd %d: chunk %d send failed attempt %d/%d: %s",
                            cmd_id, i, attempt + 1, SEND_MAX_TRIES, e,
                        )
                        if attempt == SEND_MAX_TRIES - 1:
                            raise ConnectionException(
                                f"cmd {cmd_id}: failed to send chunk {i} "
                                f"after {SEND_MAX_TRIES} attempts: {e}"
                            )
                        await asyncio.sleep(1.0)

            # Read notifications until a complete response is reassembled.
            return await self._receive_response(cmd_id)

    async def _receive_response(self, cmd_id: int) -> bytearray:
        """Drain _notify_queue until a complete response message is assembled.
        Uses a single absolute deadline so the total wait is bounded even for
        multi-chunk responses."""
        reassembler = _ChunkReassembler()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _CMD_TIMEOUT

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"Timeout waiting for response to cmd {cmd_id}"
                )

            try:
                raw_chunk = await asyncio.wait_for(
                    self._notify_queue.get(), timeout=remaining
                )
            except asyncio.TimeoutError:
                raise asyncio.TimeoutError(
                    f"Timeout waiting for response to cmd {cmd_id}"
                )

            if raw_chunk is None:
                # Sentinel placed by on_disconnect.
                raise ConnectionException(
                    f"Disconnected while waiting for response to cmd {cmd_id}"
                )

            result = reassembler.feed(raw_chunk)
            if result is not None:
                logger.debug(
                    "cmd %d: response assembled (%d bytes): %s",
                    cmd_id, len(result), result.hex(),
                )
                return result

    # ------------------------------------------------------------------
    # Device info
    # ------------------------------------------------------------------

    async def read_info(self) -> Dict[str, str]:
        """Read all readable GATT characteristics and return a name→value dict."""
        try:
            if self.last_info:
                return self.last_info

            if self.connection_status is not ConnectionStatus.CONNECTED:
                return {}

            values: Dict[str, str] = {}
            for service in self.client.services:
                logger.debug("[Service] %s: %s", service.uuid, service.description)
                for char in service.characteristics:
                    if "read" not in char.properties:
                        continue
                    try:
                        raw = await self.client.read_gatt_char(char.uuid)
                        try:
                            if char.description.endswith(" ID"):
                                value = raw.hex().replace("fe", "-").replace("ff", "")
                            else:
                                value = raw.decode()
                        except Exception:
                            value = str(raw)
                        values[char.description] = value
                    except Exception as e:
                        logger.error("Could not read char %s: %s", char.uuid, e)

            self.last_info = values
            return self.last_info
        except Exception as e:
            logger.error("read_info failed: %s", e)
            raise
