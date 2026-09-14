#!/usr/bin/env python3
"""Tkinter GUI for flashing PerryNet and configuring WiFi."""

from __future__ import annotations

import errno
import getpass
import glob
import os
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - handled at runtime in the GUI
    list_ports = None

from perrynet_serial import (
    OP_SETTINGS_SAVE,
    OP_WIFI_CONNECT,
    OP_WIFI_DIAG,
    PerryNetClient,
    PerryNetCommandError,
    PerryNetError,
    PerryNetTimeout,
    WIFI_STATUS_NAMES,
    ip4,
)

try:
    from wifi_diag import MODE_NAMES, PHY_NAMES, REASON_NAMES, SLEEP_NAMES, mac, u16, u32
except ImportError:  # pragma: no cover
    MODE_NAMES = {}
    PHY_NAMES = {}
    REASON_NAMES = {}
    SLEEP_NAMES = {}

    def u16(payload: bytes, offset: int) -> int:
        return int.from_bytes(payload[offset:offset + 2], "little")

    def u32(payload: bytes, offset: int) -> int:
        return int.from_bytes(payload[offset:offset + 4], "little")

    def mac(data: bytes) -> str:
        return ":".join(f"{b:02x}" for b in data)


REPO_ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("d1_mini", "esp12f", "esp12s", "esp01_1m", "esp01")
FLASH_SIZES = ("detect", "4MB", "2MB", "1MB", "512KB")
LED_COLORS = {
    "off": "#7a7a7a",
    "ok": "#169c48",
    "busy": "#1e75d8",
    "warn": "#d08a00",
    "error": "#c93a3a",
}


class SerialPortPermissionError(RuntimeError):
    pass


def available_ports() -> list[str]:
    ports: list[str] = []
    if list_ports is not None:
        ports.extend(port.device for port in list_ports.comports())
    ports.extend(glob.glob("/dev/ttyUSB*"))
    ports.extend(glob.glob("/dev/ttyACM*"))
    ports.extend(glob.glob("/dev/serial/by-id/*"))
    if sys.platform.startswith("win"):
        ports.extend(f"COM{index}" for index in range(1, 33))

    seen: set[str] = set()
    unique: list[str] = []
    for port in ports:
        if port not in seen:
            unique.append(port)
            seen.add(port)
    return sorted(unique)


def default_platformio() -> str:
    local = REPO_ROOT / ".venv" / "bin" / "platformio"
    if local.exists():
        return str(local)
    found = shutil.which("platformio")
    return found or "platformio"


def esptool_command() -> list[str]:
    for name in ("esptool.py", "esptool"):
        found = shutil.which(name)
        if found:
            return [found]
    local = REPO_ROOT / ".platformio" / "packages" / "tool-esptoolpy" / "esptool.py"
    if local.exists():
        return [sys.executable, str(local)]
    raise RuntimeError("esptool.py/esptool was not found")


class LedIndicator(ttk.Frame):
    def __init__(self, parent: tk.Widget, label: str) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, width=16, height=16, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="w")
        self.dot = self.canvas.create_oval(3, 3, 13, 13, fill=LED_COLORS["off"], outline="#505050")
        ttk.Label(self, text=label).grid(row=0, column=1, sticky="w", padx=(4, 12))

    def set(self, state: str) -> None:
        self.canvas.itemconfigure(self.dot, fill=LED_COLORS.get(state, LED_COLORS["off"]))


class PerryNetGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PerryNet Setup")
        self.geometry("940x700")
        self.minsize(820, 560)

        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.action_buttons: list[ttk.Button] = []
        self.leds: dict[str, LedIndicator] = {}

        ports = available_ports()
        self.port_var = tk.StringVar(value=ports[0] if ports else "/dev/ttyUSB0")
        self.serial_baud_var = tk.StringVar(value="9600")
        self.target_var = tk.StringVar(value="d1_mini")
        self.platformio_var = tk.StringVar(value=default_platformio())
        self.build_dir_var = tk.StringVar(value="/tmp/perrynet-pio-build-gui")
        self.bin_path_var = tk.StringVar()
        self.esptool_baud_var = tk.StringVar(value="460800")
        self.flash_size_var = tk.StringVar(value="detect")
        self.ssid_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.show_password_var = tk.BooleanVar(value=False)
        self.timeout_var = tk.StringVar(value="60")
        self.http_host_var = tk.StringVar(value="example.com")
        self.http_path_var = tk.StringVar(value="/")
        self.status_var = tk.StringVar(value="Idle")

        self._build_ui()
        self.port_var.trace_add("write", lambda *_args: self.update_port_led())
        self.refresh_ports()
        self.after(100, self._poll_messages)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=2)

        top = ttk.Frame(self, padding=(10, 8))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Port").grid(row=0, column=0, sticky="w")
        self.port_combo = ttk.Combobox(top, textvariable=self.port_var, width=48)
        self.port_combo.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        self.port_combo.bind("<<ComboboxSelected>>", lambda _event: self.update_port_led())
        ttk.Button(top, text="Refresh", command=self.refresh_ports).grid(row=0, column=2)
        ttk.Label(top, textvariable=self.status_var).grid(row=0, column=3, sticky="e", padx=(16, 0))

        leds = ttk.Frame(top)
        leds.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        for index, (key, label) in enumerate(
            (
                ("power", "Power"),
                ("device", "Device"),
                ("wifi", "WiFi"),
                ("internet", "Internet"),
                ("activity", "Activity"),
            )
        ):
            indicator = LedIndicator(leds, label)
            indicator.grid(row=0, column=index, sticky="w")
            self.leds[key] = indicator

        notebook = ttk.Notebook(self)
        notebook.grid(row=1, column=0, sticky="nsew", padx=10)
        self._build_flash_tab(notebook)
        self._build_wifi_tab(notebook)

        log_frame = ttk.Frame(self, padding=(10, 8))
        log_frame.grid(row=2, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        ttk.Label(log_frame, text="Log").grid(row=0, column=0, sticky="w")
        self.log_text = tk.Text(log_frame, height=14, wrap="word", state="disabled")
        self.log_text.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

    def _build_flash_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        tab.columnconfigure(1, weight=1)
        notebook.add(tab, text="Flash")

        ttk.Label(tab, text="Target").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            tab,
            textvariable=self.target_var,
            values=TARGETS,
            state="readonly",
            width=16,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))

        ttk.Label(tab, text="PlatformIO").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(tab, textvariable=self.platformio_var).grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(8, 0)
        )

        ttk.Label(tab, text="Build Dir").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(tab, textvariable=self.build_dir_var).grid(
            row=2, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(8, 0)
        )

        self._button(tab, "Build", self.build_firmware).grid(row=3, column=1, sticky="w", pady=12)
        self._button(tab, "Flash PerryNet", self.flash_firmware).grid(
            row=3, column=2, sticky="w", padx=(8, 0), pady=12
        )

        ttk.Separator(tab).grid(row=4, column=0, columnspan=4, sticky="ew", pady=(8, 12))

        ttk.Label(tab, text="BIN File").grid(row=5, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.bin_path_var).grid(
            row=5, column=1, columnspan=2, sticky="ew", padx=(8, 8)
        )
        ttk.Button(tab, text="Browse", command=self.browse_bin).grid(row=5, column=3)

        ttk.Label(tab, text="Flash Size").grid(row=6, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            tab,
            textvariable=self.flash_size_var,
            values=FLASH_SIZES,
            state="readonly",
            width=12,
        ).grid(row=6, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        ttk.Label(tab, text="Baud").grid(row=6, column=2, sticky="e", pady=(8, 0))
        ttk.Entry(tab, textvariable=self.esptool_baud_var, width=12).grid(
            row=6, column=3, sticky="w", padx=(8, 0), pady=(8, 0)
        )

        self._button(tab, "Flash BIN", self.flash_bin).grid(row=7, column=1, sticky="w", pady=12)

    def _build_wifi_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        tab.columnconfigure(1, weight=1)
        notebook.add(tab, text="WiFi")

        ttk.Label(tab, text="Baud").grid(row=0, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.serial_baud_var, width=12).grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )

        ttk.Label(tab, text="SSID").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(tab, textvariable=self.ssid_var).grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(8, 0)
        )

        ttk.Label(tab, text="Password").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.password_entry = ttk.Entry(tab, textvariable=self.password_var, show="*")
        self.password_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 8), pady=(8, 0))
        ttk.Checkbutton(
            tab,
            text="Show",
            variable=self.show_password_var,
            command=self.toggle_password,
        ).grid(row=2, column=3, sticky="w", pady=(8, 0))

        ttk.Label(tab, text="Timeout").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(tab, textvariable=self.timeout_var, width=12).grid(
            row=3, column=1, sticky="w", padx=(8, 0), pady=(8, 0)
        )

        button_row = ttk.Frame(tab)
        button_row.grid(row=4, column=0, columnspan=4, sticky="w", pady=12)
        self._button(button_row, "Probe", self.probe_perrynet).grid(row=0, column=0)
        self._button(button_row, "Read WiFi", self.read_wifi).grid(row=0, column=1, padx=(8, 0))
        self._button(button_row, "Save And Connect", self.save_and_connect).grid(
            row=0, column=2, padx=(8, 0)
        )
        self._button(button_row, "Status", self.read_status).grid(row=0, column=3, padx=(8, 0))
        self._button(button_row, "Diagnostics", self.read_diagnostics).grid(
            row=0, column=4, padx=(8, 0)
        )

        ttk.Separator(tab).grid(row=5, column=0, columnspan=4, sticky="ew", pady=(8, 12))

        ttk.Label(tab, text="HTTP Host").grid(row=6, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.http_host_var).grid(
            row=6, column=1, sticky="ew", padx=(8, 0)
        )
        ttk.Label(tab, text="Path").grid(row=6, column=2, sticky="e", padx=(8, 0))
        ttk.Entry(tab, textvariable=self.http_path_var, width=18).grid(
            row=6, column=3, sticky="ew", padx=(8, 0)
        )
        self._button(tab, "Test Internet", self.test_internet).grid(
            row=7, column=1, sticky="w", pady=12
        )

    def _button(self, parent: tk.Widget, text: str, command) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command)
        self.action_buttons.append(button)
        return button

    def refresh_ports(self) -> None:
        ports = available_ports()
        self.port_combo.configure(values=ports)
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])
        self.update_port_led()

    def port_exists(self) -> bool:
        port = self.port_var.get().strip()
        if not port:
            return False
        if sys.platform.startswith("win"):
            return True
        return Path(port).exists()

    def update_port_led(self) -> None:
        self.set_led("power", "ok" if self.port_exists() else "off")

    def set_led(self, key: str, state: str) -> None:
        indicator = self.leds.get(key)
        if indicator is not None:
            indicator.set(state)

    def post_led(self, key: str, state: str) -> None:
        self.messages.put(("led", (key, state)))

    def toggle_password(self) -> None:
        self.password_entry.configure(show="" if self.show_password_var.get() else "*")

    def browse_bin(self) -> None:
        path = filedialog.askopenfilename(
            title="Select ESP8266 firmware binary",
            filetypes=(("Firmware binaries", "*.bin"), ("All files", "*")),
        )
        if path:
            self.bin_path_var.set(path)

    def set_busy(self, busy: bool, label: str = "Idle") -> None:
        state = "disabled" if busy else "normal"
        for button in self.action_buttons:
            button.configure(state=state)
        self.status_var.set(label)
        self.set_led("activity", "busy" if busy else "off")

    def post_log(self, line: str) -> None:
        self.messages.put(("log", line))

    def run_worker(self, label: str, task) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("PerryNet Setup", "Another operation is already running.")
            return
        self.set_busy(True, label)
        self.worker = threading.Thread(target=self._worker_main, args=(label, task), daemon=True)
        self.worker.start()

    def _worker_main(self, label: str, task) -> None:
        self.messages.put(("log", f"\n== {label} =="))
        try:
            task()
        except Exception as exc:
            self.messages.put(("log", f"ERROR: {exc}"))
            self.messages.put(("led", ("activity", "error")))
            if "wifi" in label.lower():
                self.messages.put(("led", ("wifi", "error")))
            if "internet" in label.lower():
                self.messages.put(("led", ("internet", "error")))
            if self.is_port_permission_error(exc):
                self.messages.put(("permission_error", (label, task, str(exc))))
            else:
                self.messages.put(("error", str(exc)))
        else:
            self.messages.put(("log", "Done."))
        finally:
            self.messages.put(("done", None))

    def _poll_messages(self) -> None:
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "log":
                    self._append_log(str(value))
                elif kind == "led":
                    key, state = value
                    self.set_led(str(key), str(state))
                elif kind == "ssid":
                    self.ssid_var.set(str(value))
                elif kind == "permission_error":
                    label, task, text = value
                    self.handle_permission_error(str(label), task, str(text))
                elif kind == "retry_prompt":
                    label, task = value
                    if messagebox.askyesno("PerryNet Setup", f"Retry {label} now?"):
                        self.run_worker(str(label), task)
                elif kind == "error":
                    messagebox.showerror("PerryNet Setup", str(value))
                elif kind == "done":
                    self.set_busy(False)
        except queue.Empty:
            pass
        self.after(100, self._poll_messages)

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def port(self) -> str:
        port = self.port_var.get().strip()
        if not port:
            raise RuntimeError("serial port is empty")
        return port

    def serial_baud(self) -> int:
        return int(self.serial_baud_var.get().strip())

    def timeout(self) -> float:
        return float(self.timeout_var.get().strip())

    def pio_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("PLATFORMIO_CORE_DIR", str(REPO_ROOT / ".platformio"))
        build_dir = self.build_dir_var.get().strip()
        if build_dir:
            env["PLATFORMIO_BUILD_DIR"] = build_dir
        return env

    def run_subprocess(self, args: list[str], env: dict[str, str] | None = None) -> None:
        self.post_log("$ " + shlex.join(args))
        recent: list[str] = []
        process = subprocess.Popen(
            args,
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            stripped = line.rstrip()
            recent.append(stripped)
            recent = recent[-80:]
            self.post_log(stripped)
        code = process.wait()
        if code != 0:
            output = "\n".join(recent)
            if self.output_is_port_permission_error(output):
                raise SerialPortPermissionError(output)
            raise RuntimeError(f"command failed with exit code {code}")

    def is_port_permission_error(self, exc: Exception) -> bool:
        current: BaseException | None = exc
        while current is not None:
            if isinstance(current, SerialPortPermissionError):
                return True
            if isinstance(current, PermissionError):
                return True
            if getattr(current, "errno", None) == errno.EACCES:
                return True
            current = current.__cause__ or current.__context__
        return self.output_is_port_permission_error(str(exc))

    def output_is_port_permission_error(self, text: str) -> bool:
        lower = text.lower()
        return "permission denied" in lower or "errno 13" in lower

    def handle_permission_error(self, failed_label: str, failed_task, text: str) -> None:
        try:
            target = self.acl_port_target()
        except RuntimeError as exc:
            messagebox.showerror("PerryNet Setup", f"{text}\n\n{exc}")
            return

        user = getpass.getuser()
        setfacl = shutil.which("setfacl") or "/usr/bin/setfacl"
        command = ["pkexec", setfacl, "-m", f"u:{user}:rw", target]
        prompt = (
            "The selected serial port exists but cannot be opened by this user.\n\n"
            "Run a system authorization prompt to grant temporary access?\n\n"
            + shlex.join(command)
        )
        if messagebox.askyesno("Serial Port Access", prompt):
            self.start_authorization(command, failed_label, failed_task)

    def acl_port_target(self) -> str:
        port = self.port()
        if not port.startswith("/dev/"):
            raise RuntimeError("automatic authorization is only supported for /dev serial ports")
        target = Path(port).resolve(strict=False)
        if not str(target).startswith("/dev/"):
            raise RuntimeError(f"refusing to authorize non-/dev path: {target}")
        return str(target)

    def start_authorization(self, command: list[str], failed_label: str, failed_task) -> None:
        if self.worker and self.worker.is_alive():
            self.after(100, lambda: self.start_authorization(command, failed_label, failed_task))
            return

        def task() -> None:
            pkexec = shutil.which("pkexec")
            if pkexec is None:
                port = self.acl_port_target()
                raise RuntimeError(
                    "pkexec was not found. Run this locally instead: "
                    f"sudo setfacl -m u:{getpass.getuser()}:rw {shlex.quote(port)}"
                )
            setfacl = shutil.which("setfacl")
            if setfacl is None:
                raise RuntimeError("setfacl was not found")
            command[0] = pkexec
            command[1] = setfacl
            self.run_subprocess(command)
            self.post_log("serial: temporary access granted")
            self.messages.put(("retry_prompt", (failed_label, failed_task)))

        self.run_worker("Grant serial access", task)

    def build_firmware(self) -> None:
        self.run_worker("Build firmware", self._build_firmware)

    def _build_firmware(self) -> None:
        platformio = self.platformio_var.get().strip() or default_platformio()
        target = self.target_var.get().strip()
        self.run_subprocess([platformio, "run", "-e", target], env=self.pio_env())

    def flash_firmware(self) -> None:
        self.run_worker("Flash PerryNet firmware", self._flash_firmware)

    def _flash_firmware(self) -> None:
        platformio = self.platformio_var.get().strip() or default_platformio()
        target = self.target_var.get().strip()
        args = [platformio, "run", "-e", target, "-t", "upload", "--upload-port", self.port()]
        self.run_subprocess(args, env=self.pio_env())

    def flash_bin(self) -> None:
        self.run_worker("Flash binary", self._flash_bin)

    def _flash_bin(self) -> None:
        path = Path(self.bin_path_var.get().strip()).expanduser()
        if not path.exists():
            raise RuntimeError(f"firmware binary not found: {path}")
        baud = self.esptool_baud_var.get().strip()
        flash_size = self.flash_size_var.get().strip()
        args = (
            esptool_command()
            + [
                "--port",
                self.port(),
                "--baud",
                baud,
                "write_flash",
                "--flash_size",
                flash_size,
                "0x00000",
                str(path),
            ]
        )
        self.run_subprocess(args)

    def client(self) -> PerryNetClient:
        return PerryNetClient(self.port(), self.serial_baud())

    def probe_perrynet(self) -> None:
        self.run_worker("Probe PerryNet", self._probe_perrynet)

    def _probe_perrynet(self) -> None:
        with self.client() as client:
            client.drain()
            major, minor, max_payload, max_channels, max_listeners, features, name = client.hello()
            self.post_led("device", "ok")
            self.post_log(
                f"device: {name} v{major}.{minor} max_payload={max_payload} "
                f"channels={max_channels} listeners={max_listeners} "
                f"features=0x{features:08x}"
            )

    def read_wifi(self) -> None:
        self.run_worker("Read stored WiFi", self._read_wifi)

    def _read_wifi(self) -> None:
        with self.client() as client:
            client.drain()
            major, minor, *_rest, name = client.hello()
            ssid, pass_is_set = client.wifi_get()
            self.post_led("device", "ok")
            self.post_log(f"device: {name} v{major}.{minor}")
            self.post_log(f"wifi: ssid='{ssid}' password_set={int(pass_is_set)}")
            self.messages.put(("ssid", ssid))

    def save_and_connect(self) -> None:
        self.run_worker("Save WiFi and connect", self._save_and_connect)

    def _save_and_connect(self) -> None:
        ssid = self.ssid_var.get()
        password = self.password_var.get()
        if not ssid:
            raise RuntimeError("SSID is empty")
        with self.client() as client:
            client.drain()
            major, minor, *_rest, name = client.hello()
            self.post_led("device", "ok")
            self.post_log(f"device: {name} v{major}.{minor}")
            client.set_wifi(ssid, password)
            self.post_log(f"wifi: stored SSID '{ssid}'")
            client.command(OP_SETTINGS_SAVE)
            self.post_log("settings: saved to EEPROM")
            client.command(OP_WIFI_CONNECT)
            self.post_led("wifi", "busy")
            self.post_log("wifi: connecting...")
            status = client.wait_wifi(self.timeout())
            self.log_status(status)

    def read_status(self) -> None:
        self.run_worker("Read WiFi status", self._read_status)

    def _read_status(self) -> None:
        with self.client() as client:
            client.drain()
            client.hello()
            self.post_led("device", "ok")
            self.log_status(client.wifi_status(timeout=10.0))

    def log_status(self, status) -> None:
        self.post_led("wifi", "ok" if status.connected else "warn")
        self.post_log(
            f"wifi: status={status.status_name} connected={int(status.connected)} "
            f"ip={status.ip} gateway={status.gateway} dns={status.dns} "
            f"rssi={status.rssi}dBm"
        )

    def read_diagnostics(self) -> None:
        self.run_worker("Read WiFi diagnostics", self._read_diagnostics)

    def _read_diagnostics(self) -> None:
        with self.client() as client:
            client.drain()
            major, minor, *_rest, name = client.hello()
            self.post_led("device", "ok")
            self.post_log(f"device: {name} v{major}.{minor}")
            payload = client.command(OP_WIFI_DIAG, timeout=10.0)
            if len(payload) < 77:
                raise PerryNetError(f"short WIFI_DIAG response: {len(payload)} bytes")
            reason = u16(payload, 38)
            self.post_log(
                f"wifi: status={WIFI_STATUS_NAMES.get(payload[0], payload[0])} "
                f"connected={payload[1]} mode={MODE_NAMES.get(payload[2], payload[2])} "
                f"phy={PHY_NAMES.get(payload[3], payload[3])} "
                f"sleep={SLEEP_NAMES.get(payload[4], payload[4])} channel={payload[5]}"
            )
            self.post_log(
                f"signal: rssi={int.from_bytes(payload[6:10], 'little', signed=True)}dBm "
                f"last_channel={payload[76]}"
            )
            self.post_log(
                f"ip: local={ip4(payload[10:14])} gateway={ip4(payload[14:18])} "
                f"netmask={ip4(payload[18:22])} dns={ip4(payload[22:26])}"
            )
            self.post_log(f"mac: sta={mac(payload[26:32])} bssid={mac(payload[32:38])}")
            self.post_log(f"disconnect: reason={reason} {REASON_NAMES.get(reason, 'UNKNOWN')}")
            self.post_log(
                f"events: attempts={u32(payload, 40)} connected={u32(payload, 44)} "
                f"disconnected={u32(payload, 48)} got_ip={u32(payload, 52)} "
                f"dhcp_timeout={u32(payload, 56)}"
            )

    def test_internet(self) -> None:
        self.run_worker("Test internet", self._test_internet)

    def _test_internet(self) -> None:
        host = self.http_host_var.get().strip()
        path = self.http_path_var.get().strip() or "/"
        if not host:
            raise RuntimeError("HTTP host is empty")

        with self.client() as client:
            client.drain()
            major, minor, *_rest, name = client.hello()
            self.post_led("device", "ok")
            self.post_led("internet", "busy")
            self.post_log(f"device: {name} v{major}.{minor}")
            status = client.wifi_status(timeout=10.0)
            if not status.connected:
                self.post_log(f"wifi: status={status.status_name}; connecting...")
                client.command(OP_WIFI_CONNECT)
                status = client.wait_wifi(self.timeout())
            self.log_status(status)
            ip = client.dns_resolve(host)
            self.post_log(f"dns: {host} -> {ip}")

            channel = client.tcp_open(host, 80, pull_rx=True)
            self.post_log(f"tcp: connected channel={channel} {host}:80")
            request = (
                f"GET {path} HTTP/1.0\r\n"
                f"Host: {host}\r\n"
                "User-Agent: PerryNet-gui\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            written = client.tcp_send(channel, request)
            self.post_log(f"tcp: sent {written} bytes")

            received = bytearray()
            deadline = time.monotonic() + self.timeout()
            last_data = time.monotonic()
            while time.monotonic() < deadline:
                try:
                    chunk = client.tcp_recv(channel, 128)
                except PerryNetCommandError as exc:
                    if exc.status == 0x04 and received:
                        break
                    raise
                if chunk:
                    received.extend(chunk)
                    last_data = time.monotonic()
                    self.post_log(f"tcp: received {len(chunk)} bytes ({len(received)} total)")
                    continue
                if received and time.monotonic() - last_data > 2.0:
                    break
                time.sleep(0.05)
            client.tcp_close(channel)

            if not received:
                raise PerryNetTimeout("no TCP data received")
            first_line = received.splitlines()[0].decode("ascii", "replace")
            self.post_log(f"http: {first_line}")
            self.post_log(f"result: PASS ({len(received)} bytes)")
            self.post_led("internet", "ok")


def main() -> int:
    try:
        app = PerryNetGui()
    except tk.TclError as exc:
        print(f"error: cannot start Tkinter GUI: {exc}", file=sys.stderr)
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
