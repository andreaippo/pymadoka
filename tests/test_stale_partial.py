"""A partial response left over from an earlier exchange must not fail a new request.

A query that times out after losing its tail chunk leaves the first chunks of
its reply in the transport. The next reply for the same command restarts the
sequence at chunk 0, which flushed that stale partial through
response_failed(), and response_failed() cancelled the head pending request
of that command: the new, valid one. The caller saw "connection lost" while
the link was up, and the real reply then arrived with nobody waiting for it.
"""

from unittest.mock import AsyncMock

import pytest

from pymadoka.connection import Connection, ConnectionStatus

CMD_ID = 0x0040


def make_reply(size: int) -> bytearray:
    """Build a response for CMD_ID: total length, 0x00, cmd id, then payload."""
    reply = bytearray([size, 0x00]) + CMD_ID.to_bytes(2, "big")
    reply += bytes(range(size - len(reply)))
    return reply


def make_connection():
    connection = Connection("00:11:22:33:44:55", adapter=None, reconnect=False)
    connection.client = AsyncMock()
    connection.connection_status = ConnectionStatus.CONNECTED
    return connection


@pytest.mark.asyncio
async def test_stale_partial_does_not_fail_next_request():
    connection = make_connection()

    # Earlier reply lost its tail chunk; its first two chunks are still held.
    stale = connection.transport.split_in_chunks(make_reply(45))
    connection.notification_handler(None, stale[0])
    connection.notification_handler(None, stale[1])

    response = await connection.send(CMD_ID, bytearray([0x00]))

    reply = make_reply(45)
    for chunk in connection.transport.split_in_chunks(reply):
        connection.notification_handler(None, chunk)

    assert not response.cancelled()
    assert response.result() == reply


@pytest.mark.asyncio
async def test_response_failed_leaves_pending_request_alone():
    connection = make_connection()

    response = await connection.send(CMD_ID, bytearray([0x00]))
    connection.response_failed(make_reply(45)[:38])

    assert not response.done()
    assert connection.requests[CMD_ID] == [response]


def test_disconnect_clears_partial_message():
    connection = make_connection()
    stale = connection.transport.split_in_chunks(make_reply(45))
    connection.notification_handler(None, stale[0])

    connection.on_disconnect(connection.client)

    assert connection.transport.chunks == []
    assert connection.transport.last_id is None
