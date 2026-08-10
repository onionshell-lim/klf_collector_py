# -*- coding: utf-8 -*-

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import submode
from device_comm_frame import DeviceCommFrame
from port_config_frame import PortConfigFrame


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
