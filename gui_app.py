# gui_app.py
# -*- coding: utf-8 -*-

# (독립모드 GUI)

from __future__ import annotations

import threading
import time
import queue
import struct
import tkinter as tk
from tkinter import ttk, messagebox

import submode
from modbus_protocol import (
    try_extract_response_any,
    to_hex_string,
)

POPULAR_BAUDS = [4800, 9600, 38400, 115200]

FC_MENU = [
    ("0x01:Read Coil Status", 0x01),
    ("0x02:Read Input Status", 0x02),
    ("0x03:Read Holding Reg", 0x03),
    ("0x04:Read Input Reg.", 0x04),
    ("0x06:Write Integer", 0x06),
    ("0x10:Write Floating Point", 0x10),
    ("0x41:Clear the Cumulative", 0x41),
]

# -----------------------------
# 요구사항: title mapping
# -----------------------------
COMMON_FLOAT_TITLE = {
    0x0001: "Real-time velocity",
    0x0003: "Average velocity",
    0x0102: "Instantaneous flow rate",
    0x0105: "Cumulative flow",
    0x010B: "Empty height",
    0x010D: "Water level",
    0x2251: "Water level sensor range",
    0x2253: "Low level adjustment",
    0x2255: "High level adjustment",
    0x2257: "Blind zone",
    0x2259: "Distance offset",
}

CHANNEL_MODEL_ADDR = 0x220F
CHANNEL_MODEL_TITLE = "Channel flowcalculationmodel"

CHANNEL_MODEL_VALUE_TITLE = {
    0: "Rectangular",
    1: "Trapezoidal",
    2: "Rectangular–Trapezoidal",
    3: "DoubleTrapezoidal",
    4: "U-shaped",
    5: "Irregula",
}

def _mk_map(*pairs):
    """addr->list[title] (중복 주소 대응)"""
    m: dict[int, list[str]] = {}
    for addr, title in pairs:
        m.setdefault(addr, []).append(title)
    return m

MODEL_FLOAT_TITLE: dict[int, dict[int, list[str]]] = {
    0: _mk_map(
        (0x2201, "Rectangular-channel width"),
        (0x2205, "Rectangular-channel height"),
        (0x2203, "Rectangular-distance from flow meter to channel wall"),
    ),
    1: _mk_map(
        (0x2201, "Trapezoidal channel top width"),
        (0x2203, "Trapezoidal channel bottom width"),
        (0x2205, "Trapezoidal channel height"),
        (0x2207, "Trapezoidal-distance from flowmeter to channel wall"),
    ),
    2: _mk_map(
        (0x2201, "Rectangular-trapezoidal channel top width"),
        (0x2203, "Rectangular-trapezoidal channel bottom width"),
        (0x2205, "Rectangular-trapezoidal channel upper height"),
        (0x2207, "Rectangular-trapezoidal channel lower height"),
        (0x2209, "Rectangular-trapezoidal-distance from flowmeter to channel wall"),
    ),
    3: _mk_map(
        (0x2201, "Double trapezoidal channel top width"),
        (0x2203, "Double trapezoidal channel middle width"),
        (0x2205, "Double trapezoidal channel bottom width"),
        (0x2207, "Double trapezoidal channel upper height"),
        (0x2209, "Double trapezoidal channel lower height"),
        (0x220B, "Double trapezoidal-distance from flowmeter to channel wall"),
    ),
    4: _mk_map(
        (0x2201, "U-shaped channel width"),
        (0x2203, "U-shaped channel height"),
        (0x2201, "Distance from flow meter to irregular channel wall"),  # 요구사항 그대로(주소 중복)
    ),
    5: _mk_map(
        (0x2203, "Irregular channel width"),
        (0x2271, "Irregular-bottom elevation"),
    ),
}


def _parse_hex_u16(s: str) -> int:
    s = s.strip()
    v = int(s, 16) if not s.lower().startswith("0x") else int(s, 16)
    if not (0 <= v <= 0xFFFF):
        raise ValueError("u16 out of range")
    return v


