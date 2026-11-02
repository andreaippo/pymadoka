"""A request that cannot be answered must not outlive the attempt.

Every command queues a future in ``Connection.requests`` and the response for
that cmd_id resolves the oldest one. Two ways of leaving a future behind used
to cost the caller ten seconds each:

* the link dropped (or the connection was cleaned up) while a request was
  waiting: nothing failed the future, so the caller held the operation lock
  until the 10 s response timeout and got ``TimeoutError`` instead of a
  ``ConnectionException``;
* the chunk writes failed: ``send()`` raised but left its future queued. The
  next reply for that cmd_id resolved the orphan, and the next query timed
  out although the device had answered it.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from pymadoka.connection import Connection, ConnectionException, ConnectionStatus
from pymadoka.consts import SEND_MAX_TRIES
from pymadoka.features.power import PowerState

# Well below the 10 s response timeout: a test that needs it has failed.
FAST = 1.0


def make_connection():
    conn = Connection("00:11:22:33:44:55", adapter=None, reconnect=False)
    conn.client = AsyncMock()
    conn.connection_status = ConnectionStatus.CONNECTED
    return conn


def power_response(cmd_id: int) -> bytearray:
    """A PowerState reply: size, cmd_id (3 bytes), param 0x20 = on."""
    data = bytearray([0x00]) + bytearray([0x00]) + cmd_id.to_bytes(2, "big")
    data += bytearray([0x20, 0x01, 0x01])
    data[0] = len(data)
    return data


async def wait_until_queued(conn, cmd_id):
    for _ in range(100):
        if conn.requests.get(cmd_id):
            return
        await asyncio.sleep(0)
    raise AssertionError("the request never reached the queue")


@pytest.mark.asyncio
async def test_link_drop_fails_the_pending_request_at_once():
    conn = make_connection()
    feature = PowerState(conn)
    task = asyncio.create_task(feature.query())
    await wait_until_queued(conn, feature.query_cmd_id())

    conn.on_disconnect(conn.client)

    with pytest.raises(ConnectionException):
        await asyncio.wait_for(task, FAST)
    assert not conn._operation_lock.locked()


@pytest.mark.asyncio
async def test_cleanup_fails_the_pending_request_at_once():
    conn = make_connection()
    feature = PowerState(conn)
    task = asyncio.create_task(feature.query())
    await wait_until_queued(conn, feature.query_cmd_id())

    await conn.cleanup()

    with pytest.raises(ConnectionException):
        await asyncio.wait_for(task, FAST)


@pytest.mark.asyncio
async def test_failed_send_leaves_no_future_to_swallow_the_next_reply():
    conn = make_connection()
    feature = PowerState(conn)
    cmd_id = feature.query_cmd_id()

    conn.client.write_gatt_char = AsyncMock(side_effect=RuntimeError("gatt busy"))
    with patch("pymadoka.connection.asyncio.sleep", AsyncMock()):
        with pytest.raises(ConnectionException):
            await feature.query()

    # The device is reachable again and answers the next query at once.
    async def write_and_answer(uuid, chunk):
        asyncio.get_running_loop().call_soon(conn.response_rebuilt, power_response(cmd_id))

    conn.client.write_gatt_char = AsyncMock(side_effect=write_and_answer)
    status = await asyncio.wait_for(feature.query(), FAST)
    assert status.turn_on is True
    assert conn.requests[cmd_id] == []


@pytest.mark.asyncio
async def test_send_stops_at_the_first_chunk_that_cannot_be_written():
    conn = make_connection()
    conn.client.write_gatt_char = AsyncMock(side_effect=RuntimeError("gatt busy"))
    data = bytearray(40)  # several chunks
    assert len(conn.transport.split_in_chunks(bytearray(4) + data)) > 1

    with patch("pymadoka.connection.asyncio.sleep", AsyncMock()):
        with pytest.raises(ConnectionException):
            await conn.send(0x0020, data)

    # Only the first chunk was retried; the rest of the message was not sent.
    assert conn.client.write_gatt_char.await_count == SEND_MAX_TRIES
    assert conn.requests[0x0020] == []


@pytest.mark.asyncio
async def test_a_reply_skips_futures_that_are_already_done():
    conn = make_connection()
    loop = asyncio.get_running_loop()
    abandoned, waiting = loop.create_future(), loop.create_future()
    abandoned.cancel()
    conn.requests[0x0020] = [abandoned, waiting]

    reply = power_response(0x0020)
    conn.response_rebuilt(reply)

    assert waiting.result() == reply
    assert conn.requests[0x0020] == []


@pytest.mark.asyncio
async def test_a_failed_reply_skips_futures_that_are_already_done():
    conn = make_connection()
    loop = asyncio.get_running_loop()
    abandoned, waiting = loop.create_future(), loop.create_future()
    abandoned.cancel()
    conn.requests[0x0020] = [abandoned, waiting]

    conn.response_failed(power_response(0x0020))

    assert waiting.cancelled()
    assert conn.requests[0x0020] == []
