"""The owner of reconnects learns about a link drop when it happens.

Home Assistant builds the controller with reconnect=False: its coordinator is
the only thing allowed to reconnect. Without a signal it only noticed a drop on
its next poll, up to a whole scan interval later, and every command sent in
between failed with "connection not available".
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from pymadoka.connection import Connection, ConnectionStatus
from pymadoka.controller import Controller


def make_connection(callback):
    conn = Connection(
        "00:11:22:33:44:55", adapter=None, reconnect=False,
        disconnected_callback=callback,
    )
    conn.client = AsyncMock()
    conn.connection_status = ConnectionStatus.CONNECTED
    return conn


def test_a_link_drop_calls_the_callback_after_the_state_is_updated():
    seen = []
    conn = None

    def callback():
        seen.append(conn.connection_status)

    conn = make_connection(callback)
    conn.on_disconnect(conn.client)

    assert seen == [ConnectionStatus.DISCONNECTED]


def test_a_stale_client_does_not_call_it():
    callback = MagicMock()
    conn = make_connection(callback)

    conn.on_disconnect(AsyncMock())  # a failed candidate, not self.client

    callback.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_does_not_call_it():
    callback = MagicMock()
    conn = make_connection(callback)
    client = conn.client
    # bleak fires the disconnected callback from inside disconnect().
    client.disconnect.side_effect = lambda: conn.on_disconnect(client)

    await conn.cleanup()

    callback.assert_not_called()


def test_a_failing_callback_does_not_break_the_disconnect():
    conn = make_connection(MagicMock(side_effect=RuntimeError("boom")))

    conn.on_disconnect(conn.client)

    assert conn.connection_status is ConnectionStatus.DISCONNECTED


def test_the_controller_passes_it_to_the_connection():
    callback = MagicMock()
    controller = Controller("00:11:22:33:44:55", reconnect=False,
                            disconnected_callback=callback)

    assert controller.connection.disconnected_callback is callback
