"""This module contains the classes used to control the Ring Mode feature (behaviour of the status ring)
"""

from enum import IntEnum
from typing import Dict
from pymadoka.feature import Feature, FeatureStatus
from pymadoka.connection import Connection

class RingModeEnum(IntEnum):
    """Behaviour of the status ring, in the order the official app lists it.

    Both hotel modes stop the ring from blinking on an error. Hotel 2 goes
    further and shows no status at all while the screen is dimmed.
    """
    NORMAL = 0
    HOTEL_1 = 1
    HOTEL_2 = 2

class RingModeStatus(FeatureStatus):

    """
    This class is used to store the behaviour of the status ring.

    The device keeps this setting inside a sixteen byte array carried by a
    single parameter, along with the arrays of the minimum and maximum value
    allowed for each of its entries. Only the entry at MODE_INDEX is known.

    A write sends the same array with 0xff on every entry that must keep its
    value, so a single setting can be changed without knowing the meaning of
    the others.

    Attributes:
        mode (RingModeEnum): Behaviour of the status ring
        session (bool): Open or close the edit session, None for the other
            commands. The official app wraps every read and write in one.
        values (bytes): The whole array as read from the device
        minimum (bytes): Minimum value allowed for each entry of the array
        maximum (bytes): Maximum value allowed for each entry of the array
    """

    # The three parameters that address the page of settings this array belongs
    # to. The device answers nothing when they are missing, and they have to be
    # echoed unchanged on a write.
    SELECTOR = {0x01: bytes([0x02]), 0x02: bytes([0xff]), 0x03: bytes([0x01])}

    VALUES_IDX = 0x30
    MINIMUM_IDX = 0xa0
    MAXIMUM_IDX = 0xb0
    SESSION_IDX = 0xfe

    ARRAY_SIZE = 16
    MODE_INDEX = 11

    # Value that leaves an entry of the array untouched.
    UNCHANGED = 0xff

    def __init__(self, mode:RingModeEnum = None, session:bool = None):
        """Inits with the ring mode.

        Attributes:
            mode (RingModeEnum): Behaviour of the status ring, None to leave
                untouched
            session (bool): True to open the edit session, False to close it,
                None for a plain read or write
        """
        self.mode = mode
        self.session = session
        self.values = None
        self.minimum = None
        self.maximum = None

    def set_values(self, values:Dict[int,bytearray]):
        """See base class.

        The response to a session command carries no array, so every field is
        read defensively.
        """

        def read_array(idx):
            raw = values.get(idx)
            if raw is None or len(raw) != self.ARRAY_SIZE:
                return None
            return bytes(raw)

        self.values = read_array(self.VALUES_IDX)
        self.minimum = read_array(self.MINIMUM_IDX)
        self.maximum = read_array(self.MAXIMUM_IDX)

        if self.values is not None:
            mode = self.values[self.MODE_INDEX]
            if mode != self.UNCHANGED:
                self.mode = RingModeEnum(mode)

    def get_values(self) -> Dict[int,bytearray]:
        """See base class.

        Three commands are built from this status: opening or closing the edit
        session, reading the arrays, and writing a single entry of the array.
        """

        if self.session is not None:
            return {self.SESSION_IDX: bytes([0x01 if self.session else 0x00])}

        values = dict(self.SELECTOR)

        if self.mode is None:
            # Read: the three arrays are requested by name, with no value.
            values[self.VALUES_IDX] = bytes()
            values[self.MINIMUM_IDX] = bytes()
            values[self.MAXIMUM_IDX] = bytes()
            return values

        # Write: every entry but the one being set is left untouched. A 0x00
        # here would write a zero over a setting whose meaning is unknown.
        array = bytearray([self.UNCHANGED] * self.ARRAY_SIZE)
        array[self.MODE_INDEX] = int(self.mode)
        values[self.VALUES_IDX] = bytes(array)
        return values

class RingMode(Feature):

    """
    This class is used to control the behaviour of the status ring

    Attributes:
        status (RingModeStatus): Current status
    """
    def __init__(self, connection: Connection):
        """See base class."""
        self.status = None
        super().__init__(connection)

    def query_cmd_id(self) -> int:
        """See base class."""
        return 784

    def update_cmd_id(self) -> int:
        """See base class."""
        return 17168

    def new_status(self) -> FeatureStatus:
        """See base class."""
        return RingModeStatus()

    async def query(self) -> FeatureStatus:
        """Read the arrays, wrapped in the edit session the app uses.

        The official app never reads this feature outside a session, so the
        session is opened here too. It costs two extra round-trips per read.
        """
        await super().update(RingModeStatus(session=True))
        try:
            return await super().query()
        finally:
            await super().update(RingModeStatus(session=False))

    async def update(self, update_status: FeatureStatus) -> FeatureStatus:
        """Write the ring mode, wrapped in the edit session the app uses.

        The official app opens a session, writes, and closes it. Whether the
        device enforces it is unknown, so the sequence is reproduced as
        captured. The session is closed even when the write fails, otherwise
        the device would be left in edit mode.

        The device is read back at the end because the response to a write does
        not carry the array.
        """
        await super().update(RingModeStatus(session=True))
        try:
            await super().update(update_status)
        finally:
            await super().update(RingModeStatus(session=False))
        return await self.query()
