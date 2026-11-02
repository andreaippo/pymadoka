"""Unit tests for the SetPoint status codec (parse/serialize)."""

import pytest

from pymadoka.features.setpoint import SetPoint, SetPointStatus


def serialize_params(values) -> bytearray:
    """Wrap a param dict in a full response message.

    Layout: size(1) + cmd_id(3) + params. The first byte holds the total
    message length, as produced by the device after chunk reassembly.
    """
    out = bytearray()
    for k, v in values.items():
        out.append(k)
        out.append(len(v))
        out.extend(v)
    message = bytearray([0x00, 0x00, 0x00, 0x40]) + out
    message[0] = len(message)
    return message


def make_response(status: SetPointStatus) -> bytearray:
    return serialize_params(status.get_values())


def test_serialize_uses_128_multiplier_encoding():
    status = SetPointStatus(25, 22)
    values = status.get_values()

    assert values[SetPointStatus.COOLING_IDX[0]] == (25 * 128).to_bytes(2, "big")
    assert values[SetPointStatus.HEATING_IDX[0]] == (22 * 128).to_bytes(2, "big")


def test_parse_serialize_round_trip():
    original = SetPointStatus(25, 22)

    parsed = SetPointStatus(0, 0)
    parsed.parse(make_response(original))

    assert parsed.cooling_set_point == 25
    assert parsed.heating_set_point == 22


def test_parse_hardware_dump():
    """Params exactly as captured from a real BRC1H diagnostics dump.

    2-byte temperature params are value*128; 1-byte params are raw ints
    (mode arrives as 0x01, upper limit symbols as 0x17, max upper as 0x20).
    """
    values = {
        0x20: (25 * 128).to_bytes(2, "big"),  # cooling set point
        0x21: (25 * 128).to_bytes(2, "big"),  # heating set point
        0x30: bytes([0x00]),                  # range disabled
        0x31: bytes([0x01]),                  # mode = 1 (raw)
        0x32: bytes([0x00]),
        0xA0: bytes([0x10]),                  # min cooling lower limit = 16
        0xA1: bytes([0x10]),
        0xA2: (16 * 128).to_bytes(2, "big"),  # cooling lower limit = 16
        0xA3: (16 * 128).to_bytes(2, "big"),
        0xA4: bytes([0x17]),
        0xA5: bytes([0x17]),
        0xB0: bytes([0x20]),                  # max cooling upper limit = 32
        0xB1: bytes([0x20]),
        0xB2: (32 * 128).to_bytes(2, "big"),  # cooling upper limit = 32
        0xB3: (32 * 128).to_bytes(2, "big"),
        0xB4: bytes([0x1D]),
        0xB5: bytes([0x1D]),
    }

    parsed = SetPointStatus(0, 0)
    parsed.parse(serialize_params(values))

    assert parsed.cooling_set_point == 25
    assert parsed.range_enabled == 0
    assert parsed.mode == 1
    assert parsed.min_cooling_lowerlimit == 16
    assert parsed.cooling_lowerlimit == 16
    assert parsed.max_cooling_upperlimit == 32
    assert parsed.cooling_upperlimit == 32


def test_range_enabled_raw_flag_detected():
    """range_enabled arrives as a raw 0x01 — the historical /128 decode
    read it as 0 and silently disabled dual-setpoint detection."""
    original = SetPointStatus(26, 20)
    values = original.get_values()
    values[SetPointStatus.RANGE_ENABLED_IDX[0]] = bytes([0x01])

    parsed = SetPointStatus(0, 0)
    parsed.parse(serialize_params(values))

    assert parsed.range_enabled == 1


def test_parse_tolerates_missing_optional_params():
    """Only the set points are mandatory; a response without the limit params
    must not raise (firmware variants, shortened responses)."""
    values = {
        SetPointStatus.COOLING_IDX[0]: (24 * 128).to_bytes(2, "big"),
        SetPointStatus.HEATING_IDX[0]: (21 * 128).to_bytes(2, "big"),
    }

    parsed = SetPointStatus(0, 0)
    parsed.parse(serialize_params(values))

    assert parsed.cooling_set_point == 24
    assert parsed.heating_set_point == 21
    assert parsed.range_enabled == 0
    assert parsed.cooling_upperlimit == 0


def test_update_echoes_device_params():
    """Serializing a parsed status must echo the device's own raw params."""
    device_state = SetPointStatus(24, 20)
    values = device_state.get_values()
    values[SetPointStatus.RANGE_ENABLED_IDX[0]] = bytes([0x01])
    values[SetPointStatus.COOLING_LOWERLIMIT_IDX[0]] = (18 * 128).to_bytes(2, "big")

    parsed = SetPointStatus(0, 0)
    parsed.parse(serialize_params(values))

    # Simulate a user changing the cooling set point on the parsed status.
    parsed.cooling_set_point = 26
    serialized = parsed.get_values()

    assert serialized[SetPointStatus.COOLING_IDX[0]] == (26 * 128).to_bytes(2, "big")
    assert serialized[SetPointStatus.RANGE_ENABLED_IDX[0]] == bytes([0x01])
    assert serialized[SetPointStatus.COOLING_LOWERLIMIT_IDX[0]] == (18 * 128).to_bytes(2, "big")


