"""Win32 helpers to bring an existing Brave window to the foreground.

Only window handles are touched (enumerate, restore, focus). Nothing reads
window contents, injects input or attaches to the browser.
"""

from __future__ import annotations

import sys


def focus_process_windows(pids: set[int]) -> bool:  # pragma: no cover - Windows only
    if not sys.platform.startswith("win") or not pids:
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    found: list[int] = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        if user32.GetWindow(hwnd, 4):  # GW_OWNER: skip owned popups
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            found.append(hwnd)
            return False  # stop at the first (top-most in z-order) window
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    if not found:
        return False
    hwnd = found[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    return bool(user32.SetForegroundWindow(hwnd))


def read_app_path_registry() -> list[str]:  # pragma: no cover - Windows only
    """Return brave.exe paths registered under 'App Paths' (HKCU and HKLM)."""
    if not sys.platform.startswith("win"):
        return []
    import winreg

    results: list[str] = []
    subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\brave.exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (0, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                    if value and value not in results:
                        results.append(value.strip('"'))
            except OSError:
                continue
    return results
