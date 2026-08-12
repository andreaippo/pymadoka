"""This module implements the base class of the features supported by the device
"""

from abc import ABC, abstractmethod
import asyncio
import logging
import json
from typing import Dict

from asyncio.exceptions import CancelledError

from pymadoka.connection import Connection, ConnectionException, ConnectionStatus

logger = logging.getLogger(__name__)

class ParseException(Exception):
     pass

class NotImplementedException(Exception):
     pass

class FeatureStatus(ABC):
    """
    This interface defines the methods used by the Transport to notify the result of the rebuild process.
    """

    """This method must be implemented by subclasses to provide with the list of parameters used by the feature     
    Returns:
        Dict[int,bytearray]: Dictionary of parameter ids and values
    """
    @abstractmethod
    def get_values(self) -> Dict[int,bytearray]:
        pass

    """This method must be implemented by subclasses to provide with the list of parameters used by the feature.

    Returns:
        Dict[int,bytearray]: Dictionary of parameter ids and values
    """
    @abstractmethod
    def set_values(self,values:Dict[int,bytearray]):
        pass

    """Parse the provided data into a dictionary of parameter names and values.

    Once the parameters have been parsed, they are passed to the feature using the method `set_values`
    Args:
        data (bytearray): Data to be parsed    
    Raises:
        ParseException: There is missing data or there is a data mismatch
    """
    def parse(self,data:bytearray):

        if len(data)<4:
            raise ParseException("Not enough bytes to parse")

        if data[0] != len(data):
            raise ParseException("Message size and data size mismatchs")


        # We have already skipped chunk_id(1byte)
        # We process the following data: size(1),cmd_id(3),param_id(1),param_size(1),param_value...
     
        values = {}
        value_size = 0
        i = 4
        while i < len(data):
            if (i+1) >= len(data):
                raise ParseException("Not enough data to parse while processing arguments")
            
            value_id = data[i]
            if data[i+1] == 0xff:
                value_size = 0
            else: 
                value_size = data[i+1]

            if i+1+value_size >= len(data):
                raise ParseException("Not enough data to parse while processing arguments")
            
            value_bytes = data[i+2:i+2+value_size]
            if len(value_bytes) == 0:
                value_bytes = bytes([0x00])
            values[value_id] = value_bytes

            i += 2 + value_size
        
        self.set_values(values)


    """Serialize the status parameters into a bytearray.

    Each parameter is written with the following structure: <param_id><param_contents_size><param_contents>

    Returns:
        bytearray: Data with all the parameter info
    """
    def serialize(self) -> bytearray:

        values = self.get_values()
    
        out = bytearray()

        for k,v in values.items():
            out.append(k)
            out.append(len(v))
            out.extend(v)

        # Special case when no parameters are used

        if len(out) == 0:
            out = bytearray([0x00,0x00])

        return out
            

