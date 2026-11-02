"""Unit tests for the FanSpeed codec and the accepted-speed masks.

The masks were read off four indoor units: three accept the automatic speed
and report 0x0d for heating, the fourth refuses it (from the official app as
well) and reports 0x0c. The cooling mask is 0x1d on all four.
"""

import pytest

from pymadoka.features.fanspeed import FanSpeed, FanSpeedEnum, FanSpeedStatus


def block(heating_speeds):
    """A whole fan speed block, as answered to a query with no argument."""
    return {
        0x12: bytearray([0x1D]),
        0x13: bytearray([heating_speeds]),
        0x20: bytearray([0x05]),
        0x21: bytearray([0x01]),
    }


def test_a_query_asks_for_the_whole_block():
    """No argument: the device then answers every parameter, not just the two
    speeds the query would otherwise carry."""
    assert FanSpeedStatus().serialize() == bytearray([0x00, 0x00])


def test_a_write_carries_only_the_speeds():
    status = FanSpeedStatus(FanSpeedEnum.LOW, FanSpeedEnum.HIGH)

    assert status.get_values() == {0x20: b"\x01", 0x21: b"\x05"}


def test_parses_the_speeds_and_the_masks():
    status = FanSpeedStatus()
    status.set_values(block(0x0D))

    assert status.cooling_fan_speed is FanSpeedEnum.HIGH
    assert status.heating_fan_speed is FanSpeedEnum.LOW
    assert status.cooling_speeds == 0x1D
    assert status.heating_speeds == 0x0D


def test_mid_is_widened():
    status = FanSpeedStatus()
    status.set_values({0x20: bytearray([0x02]), 0x21: bytearray([0x04])})

    assert status.cooling_fan_speed is FanSpeedEnum.MID
    assert status.heating_fan_speed is FanSpeedEnum.MID


def test_a_unit_reporting_0x0d_accepts_the_automatic_speed():
    status = FanSpeedStatus()
    status.set_values(block(0x0D))

    assert status.supports_auto is True


def test_a_unit_reporting_0x0c_refuses_the_automatic_speed():
    status = FanSpeedStatus()
    status.set_values(block(0x0C))

    assert status.supports_auto is False


def test_unread_masks_do_not_withhold_the_automatic_speed():
    """The response to a write echoes the speeds only: nothing is known about
    the masks, so no speed is taken away on the strength of it."""
    status = FanSpeedStatus()
    status.set_values({0x20: bytearray([0x00]), 0x21: bytearray([0x00])})

    assert status.cooling_speeds is None
    assert status.supports_auto is True


def test_unmodelled_parameters_are_kept():
    status = FanSpeedStatus()
    status.set_values(block(0x0D) | {0x99: bytearray([0xFF])})

    assert status.other == {0x99: bytearray([0xFF])}


def _fan_speed_with_status(status):
    """A FanSpeed whose base-class update() writes nothing to a device."""
    feature = FanSpeed.__new__(FanSpeed)
    feature.status = status
    return feature


@pytest.fixture
def no_device(monkeypatch):
    async def fake_update(self, update_status):
        self.status = update_status
        return update_status

    monkeypatch.setattr("pymadoka.feature.Feature.update", fake_update)


@pytest.mark.asyncio
async def test_update_keeps_the_masks(no_device):
    """Otherwise a unit refusing the automatic speed would be offered it again
    after every write, until the next poll."""
    previous = FanSpeedStatus()
    previous.set_values(block(0x0C))
    feature = _fan_speed_with_status(previous)

    result = await feature.update(FanSpeedStatus(FanSpeedEnum.LOW, FanSpeedEnum.LOW))

    assert result.cooling_fan_speed is FanSpeedEnum.LOW
    assert result.heating_speeds == 0x0C
    assert result.supports_auto is False


@pytest.mark.asyncio
async def test_update_keeps_the_speed_it_did_not_write(no_device):
    previous = FanSpeedStatus()
    previous.set_values(block(0x0D))
    feature = _fan_speed_with_status(previous)

    result = await feature.update(FanSpeedStatus(cooling_fan_speed=FanSpeedEnum.MID))

    assert result.cooling_fan_speed is FanSpeedEnum.MID
    assert result.heating_fan_speed is FanSpeedEnum.LOW


@pytest.mark.asyncio
async def test_update_with_no_previous_status_keeps_only_what_was_written(no_device):
    feature = _fan_speed_with_status(None)

    result = await feature.update(FanSpeedStatus(FanSpeedEnum.HIGH, FanSpeedEnum.HIGH))

    assert result.heating_fan_speed is FanSpeedEnum.HIGH
    assert result.heating_speeds is None
