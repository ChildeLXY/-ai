# 上线清单：dabaifan.cn 换成自己的站

> 现状：`https://dabaifan.cn` 跑的是阿里云「智能建站」托管的版本（源码里带 `wanwang.xin` / siteId `WS20261105110022000001`）。
> 本地做好的门户页和 AI 站**一行代码都还没上云**，所以域名当然还是老样子。

---

## 一、目标结构

| 域名 | 内容 | 本地目录 | OSS Bucket |
|---|---|---|---|
| `dabaifan.cn` + `www` | 白底门户页 | `site-home/` | `dabaifan-main` |
| `ai.dabaifan.cn` | 深色 AI 站（现在的线上版本就是这套内容） | `dist-site/` | `dabaifan-ai` |
| `game.dabaifan.cn` | 占位页（待做） | `site-game/` | `dabaifan-game` |

上传工具：`tools/publish.py`（基于 oss2，已装到 `.tools/`）
凭据文件：`deploy/oss_config.json`（已在 `.gitignore`，不会进仓库）

---

## 二、卡在哪：需要一份 OSS 权限的 AK

上传、建 Bucket、绑域名都是云端操作，必须有凭据。建议**不要给主账号 AK**，用 RAM 子账号，五分钟：

1. 打开 [RAM 控制台 → 用户](https://ram.console.aliyun.com/users)
2. 创建用户 → 访问方式勾选 **OpenAPI 调用访问** → 创建
3. 把这个用户加入授权：
   - `AliyunOSSFullAccess`（传文件、建 Bucket）
   - `AliyunCDNFullAccess`（加加速域名、刷缓存，可选）
   - `AliyunDNSFullAccess`（改解析，可选）
4. 复制出来的 **AccessKey ID** 和 **AccessKey Secret**（Secret 只显示一次）

拿到后填进 `deploy/oss_config.json`：

```json
{
  "access_key_id": "LTAI...",
  "access_key_secret": "...",
  "endpoint": "oss-cn-hangzhou.aliyuncs.com",
  "region": "cn-hangzhou",
  "buckets": {
    "home": "dabaifan-main",
    "ai": "dabaifan-ai",
    "game": "dabaifan-game"
  }
}
```

> Bucket 名全局唯一。`dabaifan-main` 若已被别人占用，脚本会报 `BucketAlreadyExists`，换成 `dabaifan-hz-main` 之类即可，后面的 CDN 和 DNS 都用新名字，不影响任何东西。

---

## 三、我拿到 AK 后会跑的

```bash
cd D:/大肥鱼AI
PYTHONPATH=D:/大肥鱼AI/.tools python tools/publish.py --site all
```

脚本做的事：
- Bucket 不存在就建（标准存储 / 本地冗余 / 公共读）
- 开启静态网站托管（首页 `index.html`、404 页 `404.html`）
- 按扩展名设 Content-Type 和缓存头：HTML 不缓存，图片缓存 7 天
- 已存在且大小一致的文件跳过，省流量

传完后的验证地址（先不绑域名也能开）：
`http://dabaifan-main.oss-cn-hangzhou.aliyuncs.com/index.html`

> ⚠️ 直接用 OSS 默认域名访问网页会被强制下载（响应头带 `Content-Disposition: attachment`），这是阿里云的限制，不是配置错。**必须绑自定义域名**才能正常浏览。

---

## 四、CDN + 证书（我可以用 API 做，也可以你点）

每个加速域名一套，三个站重复三次：

| 配置项 | 值 |
|---|---|
| 加速域名 | `dabaifan.cn` / `www.dabaifan.cn` / `ai.dabaifan.cn` |
| 业务类型 | 图片小文件 |
| 源站类型 | OSS 域名 |
| 源站地址 | 对应 Bucket 的 `*.oss-cn-hangzhou.aliyuncs.com` |
| 端口 | 443（HTTPS） |
| 证书 | 免费证书（CDN 控制台直接申请，自动续期） |
| 缓存 | `index.html` 不缓存；`assets/*` 缓存 7 天 |

证书是**单域名**的，所以 `dabaifan.cn` 和 `www.dabaifan.cn` 要各申请一张，`ai` 再一张，共三张。全部免费。

---

## 五、DNS 改动（顺序不能错）

现在阿里云云解析里：

| 记录 | 当前值 | 处理 |
|---|---|---|
| `@` | CNAME → `dabaifan.cn.w.kunlunle.com` | **先删**，再加 CNAME → CDN 分配的地址 |
| `www` | CNAME → `www.dabaifan.cn.w.kunlunle.com` | **先删**，再加 CNAME → CDN 分配的地址 |
| `ai` | 无记录 | 直接新建 CNAME → CDN 分配的地址 |

**为什么必须先删**：CNAME 优先级高于 A 记录，同主机记录下 CNAME 独占。旧记录不删，新记录加不上去，或者加了也不生效——这是最容易卡住的地方。

改完等 TTL（默认 10 分钟），期间可能短暂打不开，正常。

---

## 六、验收（6 条）

```bash
# 1. 解析到了 CDN 而不是 kunlunle
curl -s "https://dns.alidns.com/resolve?name=dabaifan.cn&type=CNAME"

# 2. 返回的是门户页，不是 1996 字节的智能建站壳
curl -s https://dabaifan.cn/ | wc -c

# 3. 页面标题对不对
curl -s https://dabaifan.cn/ | grep -o '<title>[^<]*</title>'

# 4. AI 站能开
curl -sI https://ai.dabaifan.cn/ | head -3

# 5. 404 走的是自定义页
curl -sI https://dabaifan.cn/not-exist | head -3

# 6. 缓存头生效
curl -sI https://dabaifan.cn/assets/char.webp | grep -i cache-control
```

期望：第 2 条输出约 **1.6 万字节**（现在是 1996），第 3 条是「页面走丢了 · 大肥鱼AI」以外的门户标题。

---

## 七、两个合规项

1. **备案号必须挂在页脚**，并链接到 `https://beian.miit.gov.cn/`。门户页已经做了（`#icpSlot` → 湘ICP备2026054051号-1），AI 站也要检查一遍。智能建站那版我搜过源码，没找到备案号，这是管局抽查的必查项。
2. **子域名不用单独备案**，主域备案覆盖 `www` / `ai` / `game`。

---

## 八、遗留问题

- 智能建站那版站点上线后怎么办？两条路：留着当备份（不再更新），或在阿里云控制台关停。**关停后原 siteId 的页面就访问不到了**，确认新站跑通再关。
- 买的 100GB CDN 流量包只有走「OSS → CDN → 用户」这条链路才抵扣。智能建站自带分发，用不上这个包。
