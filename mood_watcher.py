# -*- coding: utf-8 -*-
"""
大肥鱼 · 情绪守望者（独立后台进程）
每 20 秒采样一次：更新情绪(mood.json)、检测事件；
有事件就把一句反应写进桌宠气泡(pet_say.txt)。不弹窗、不碰桌宠本体、不调大模型，开销极低。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mood_engine as eng

INTERVAL = 20        # 秒；纯本地 ctypes，开销小


def main():
    time.sleep(60)    # 开机先等系统稳定
    while True:
        try:
            for event in eng.tick():
                line = eng.say_for(event)
                if line:
                    eng.deliver(line)
        except Exception:
            pass      # 任一采样失败不影响下一轮
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
