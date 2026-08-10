# submode.py
# -*- coding: utf-8 -*-

# (서브모드:  함수들)

from __future__ import annotations

from typing import List, Tuple, Union, Optional

from serial.tools import list_ports
import serial

from serial_comm import SerialManager
from modbus_crc import append_crc


# 내부 상태 저장
_manager = SerialManager()

_baudrate: int = 9600
_parity: str = "N"     # "N", "E", "O"
_stopbits: int = 1     # 1 or 2
_selected_port: Optional[str] = None

_last_tx_frame: bytes = b""


def Get_Active_SerialPort() -> List[str]:
    """현재 유효한 Serial Port 리스트 반환"""
    ports = []
    for p in list_ports.comports():
        # p.device: Windows -> "COM3", Linux -> "/dev/ttyUSB0"
        ports.append(p.device)
    return ports


def Set_SerialPort(baud_rate: int, Parity: str, Stop_Bit: int) -> bool:
    """
    baud rate, Parity, Stop Bit 3개를 받아 검증 후 저장
    """
    global _baudrate, _parity, _stopbits

    popular = {4800, 9600, 19200, 38400, 57600, 115200}
    if baud_rate not in popular:
        return False

    if Parity not in ("N", "E", "O"):
        return False

    if Stop_Bit not in (1, 2):
        return False

    _baudrate = int(baud_rate)
    _parity = Parity
    _stopbits = int(Stop_Bit)
    return True


def Open_SerialPort(SerialPort_No: str) -> bool:
    """
    Serial Port 번호(이름) 저장 및 포트 오픈
    1-3-1 이미 열린 포트가 없으면 열고
    1-3-2 저장된 baud/parity/stopbits로 설정
    1-3-3 성공/실패 bool
    """
    global _selected_port
    _selected_port = SerialPort_No

    if _manager.is_open():
        return True

    parity_map = {
        "N": serial.PARITY_NONE,
        "E": serial.PARITY_EVEN,
        "O": serial.PARITY_ODD,
    }
    stop_map = {
        1: serial.STOPBITS_ONE,
        2: serial.STOPBITS_TWO,
    }

    parity = parity_map.get(_parity, serial.PARITY_NONE)
    stopbits = stop_map.get(_stopbits, serial.STOPBITS_ONE)

    return _manager.open(
        port=_selected_port,
        baudrate=_baudrate,
        parity=parity,
        stopbits=stopbits,
        timeout=0.05,
    )


def Get_Modbus_Reveive_Data() -> Tuple[bytes, int]:
    """
    수신 버퍼에서 현재까지 수신된 데이터를 모두 반환(consume)
    (data, byte_count)
    """
    data = _manager.read_received_all()
    return data, len(data)


def Send_Modbus_Transmit_Data(arg1, arg2) -> Tuple[bool, bytes]:
    """
    송신할 data와 byte 수를 받아 Modbus RTU(binary) 프레임으로 변환(CRC 추가) 후 송신
    - 입력 data는 'CRC 없는 payload'로 가정
    - 반환: (성공여부, 실제 송신 프레임(bytes))
    요구사항 반영:
      - GUI에서 Send_Modbus_Transmit_Data(Length, Payload) 로 호출
    동시에 기존 호환:
      - Send_Modbus_Transmit_Data(data, byte_count) 형태도 허용

    동작:
      - Payload(=CRC 없는 바이트열)에서 Length 바이트만 취해서 CRC 추가 후 송신
    """
    global _last_tx_frame

    # (Length, Payload) 형태
    if isinstance(arg1, int):
        byte_count = int(arg1)
        data = arg2
    else:
        # (data, byte_count) 형태 (기존)
        data = arg1
        byte_count = int(arg2)

    if isinstance(data, list):
        payload = bytes(data[:byte_count])
    else:
        payload = bytes(data[:byte_count])

    frame = append_crc(payload)
    ok = _manager.write(frame)
    if ok:
        _last_tx_frame = frame
    return ok, frame


def Close_SerialPort() -> None:
    """Port 닫고 수신 쓰레드 종료하고 Circular buffer free"""
    global _selected_port, _last_tx_frame
    _manager.close()
    _selected_port = None
    _last_tx_frame = b""


# (GUI 상태 표시용) - 요구사항 외 추가 헬퍼지만 독립모드에서 상태 표시가 필요하여 제공
def Is_SerialPort_Open() -> bool:
    return _manager.is_open()

def Get_Last_Tx_Frame() -> bytes:
    return _last_tx_frame

def Get_Last_Serial_Error() -> str:
    return _manager.last_error

#
# end of file
#