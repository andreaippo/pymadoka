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

    # Whether `Controller.update` reads this feature on every poll cycle. A
    # feature that costs several round-trips to read, for a setting that only
    # changes when somebody changes it, is not worth that on every cycle.
    polled = True

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
        """Device address and feature name, so the log lines of several
        devices controlled from the same process can be told apart."""
        return f"[{getattr(self.connection, 'address', None)}] {self.__class__.__name__}"
    
    
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

    async def _send_command(self, cmd_id:int, payload:bytearray) -> bytes:
        """Send a command and wait for its response, retrying a timeout.

        The connection's command policy decides how long one attempt waits
        (`command_timeout`) and how many attempts are made
        (`command_max_tries`); the default is a single 10 s attempt. Only a
        timeout is retried: the commands carry absolute values, so sending
        one again is harmless. A dropped link is not retried here, it fails
        at once so the caller can reconnect.

        Every attempt that timed out may still get its reply later. The
        connection's stale guard drops that many replies for this cmd_id, so
        a late one cannot resolve the next request and desync the pairing of
        requests and replies.

        Raises:
            ConnectionAbortedError: The connection is not available
            ConnectionException: The link dropped while waiting for the response
            asyncio.TimeoutError: No response after every attempt
            asyncio.CancelledError: The calling task was cancelled (propagated)
        """
        conn = self.connection
        tries = max(1, int(getattr(conn, "command_max_tries", 1)))
        timeout = getattr(conn, "command_timeout", 10.0)
        timeouts = 0
        link_lost = False
        try:
            for attempt in range(1, tries + 1):
                try:
                    return await self._send_once(cmd_id, payload, timeout)
                except asyncio.TimeoutError:
                    timeouts += 1
                    if attempt == tries:
                        raise
                    logger.warning(
                        f"{self.log_id} cmd {cmd_id} timed out "
                        f"(attempt {attempt}/{tries}), retrying"
                    )
                    await asyncio.sleep(conn.command_retry_delay * attempt)
                except (ConnectionException, ConnectionAbortedError):
                    link_lost = True
                    raise
            raise AssertionError("unreachable")
        finally:
            if not link_lost and hasattr(conn, "arm_stale_guard"):
                conn.arm_stale_guard(cmd_id, timeouts)

    async def _send_once(self, cmd_id:int, payload:bytearray, timeout:float) -> bytes:
        """One attempt, with strict queue hygiene.

        On timeout or cancellation the pending future is removed from the
        connection's request queue: leaving it there would let a late response
        resolve the abandoned future and permanently desync the FIFO pairing
        of requests and responses for this cmd_id.
        """
        if self.connection.connection_status == ConnectionStatus.ABORTED:
            raise ConnectionAbortedError("Could not send command: connection is not available")

        response = None
        try:
            async with self.connection._operation_lock:
                response = await self.connection.send(cmd_id, payload)
                await asyncio.wait_for(asyncio.shield(response), timeout=timeout)
            return response.result()
        except asyncio.TimeoutError:
            if response is not None:
                self.connection.discard_request(cmd_id, response)
            raise
        except CancelledError:
            if response is not None:
                self.connection.discard_request(cmd_id, response)
            if response is not None and response.cancelled():
                # send() cancelled the request future because the link dropped;
                # the surrounding task is still alive.
                if self.connection.connection_status == ConnectionStatus.ABORTED:
                    raise ConnectionAbortedError(
                        "Could not send command: connection is not available"
                    ) from None
                raise ConnectionException("Could not send command: connection lost") from None
            # Real task cancellation (caller timeout, shutdown): propagate.
            raise

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
        cmd_id = self.query_cmd_id()
        new_status = self.new_status()
        result = await self._send_command(cmd_id, new_status.serialize())
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
        cmd_id = self.update_cmd_id()
        result = await self._send_command(cmd_id, update_status.serialize())
        logger.debug(f"{self.log_id} UPDATE response received ({len(result)} bytes)")
        response_status = self.new_status()
        response_status.parse(result)
        logger.debug(f"{self.log_id} status updated, new value:\n{json.dumps(vars(update_status), default=str)}")
        self.status = update_status
        return self.status
       
