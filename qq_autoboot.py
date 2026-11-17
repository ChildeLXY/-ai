"""无人值守启动机器人 QQ（挂在系统外壳下运行，不依赖大肥鱼AI 窗口）。

用法：由系统外壳（Win+R / 资源管理器）拉起 pythonw 执行本文件即可。
它会：修 NapCat 上报配置 → 关掉现有 QQ → 注入 → 等登录 → 写日志。
"""
import json
import os
import sys
import time

sys.path.insert(0, r"D:/大肥鱼AI")

LOG = r"D:/大肥鱼缓存/_auto_qq.log"
SETTINGS = r"D:/大肥鱼AI/ds图片/settings.json"


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def setting_get(key):
    try:
        with open(SETTINGS, encoding="utf-8-sig") as f:
            return json.load(f).get(key, "")
    except Exception:
        return ""


def main():
    log("=== 自动启动开始 ===")
    import qq_launcher as L

    cfg = L.cfg_from(setting_get)
    log("cfg: qq_bot=%s napcat=%s api=%s bridge=%s" % (
        cfg.get("qq_bot"), cfg.get("napcat_dir"), cfg.get("api_port"), cfg.get("bridge_port")))
    chk = L.check_files(cfg)
    log("文件检查: %s" % ("OK" if chk["ok"] else "缺 " + "、".join(chk["missing"])))
    if not chk["ok"]:
        return
    rep = L.ensure_report(cfg)
    log("上报配置: %s" % json.dumps(rep, ensure_ascii=False))
    r = L.launch(cfg, kill_all=True)
    log("launch: %s" % r)
    for _ in range(60):
        time.sleep(2)
        st = L.state(bot_uin=cfg["qq_bot"])
        if st.get("logged_in"):
            log("已登录 %s(%s)，pid=%s" % (st.get("nick"), st.get("uin"), st.get("bot_pids")))
            break
        if st.get("error"):
            log("出错: %s" % st.get("error"))
            break
    else:
        log("等待超时，最后状态: step=%s msg=%s" % (st.get("step"), st.get("msg")))
    log("=== 自动启动结束 ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("异常: %r" % e)
