"""Unit tests for the Display codec (function 0x0302 / 0x4302).

The parameter ids were recovered from a Bluetooth HCI capture of the official
app: 0x31 contrast, 0x32 display brightness, 0x33 status ring brightness, each
one byte on a 0-19 scale.
"""

import pytest

from pymadoka.controller import Controller
from pymadoka.features.display import Display, DisplayStatus


def test_a_query_asks_for_the_whole_block():
    assert DisplayStatus().serialize() == bytearray([0x00, 0x00])


def test_parses_the_three_levels_and_keeps_the_rest():
    status = DisplayStatus()
    status.set_values({
        0x31: bytearray([0x0A]),
        0x32: bytearray([0x13]),
        0x33: bytearray([0x05]),
        0x40: bytearray([0x01]),
    })

    assert status.contrast == 10
    assert status.brightness == 19
    assert status.ring_brightness == 5
    assert status.other == {0x40: bytearray([0x01])}


@pytest.mark.parametrize(
    "status, expected",
    [
        (DisplayStatus(brightness=7), {0x32: b"\x07"}),
        (DisplayStatus(contrast=12), {0x31: b"\x0c"}),
        (DisplayStatus(ring_brightness=0), {0x33: b"\x00"}),
    ],
)
def test_a_write_carries_only_the_level_it_sets(status, expected):
    """The official app writes one parameter at a time."""
    assert status.get_values() == expected


def _display_with_status(status):
    """A Display whose base-class update() writes nothing to a device."""
    feature = Display.__new__(Display)
    feature.status = status
    return feature


@pytest.fixture
def no_device(monkeypatch):
    async def fake_update(self, update_status):
        self.status = update_status
        return update_status

    monkeypatch.setattr("pymadoka.feature.Feature.update", fake_update)


@pytest.mark.asyncio
async def test_update_keeps_the_levels_it_did_not_write(no_device):
    previous = DisplayStatus(brightness=19, contrast=10, ring_brightness=5)
    feature = _display_with_status(previous)

    result = await feature.update(DisplayStatus(contrast=3))

    assert result.contrast == 3
    assert result.brightness == 19
    assert result.ring_brightness == 5


@pytest.mark.asyncio
async def test_update_with_no_previous_status_keeps_only_what_was_written(no_device):
    feature = _display_with_status(None)

    result = await feature.update(DisplayStatus(brightness=4))

    assert result.brightness == 4
    assert result.contrast is None


def test_the_controller_polls_the_display_once():
    """0x33 is a level of the display block: polling EyeBrightness beside
    Display would cost a second round trip on the same function."""
    controller = Controller("00:11:22:33:44:55")

    assert isinstance(controller.display, Display)
    assert not hasattr(controller, "eye_brightness")
