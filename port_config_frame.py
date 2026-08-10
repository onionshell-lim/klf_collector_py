# -*- coding: utf-8 -*-

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

import submode
from device_comm_frame import AUTO_MONITOR_ITEMS, read_single_monitor_value

POPULAR_BAUDS = [4800, 9600, 38400, 115200]


class PortConfigFrame(ttk.Frame):
    """Tkinter frame for selecting and opening the serial port."""

    def __init__(self, master, on_status_change):
        """Initialize the port configuration UI.

        Args:
            master: Parent widget for this frame.
            on_status_change: Callback invoked when the serial-open state changes.
        """
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

        self.auto_start_btn = ttk.Button(self, text="Auto Start", command=self.start_auto_logging)
        self.auto_start_btn.grid(row=3, column=0, padx=6, pady=6, sticky="we")

        self.auto_stop_btn = ttk.Button(self, text="Auto Stop", command=self.stop_auto_logging)
        self.auto_stop_btn.grid(row=3, column=1, padx=6, pady=6, sticky="we")

        ttk.Label(self, text="통신 상태").grid(row=4, column=0, sticky="w", padx=6, pady=6)
        self.status_lbl = ttk.Label(self, text="닫힘", width=20)
        self.status_lbl.grid(row=4, column=1, sticky="w", padx=6, pady=6)

        self.monitor_value_vars: dict[str, tk.StringVar] = {}
        self.success_count = 0
        self.fail_count = 0
        self.success_count_var = tk.StringVar(value="0")
        self.fail_count_var = tk.StringVar(value="0")

        for row_offset, (_, title) in enumerate(AUTO_MONITOR_ITEMS):
            row_index = 5 + row_offset
            ttk.Label(self, text=title).grid(row=row_index, column=0, sticky="w", padx=6, pady=2)
            value_var = tk.StringVar(value="--")
            self.monitor_value_vars[title] = value_var
            ttk.Label(self, textvariable=value_var, width=24, anchor="w").grid(
                row=row_index, column=1, sticky="w", padx=6, pady=2
            )

        ttk.Label(self, text="성공 카운터").grid(row=10, column=0, sticky="w", padx=6, pady=6)
        ttk.Label(self, textvariable=self.success_count_var, width=12, anchor="w").grid(
            row=10, column=1, sticky="w", padx=6, pady=6
        )

        ttk.Label(self, text="실패 카운터").grid(row=11, column=0, sticky="w", padx=6, pady=6)
        ttk.Label(self, textvariable=self.fail_count_var, width=12, anchor="w").grid(
            row=11, column=1, sticky="w", padx=6, pady=6
        )

        ttk.Label(self, text="송신 패킷 (HEX)").grid(row=12, column=0, sticky="w", padx=6, pady=(8, 2))
        self.tx_packet_var = tk.StringVar(value="--")
        ttk.Label(self, textvariable=self.tx_packet_var, width=60, anchor="w", wraplength=420).grid(
            row=12, column=1, sticky="w", padx=6, pady=(8, 2)
        )

        ttk.Label(self, text="수신 패킷 (HEX)").grid(row=13, column=0, sticky="w", padx=6, pady=(2, 6))
        self.rx_packet_var = tk.StringVar(value="--")
        ttk.Label(self, textvariable=self.rx_packet_var, width=60, anchor="w", wraplength=420).grid(
            row=13, column=1, sticky="w", padx=6, pady=(2, 6)
        )

        self.refresh_ports()

        self._auto_running = False
        self._auto_thread: threading.Thread | None = None
        self._auto_file = None
        self._auto_device_id = 1

        # 주기적 상태 업데이트
        self.after(500, self._poll_status)

    def refresh_ports(self):
        """Refresh the available port list from the submode layer."""
        ports = submode.Get_Active_SerialPort()
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def open_port(self):
        """Open the selected serial port using the current UI settings."""
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
        """Close the currently open serial port and refresh the UI state."""
        self.stop_auto_logging()
        submode.Close_SerialPort()
        self._update_status()

    def start_auto_logging(self):
        """Start repeated polling of the monitored values and save them to a CSV file."""
        if not submode.Is_SerialPort_Open():
            messagebox.showerror("오류", "먼저 포트를 열어주세요.")
            return
        if self._auto_running:
            return

        self.success_count = 0
        self.fail_count = 0
        self.success_count_var.set("0")
        self.fail_count_var.set("0")
        self.tx_packet_var.set("--")
        self.rx_packet_var.set("--")
        for title in self.monitor_value_vars:
            self.monitor_value_vars[title].set("--")

        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        filename = f"KLF-{timestamp}.csv"
        self._auto_file = open(filename, "a", encoding="utf-8")
        self._auto_running = True
        self.auto_start_btn.config(state="disabled")
        self.auto_stop_btn.config(state="normal")

        self._auto_thread = threading.Thread(target=self._auto_logging_loop, daemon=True)
        self._auto_thread.start()

    def stop_auto_logging(self):
        """Stop the repeated polling loop and close the CSV file."""
        if not self._auto_running:
            return

        self._auto_running = False
        if self._auto_thread is not None:
            self._auto_thread.join(timeout=0.5)
        if self._auto_file is not None:
            self._auto_file.close()
            self._auto_file = None
        self.auto_start_btn.config(state="normal")
        self.auto_stop_btn.config(state="disabled")

    def _auto_logging_loop(self):
        """Repeatedly read the monitored values and update the UI counters and CSV file."""
        while self._auto_running:
            if not submode.Is_SerialPort_Open():
                break

            try:
                read_values: list[tuple[str, str]] = []
                for address, title in AUTO_MONITOR_ITEMS:
                    value = read_single_monitor_value(self._auto_device_id, address, timeout_sec=0.2)
                    read_values.append((title, f"{value:.6f}"))
                    time.sleep(0.05)

                self.success_count += 1
                self._schedule_monitor_update(read_values, self.success_count, self.fail_count)
            except Exception:
                self.fail_count += 1
                failed_values = [(title, "ERROR") for _, title in AUTO_MONITOR_ITEMS]
                self._schedule_monitor_update(failed_values, self.success_count, self.fail_count)

            self._schedule_packet_debug_update()

            if self._auto_file is not None:
                ts = datetime.now()
                values_text = ",".join(value for _, value in read_values) if 'read_values' in locals() else ",".join("ERROR" for _ in AUTO_MONITOR_ITEMS)
                line = f"{ts.strftime('%Y-%m-%d')},{ts.strftime('%H:%M:%S')},{values_text}\n"
                self._auto_file.write(line)
                self._auto_file.flush()

            time.sleep(0.1)

        if self._auto_file is not None:
            try:
                self._auto_file.close()
            except Exception:
                pass
            self._auto_file = None

    def _schedule_monitor_update(self, values: list[tuple[str, str]], success_count: int, fail_count: int):
        """Schedule UI updates from the background thread on the main Tkinter thread."""
        self.after(0, lambda: self._apply_monitor_update(values, success_count, fail_count))

    def _apply_monitor_update(self, values: list[tuple[str, str]], success_count: int, fail_count: int):
        """Apply the latest monitoring values and counters to the UI widgets."""
        for title, value in values:
            if title in self.monitor_value_vars:
                self.monitor_value_vars[title].set(value)
        self.success_count_var.set(str(success_count))
        self.fail_count_var.set(str(fail_count))

    def _schedule_packet_debug_update(self):
        """Schedule the packet debug labels to refresh from the shared serial wrapper."""
        self.after(0, self._apply_packet_debug_update)

    def _apply_packet_debug_update(self):
        """Apply the latest transmit and receive packet bytes to the debug labels."""
        self.update_packet_debug(
            tx_frame=submode.Get_Last_Tx_Frame(),
            rx_frame=submode.Get_Last_Rx_Frame(),
        )

    def update_packet_debug(self, tx_frame: bytes | None = None, rx_frame: bytes | None = None):
        """Update the debug packet display with hexadecimal data."""
        if tx_frame is not None:
            self.tx_packet_var.set(tx_frame.hex().upper())
        if rx_frame is not None:
            self.rx_packet_var.set(rx_frame.hex().upper())

    def _update_status(self):
        """Update the displayed connection state and notify the parent callback."""
        is_open = submode.Is_SerialPort_Open()
        self.status_lbl.config(text="열림" if is_open else "닫힘")
        self.on_status_change(is_open)

    def _poll_status(self):
        """Periodically refresh the connection status label."""
        self._update_status()
        self.after(500, self._poll_status)