def test_fresh_status_serializes_legacy_defaults():
    """A fresh status (query path) keeps the historical wire payload."""
    values = SetPointStatus(0, 0).get_values()
    assert values[SetPointStatus.RANGE_ENABLED_IDX[0]] == (0).to_bytes(1, "big")
    assert values[SetPointStatus.MODE_IDX[0]] == (2).to_bytes(1, "big")


# Device state with a range configuration stored on it (e.g. from the Daikin
# app): every param differs from the legacy defaults a fresh status sends.
RANGE_CONFIGURED_PARAMS = {
    0x20: (24 * 128).to_bytes(2, "big"),
    0x21: (20 * 128).to_bytes(2, "big"),
    0x30: bytes([0x01]),
    0x31: bytes([0x01]),
    0x32: bytes([0x02]),
    0xA0: bytes([0x10]),
    0xA1: bytes([0x10]),
    0xA2: (18 * 128).to_bytes(2, "big"),
    0xA3: (16 * 128).to_bytes(2, "big"),
    0xA4: bytes([0x17]),
    0xA5: bytes([0x17]),
    0xB0: bytes([0x20]),
    0xB1: bytes([0x20]),
    0xB2: (30 * 128).to_bytes(2, "big"),
    0xB3: (28 * 128).to_bytes(2, "big"),
    0xB4: bytes([0x1D]),
    0xB5: bytes([0x1D]),
}


def _set_point_feature():
    """A SetPoint whose commands are recorded and answered with the device
    state above, instead of going through a live connection."""
    feature = SetPoint.__new__(SetPoint)
    feature.status = None
    feature.sent = []

    async def fake_send_command(cmd_id, payload):
        feature.sent.append((cmd_id, bytes(payload)))
        return serialize_params(RANGE_CONFIGURED_PARAMS)

    feature._send_command = fake_send_command
    return feature


def _payload_params(payload: bytes) -> dict:
    """Decode a serialized payload back into {param_id: raw bytes}."""
    return {
        payload[i]: payload[i + 2:i + 2 + payload[i + 1]]
        for i in _param_offsets(payload)
    }


def _param_offsets(payload: bytes):
    i = 0
    while i < len(payload):
        yield i
        i += 2 + payload[i + 1]


@pytest.mark.asyncio
async def test_update_with_fresh_status_keeps_device_range_params():
    """cli/mqtt write a freshly built SetPointStatus(c, h). It must carry the
    device's range/limit params, not range_enabled=0, MODE=2 and zero limits,
    which the device silently drops (or which clobber its configuration)."""
    feature = _set_point_feature()
    await feature.query()

    await feature.update(SetPointStatus(26, 21))

    cmd_id, payload = feature.sent[-1]
    assert cmd_id == feature.update_cmd_id()
    sent = _payload_params(payload)
    assert sent[0x20] == (26 * 128).to_bytes(2, "big")
    assert sent[0x21] == (21 * 128).to_bytes(2, "big")
    for key, raw in RANGE_CONFIGURED_PARAMS.items():
        if key not in (0x20, 0x21):
            assert sent[key] == raw, hex(key)


@pytest.mark.asyncio
async def test_second_fresh_write_still_keeps_device_range_params():
    """Feature.update stores the written status; a later fresh write must not
    fall back to the defaults before the next poll."""
    feature = _set_point_feature()
    await feature.query()

    await feature.update(SetPointStatus(26, 21))
    await feature.update(SetPointStatus(25, 20))

    sent = _payload_params(feature.sent[-1][1])
    assert sent[0x20] == (25 * 128).to_bytes(2, "big")
    assert sent[0x30] == bytes([0x01])
    assert sent[0xB2] == (30 * 128).to_bytes(2, "big")


@pytest.mark.asyncio
async def test_update_with_parsed_status_is_unchanged():
    """A caller that copies the parsed status (Home Assistant) already echoes
    the device params; the payload must be exactly what it serializes."""
    feature = _set_point_feature()
    await feature.query()

    update_status = SetPointStatus(0, 0)
    update_status.parse(serialize_params(RANGE_CONFIGURED_PARAMS))
    # Differs from the cached status: a parsed status's own params win.
    update_status._raw_values[0x31] = bytes([0x02])
    update_status.cooling_set_point = 27
    expected = bytes(update_status.serialize())

    await feature.update(update_status)

    assert feature.sent[-1][1] == expected