class Feature(ABC):
    """
    This interface defines the methods used by the features.

    Attributes:
        connection (Connection): Connection to be used to send messages
        status (FeatureStatus): Status 
    """
    def __init__(self, connection: Connection):
        """Inits the feature with the connection.

        Args:
            connection (Connection): Connection to be used to send messages
        """
        self.connection = connection
        self.status = None
        super().__init__()

    @property
    def log_id(self) -> str:
        """Device address and feature name, so log lines can be told apart when
        several devices are controlled from the same process."""
        return f"[{self.connection.address}] {self.__class__.__name__}"
    
    
    @abstractmethod
    def new_status(self) -> FeatureStatus:
        """This method must be implemented by subclasses to return a new instance of the status used by this feature.

        Returns:
            FeatureStatus: New status instance
        """
        pass

    
    @property
    @abstractmethod
    def query_cmd_id(self) -> int:
        """This method must be implemented by subclasses to return a the id used to query the device feature.

        Returns:
            int: Query status cmd id
        """
        pass


    @property
    @abstractmethod
    def update_cmd_id(self) -> int:
        """This method must be implemented by subclasses to return a the id used to update the device feature.

        Returns:
            int: Update status cmd id
        """
        pass

    async def _roundtrip(self, cmd_id: int, payload: bytearray) -> bytearray:
        """Send a command and wait for its response, with a short per-attempt
        timeout and bounded retries.

        A short timeout combined with retries keeps the effective latency low
        (a lost response is retried after `command_timeout` instead of blocking
        for a long single wait) while surviving dropped BLE notifications.

        The commands issued by this library are idempotent (they carry absolute
        values, not increments), so retrying a write is safe.

        Returns:
            bytearray: Raw response data, or None if the link is (re)connecting.
        Raises:
            ConnectionAbortedError: If the connection is not available
            ConnectionException: If the command could not be delivered/rebuilt
            asyncio.TimeoutError: If no response arrived after all retries
        """
        conn = self.connection
        tries = max(1, conn.command_max_tries)
        last_error = None
        # Number of attempts that timed out. Each one leaves a response in flight
        # on the device side; we arm the connection's stale guard for that many
        # so those delayed stragglers cannot desync the next command.
        timeouts = 0

        try:
            for attempt in range(1, tries + 1):
                if conn.connection_status == ConnectionStatus.ABORTED:
                    raise ConnectionAbortedError("Could not send command: connection is not available")

                response = None
                try:
                    async with conn._operation_lock:
                        response = await conn.send(cmd_id, payload)
                        await asyncio.wait_for(asyncio.shield(response), timeout=conn.command_timeout)
                    return response.result()
                except asyncio.TimeoutError:
                    if response is not None:
                        conn.discard_request(cmd_id, response)
                    timeouts += 1
                    last_error = asyncio.TimeoutError()
                    logger.warning(
                        f"{self.log_id} cmd {cmd_id} timed out "
                        f"(attempt {attempt}/{tries})"
                    )
                except CancelledError:
                    # Two very different things raise here:
                    #  - the link dropped and _fail_all_requests() cancelled our
                    #    response future (response.cancelled() is True) -> handle it
                    #    as a connection error below,
                    #  - our own task was cancelled from outside (wait_for, HA
                    #    unload). The shielded future is still pending, so it must
                    #    propagate instead of being retried/swallowed.
                    if response is None or not response.cancelled():
                        raise
                    conn.discard_request(cmd_id, response)
                    if conn.connection_status == ConnectionStatus.ABORTED:
                        raise ConnectionAbortedError("Could not send command: connection is not available")
                    if conn.connection_status == ConnectionStatus.CONNECTING:
                        # Link is coming back up; do not hammer it with retries.
                        return None
                    last_error = ConnectionException("Could not send command: message could not be rebuilt")
                    logger.debug(
                        f"{self.log_id} cmd {cmd_id} not rebuilt "
                        f"(attempt {attempt}/{tries})"
                    )

                if attempt < tries:
                    await asyncio.sleep(conn.command_retry_delay * attempt)

            if last_error is not None:
                raise last_error
            raise ConnectionException("Could not send command")
        finally:
            conn.arm_stale_guard(cmd_id, timeouts)

    async def query(self) -> FeatureStatus:
        """This method is used to query the device for this feature.

        The method waits until the response is received, parses the result and updates the feature state accordingly.

        Returns:
            FeatureStatus: New status
        Raises:
            ConnectionAbortedError: If the connection is not available
            ConnectionException: If an error appeared during message delivery or reception
            Exception: Any other exception raised is bubbled-up
        """

        if self.connection.connection_status == ConnectionStatus.ABORTED:
                raise ConnectionAbortedError(f"Could not send command: connection is not available")

        cmd_id = self.query_cmd_id()
        new_status = self.new_status()
        result = await self._roundtrip(cmd_id, new_status.serialize())
        if result is None:
            return self.status
        logger.debug(f"{self.log_id} QUERY response received ({len(result)} bytes)")
        new_status.parse(result)
        logger.debug(f"{self.log_id} status updated, new value:\n{json.dumps(vars(new_status), default=str)}")
        self.status = new_status
        return self.status


    async def update(self,update_status:FeatureStatus) -> FeatureStatus:
        """This method is used to update the device for this feature.

        The method waits until the response is received, parses the result and updates the feature state accordingly.

        We can assume that if the response was parsed correctly, the command went OK. The response data, algthough parseable, does not
        reflect the actual status of the device. e.g:

        Operation Mode Command Set DRY:
        < ACL Data TX: Handle 73 flags 0x00 dlen 15             #1757 [hci0] 984.889214
        ATT: Write Command (0x52) len 10
        Handle: 0x0205
          Data: 0007004030200101 <---- DRY

        Operation Mode Command Set DRY - Response :
        > ACL Data RX: Handle 73 flags 0x02 dlen 15             #1759 [hci0] 984.951395
        ATT: Handle Value Notification (0x1b) len 10
        Handle: 0x0202
          Data: 0007004030200100 <---- FAN_ONLY

        Please note the last byte as it 

        Args:
            update_status (FeatureStatus): New status to be set
        Returns:
            FeatureStatus: New status
        Raises:
            ConnectionAbortedError: If the connection is not available
            ConnectionException: If an error appeared during message delivery or reception
            Exception: Any other exception raised is bubbled-up
        """

        if self.connection.connection_status == ConnectionStatus.ABORTED:
                raise ConnectionAbortedError(f"Could not send command: connection is not available")

        cmd_id = self.update_cmd_id()
        result = await self._roundtrip(cmd_id, update_status.serialize())
        if result is None:
            return self.status
        logger.debug(f"{self.log_id} UPDATE response received ({len(result)} bytes)")
        # The response is parsed only to validate that the command was accepted:
        # its contents do not reflect the device state (see the docstring above),
        # so the status written is the one logged.
        response_status = self.new_status()
        response_status.parse(result)
        logger.debug(f"{self.log_id} UPDATE applied, values sent:\n{json.dumps(vars(update_status), default=str)}")
        self.status = update_status
        return self.status
       
