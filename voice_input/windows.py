from __future__ import annotations

import ctypes
import io
import math
import os
import struct
import sys
import threading
import time
import wave
import winreg
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from .hotkeys import parse_hotkey


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000
VK_SPACE = 0x20
VK_F8 = 0x77
VK_CONTROL = 0x11
VK_V = 0x56
VK_Z = 0x5A
VK_RETURN = 0x0D
VK_TAB = 0x09
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
ERROR_ALREADY_EXISTS = 183
ERROR_INSUFFICIENT_BUFFER = 122
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SW_SHOWNOACTIVATE = 4
SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SHOW_SETTINGS_EVENT_NAME = "Local\\RechkaShowSettings"

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class INPUT_UNION(ctypes.Union):
    _fields_ = (
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    )


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = (
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    )


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetWindowLongW.restype = wintypes.LONG
user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
user32.SetWindowLongW.restype = wintypes.LONG
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.ShowWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = ()
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.IsWindow.argtypes = (wintypes.HWND,)
user32.IsWindow.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = (
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
)
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalFree.restype = wintypes.HGLOBAL
kernel32.GetCurrentThreadId.argtypes = ()
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
)
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = (
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.BOOL,
    wintypes.LPCWSTR,
)
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
kernel32.SetEvent.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetLogicalProcessorInformationEx.argtypes = (
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.DWORD),
)
kernel32.GetLogicalProcessorInformationEx.restype = wintypes.BOOL
user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
user32.SetClipboardData.restype = wintypes.HANDLE


def _keyboard_input(
    virtual_key: int = 0,
    scan_code: int = 0,
    flags: int = 0,
) -> INPUT:
    return INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(
            wVk=virtual_key,
            wScan=scan_code,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        ),
    )


def _send_inputs(items: list[INPUT]) -> None:
    if not items:
        return
    array_type = INPUT * len(items)
    array = array_type(*items)
    sent = user32.SendInput(len(items), array, ctypes.sizeof(INPUT))
    if sent != len(items):
        raise OSError(ctypes.get_last_error(), "Не удалось отправить ввод в активное окно")


def set_clipboard_text(text: str) -> None:
    encoded = (text + "\0").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
    if not handle:
        raise MemoryError("Не удалось выделить память для буфера обмена")

    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise OSError(ctypes.get_last_error(), "Не удалось открыть память буфера обмена")
    try:
        ctypes.memmove(pointer, encoded, len(encoded))
    finally:
        kernel32.GlobalUnlock(handle)

    opened = False
    for _ in range(20):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.025)
    if not opened:
        kernel32.GlobalFree(handle)
        raise OSError(ctypes.get_last_error(), "Буфер обмена занят другим приложением")

    try:
        if not user32.EmptyClipboard():
            raise OSError(ctypes.get_last_error(), "Не удалось очистить буфер обмена")
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise OSError(ctypes.get_last_error(), "Не удалось записать текст в буфер обмена")
        handle = None
    finally:
        user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)


def paste_from_clipboard() -> None:
    _send_inputs(
        [
            _keyboard_input(VK_CONTROL),
            _keyboard_input(VK_V),
            _keyboard_input(VK_V, flags=KEYEVENTF_KEYUP),
            _keyboard_input(VK_CONTROL, flags=KEYEVENTF_KEYUP),
        ]
    )


def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


def window_process_name(hwnd: int) -> str:
    if not hwnd or not user32.IsWindow(hwnd):
        return ""
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    if not process_id.value:
        return ""
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        process_id.value,
    )
    if not handle:
        return ""
    try:
        capacity = 32_768
        buffer = ctypes.create_unicode_buffer(capacity)
        size = wintypes.DWORD(capacity)
        if not kernel32.QueryFullProcessImageNameW(
            handle,
            0,
            buffer,
            ctypes.byref(size),
        ):
            return ""
        return Path(buffer.value).name
    finally:
        kernel32.CloseHandle(handle)


