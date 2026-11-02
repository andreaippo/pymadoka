"""Unit tests for the RingMode codec (function 0x0310 / 0x4310).

Recovered from a Bluetooth HCI capture of the official app: the setting is
entry 11 of a 16-byte array, and a write sends 0xff on every other entry.
"""

from unittest.mock import AsyncMock

import pytest

from pymadoka.controller import Controller
from pymadoka.features.ringmode import RingModeEnum, RingModeStatus

SELECTOR = {0x01: b"\x02", 0x02: b"\xff", 0x03: b"\x01"}


def test_a_read_names_the_three_arrays():
    assert RingModeStatus().get_values() == SELECTOR | {
        0x30: b"",
        0xA0: b"",
        0xB0: b"",
    }


def test_a_write_leaves_every_other_entry_untouched():
    values = RingModeStatus(mode=RingModeEnum.HOTEL_2).get_values()

    expected = bytearray([0xFF] * 16)
    expected[11] = 2
    assert values == SELECTOR | {0x30: bytes(expected)}


@pytest.mark.parametrize("opened, flag", [(True, b"\x01"), (False, b"\x00")])
def test_a_session_command_carries_the_session_flag_only(opened, flag):
    assert RingModeStatus(session=opened).get_values() == {0xFE: flag}


def test_parses_the_mode_out_of_the_array():
    array = bytearray(16)
    array[11] = 1
    status = RingModeStatus()
    status.set_values({0x30: array, 0xA0: bytearray(16), 0xB0: bytearray(16)})

    assert status.mode is RingModeEnum.HOTEL_1


def test_a_session_response_carries_no_mode():
    status = RingModeStatus()
    status.set_values({0xFE: bytearray([0x01])})

    assert status.mode is None


@pytest.mark.asyncio
async def test_the_poll_skips_the_ring_mode():
    """Reading it opens and closes an edit session: three round trips."""
    controller = Controller("00:11:22:33:44:55")
    controller.ring_mode.query = AsyncMock()
    for name, feature in vars(controller).items():
        if name != "ring_mode" and hasattr(feature, "query"):
            feature.query = AsyncMock()

    await controller.update()

    controller.ring_mode.query.assert_not_called()
    controller.display.query.assert_awaited_once()
