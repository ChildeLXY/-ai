"""大肥鱼AI 动态桌宠"""
import tkinter as tk
import os
import math
from PIL import Image, ImageTk, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
PET_IMG = os.path.join(BASE, "ds图片", "bg-dark.png")

class DesktopPet:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", "magenta")
        self.root.config(bg="magenta")

        # 原图尺寸，不缩放避免毛边
        orig = Image.open(PET_IMG).convert("RGBA")
        self.scale = 0.35  # 缩小到 35%
        self.W = int(orig.width * self.scale)
        self.H = int(orig.height * self.scale)
        self.base = orig.resize((self.W, self.H), Image.LANCZOS)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.px = sw // 2 + 100
        self.py = sh - self.H - 100
        self.root.geometry(f"{self.W}x{self.H}+{self.px}+{self.py}")

        # 眨眼帧：在眼睛位置画细线
        self.closed = self._make_closed()

        self.label = tk.Label(self.root, bg="magenta", cursor="hand2")
        self.label.pack()

        self.label.bind("<ButtonPress-1>", self.on_press)
        self.label.bind("<B1-Motion>", self.on_drag)
        self.label.bind("<ButtonRelease-1>", self.on_release)
        self.label.bind("<Double-Button-1>", lambda e: self.root.destroy())

        self.dragging = False
        self.phase = 0
        self.blink_phase = 0
        self.next_blink = 100
        self._img = None
        self.tick()

    def _make_closed(self):
        c = self.base.copy()
        d = ImageDraw.Draw(c)
        # 眼睛位置（按比例）
        ex1, ey1 = self.W * 0.36, self.H * 0.46
        ex2, ey2 = self.W * 0.62, self.H * 0.46
        ew = self.W * 0.10
        # 画闭眼弧线
        d.arc([ex1 - ew/2, ey1 - ew/3, ex1 + ew/2, ey1 + ew/3], 0, 180, fill=(40, 55, 100, 255), width=2)
        d.arc([ex2 - ew/2, ey2 - ew/3, ex2 + ew/2, ey2 + ew/3], 0, 180, fill=(40, 55, 100, 255), width=2)
        return c

    def _blend(self, pil_img):
        """把 RGBA 合成到品红底上，但只在完全透明处用品红"""
        bg = Image.new("RGBA", pil_img.size, (255, 0, 255, 255))
        bg.paste(pil_img, (0, 0), pil_img)
        return ImageTk.PhotoImage(bg.convert("RGB"))

    def on_press(self, e):
        self.dx = e.x
        self.dy = e.y
        self.dragging = True

    def on_drag(self, e):
        self.px = self.root.winfo_x() + e.x - self.dx
        self.py = self.root.winfo_y() + e.y - self.dy

    def on_release(self, e):
        self.dragging = False

    def tick(self):
        self.phase += 1
        self.blink_phase += 1

        # 眨眼：1帧闭眼
        if self.blink_phase >= self.next_blink:
            if self.blink_phase > self.next_blink + 3:
                self.blink_phase = 0
                self.next_blink = 80 + (self.phase * 29) % 100
            src = self.closed
        else:
            src = self.base

        # 呼吸：轻微缩放
        s = 1.0 + 0.02 * math.sin(self.phase * 0.04)
        rw, rh = int(self.W * s), int(self.H * s)
        frame = src.resize((rw, rh), Image.LANCZOS)
        canvas = Image.new("RGBA", (self.W, self.H), (255, 0, 255, 255))
        canvas.paste(frame, ((self.W - rw) // 2, (self.H - rh) // 2), frame)

        self._img = self._blend(canvas)
        self.label.configure(image=self._img)

        # 浮动 + 挣扎
        fy = int(2 * math.sin(self.phase * 0.04))
        wig = int(4 * math.sin(self.phase * 0.8)) if self.dragging else 0
        self.root.geometry(f"+{self.px + wig}+{self.py + fy}")

        self.root.after(50, self.tick)

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    pet = DesktopPet()
    pet.run()
