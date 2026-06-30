"""Base classes for device features.

Each Feature subclass owns a FeatureStatus and knows the cmd_ids to query and
update that status over BLE.  query() and update() are the only public entry
points; they delegate all BLE work to Connection.send(), which serialises
operations and manages retries, timeouts, and reconnection internally.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict

# These re-exports must stay so that callers that do
#   from pymadoka.feature import ConnectionException, ConnectionStatus
# continue to work unchanged.
from pymadoka.connection import (  # noqa: F401  (re-exported)
    Connection,
    ConnectionException,
    ConnectionStatus,
)

logger = logging.getLogger(__name__)


class ParseException(Exception):
    pass


class NotImplementedException(Exception):
    pass


# ---------------------------------------------------------------------------
# FeatureStatus
# ---------------------------------------------------------------------------

class FeatureStatus(ABC):
    """Serialisable snapshot of a single device feature."""

    @abstractmethod
    def get_values(self) -> Dict[int, bytearray]:
        """Return a {param_id: param_bytes} mapping to be sent to the device."""
        pass

    @abstractmethod
    def set_values(self, values: Dict[int, bytearray]):
        """Populate this status from a {param_id: param_bytes} mapping."""
        pass

    def parse(self, data: bytearray):
        """Decode a raw response payload into parameter values.

        Wire layout (after chunk reassembly):
            byte 0        – total payload length
            byte 1        – 0x00
            bytes 2-3     – cmd_id
            bytes 4+      – repeated: [param_id (1)] [param_size (1)] [value ...]
        """
        if len(data) < 4:
            raise ParseException("Response too short to parse")

        if data[0] != len(data):
            raise ParseException(
                f"Declared size {data[0]} does not match actual size {len(data)}"
            )

        values: Dict[int, bytearray] = {}
        i = 4
        while i < len(data):
            if i + 1 >= len(data):
                raise ParseException("Truncated parameter header at offset %d" % i)

            param_id = data[i]
            raw_size = data[i + 1]
            param_size = 0 if raw_size == 0xFF else raw_size

            if i + 1 + param_size >= len(data):
                raise ParseException(
                    "Truncated parameter value at offset %d (need %d more bytes)"
                    % (i, param_size)
                )

            param_bytes = data[i + 2 : i + 2 + param_size]
            values[param_id] = param_bytes if param_bytes else bytes([0x00])

            i += 2 + param_size

        try:
            self.set_values(values)
        except KeyError as e:
            raise ParseException(f"Missing required parameter {e} in response")

    def serialize(self) -> bytearray:
        """Encode parameter values into the wire format expected by the device.

        Each parameter is written as: [param_id (1)] [value_len (1)] [value ...]
        An empty parameter list is represented as [0x00, 0x00].
        """
        values = self.get_values()
        out = bytearray()
        for param_id, param_bytes in values.items():
            out.append(param_id)
            out.append(len(param_bytes))
            out.extend(param_bytes)
        return out if out else bytearray([0x00, 0x00])


# ---------------------------------------------------------------------------
# Feature
# ---------------------------------------------------------------------------

class Feature(ABC):
    """Abstract base for all device features.

    Subclasses declare their cmd_ids and status type; this class provides the
    query/update logic that translates between Python objects and BLE payloads.
    """

    def __init__(self, connection: Connection):
        self.connection = connection
        self.status: FeatureStatus = None
        super().__init__()

    @abstractmethod
    def new_status(self) -> FeatureStatus:
        pass

    @property
    @abstractmethod
    def query_cmd_id(self) -> int:
        pass

    @property
    @abstractmethod
    def update_cmd_id(self) -> int:
        pass

    async def query(self) -> FeatureStatus:
        """Query the device and update self.status.

        Raises:
            ConnectionAbortedError  – permanent connection failure.
            ConnectionException     – transient BLE error.
            asyncio.TimeoutError    – no response within the timeout window.
            NotImplementedException – this feature cannot be queried.
            ParseException          – malformed response payload.
        """
        if self.connection.connection_status == ConnectionStatus.ABORTED:
            raise ConnectionAbortedError(
                "Cannot query %s: connection is aborted" % self.__class__.__name__
            )

        cmd_id = self.query_cmd_id()
        new_status = self.new_status()

        result = await self.connection.send(cmd_id, new_status.serialize())

        logger.debug(
            "%s QUERY response (%d bytes): %s",
            self.__class__.__name__, len(result), result.hex(),
        )
        new_status.parse(result)
        logger.debug(
            "%s status: %s",
            self.__class__.__name__,
            json.dumps(vars(new_status), default=str),
        )
        self.status = new_status
        return self.status

    async def update(self, update_status: FeatureStatus) -> FeatureStatus:
        """Push *update_status* to the device and confirm acknowledgement.

        Note: the device's acknowledgement payload does not reflect the new
        state (it echoes back a default value), so self.status is set to the
        requested *update_status* rather than parsed from the response.

        Raises: same as query().
        """
        if self.connection.connection_status == ConnectionStatus.ABORTED:
            raise ConnectionAbortedError(
                "Cannot update %s: connection is aborted" % self.__class__.__name__
            )

        cmd_id = self.update_cmd_id()

        result = await self.connection.send(cmd_id, update_status.serialize())

        logger.debug(
            "%s UPDATE ack (%d bytes): %s",
            self.__class__.__name__, len(result), result.hex(),
        )
        # Parse for validation / logging only; use the requested status as the
        # new state because the ack payload carries a device default, not the
        # value we just set.
        try:
            ack_status = self.new_status()
            ack_status.parse(result)
            logger.debug(
                "%s ack parsed: %s",
                self.__class__.__name__,
                json.dumps(vars(ack_status), default=str),
            )
        except ParseException as e:
            logger.debug("%s ack parse warning: %s", self.__class__.__name__, e)

        self.status = update_status
        return self.status
