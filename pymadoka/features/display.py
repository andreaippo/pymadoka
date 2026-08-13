"""This module contains the classes used to control the Display feature (on-board display settings)
"""

from typing import Dict
from pymadoka.feature import Feature, FeatureStatus
from pymadoka.connection import Connection

class DisplayStatus(FeatureStatus):

    """
    This class is used to store the on-board display settings.

    The device answers a query with fifteen parameters, but a write carries a
    single one: writing the whole block is not what the official app does and
    the meaning of most parameters is still unknown, so the ones that are not
    set explicitly are left out of the command.

    Attributes:
        brightness (int): Display brightness, 0-19
        contrast (int): Display contrast, 0-19
        other (Dict[int,bytearray]): Every other parameter reported by the
            device, keyed by parameter id, kept as read for further analysis
    """

    BRIGHTNESS_IDX = 0x32
    CONTRAST_IDX = 0x31

    MIN_LEVEL = 0
    MAX_LEVEL = 19

    def __init__(self, brightness:int = None, contrast:int = None):
        """Inits with the display settings.

        Attributes:
            brightness (int): Display brightness, 0-19, None to leave untouched
            contrast (int): Display contrast, 0-19, None to leave untouched
        """
        self.brightness = brightness
        self.contrast = contrast
        self.other = {}

    def set_values(self, values:Dict[int,bytearray]):
        """See base class."""

        brightness = values.get(self.BRIGHTNESS_IDX)
        if brightness:
            self.brightness = brightness[0]

        contrast = values.get(self.CONTRAST_IDX)
        if contrast:
            self.contrast = contrast[0]

        self.other = {k:v for k,v in values.items()
                      if k not in (self.BRIGHTNESS_IDX, self.CONTRAST_IDX)}

    def get_values(self) -> Dict[int,bytearray]:
        """See base class.

        Only the attributes that were set are serialized. With none of them set
        the command carries no parameter, which is how the device is asked for
        the whole block.
        """
        values = {}

        if self.brightness is not None:
            values[self.BRIGHTNESS_IDX] = bytes([self.brightness])

        if self.contrast is not None:
            values[self.CONTRAST_IDX] = bytes([self.contrast])

        return values

class Display(Feature):

    """
    This class is used to control the settings of the display built in the device

    Attributes:
        status (DisplayStatus): Current status
    """
    def __init__(self, connection: Connection):
        """See base class."""
        self.status = None
        super().__init__(connection)

    def query_cmd_id(self) -> int:
        """See base class."""
        return 770

    def update_cmd_id(self) -> int:
        """See base class."""
        return 17154

    def new_status(self) -> FeatureStatus:
        """See base class."""
        return DisplayStatus()
