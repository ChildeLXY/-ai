# -*- coding: utf-8 -*-
"""把当前前端的 3 处插件中心修复精确回滚，生成"旧行为"副本，用于 A/B 证明。"""
import io

SRC = r"D:\大肥鱼AI\大肥鱼AI.html"
DST = r"D:\大肥鱼缓存\_hub_oldsim.html"

s = io.open(SRC, encoding="utf-8").read()

# 修复1：去掉 _hubTab='detail'
KEY1 = "  _hubTab='detail';   // 关键：不置位的话 hubRefresh() 会按旧的 'installed' 把页面弹回列表\n"
if KEY1 in s:
    s = s.replace(KEY1, "")
    print("回滚1（_hubTab='detail'）: 已去掉")
else:
    print("回滚1: 没找到，跳过")

# 修复3：hubRefresh 换回旧版
i = s.find("function hubRefresh(){")
j = s.find("function hubStat(", i)
if i > 0 and j > i:
    old_fn = (
        "function hubRefresh(){\n"
        "  if(_hubTab==='installed'){hubLoad('installed');return Promise.resolve()}\n"
        "  if(_hubTab==='market'){_mk.page=0;return mkLoad()}\n"
        "  const v=_hubViews[_hubPid];\n"
        "  if(!v||!v.refresh)return Promise.resolve();\n"
        "  return v.refresh().catch(function(e){pgToast('刷新失败：'+((e&&e.message)||e))});\n"
        "}\n"
    )
    s = s[:i] + old_fn + s[j:]
    print("回滚3（hubRefresh 详情优先）: 已换回旧实现")
else:
    print("回滚3: 没找到区间 i=%s j=%s" % (i, j))

io.open(DST, "w", encoding="utf-8", newline="").write(s)
print("生成:", DST, len(s), "字符")
