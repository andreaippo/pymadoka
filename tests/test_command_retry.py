"""Short attempts with retries, and late replies that cannot desync.

A lost notification used to cost one 10 s wait and then fail. With the opt-in
policy (e.g. 3 attempts of 2 s) it is retried after 2 s instead. A reply to an
attempt that timed out can still arrive later; the stale guard drops it so it
does not resolve the next request for the same cmd_id.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from pymadoka.connection import Connection, ConnectionException, ConnectionStatus
from pymadoka.features.power import PowerState


def make_connection(tries: int = 3, timeout: float = 0.05):
    conn = Connection("00:11:22:33:44:55", adapter=None, reconnect=False)
    conn.client = AsyncMock()
    conn.connection_status = ConnectionStatus.CONNECTED
    conn.command_max_tries = tries
    conn.command_timeout = timeout
    conn.command_retry_delay = 0
    return conn


def power_response(cmd_id: int, on: bool = True) -> bytearray:
    data = bytearray([0x00]) + bytearray([0x00]) + cmd_id.to_bytes(2, "big")
    data += bytearray([0x20, 0x01, 0x01 if on else 0x00])
    data[0] = len(data)
    return data


def writes(conn) -> int:
    return conn.client.write_gatt_char.await_count


def test_defaults_keep_one_ten_second_attempt():
    conn = Connection("00:11:22:33:44:55", adapter=None, reconnect=False)
    assert conn.command_max_tries == 1
    assert conn.command_timeout == 10.0


@pytest.mark.asyncio
async def test_a_lost_reply_is_retried():
    conn = make_connection()
    feature = PowerState(conn)
    cmd_id = feature.query_cmd_id()

    async def answer_second_write(*_args):
        if writes(conn) == 2:
            asyncio.get_event_loop().call_soon(
                conn.response_rebuilt, power_response(cmd_id))

    conn.client.write_gatt_char.side_effect = answer_second_write

    status = await feature.query()

    assert status.turn_on is True
    assert writes(conn) == 2


@pytest.mark.asyncio
async def test_every_attempt_timing_out_raises_timeout():
    conn = make_connection(tries=3)
    feature = PowerState(conn)

    with pytest.raises(asyncio.TimeoutError):
        await feature.query()

    assert writes(conn) == 3


@pytest.mark.asyncio
async def test_a_single_attempt_policy_does_not_retry():
    conn = make_connection(tries=1)
    feature = PowerState(conn)

    with pytest.raises(asyncio.TimeoutError):
        await feature.query()

    assert writes(conn) == 1


@pytest.mark.asyncio
async def test_a_late_reply_does_not_resolve_the_next_request():
    conn = make_connection(tries=2)
    feature = PowerState(conn)
    cmd_id = feature.query_cmd_id()
    straggler = power_response(cmd_id, on=False)
    own_reply = power_response(cmd_id, on=True)

    async def on_write(*_args):
        n = writes(conn)
        loop = asyncio.get_event_loop()
        if n == 2:
            # Attempt 1's late reply answers attempt 2.
            loop.call_soon(conn.response_rebuilt, straggler)
        elif n == 3:
            # Attempt 2's own reply only lands now, while the NEXT query is
            # waiting, just ahead of that query's real reply.
            loop.call_soon(conn.response_rebuilt, straggler)
            loop.call_later(0.01, conn.response_rebuilt, own_reply)

    conn.client.write_gatt_char.side_effect = on_write

    await feature.query()

    conn.command_max_tries = 1
    conn.command_timeout = 1.0
    status = await feature.query()

    # Answered by its own reply, not by the straggler of the previous query.
    assert status.turn_on is True
    assert writes(conn) == 3


@pytest.mark.asyncio
async def test_a_link_drop_is_not_retried_and_clears_the_guard():
    conn = make_connection(tries=3)
    feature = PowerState(conn)
    cmd_id = feature.query_cmd_id()
    conn.arm_stale_guard(cmd_id, 2)

    async def drop(*_args):
        asyncio.get_event_loop().call_soon(conn.on_disconnect, conn.client)

    conn.client.write_gatt_char.side_effect = drop

    with pytest.raises(ConnectionException):
        await feature.query()

    assert writes(conn) == 1
    assert conn._stale_guard == {}


@pytest.mark.asyncio
async def test_the_guard_expires():
    conn = make_connection()
    cmd_id = PowerState(conn).query_cmd_id()
    conn.stale_response_window = 0.01
    conn.arm_stale_guard(cmd_id, 1)

    await asyncio.sleep(0.02)
    future = asyncio.get_event_loop().create_future()
    conn.requests[cmd_id] = [future]
    conn.response_rebuilt(power_response(cmd_id))

    assert future.done()
