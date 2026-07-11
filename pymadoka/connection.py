import asyncio
from asyncio.exceptions import CancelledError
import logging

import subprocess
import sys
from enum import Enum

from bleak import BleakClient, BleakScanner
discover = BleakScanner.discover
from typing import Dict

from pymadoka.transport import Transport, TransportDelegate
from pymadoka.consts import (
    NOTIFY_CHAR_UUID,
    WRITE_CHAR_UUID,
    SEND_MAX_TRIES,
    COMMAND_TIMEOUT,
    COMMAND_MAX_TRIES,
    COMMAND_RETRY_DELAY,
    WRITE_RETRY_DELAY,
    RECONNECT_BASE_DELAY,
    RECONNECT_MAX_DELAY,
    STALE_RESPONSE_WINDOW,
)

logger = logging.getLogger(__name__)

class ConnectionException(Exception):
    """Exceptions are documented in the same way as classes."""
    pass


class ConnectionStatus(Enum):
    DISCONNECTED = 0
    CONNECTING = 1
    CONNECTED = 2
    ABORTED = 3


async def discover_devices(timeout=5, adapter="hci0", force_disconnect=True):
    """Trigger a bluetooth devices discovery on the adapter for the timeout interval."""
    scanner = BleakScanner(adapter=adapter)
    await scanner.start()
    await asyncio.sleep(timeout)
    await scanner.stop()
    return scanner.discovered_devices

