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
    speeds. Two of the extra parameters are a bit mask of the speeds the indoor
    unit accepts, one per mode; the rest are kept as read in `other`.

    Only bit 0 of the masks is mapped, and it means the automatic speed. It was
    read off four indoor units: three accept the automatic speed and report 0x0d
    for heating, the fourth refuses it, from the official app as much as from
    here, and reports 0x0c. The cooling mask is 0x1d on all four. The remaining
    bits do not line up with the low and high speeds in any obvious way, so
    nothing is claimed about them.

    Attributes:
        cooling_fan_speed (FanSpeedEnum): Cooling fan speed
        heating_fan_speed (FanSpeedEnum): Heating fan speed
        cooling_speeds (int): Mask of the speeds accepted while cooling, None
            until the whole block has been read
        heating_speeds (int): Mask of the speeds accepted while heating, None
            likewise
        other (Dict[int,bytearray]): Every other parameter reported by the
            device, keyed by parameter id, kept as read for further analysis
    """

    COOLING_IDX = 0x20
    HEATING_IDX = 0x21

    COOLING_SPEEDS_IDX = 0x12
    HEATING_SPEEDS_IDX = 0x13

    # Bit of the masks above that stands for the automatic speed.
    AUTO_BIT = 0x01

    def __init__(self,cooling_fan_speed:FanSpeedEnum = None, heating_fan_speed:FanSpeedEnum = None):
        """Inits with the cooling and heating fan speeds.

        Args:
            cooling_fan_speed (FanSpeedEnum): Cooling fan speed, None to leave
                out of the command, which is how the whole block is asked for
            heating_fan_speed (FanSpeedEnum): Heating fan speed, None likewise
        """
        self.cooling_fan_speed = cooling_fan_speed
        self.heating_fan_speed = heating_fan_speed
        self.cooling_speeds = None
        self.heating_speeds = None
        self.other = {}

    @property
    def supports_auto(self) -> bool:
        """Whether the indoor unit accepts the automatic fan speed.

        True while the masks have not been read, so a unit is not stripped of a
        speed it does have on the strength of a value nobody has seen yet.

        Both masks have to carry the bit. A unit that took the automatic speed
        in one mode and refused it in the other would still be described by a
        single set of speeds upstream, so the narrower answer is the safe one.
        """
        masks = [m for m in (self.cooling_speeds, self.heating_speeds) if m is not None]
        if not masks:
            return True
        return all(mask & self.AUTO_BIT for mask in masks)

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

        def read_mask(idx):
            raw = values.get(idx)
            if raw is None:
                return None
            return int.from_bytes(raw,"big")

        cooling_speeds = read_mask(self.COOLING_SPEEDS_IDX)
        if cooling_speeds is not None:
            self.cooling_speeds = cooling_speeds

        heating_speeds = read_mask(self.HEATING_SPEEDS_IDX)
        if heating_speeds is not None:
            self.heating_speeds = heating_speeds

        known = (self.COOLING_IDX, self.HEATING_IDX,
                 self.COOLING_SPEEDS_IDX, self.HEATING_SPEEDS_IDX)
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
