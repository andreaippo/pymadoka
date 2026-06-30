"""BLE framing layer: chunk splitting and reassembly for the Madoka protocol.

Each chunk is at most 20 bytes: 1-byte sequential index followed by up to 19
bytes of payload.  The first byte of the assembled payload is the total payload
length, which lets us know how many chunks to expect.
"""

import logging
import math
import typing
from abc import ABC, abstractmethod

MAX_CHUNK_SIZE = 20
_PAYLOAD_PER_CHUNK = MAX_CHUNK_SIZE - 1  # 19 bytes of payload per chunk

logger = logging.getLogger(__name__)


class TransportDelegate(ABC):
    @abstractmethod
    def response_rebuilt(self, data: bytearray):
        pass

    @abstractmethod
    def response_failed(self, data: bytearray):
        pass


class Transport:
    """Chunk splitter / reassembler used as a utility by Connection.

    split_in_chunks() is the main production path.
    rebuild_chunk() / response_rebuilt / response_failed are kept for API
    compatibility but are no longer called in the core send/receive path.
    """

    def __init__(self, delegate: TransportDelegate):
        self.chunks: list = []
        self.delegate = delegate
        self.last_id: typing.Optional[int] = None

    def clear(self):
        self.chunks.clear()
        self.last_id = None

    def is_message_complete(self) -> bool:
        if not self.chunks:
            return False
        total_payload_size = self.chunks[0][1]
        expected_chunks = math.ceil(total_payload_size / _PAYLOAD_PER_CHUNK)
        return len(self.chunks) == expected_chunks

    def rebuild_chunk(self, chunk: bytearray):
        if len(chunk) < 2:
            logger.debug(f"Chunk too short ({len(chunk)} bytes), discarding")
            return

        chunk_id = chunk[0]

        if self.last_id is not None and chunk_id <= self.last_id:
            logger.debug("New message started while reassembling, discarding previous chunks")
            out = self.chunks_data()
            self.delegate.response_failed(out)

        self.last_id = chunk_id
        self.chunks.append(chunk)

        if self.is_message_complete():
            logger.debug("Message complete")
            out = self.chunks_data()
            self.last_id = None
            self.delegate.response_rebuilt(out)

    def chunks_data(self) -> bytearray:
        out = bytearray()
        for c in self.chunks:
            out.extend(c[1:])
        self.chunks.clear()
        return out

    def split_in_chunks(self, data: bytearray) -> typing.List[bytearray]:
        """Split payload into MAX_CHUNK_SIZE-byte chunks with a sequential index prefix."""
        chunks: typing.List[bytearray] = []
        idx = 0
        while True:
            slice_ = data[idx * _PAYLOAD_PER_CHUNK : (idx + 1) * _PAYLOAD_PER_CHUNK]
            chunks.append(bytearray([idx]) + slice_)
            idx += 1
            if idx * _PAYLOAD_PER_CHUNK >= len(data):
                break
        return chunks