async def force_device_disconnect(address):
    """Force a device disconnect so it can be listed during the scan."""
    logger.debug("Forcing disconnect...")
    process = await asyncio.create_subprocess_exec(
        "bluetoothctl", "disconnect", address,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        logger.debug(f"Disconnect failed: {stderr.decode().strip()}")


class Connection(TransportDelegate):
    """Bluetooth client"""

    client: BleakClient = None

    def __init__(
        self,
        address: str,
        adapter: str,
        reconnect: bool = True,
        hass=None,
        name: str = None,
    ):
        self.reconnect = reconnect
        self.adapter = adapter
        self.address = address
        self.name = name or address
        self.hass = hass
        self.connection_status = ConnectionStatus.DISCONNECTED
        self.last_info = None
        self.transport = Transport(self)
        self.current_future = None
        self.requests = {}
        self._is_starting = False
        self._operation_lock = asyncio.Lock()
        self._retry_delay = RECONNECT_BASE_DELAY

        # Resilience / latency tuning. Exposed as attributes so callers can tune
        # them without changing the public constructor signature.
        self.command_timeout = COMMAND_TIMEOUT
        self.command_max_tries = COMMAND_MAX_TRIES
        self.command_retry_delay = COMMAND_RETRY_DELAY
        self.write_retry_delay = WRITE_RETRY_DELAY
        self.stale_response_window = STALE_RESPONSE_WINDOW

        # Guards against delayed responses of timed-out commands being matched to
        # a later command with the same id. Maps cmd_id -> (remaining, expiry).
        self._stale_guard = {}

    def _loop_time(self) -> float:
        return asyncio.get_event_loop().time()

    def arm_stale_guard(self, cmd_id: int, count: int):
        """Arm the guard so the next `count` responses for `cmd_id` (within the
        stale window) are dropped as delayed stragglers of a timed-out command."""
        if count <= 0:
            return
        expiry = self._loop_time() + self.stale_response_window
        self._stale_guard[cmd_id] = (count, expiry)

    def _reset_backoff(self):
        self._retry_delay = RECONNECT_BASE_DELAY

    def _next_backoff(self) -> float:
        delay = self._retry_delay
        self._retry_delay = min(self._retry_delay * 2, RECONNECT_MAX_DELAY)
        return delay

    def on_disconnect(self, client: BleakClient):
        self.connection_status = ConnectionStatus.DISCONNECTED
        logger.info(f"Disconnected {self.address}")
        # Any in-flight request can no longer be answered. Fail them fast
        # instead of letting callers block until their per-command timeout.
        self._fail_all_requests()
        if self.reconnect and not self._is_starting:
            asyncio.create_task(self.start())

    async def cleanup(self):
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
        self._fail_all_requests()

    async def start(self):
        if self._is_starting:
            logger.debug(f"start() already running for {self.address}, skipping")
            return
        self._is_starting = True
        logger.debug(f"Starting connection manager on {self.address}")
        self.connection_status = ConnectionStatus.CONNECTING
        try:
            while self.connection_status not in (ConnectionStatus.CONNECTED, ConnectionStatus.ABORTED):
                try:
                    if self.hass is not None:
                        await self._connect_via_ha()
                    else:
                        if self.client is None:
                            await self._select_device()
                        await self._connect()
                    if self.connection_status != ConnectionStatus.CONNECTED:
                        await asyncio.sleep(self._next_backoff())
                except ConnectionAbortedError:
                    self.connection_status = ConnectionStatus.ABORTED
                except CancelledError as e:
                    logger.error(f"Connection task cancelled for {self.address}: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error in connection loop for {self.address}: {e}")
                    self.connection_status = ConnectionStatus.ABORTED
        finally:
            self._is_starting = False

    async def _connect_via_ha(self):
        """Connect using HA's BLE device registry and bleak_retry_connector."""
        from homeassistant.components.bluetooth import async_ble_device_from_address
        try:
            from bleak_retry_connector import establish_connection
        except ImportError:
            logger.warning("bleak_retry_connector not available, falling back to direct BleakClient")
            if self.client is None:
                await self._select_device()
            await self._connect()
            return

        ble_device = async_ble_device_from_address(self.hass, self.address, connectable=True)
        if ble_device is None:
            logger.warning(f"Device {self.address} not found in HA BLE tracker, will retry...")
            await asyncio.sleep(self._next_backoff())
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
            self.connection_status = ConnectionStatus.CONNECTED
            self._reset_backoff()  # reset backoff on successful connect
            await self.client.start_notify(NOTIFY_CHAR_UUID, self.notification_handler)
            logger.info(f"Connected to {self.address} ({self.name}) via bleak_retry_connector")
        except Exception as e:
            logger.error(f"Failed to connect to {self.address}: {e}")
            delay = self._next_backoff()
            logger.info(f"Retrying {self.address} in {delay:.0f}s")
            await asyncio.sleep(delay)

    async def _connect(self):
        try:
            connected = self.client.is_connected
            if not connected:
                await self.client.connect()
                connected = self.client.is_connected

            if connected:
                logger.info(f"Connected to {self.address}")
                self.connection_status = ConnectionStatus.CONNECTED
                self._reset_backoff()
                await self.client.start_notify(
                    NOTIFY_CHAR_UUID, self.notification_handler,
                )
            else:
                logger.warning(f"Failed to connect to {self.address}")

        except Exception as e:
            logger.error(f"Connection error for {self.address}: {e}")
            if not self.reconnect:
                raise e

    async def _select_device(self):
        """Create a BleakClient from the address string (standalone / non-HA path)."""
        logger.debug(f"Creating BleakClient for {self.address}")
        self.client = BleakClient(
            self.address,
            adapter=self.adapter,
            disconnected_callback=self.on_disconnect,
        )

    def notification_handler(self, sender: str, data: bytearray):
        self.transport.rebuild_chunk(data)

    def cmd_id_to_bytes(self, cmd_id: int):
        return bytearray([0x00]) + cmd_id.to_bytes(2, "big")

    def bytes_to_cmd_id(self, data: bytes):
        return int.from_bytes(data[2:4], "big")

    def discard_request(self, cmd_id: int, cmd_response):
        """Remove a specific pending request future from the registry.

        Used by callers when a command attempt times out or is abandoned, so the
        stale future is not later matched against a newly arrived response (which
        would desync every subsequent response by one message).
        """
        pending = self.requests.get(cmd_id)
        if not pending:
            return
        try:
            pending.remove(cmd_response)
        except ValueError:
            pass
        if not cmd_response.done():
            cmd_response.cancel()

    def _fail_all_requests(self):
        """Cancel every pending request. Called when the link drops so callers
        unblock immediately instead of waiting for their timeout."""
        for cmd_id, pending in list(self.requests.items()):
            for req in pending:
                if not req.done():
                    req.cancel()
            pending.clear()

    async def send(self, cmd_id: int, data: bytearray):
        cmd_response = asyncio.get_event_loop().create_future()
        if not cmd_id in self.requests:
            self.requests[cmd_id] = []

        self.requests[cmd_id].append(cmd_response)

        if self.connection_status is not ConnectionStatus.CONNECTED:
            self.discard_request(cmd_id, cmd_response)
            return cmd_response

        payload = bytearray([0x00]) + self.cmd_id_to_bytes(cmd_id) + data
        payload[0] = len(payload)

        logger.debug(f"Sending cmd payload: {bytes(payload).hex()}")

        chunks = self.transport.split_in_chunks(payload)
        sent = 0

        self.current_cmd_id = cmd_id
        for chunknum, chunk in enumerate(chunks):
            for i in range(0, SEND_MAX_TRIES):
                try:
                    if self.connection_status is not ConnectionStatus.CONNECTED:
                        self.discard_request(cmd_id, cmd_response)
                        return cmd_response

                    await self.client.write_gatt_char(WRITE_CHAR_UUID, chunk)
                    logger.debug(f"CMD {cmd_id}. Chunk #{chunknum+1}/{len(chunks)} sent with size {len(chunk)} bytes")
                    sent += 1
                    break
                except CancelledError as e:
                    logger.debug(f"Send command failed. Retrying ({i+1}/{SEND_MAX_TRIES}) for chunk #{chunknum} : {str(e)}", exc_info=e)
                    await asyncio.sleep(self.write_retry_delay * (i + 1))
                except Exception as e:
                    logger.debug(f"Send command failed. Retrying ({i+1}/{SEND_MAX_TRIES}) for chunk #{chunknum} : {str(e)}")
                    await asyncio.sleep(self.write_retry_delay * (i + 1))

        if sent != len(chunks):
            # Could not push all chunks. Drop the future so it does not linger.
            self.discard_request(cmd_id, cmd_response)
            if self.connection_status == ConnectionStatus.CONNECTED:
                raise ConnectionException("Command chunks could not be sent")

        return cmd_response

    def response_rebuilt(self, data: bytearray):
        if len(data) <= 4:
            return

        cmd_id = self.bytes_to_cmd_id(data)

        # Drop delayed stragglers of a previously timed-out command so they are
        # not mis-attributed to a later request sharing this command id.
        guard = self._stale_guard.get(cmd_id)
        if guard is not None:
            remaining, expiry = guard
            if self._loop_time() < expiry and remaining > 0:
                remaining -= 1
                if remaining > 0:
                    self._stale_guard[cmd_id] = (remaining, expiry)
                else:
                    del self._stale_guard[cmd_id]
                logger.debug(f"Dropping stale response for cmd {cmd_id}")
                return
            del self._stale_guard[cmd_id]

        pending = self.requests.get(cmd_id)
        if not pending:
            return
        # Resolve the oldest still-pending future for this command. Skip any that
        # were already cancelled/completed (e.g. abandoned after a timeout).
        while pending:
            req = pending.pop(0)
            if req.done():
                continue
            req.set_result(data)
            return

    def response_failed(self, data: bytearray):
        if len(data) <= 4:
            return

        cmd_id = self.bytes_to_cmd_id(data)

        pending = self.requests.get(cmd_id)
        if not pending:
            return

        while pending:
            req = pending.pop(0)
            if req.done():
                continue
            req.cancel()
            return

    async def read_info(self) -> Dict[str, str]:
        try:
            if self.last_info:
                 return self.last_info

            if self.connection_status is not ConnectionStatus.CONNECTED:
                return {}

            values = {}

            for service in self.client.services:
                logger.debug("[Service] {0}: {1}".format(service.uuid, service.description))
                for char in service.characteristics:
                    if "read" in char.properties:
                        try:
                            raw = await self.client.read_gatt_char(char.uuid)
                            value = None

                            try:
                                if char.description.endswith(" ID"):
                                    value = raw.hex().replace("fe", "-").replace("ff", "")
                                else:
                                    value = raw.decode()
                            except:
                                value = str(raw)
                            values[char.description] = value
                        except Exception as e:
                            logger.error(e)

            self.last_info = values
            return self.last_info
        except Exception as e:
            logger.error(e)
            raise e
