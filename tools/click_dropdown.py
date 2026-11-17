# -*- coding: utf-8 -*-
"""展开 DeepBuddy 模型下拉框并用「全屏抓屏」验证云端模型。

原生 <select> 的弹出列表是独立顶层窗口，PrintWindow(主窗口) 抓不到它，
所以这里改用 BitBlt 抓整块屏幕，再裁到窗口区域。
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()

OUT = r"D:\大肥鱼AI\design-source\verify\deepbuddy_dropdown.png"


def find_window():
    import subprocess
    ps = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-Process -Name DeepBuddy -ErrorAction SilentlyContinue | "
         "Where-Object {$_.MainWindowHandle -ne 0} | Select-Object -First 1).MainWindowHandle"],
        capture_output=True)
    return int(ps.stdout.decode().strip() or 0)


def screen_shot(left, top, w, h, out):
    """抓屏幕矩形区域（能抓到独立弹出窗口）。"""
    hdc = user32.GetDC(0)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(memdc, bmp)
    gdi32.BitBlt(memdc, 0, 0, w, h, hdc, left, top, 0x00CC0020)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    bi = BIH()
    bi.biSize = ctypes.sizeof(BIH)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(0, hdc)
    from PIL import Image
    im = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
    im.convert("RGB").save(out)
    return im.size


hwnd = find_window()
if not hwnd:
    print("no window")
    sys.exit(1)

user32.ShowWindow(hwnd, 9)  # SW_RESTORE
# 置顶一下，不然可能被别的窗口盖住截不到
user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
user32.SetForegroundWindow(hwnd)
time.sleep(1.0)

rect = wintypes.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(rect))
w, h = rect.right - rect.left, rect.bottom - rect.top
print("window rect:", rect.left, rect.top, w, h)

# 模型下拉框在窗口内约 (100, 550)
cx = rect.left + 100
cy = rect.top + 550
print("click at", cx, cy)

user32.SetCursorPos(cx, cy)
time.sleep(0.4)
user32.mouse_event(0x0002, 0, 0, 0, 0)
time.sleep(0.12)
user32.mouse_event(0x0004, 0, 0, 0, 0)
time.sleep(1.5)

# 抓整个屏幕再裁窗口区域（弹出层可能在窗口外）
SW, SH = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
tmp = OUT + ".full.png"
screen_shot(0, 0, SW, SH, tmp)
from PIL import Image
im = Image.open(tmp)
pad = 260
box = (max(0, rect.left - 40), max(0, rect.top - 40),
       min(SW, rect.right + 40), min(SH, rect.bottom + pad))
im.crop(box).save(OUT)
os.remove(tmp)
print("saved", OUT, Image.open(OUT).size)

# 收起，别把界面留在展开状态
user32.keybd_event(0x1B, 0, 0, 0)
user32.keybd_event(0x1B, 0, 0x0002, 0)
time.sleep(0.4)
# 取消置顶，别留个总在最上面的窗口给用户
user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
print("closed")
