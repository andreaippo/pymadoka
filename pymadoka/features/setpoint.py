"""This module contains the classes used to control the Set Point feature (temperatures set by the user)
"""

import logging
from typing import Dict
from pymadoka.feature import Feature, FeatureStatus
from pymadoka.connection import Connection

logger = logging.getLogger(__name__)

class SetPointStatus(FeatureStatus):
    """
    This class is used to store the Set Point temperatures.
    
    The values must be set as in Celsius degrees and are converted to the device format when read/written.

    No ranges validation is performed.

    Attributes:
        cooling_set_point (int): Cooling set point
        heating_set_point (int): Heating set point
    """
    
    COOLING_IDX = (0x20,2)
    HEATING_IDX = (0x21,2)
    RANGE_ENABLED_IDX = (0x30,1)
    MODE_IDX = (0x31,1)
    MINIMUM_DIFFERENTIAL_IDX = (0x32,1)
    MIN_COOLING_LOWERLIMIT_IDX = (0xa0,1)
    MIN_HEATING_LOWERLIMIT_IDX = (0xa1,1)
    COOLING_LOWERLIMIT_IDX = (0xa2,2)
    HEATING_LOWERLIMIT_IDX = (0xa3,2)
    COOLING_LOWERLIMIT_SYMBOL_IDX = (0xa4,1)
    HEATING_LOWERLIMIT_SYMBOL_IDX = (0xa5,1)
    MAX_COOLING_UPPERLIMIT_IDX = (0xb0,1)
    MAX_HEATING_UPPERLIMIT_IDX = (0xb1,1)
    COOLING_UPPERLIMIT_IDX = (0xb2,2)
    HEATING_UPPERLIMIT_IDX = (0xb3,2)
    COOLING_UPPERLIMIT_SYMBOL_IDX = (0xb4,1)
    HEATING_UPPERLIMIT_SYMBOL_IDX = (0xb5,1)
    
    def __init__(self,cooling_set_point:int, heating_set_point:int):
        """Inits the status with the set points
        
        Args: 
            cooling_set_point (int): Cooling set point
            heating_set_point (int): Heating set point
        """
        self.cooling_set_point = cooling_set_point
        self.heating_set_point = heating_set_point
        self.range_enabled = 0
        self.mode = 0
        self.min_differential = 0
        self.min_cooling_lowerlimit = 0
        self.min_heating_lowerlimit = 0
        self.cooling_lowerlimit = 0
        self.heating_lowerlimit = 0
        self.cooling_lowerlimit_symbol = 0
        self.heating_lowerlimit_symbol = 0
        self.max_cooling_upperlimit = 0
        self.max_heating_upperlimit = 0
        self.cooling_upperlimit = 0
        self.heating_upperlimit = 0
        self.cooling_upperlimit_symbol = 0
        self.heating_upperlimit_symbol = 0
        
    def set_values(self, values:Dict[str,bytearray]):
        """See base class.

        Only the 2-byte parameters carry a temperature in the device's 1/128
        degree scale. Flags, symbols and the differential are single-byte plain
        values: dividing them by 128 would collapse every one of them to 0 and
        the value could no longer be echoed back on a write.
        """

        def read(idx, current):
            raw = values.get(idx[0])
            if raw is None:
                return current
            value = int.from_bytes(raw, "big")
            return round(value / 128.0) if idx[1] == 2 else value

        self.cooling_set_point = read(self.COOLING_IDX, self.cooling_set_point)
        self.heating_set_point = read(self.HEATING_IDX, self.heating_set_point)
        self.range_enabled = read(self.RANGE_ENABLED_IDX, self.range_enabled)
        self.mode = read(self.MODE_IDX, self.mode)
        self.min_differential = read(self.MINIMUM_DIFFERENTIAL_IDX, self.min_differential)
        self.min_cooling_lowerlimit = read(self.MIN_COOLING_LOWERLIMIT_IDX, self.min_cooling_lowerlimit)
        self.min_heating_lowerlimit = read(self.MIN_HEATING_LOWERLIMIT_IDX, self.min_heating_lowerlimit)
        self.cooling_lowerlimit = read(self.COOLING_LOWERLIMIT_IDX, self.cooling_lowerlimit)
        self.heating_lowerlimit = read(self.HEATING_LOWERLIMIT_IDX, self.heating_lowerlimit)
        self.cooling_lowerlimit_symbol = read(self.COOLING_LOWERLIMIT_SYMBOL_IDX, self.cooling_lowerlimit_symbol)
        self.heating_lowerlimit_symbol = read(self.HEATING_LOWERLIMIT_SYMBOL_IDX, self.heating_lowerlimit_symbol)
        self.max_cooling_upperlimit = read(self.MAX_COOLING_UPPERLIMIT_IDX, self.max_cooling_upperlimit)
        self.max_heating_upperlimit = read(self.MAX_HEATING_UPPERLIMIT_IDX, self.max_heating_upperlimit)
        self.cooling_upperlimit = read(self.COOLING_UPPERLIMIT_IDX, self.cooling_upperlimit)
        self.heating_upperlimit = read(self.HEATING_UPPERLIMIT_IDX, self.heating_upperlimit)
        self.cooling_upperlimit_symbol = read(self.COOLING_UPPERLIMIT_SYMBOL_IDX, self.cooling_upperlimit_symbol)
        self.heating_upperlimit_symbol = read(self.HEATING_UPPERLIMIT_SYMBOL_IDX, self.heating_upperlimit_symbol)


        
    def get_values(self) -> Dict[str,bytearray]:
        """See base class.

        Every parameter is serialized from the instance attributes, never from
        hardcoded zeros: the device rejects a set-point write whose range/limit
        parameters contradict the ones it currently holds (they are configurable
        from the official Daikin app), so the values read back from the device
        must be echoed unchanged and only the set points modified.
        """
        values = {}
        for idx, value in (
            (self.COOLING_IDX, self.cooling_set_point),
            (self.HEATING_IDX, self.heating_set_point),
            (self.RANGE_ENABLED_IDX, self.range_enabled),
            (self.MODE_IDX, self.mode),
            (self.MINIMUM_DIFFERENTIAL_IDX, self.min_differential),
            (self.MIN_COOLING_LOWERLIMIT_IDX, self.min_cooling_lowerlimit),
            (self.MIN_HEATING_LOWERLIMIT_IDX, self.min_heating_lowerlimit),
            (self.COOLING_LOWERLIMIT_IDX, self.cooling_lowerlimit),
            (self.HEATING_LOWERLIMIT_IDX, self.heating_lowerlimit),
            (self.COOLING_LOWERLIMIT_SYMBOL_IDX, self.cooling_lowerlimit_symbol),
            (self.HEATING_LOWERLIMIT_SYMBOL_IDX, self.heating_lowerlimit_symbol),
            (self.MAX_COOLING_UPPERLIMIT_IDX, self.max_cooling_upperlimit),
            (self.MAX_HEATING_UPPERLIMIT_IDX, self.max_heating_upperlimit),
            (self.COOLING_UPPERLIMIT_IDX, self.cooling_upperlimit),
            (self.HEATING_UPPERLIMIT_IDX, self.heating_upperlimit),
            (self.COOLING_UPPERLIMIT_SYMBOL_IDX, self.cooling_upperlimit_symbol),
            (self.HEATING_UPPERLIMIT_SYMBOL_IDX, self.heating_upperlimit_symbol),
        ):
            param_id, size = idx
            # Temperatures use the device's 1/128 degree scale (2 bytes), flags and
            # symbols are plain single-byte values.
            raw = round(value * 128) if size == 2 else int(value)
            values[param_id] = raw.to_bytes(size, "big")
        return values

