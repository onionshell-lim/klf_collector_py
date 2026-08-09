# modbus_protocol.py
# -*- coding: utf-8 -*-

# (프로토콜 구현)

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Tuple, Optional

from modbus_crc import verify_crc


@dataclass(frozen=True)
class ParsedResponse:
    raw_frame: bytes
    is_exception: bool
    exception_code: Optional[int]
    items: List[Tuple[int, int]]  # (address, value)


def to_hex_string(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


def build_read_request_payload(device_id: int, function_code: int, address: int, length: int) -> bytes:
    """
    Read requests for FC 1~4
    Payload WITHOUT CRC: [id][fc][addr_hi][addr_lo][len_hi][len_lo]
    """
    if not (1 <= device_id <= 247):
        raise ValueError("device_id must be 1..247 (typical range)")
    if function_code not in (1, 2, 3, 4):
        raise ValueError("function_code must be 1,2,3,4")
    if not (0 <= address <= 0xFFFF):
        raise ValueError("address must be 0x0000..0xFFFF")
    if not (1 <= length <= 0x07D0):  # 2000 typical max depending on device/spec
        raise ValueError("length must be 1..2000 (typical upper bound)")
    return bytes([
        device_id & 0xFF,
        function_code & 0xFF,
        (address >> 8) & 0xFF,
        address & 0xFF,
        (length >> 8) & 0xFF,
        length & 0xFF,
    ])


def _expected_data_byte_count(function_code: int, length: int) -> int:
    if function_code in (1, 2):
        return int(math.ceil(length / 8.0))
    if function_code in (3, 4):
        return length * 2
    raise ValueError("unsupported function_code")


def try_extract_response_any(buffer: bytes, device_id: int, function_code: int) -> Tuple[Optional[bytes], bytes]:
    """
    (device_id, function_code)에 해당하는 RTU 응답 프레임을 buffer에서 찾아 반환.
    - 예외: [id][fc|0x80][ex_code][crc_lo][crc_hi] => 5 bytes
    - Read 정상(FC 01~04): [id][fc][byte_count][data...][crc_lo][crc_hi]
    - Write 정상(FC 05/06/0F/10 및 요구사항 15/16 포함): [id][fc][addr_hi][addr_lo][val/qty_hi][val/qty_lo][crc_lo][crc_hi] => 8 bytes
    """
    if len(buffer) < 5:
        return None, buffer

    expected_fc = function_code & 0xFF
    expected_exc_fc = expected_fc | 0x80

    WRITE_FCS = {0x06, 0x10, 0x41}
    READ_FCS  = {0x01, 0x02, 0x03, 0x04}

    for start in range(0, len(buffer) - 4):
        if buffer[start] != (device_id & 0xFF):
            continue

        fc = buffer[start + 1]
        if fc not in (expected_fc, expected_exc_fc):
            continue

        # 예외 응답
        if fc == expected_exc_fc:
            if start + 5 <= len(buffer):
                frame = buffer[start:start + 5]
                if verify_crc(frame):
                    return frame, buffer[start + 5:]
            continue

        # 정상 응답
        if fc in WRITE_FCS:
            if start + 8 <= len(buffer):
                frame = buffer[start:start + 8]
                if verify_crc(frame):
                    return frame, buffer[start + 8:]
            continue

        if fc in READ_FCS:
            if start + 3 <= len(buffer):
                byte_count = buffer[start + 2]
                total_len = 3 + byte_count + 2
                if start + total_len <= len(buffer):
                    frame = buffer[start:start + total_len]
                    if verify_crc(frame):
                        return frame, buffer[start + total_len:]
            continue

    return None, buffer


def parse_response_auto(frame: bytes, start_address: int) -> ParsedResponse:
    """
    요청 길이(length)가 없어도 응답의 byte_count로 자동 파싱.
    - FC1/2: bit 단위로 byte_count*8개 표시
    - FC3/4: 16-bit 레지스터로 byte_count/2개 표시
    """
    if not verify_crc(frame):
        raise ValueError("CRC mismatch")
    if len(frame) < 5:
        raise ValueError("Frame too short")

    fc = frame[1]

    # 예외 응답
    if fc & 0x80:
        exc = frame[2]
        return ParsedResponse(raw_frame=frame, is_exception=True, exception_code=exc, items=[])

    byte_count = frame[2]
    data = frame[3:-2]
    if len(data) != byte_count:
        raise ValueError("Byte count mismatch")

    items: List[Tuple[int, int]] = []

    if fc in (1, 2):
        # bit packed, LSB first
        bit_len = byte_count * 8
        for i in range(bit_len):
            b = data[i // 8]
            bit = (b >> (i % 8)) & 0x01
            items.append((start_address + i, bit))

    elif fc in (3, 4):
        if byte_count % 2 != 0:
            raise ValueError("Register response byte_count must be even")
        reg_len = byte_count // 2
        for i in range(reg_len):
            hi = data[2 * i]
            lo = data[2 * i + 1]
            val = (hi << 8) | lo
            items.append((start_address + i, val))
    else:
        raise ValueError("Unsupported function code (expected 1~4)")

    return ParsedResponse(raw_frame=frame, is_exception=False, exception_code=None, items=items)


def to_hex_string(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)

#
# end of file
#