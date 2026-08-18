"""This module contains the classes used to control the Fan Speed feature
"""

from enum import Enum
from typing import Dict
from pymadoka.feature import Feature, FeatureStatus
from pymadoka.connection import Connection

class FanSpeedEnum(Enum):
    HIGH = 5
    MID = 3
    LOW = 1
    AUTO = 0
    def __str__(self):
        return self.name

class FanSpeedStatus(FeatureStatus):

    """
    This class is used to store the Fan Speed status.

    Asked with no parameter the device answers the whole block, not just the two
    speeds: the extra parameters are kept as read in `other`, because which fan
    speeds an indoor unit actually accepts is expected to be in there and is not
    mapped yet.

    Attributes:
        cooling_fan_speed (FanSpeedEnum): Cooling fan speed
        heating_fan_speed (FanSpeedEnum): Heating fan speed
        other (Dict[int,bytearray]): Every other parameter reported by the
            device, keyed by parameter id, kept as read for further analysis
    """

    COOLING_IDX = 0x20
    HEATING_IDX = 0x21

    def __init__(self,cooling_fan_speed:FanSpeedEnum = None, heating_fan_speed:FanSpeedEnum = None):
        """Inits with the cooling and heating fan speeds.

        Args:
            cooling_fan_speed (FanSpeedEnum): Cooling fan speed, None to leave
                out of the command, which is how the whole block is asked for
            heating_fan_speed (FanSpeedEnum): Heating fan speed, None likewise
        """
        self.cooling_fan_speed = cooling_fan_speed
        self.heating_fan_speed = heating_fan_speed
        self.other = {}

    def set_values(self, values:Dict[str,bytearray]):
        """See base class.

        A write echoes back only the parameters it carried, so both speeds are
        read defensively.
        """

        def read_speed(idx):
            raw = values.get(idx)
            if raw is None:
                return None
            value = int.from_bytes(raw,"big")
            # The device counts the speeds one by one, the thermostat shows
            # three: everything between low and high is the middle one.
            if 2 <= value <= 4:
                return FanSpeedEnum.MID
            return FanSpeedEnum(value)

        cooling = read_speed(self.COOLING_IDX)
        if cooling is not None:
            self.cooling_fan_speed = cooling

        heating = read_speed(self.HEATING_IDX)
        if heating is not None:
            self.heating_fan_speed = heating

        known = (self.COOLING_IDX, self.HEATING_IDX)
        self.other = {k:v for k,v in values.items() if k not in known}

    def get_values(self) -> Dict[str,bytearray]:
        """See base class.

        Only the speeds that were set are serialized. With neither of them set
        the command carries no parameter, which is how the device is asked for
        the whole block.
        """
        values = {}

        if self.cooling_fan_speed is not None:
            values[self.COOLING_IDX] = self.cooling_fan_speed.value.to_bytes(1,"big")

        if self.heating_fan_speed is not None:
            values[self.HEATING_IDX] = self.heating_fan_speed.value.to_bytes(1,"big")

        return values

class FanSpeed(Feature):
    """
    This class is used to control the Fan Speed.

    Attributes:
        status (FanSpeedStatus): Current status
    """
    def __init__(self, connection: Connection):
        """See base class."""
        self.status = None
        super().__init__(connection)

    def query_cmd_id(self) -> int:
        """See base class."""
        return 80
    
    def update_cmd_id(self) -> int:
        """See base class."""
        return 16464

    def new_status(self) -> FeatureStatus:
        """See base class.

        With no speed set the query asks for the whole block, so the parameters
        beside the two speeds are reported too.
        """
        return FanSpeedStatus()