def activate_window(hwnd: int) -> bool:
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    user32.ShowWindow(hwnd, SW_RESTORE)
    return bool(user32.SetForegroundWindow(hwnd))


def send_undo(hwnd: int = 0) -> None:
    if hwnd and not activate_window(hwnd):
        raise OSError("Не удалось вернуть фокус в окно последней вставки")
    time.sleep(0.04)
    _send_inputs(
        [
            _keyboard_input(VK_CONTROL),
            _keyboard_input(VK_Z),
            _keyboard_input(VK_Z, flags=KEYEVENTF_KEYUP),
            _keyboard_input(VK_CONTROL, flags=KEYEVENTF_KEYUP),
        ]
    )


def send_enter(hwnd: int = 0) -> None:
    if hwnd and not activate_window(hwnd):
        raise OSError("Не удалось вернуть фокус в окно диктовки")
    time.sleep(0.04)
    _send_inputs(
        [
            _keyboard_input(VK_RETURN),
            _keyboard_input(VK_RETURN, flags=KEYEVENTF_KEYUP),
        ]
    )


def type_unicode_text(text: str) -> None:
    pending: list[INPUT] = []
    utf16_units = struct.unpack(f"<{len(text.encode('utf-16-le')) // 2}H", text.encode("utf-16-le"))
    for unit in utf16_units:
        if unit == ord("\n"):
            pending.extend(
                [
                    _keyboard_input(VK_RETURN),
                    _keyboard_input(VK_RETURN, flags=KEYEVENTF_KEYUP),
                ]
            )
        elif unit == ord("\t"):
            pending.extend(
                [
                    _keyboard_input(VK_TAB),
                    _keyboard_input(VK_TAB, flags=KEYEVENTF_KEYUP),
                ]
            )
        else:
            pending.extend(
                [
                    _keyboard_input(scan_code=unit, flags=KEYEVENTF_UNICODE),
                    _keyboard_input(
                        scan_code=unit,
                        flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                    ),
                ]
            )
        if len(pending) >= 400:
            _send_inputs(pending)
            pending = []
    _send_inputs(pending)


def insert_text(text: str, mode: str) -> None:
    if mode == "clipboard":
        set_clipboard_text(text)
        return
    if mode == "type":
        type_unicode_text(text)
        return

    set_clipboard_text(text)
    time.sleep(0.06)
    paste_from_clipboard()


class GlobalHotkey:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._error: OSError | None = None

    def start(
        self,
        name: str,
        callback: Callable[[], None],
        *,
        allow_unmodified: bool = False,
    ) -> None:
        self.stop()
        specification = parse_hotkey(
            name,
            allow_unmodified=allow_unmodified,
        )
        modifiers = specification.modifiers
        virtual_key = specification.virtual_key
        self._ready.clear()
        self._error = None

        def worker() -> None:
            self._thread_id = kernel32.GetCurrentThreadId()
            if not user32.RegisterHotKey(
                None,
                1,
                modifiers | MOD_NOREPEAT,
                virtual_key,
            ):
                self._error = OSError(
                    ctypes.get_last_error(),
                    "Горячая клавиша уже занята другой программой",
                )
                self._ready.set()
                return

            self._ready.set()
            message = wintypes.MSG()
            try:
                while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                    if message.message == WM_HOTKEY:
                        callback()
            finally:
                user32.UnregisterHotKey(None, 1)

        self._thread = threading.Thread(target=worker, name="global-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)
        if self._error:
            raise self._error
        if not self._ready.is_set():
            raise TimeoutError("Не удалось зарегистрировать горячую клавишу")

    def stop(self) -> None:
        thread = self._thread
        thread_id = self._thread_id
        self._thread = None
        self._thread_id = None
        if thread and thread.is_alive() and thread_id:
            user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            thread.join(timeout=2)


def set_autostart(enabled: bool, command: str) -> None:
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        key_path,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        try:
            winreg.DeleteValue(key, LEGACY_APP_RUN_KEY)
        except FileNotFoundError:
            pass
        if enabled:
            winreg.SetValueEx(key, APP_RUN_KEY, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, APP_RUN_KEY)
            except FileNotFoundError:
                pass


APP_RUN_KEY = "Rechka"
LEGACY_APP_RUN_KEY = "VoiceInput"


def autostart_command(main_script: Path) -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" --minimized'

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    executable = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{executable.resolve()}" "{main_script.resolve()}" --minimized'


def make_window_non_activating(hwnd: int) -> None:
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(
        hwnd,
        GWL_EXSTYLE,
        style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
    )
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)