def _parse_hex_byte(s: str) -> int:
    s = s.strip()
    v = int(s, 16) if not s.lower().startswith("0x") else int(s, 16)
    if not (0 <= v <= 0xFF):
        raise ValueError("byte out of range")
    return v


def _float_from_4bytes_reorder(b4: bytes) -> float:
    """
    value: 수신 데이터 4byte를 32-bit float로 변환
    변환 순서: [2][3][0][1]
    """
    if len(b4) != 4:
        raise ValueError("need 4 bytes")
    reordered = bytes([b4[2], b4[3], b4[0], b4[1]])
    return struct.unpack(">f", reordered)[0]


def _get_data_region_from_rtu_frame(frame: bytes) -> tuple[int, bytes]:
    """
    정상 응답: [id][fc][byte_count][data...][crc_lo][crc_hi]
    반환: (fc, data)
    """
    if len(frame) < 5:
        raise ValueError("frame too short")
    fc = frame[1]
    if fc & 0x80:
        return fc, b""
    byte_count = frame[2]
    data = frame[3:-2]
    if len(data) != byte_count:
        raise ValueError("byte_count mismatch")
    return fc, data


class PortConfigFrame(ttk.Frame):
    def __init__(self, master, on_status_change):
        super().__init__(master)
        self.on_status_change = on_status_change

        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value=str(9600))

        # UI
        ttk.Label(self, text="Serial Port").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.port_combo = ttk.Combobox(self, textvariable=self.port_var, state="readonly", width=20)
        self.port_combo.grid(row=0, column=1, sticky="w", padx=6, pady=6)

        ttk.Label(self, text="Baudrate").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        self.baud_combo = ttk.Combobox(
            self, textvariable=self.baud_var, state="readonly", width=20,
            values=[str(b) for b in POPULAR_BAUDS]
        )
        self.baud_combo.grid(row=1, column=1, sticky="w", padx=6, pady=6)

        self.open_btn = ttk.Button(self, text="포트 열기", command=self.open_port)
        self.open_btn.grid(row=2, column=0, padx=6, pady=6, sticky="we")

        self.close_btn = ttk.Button(self, text="포트 닫기", command=self.close_port)
        self.close_btn.grid(row=2, column=1, padx=6, pady=6, sticky="we")

        ttk.Label(self, text="통신 상태").grid(row=3, column=0, sticky="w", padx=6, pady=6)
        self.status_lbl = ttk.Label(self, text="닫힘", width=20)
        self.status_lbl.grid(row=3, column=1, sticky="w", padx=6, pady=6)

        self.refresh_ports()

        # 주기적 상태 업데이트
        self.after(500, self._poll_status)

    def refresh_ports(self):
        ports = submode.Get_Active_SerialPort()
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def open_port(self):
        self.refresh_ports()
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("오류", "Serial Port를 선택하세요.")
            return

        baud = int(self.baud_var.get())
        # 요구사항(2-1)엔 baud만 선택 UI가 있으므로 parity/stopbits는 기본값으로 호출
        ok = submode.Set_SerialPort(baud, "N", 1)
        if not ok:
            messagebox.showerror("오류", "Baud/Parity/StopBit 설정값이 유효하지 않습니다.")
            return

        opened = submode.Open_SerialPort(port)
        if not opened:
            # 에러 문자열이 구현되어 있으면 표시
            err = submode.Get_Last_Serial_Error() if hasattr(submode, "Get_Last_Serial_Error") else ""
            messagebox.showerror("오류", f"포트 열기 실패: {port}\n{err}")

        self._update_status()

    def close_port(self):
        submode.Close_SerialPort()
        self._update_status()

    def _update_status(self):
        is_open = submode.Is_SerialPort_Open()
        self.status_lbl.config(text="열림" if is_open else "닫힘")
        self.on_status_change(is_open)

    def _poll_status(self):
        self._update_status()
        self.after(500, self._poll_status)


