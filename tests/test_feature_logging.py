"""Feature log lines carry the device address.

With several thermostats on the same process the lines of each device
interleave; the address is what tells them apart.
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from pymadoka.features.setpoint import SetPoint, SetPointStatus

ADDRESS = "00:11:22:33:44:55"


def make_response(status: SetPointStatus) -> bytearray:
    """Full response message: size(1) + cmd_id(3) + params."""
    out = bytearray()
    for k, v in status.get_values().items():
        out.append(k)
        out.append(len(v))
        out.extend(v)
    message = bytearray([0x00, 0x00, 0x40, 0x40]) + out
    message[0] = len(message)
    return message


@pytest.mark.asyncio
async def test_update_logs_carry_the_address_and_the_status_written(caplog):
    feature = SetPoint(SimpleNamespace(address=ADDRESS))
    # The device echoes something unrelated to what was written.
    response = make_response(SetPointStatus(0, 0))

    with patch.object(SetPoint, "_send_command", AsyncMock(return_value=response)), \
         caplog.at_level(logging.DEBUG, logger="pymadoka.feature"):
        await feature.update(SetPointStatus(25, 22))

    messages = [r.getMessage() for r in caplog.records]
    assert messages
    assert all(m.startswith(f"[{ADDRESS}] SetPoint ") for m in messages)
    assert '"cooling_set_point": 25' in messages[-1]