def create_single_instance_mutex() -> tuple[int, bool]:
    handle = kernel32.CreateMutexW(None, False, "Local\\RechkaDesktopApp")
    already_exists = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    return int(handle or 0), already_exists


def create_show_settings_event() -> int:
    handle = kernel32.CreateEventW(
        None,
        False,
        False,
        SHOW_SETTINGS_EVENT_NAME,
    )
    if not handle:
        raise OSError(
            ctypes.get_last_error(),
            "Не удалось создать канал открытия настроек",
        )
    return int(handle)


def signal_show_settings_event() -> bool:
    for _ in range(10):
        handle = kernel32.OpenEventW(
            EVENT_MODIFY_STATE | SYNCHRONIZE,
            False,
            SHOW_SETTINGS_EVENT_NAME,
        )
        if handle:
            try:
                return bool(kernel32.SetEvent(handle))
            finally:
                kernel32.CloseHandle(handle)
        time.sleep(0.05)
    return False


def consume_show_settings_event(handle: int) -> bool:
    if not handle:
        return False
    return kernel32.WaitForSingleObject(handle, 0) == WAIT_OBJECT_0


def close_handle(handle: int) -> None:
    if handle:
        kernel32.CloseHandle(handle)


def message_box(text: str, title: str = "Речка") -> None:
    user32.MessageBoxW(None, text, title, 0x00000040)


def physical_core_count() -> int:
    required = wintypes.DWORD(0)
    kernel32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(required))
    if required.value and ctypes.get_last_error() == ERROR_INSUFFICIENT_BUFFER:
        buffer = ctypes.create_string_buffer(required.value)
        if kernel32.GetLogicalProcessorInformationEx(
            0,
            buffer,
            ctypes.byref(required),
        ):
            count = 0
            offset = 0
            raw = buffer.raw
            while offset + 8 <= required.value:
                relationship, size = struct.unpack_from("<II", raw, offset)
                if size < 8 or offset + size > required.value:
                    break
                if relationship == 0:
                    count += 1
                offset += size
            if count:
                return count
    return max(1, os.cpu_count() or 1)


def _feedback_wave(kind: str) -> bytes:
    patterns = {
        "start": ((392.0, 0.045), (523.25, 0.055)),
        "stop": ((523.25, 0.045), (392.0, 0.055)),
        "error": ((261.63, 0.060), (0.0, 0.025), (220.0, 0.075)),
    }
    pattern = patterns.get(kind, patterns["stop"])
    sample_rate = 22_050
    amplitude = 0.035
    frames: list[int] = []
    phase = 0.0

    for frequency, duration in pattern:
        sample_count = max(1, int(sample_rate * duration))
        for index in range(sample_count):
            if frequency <= 0:
                value = 0.0
            else:
                envelope = math.sin(math.pi * index / max(1, sample_count - 1)) ** 2
                phase += 2 * math.pi * frequency / sample_rate
                value = math.sin(phase) * envelope * amplitude
            frames.append(int(max(-1.0, min(1.0, value)) * 32767))

    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack(f"<{len(frames)}h", *frames))
    return output.getvalue()


def play_feedback(kind: str) -> None:
    import winsound

    if kind == "done":
        return
    try:
        winsound.PlaySound(
            _feedback_wave(kind),
            winsound.SND_MEMORY | winsound.SND_NODEFAULT,
        )
    except RuntimeError:
        pass


def is_windows() -> bool:
    return os.name == "nt"