class DeviceCommFrame(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)

        # 요구사항 변수
        self.Device_ID = 1
        self.Function_Code = 1
        self.Address = 0x0000
        self.Length = 6  # 송신 바이트 수(DEC)
        self.Payload = [0x00, 0x02]  # 2 bytes
        self.request_length = 0x0002  # big-endian from Payload

        # 채널 모델(0x220F로 읽은 값) 상태 저장
        self.channel_model: int | None = None

        # UI 변수
        self.device_id_var = tk.StringVar(value="1")
        self.fc_var = tk.StringVar(value=FC_MENU[2][0])  # 기본: Holding Reg
        self.addr_var = tk.StringVar(value="0000")       # HEX
        self.len_var = tk.StringVar(value="6")           # DEC
        self.p1_var = tk.StringVar(value="00")           # HEX byte
        self.p2_var = tk.StringVar(value="02")           # HEX byte

        self.tx_var = tk.StringVar(value="--")
        self.rx_var = tk.StringVar(value="--")

        # 결과 큐(스레드->UI)
        self._result_q: "queue.Queue[tuple]" = queue.Queue()

        # ---------- UI ----------
        ttk.Label(self, text="장치 번호").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Combobox(
            self, textvariable=self.device_id_var, state="readonly", width=18,
            values=[str(i) for i in range(1, 17)]
        ).grid(row=0, column=1, sticky="w", padx=6, pady=6)

        ttk.Label(self, text="function code").grid(row=0, column=2, sticky="w", padx=6, pady=6)
        ttk.Combobox(
            self, textvariable=self.fc_var, state="readonly", width=22,
            values=[name for name, _ in FC_MENU]
        ).grid(row=0, column=3, sticky="w", padx=6, pady=6)

        ttk.Label(self, text="주소(HEX)").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(self, textvariable=self.addr_var, width=20).grid(row=1, column=1, sticky="w", padx=6, pady=6)

        ttk.Label(self, text="길이(DEC)").grid(row=1, column=2, sticky="w", padx=6, pady=6)
        ttk.Entry(self, textvariable=self.len_var, width=20).grid(row=1, column=3, sticky="w", padx=6, pady=6)

        ttk.Label(self, text="Write 데이터(HEX byte) 2개").grid(row=2, column=0, sticky="w", padx=6, pady=6)
        pf = ttk.Frame(self)
        pf.grid(row=2, column=1, columnspan=3, sticky="w", padx=6, pady=6)
        ttk.Label(pf, text="P1").grid(row=0, column=0, padx=3)
        ttk.Entry(pf, textvariable=self.p1_var, width=8).grid(row=0, column=1, padx=3)
        ttk.Label(pf, text="P2").grid(row=0, column=2, padx=3)
        ttk.Entry(pf, textvariable=self.p2_var, width=8).grid(row=0, column=3, padx=3)

        ttk.Button(self, text="요청 전송", command=self.send_request).grid(
            row=3, column=0, columnspan=4, sticky="we", padx=6, pady=6
        )

        # 통신 상태 창
        ttk.Label(self, text="송신(HEX)").grid(row=4, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(self, textvariable=self.tx_var, width=90, state="readonly").grid(
            row=4, column=1, columnspan=3, sticky="we", padx=6, pady=6
        )

        ttk.Label(self, text="수신(HEX)").grid(row=5, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(self, textvariable=self.rx_var, width=90, state="readonly").grid(
            row=5, column=1, columnspan=3, sticky="we", padx=6, pady=6
        )

        # 데이터 표시 창
        ttk.Label(self, text='데이터 표시 (address:data)  / data는 16bit HEX').grid(row=6, column=0, sticky="w", padx=6, pady=6)
        self.data_text = tk.Text(self, height=10, width=90)
        self.data_text.grid(row=7, column=0, columnspan=4, sticky="nsew", padx=6, pady=6)

        # 항목 표시 창
        ttk.Label(self, text='항목 표시 (title:value)').grid(row=8, column=0, sticky="w", padx=6, pady=6)
        self.item_text = tk.Text(self, height=10, width=90)
        self.item_text.grid(row=9, column=0, columnspan=4, sticky="nsew", padx=6, pady=6)

        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(3, weight=1)
        self.grid_rowconfigure(7, weight=1)
        self.grid_rowconfigure(9, weight=1)

        self.after(100, self._poll_results)

    def send_request(self):
        if not submode.Is_SerialPort_Open():
            messagebox.showerror("오류", "먼저 포트를 열어주세요.")
            return

        # 2-2-1 ~ 2-2-5 입력값 파싱 및 변수 저장
        try:
            self.Device_ID = int(self.device_id_var.get())
            self.Function_Code = dict(FC_MENU)[self.fc_var.get()]
            self.Address = _parse_hex_u16(self.addr_var.get())

            self.Length = int(self.len_var.get().strip())
            if self.Length <= 0:
                raise ValueError

            p1 = _parse_hex_byte(self.p1_var.get())
            p2 = _parse_hex_byte(self.p2_var.get())
            self.Payload = [p1, p2]

            # 2-2-5-1 request_length (big-endian)
            self.request_length = (p1 << 8) | p2

        except Exception:
            messagebox.showerror(
                "오류",
                "입력값을 확인하세요.\n"
                "- 주소: HEX (예: 0102 또는 0x0102)\n"
                "- 길이: DEC (보통 6)\n"
                "- Payload: HEX byte 2개(00~FF). (request_length=Payload big-endian)"
            )
            return

        # Payload 2바이트로 request_length(big-endian) 계산은 그대로 사용
        # self.request_length = (p1<<8) | p2
        # Modbus Read 요청 payload 구성( CRC는 submode가 붙임 )
        
        fc = self.Function_Code

        if fc in (0x01, 0x02, 0x03, 0x04):
            # READ: [ID][FC][ADDR_H][ADDR_L][QTY_H][QTY_L]
            tx_payload = bytes([
                self.Device_ID & 0xFF,
                fc & 0xFF,
                (self.Address >> 8) & 0xFF,
                self.Address & 0xFF,
                self.Payload[0],
                self.Payload[1],
            ])

        elif fc in (0x05, 0x06):
            # WRITE SINGLE: [ID][FC][ADDR_H][ADDR_L][VAL_H][VAL_L]
            tx_payload = bytes([
                self.Device_ID & 0xFF,
                fc & 0xFF,
                (self.Address >> 8) & 0xFF,
                self.Address & 0xFF,
                self.Payload[0],
                self.Payload[1],
            ])

        elif fc in (0x15, 0x16, 0x0F, 0x10):
            # WRITE MULTIPLE: UI에 실제 데이터 블록(바이트카운트 + 데이터)이 없어서 전송을 막는 것이 안전
            messagebox.showerror(
                "미지원",
                "Write Multiple(0x15/0x16)은 현재 UI 입력(2바이트 Payload)만으로는 데이터 블록이 부족합니다.\n"
                "추가 입력(Quantity, ByteCount, Data bytes)을 UI에 추가한 뒤 구현하는 것을 권장합니다."
            )

        # Length(DEC) 만큼만 전송
        if self.Length < 4:
            messagebox.showerror("오류", "길이(DEC)는 최소 4 이상이어야 합니다.")
            return
        if self.Length > len(tx_payload):
            messagebox.showerror("오류", f"길이(DEC)={self.Length} 이(가) payload 길이({len(tx_payload)})보다 큽니다.")
            return

        # 화면 초기화
        self.rx_var.set("--")
        self.data_text.delete("1.0", "end")
        self.item_text.delete("1.0", "end")

        # RX 드레인
        while True:
            _, n = submode.Get_Modbus_Reveive_Data()
            if n == 0:
                break

        # 2-2-6: Send_Modbus_Transmit_Data(Length, Payload) 호출
        ok, tx_frame = submode.Send_Modbus_Transmit_Data(self.Length, tx_payload)
        if not ok:
            err = submode.Get_Last_Serial_Error() if hasattr(submode, "Get_Last_Serial_Error") else ""
            messagebox.showerror("오류", f"송신 실패\n{err}")
            return

        self.tx_var.set(to_hex_string(tx_frame))

        # 응답 대기
        threading.Thread(
            target=self._wait_response_worker,
            args=(self.Device_ID, self.Function_Code, self.Address, self.request_length),
            daemon=True
        ).start()

    def _wait_response_worker(self, device_id: int, fc: int, address: int, request_length: int):
        timeout_sec = 1.5
        start = time.time()
        buf = b""
        frame = None

        while time.time() - start < timeout_sec:
            chunk, n = submode.Get_Modbus_Reveive_Data()
            if n > 0:
                buf += chunk
                found, remain = try_extract_response_any(buf, device_id, fc)
                if found is not None:
                    frame = found
                    buf = remain
                    break
            time.sleep(0.02)

        if frame is None:
            self._result_q.put(("no_response", None, None, None))
            return

        self._result_q.put(("ok", frame, address, request_length))

    def _poll_results(self):
        try:
            while True:
                kind, frame, address, request_length = self._result_q.get_nowait()

                # 응답 없음
                if kind == "no_response":
                    self.rx_var.set("--")
                    continue

                # 수신 HEX 표시
                self.rx_var.set(to_hex_string(frame))

                # 예외 응답 처리: [id][fc|0x80][ex_code][crc_lo][crc_hi]
                fc = frame[1] & 0xFF
                if fc & 0x80:
                    exc = frame[2] if len(frame) > 2 else 0
                    self.data_text.delete("1.0", "end")
                    self.item_text.delete("1.0", "end")
                    self.data_text.insert("end", f"EXCEPTION: code=0x{exc:02X}\n")
                    self.item_text.insert("end", f"EXCEPTION: code=0x{exc:02X}\n")
                    continue

                # -----------------------------------------
                # ✅ WRITE 응답 처리(고정 8바이트 에코 응답)
                #   [id][fc][addr_hi][addr_lo][val/qty_hi][val/qty_lo][crc_lo][crc_hi]
                # -----------------------------------------
                WRITE_FCS = {0x06, 0x10, 0x41}
                if fc in WRITE_FCS:
                    self.data_text.delete("1.0", "end")
                    self.item_text.delete("1.0", "end")

                    if len(frame) < 8:
                        self.data_text.insert("end", "WRITE 응답 길이 오류(8바이트 미만)\n")
                        self.item_text.insert("end", "WRITE 응답 길이 오류(8바이트 미만)\n")
                        continue

                    addr = (frame[2] << 8) | frame[3]
                    val_or_qty = (frame[4] << 8) | frame[5]

                    # 2-2-8: address:data (data는 16bit HEX)
                    self.data_text.insert("end", f"0x{addr:04X}:0x{val_or_qty:04X}\n")

                    # 항목 표시(요구사항에 write 항목 규칙은 없어서 최소 표시)
                    self.item_text.insert(
                        "end",
                        f"WRITE_OK: FC=0x{fc:02X} ADDR=0x{addr:04X} VALUE/QTY=0x{val_or_qty:04X}\n"
                    )
                    continue


                # -----------------------------------------
                # ✅ READ 응답 처리(FC 01~04): [id][fc][byte_count][data...][crc]
                # -----------------------------------------
                try:
                    fc2, data = _get_data_region_from_rtu_frame(frame)
                except Exception as e:
                    self.data_text.delete("1.0", "end")
                    self.item_text.delete("1.0", "end")
                    self.data_text.insert("end", f"Parse error: {e}\n")
                    self.item_text.insert("end", f"Parse error: {e}\n")
                    continue

                # 2-2-8 데이터 표시창(16bit HEX로)
                self._render_data_hex16(fc2, address, data, request_length)

                # 2-2-9 항목 표시창(title:value)
                self._render_items(fc2, address, data, request_length)

        except queue.Empty:
            pass

        self.after(100, self._poll_results)

    def _render_data_hex16(self, fc: int, start_addr: int, data: bytes, request_length: int):
        self.data_text.delete("1.0", "end")

        if fc in (3, 4):
            # 레지스터: 2 bytes * request_length
            max_regs = min(request_length, len(data) // 2)
            for i in range(max_regs):
                hi = data[2*i]
                lo = data[2*i+1]
                val = (hi << 8) | lo
                self.data_text.insert("end", f"0x{(start_addr+i)&0xFFFF:04X}:0x{val:04X}\n")

        elif fc in (1, 2):
            # 비트: 0/1을 16-bit HEX로 표시(0x0000/0x0001)
            bit_count = min(request_length, len(data) * 8)
            for i in range(bit_count):
                b = data[i // 8]
                bit = (b >> (i % 8)) & 0x01
                self.data_text.insert("end", f"0x{(start_addr+i)&0xFFFF:04X}:0x{bit:04X}\n")

        else:
            self.data_text.insert("end", "(지원하지 않는 function code)\n")

    def _render_items(self, fc: int, start_addr: int, data: bytes, request_length: int):
        self.item_text.delete("1.0", "end")

        if fc not in (3, 4):
            self.item_text.insert("end", "(항목 표시: FC3/FC4에서만 동작)\n")
            return

        # 2-2-9-3: 0x220F는 2byte(1 register) -> 16-bit int 모델값
        # 요청 시작 주소가 0x220F면 모델 업데이트
        if start_addr == CHANNEL_MODEL_ADDR and len(data) >= 2:
            model_val = (data[0] << 8) | data[1]
            model_val = int(model_val) & 0xFFFF
            if model_val in CHANNEL_MODEL_VALUE_TITLE:
                self.channel_model = model_val
                model_name = CHANNEL_MODEL_VALUE_TITLE[model_val]
                self.item_text.insert("end", f"{CHANNEL_MODEL_TITLE}:{model_name}\n")
            else:
                self.channel_model = None
                self.item_text.insert("end", f"{CHANNEL_MODEL_TITLE}:UNKNOWN({model_val})\n")
            # 0x220F는 여기서 끝(추가 float 처리 없음)
            return

        # 공통/모델별 float 주소 처리: 4바이트(2레지스터)씩
        if len(data) < 4:
            self.item_text.insert("end", "(float 변환용 데이터 4바이트 부족)\n")
            return

        float_groups = min(len(data) // 4, request_length // 2 if request_length >= 2 else len(data)//4)

        # 모델 미확정인데 0x22xx 영역을 읽는 경우 안내
        if self.channel_model is None and (start_addr & 0xFF00) == 0x2200:
            self.item_text.insert("end", "(Channel flowcalculationmodel 미확정: 0x220F를 먼저 읽으세요)\n")

        for i in range(float_groups):
            addr = (start_addr + i*2) & 0xFFFF
            chunk = data[i*4:(i+1)*4]

            try:
                fval = _float_from_4bytes_reorder(chunk)
            except Exception:
                continue

            # title 선택 우선순위:
            # 1) 공통 address
            # 2) channel_model 기반 address
            # 3) 기본(0xADDR)
            title = None
            if addr in COMMON_FLOAT_TITLE:
                title = COMMON_FLOAT_TITLE[addr]
            else:
                if self.channel_model in MODEL_FLOAT_TITLE:
                    titles = MODEL_FLOAT_TITLE[self.channel_model].get(addr)
                    if titles:
                        title = " / ".join(titles)

            if title is None:
                title = f"0x{addr:04X}"

            self.item_text.insert("end", f"{title}:{fval}\n")


class ModbusToolApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Modbus RTU Tool (RS485)")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        nb.add(PortConfigFrame(nb, on_status_change=self.on_status_change), text="포트 설정")
        nb.add(DeviceCommFrame(nb), text="장치 통신")

        self.geometry("980x740")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_status_change(self, is_open: bool):
        pass

    def on_close(self):
        submode.Close_SerialPort()
        self.destroy()

#
# end of file
#