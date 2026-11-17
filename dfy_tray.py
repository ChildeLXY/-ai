"""大肥鱼AI 的系统托盘。

需求：点关闭按钮不退出，而是最小化到托盘；真要退必须从托盘菜单选「退出」。

做法：
    pywebview 的 window.events.closing 允许 handler 返回 False 来取消关闭，
    所以我们在这里拦一下 → 隐藏窗口 → 留在托盘。
    托盘本身用 pystray（Windows 上跑在独立线程，不影响 UI 线程）。
"""
import os
import threading
import time

_STATE = {"quitting": False, "icon": None, "window": None, "hinted": False}
_LOCK = threading.Lock()


def _load_image(icon_path):
    from PIL import Image
    try:
        if icon_path and os.path.isfile(icon_path):
            return Image.open(icon_path)
    except Exception:
        pass
    # 没图标也要能起托盘：画一个蓝色圆（大肥鱼的色）
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    try:
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        d.ellipse((6, 6, 58, 58), fill=(74, 144, 226, 255))
    except Exception:
        pass
    return img


def _notify(title, msg):
    """托盘气泡提示（首次最小化时告诉用户它去哪了）。"""
    icon = _STATE.get("icon")
    try:
        if icon:
            icon.notify(msg, title)
    except Exception:
        pass


def install(window, icon_path="", on_quit=None):
    """挂上托盘 + 关闭拦截。返回托盘线程（已 start）。"""
    _STATE["window"] = window
    try:
        import pystray
    except Exception as e:
        print("[托盘] pystray 不可用：%r" % e)
        return None

    def _do_show(icon=None, item=None):
        try:
            window.show()
            window.restore()
        except Exception:
            pass

    def _do_hide(icon=None, item=None):
        try:
            window.hide()
        except Exception:
            pass

    def _do_quit(icon=None, item=None):
        _STATE["quitting"] = True
        try:
            if on_quit:
                on_quit()
        except Exception:
            pass
        try:
            if icon:
                icon.stop()
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            pass
        # 兜底：窗口没关掉就强退，避免留个僵尸进程
        time.sleep(0.8)
        try:
            os._exit(0)
        except Exception:
            pass

    menu = pystray.Menu(
        pystray.MenuItem("显示大肥鱼", _do_show, default=True),
        pystray.MenuItem("隐藏到托盘", _do_hide),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", _do_quit),
    )
    icon = pystray.Icon("dfy_ai", _load_image(icon_path), "大肥鱼AI", menu)
    _STATE["icon"] = icon

    def _run():
        try:
            icon.run()
        except Exception as e:
            print("[托盘] 运行异常：%r" % e)

    t = threading.Thread(target=_run, daemon=True, name="dfy-tray")
    t.start()

    def _on_closing():
        """返回 False 取消关闭。真退出时 _STATE['quitting'] 已置 True。"""
        if _STATE["quitting"]:
            return True
        try:
            window.hide()
        except Exception:
            pass
        if not _STATE["hinted"]:
            _STATE["hinted"] = True
            _notify("大肥鱼AI 还在后台", "点托盘图标可以叫回来；要退出请右键托盘选「退出」。")
        return False

    try:
        window.events.closing += _on_closing
    except Exception as e:
        print("[托盘] 关闭拦截挂载失败：%r" % e)
    return t


def quit_now(window=None):
    """给别处调用：真的要退出。"""
    _STATE["quitting"] = True
    icon = _STATE.get("icon")
    try:
        if icon:
            icon.stop()
    except Exception:
        pass
    w = window or _STATE.get("window")
    try:
        if w:
            w.destroy()
    except Exception:
        pass
