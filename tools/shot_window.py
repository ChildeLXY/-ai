# -*- coding: utf-8 -*-
"""截 DeepBuddy 的窗口图，检查模型下拉框里有没有云端模型。

不用 pyautogui（不一定装了），直接用 Win32 GDI 把窗口位图抠出来。
"""
import ctypes
import sys
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()

HWND = int(sys.argv[1]) if len(sys.argv) > 1 else 0
OUT = sys.argv[2] if len(sys.argv) > 2 else r"D:\大肥鱼AI\design-source\verify\deepbuddy.png"

if not HWND:
    import subprocess
    ps = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-Process -Name DeepBuddy -ErrorAction SilentlyContinue | "
         "Where-Object {$_.MainWindowHandle -ne 0} | Select-Object -First 1).MainWindowHandle"],
        capture_output=True)
    HWND = int(ps.stdout.decode().strip() or 0)
if not HWND:
    print("找不到 DeepBuddy 窗口")
    sys.exit(1)

rect = wintypes.RECT()
user32.GetWindowRect(HWND, ctypes.byref(rect))
w, h = rect.right - rect.left, rect.bottom - rect.top
print("window rect: %dx%d" % (w, h))

hdc = user32.GetWindowDC(HWND)
memdc = gdi32.CreateCompatibleDC(hdc)
bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
gdi32.SelectObject(memdc, bmp)

# 打印窗口（PW_RENDERFULLCONTENT=2），能抓到 WebView2 的内容
ok = user32.PrintWindow(HWND, memdc, 2)
print("PrintWindow:", ok)

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]

bi = BITMAPINFOHEADER()
bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
bi.biWidth = w
bi.biHeight = -h          # 负数 = 自上而下
bi.biPlanes = 1
bi.biBitCount = 32
bi.biCompression = 0
buf = ctypes.create_string_buffer(w * h * 4)
lines = gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
print("GetDIBits lines:", lines)

gdi32.DeleteObject(bmp)
gdi32.DeleteDC(memdc)
user32.ReleaseDC(HWND, hdc)

try:
    from PIL import Image
    im = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
    im.convert("RGB").save(OUT)
    print("saved ->", OUT, im.size)
except Exception as e:
    open(OUT + ".raw", "wb").write(buf)
    print("PIL unavailable, raw saved:", e)