class SetPoint(Feature):
    """
    This class is used to control the Set Point temperatures (temperatures set by the user)

    Attributes:
        status (SetPointStatus): Current status
    """
    def __init__(self, connection: Connection):
        """See base class."""
        self.status = None
        super().__init__(connection)

    def query_cmd_id(self) -> int:
        """See base class."""
        return 64
    
    def update_cmd_id(self) -> int:
        """See base class."""
        return 16448

    def new_status(self) -> FeatureStatus:
        """See base class."""
        return SetPointStatus(0,0)

    # Parameters that describe the set-point range configuration. They belong to
    # the device (they can be changed from the official Daikin app) and must be
    # written back exactly as read, otherwise the device silently rejects the
    # whole set-point command.
    RANGE_PARAMS = (
        "range_enabled",
        "mode",
        "min_differential",
        "min_cooling_lowerlimit",
        "min_heating_lowerlimit",
        "cooling_lowerlimit",
        "heating_lowerlimit",
        "cooling_lowerlimit_symbol",
        "heating_lowerlimit_symbol",
        "max_cooling_upperlimit",
        "max_heating_upperlimit",
        "cooling_upperlimit",
        "heating_upperlimit",
        "cooling_upperlimit_symbol",
        "heating_upperlimit_symbol",
    )

    # Value of the MODE parameter (0x31) when the device keeps one set point per
    # mode (cooling and heating can differ). Observed values: 2 on devices with
    # the dual set point enabled, 0 and 1 on devices sharing a single set point -
    # those refuse any command whose two set points differ.
    DUAL_SET_POINT_MODE = 2

    async def update(self, update_status: FeatureStatus) -> FeatureStatus:
        """Update the set points, preserving the device's range configuration.

        Callers build a `SetPointStatus` with the set points only, so the range
        parameters are taken from the last status read from the device.
        """
        if self.status is not None:
            for param in self.RANGE_PARAMS:
                setattr(update_status, param, getattr(self.status, param))
            if update_status.mode != self.DUAL_SET_POINT_MODE:
                self._unify_set_points(update_status)
            if update_status.range_enabled:
                self._warn_out_of_range(update_status)
        return await super().update(update_status)

    def _unify_set_points(self, status: "SetPointStatus"):
        """Write the same value to both set points when the device is not in dual
        set-point mode.

        Outside that mode the device holds one temperature for cooling and heating
        and refuses the whole command if the two differ. Callers only know the mode
        they are acting on (e.g. cooling while in COOL), so they leave the other
        set point at its previous value: the changed one is the requested
        temperature and is applied to both.
        """
        if status.cooling_set_point == status.heating_set_point:
            return

        cooling_changed = status.cooling_set_point != self.status.cooling_set_point
        heating_changed = status.heating_set_point != self.status.heating_set_point

        if cooling_changed and not heating_changed:
            requested = status.cooling_set_point
        elif heating_changed and not cooling_changed:
            requested = status.heating_set_point
        else:
            # Both (or neither) changed: no way to tell which one was requested,
            # so keep the cooling value and make it explicit in the log.
            requested = status.cooling_set_point
            logger.warning(
                f"{self.log_id} device is not in dual set-point mode but cooling "
                f"({status.cooling_set_point}) and heating ({status.heating_set_point}) "
                f"were both requested; using {requested} for both"
            )

        logger.debug(
            f"{self.log_id} shared set-point mode: writing {requested} to both set points"
        )
        status.cooling_set_point = requested
        status.heating_set_point = requested

    def _warn_out_of_range(self, status: "SetPointStatus"):
        """Log the set points the device would refuse, so a rejected command is
        not silent. The device enforces the range limits when range_enabled is set
        and drops the whole command instead of clamping."""
        for label, value, lower, upper in (
            ("cooling", status.cooling_set_point, status.cooling_lowerlimit, status.cooling_upperlimit),
            ("heating", status.heating_set_point, status.heating_lowerlimit, status.heating_upperlimit),
        ):
            if upper and not (lower <= value <= upper):
                logger.warning(
                    f"{self.log_id} {label} set point {value} is outside the range configured on the "
                    f"device ({lower}-{upper}); the device may refuse the command"
                )
