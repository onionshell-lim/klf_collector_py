# -*- coding: utf-8 -*-

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

import submode

POPULAR_BAUDS = [4800, 9600, 38400, 115200]


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
        ok = submode.Set_SerialPort(baud, "N", 1)
        if not ok:
            messagebox.showerror("오류", "Baud/Parity/StopBit 설정값이 유효하지 않습니다.")
            return

        opened = submode.Open_SerialPort(port)
        if not opened:
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
