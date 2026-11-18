# -*- coding: utf-8 -*-
"""把引擎分卷发布到 Gitee Release，并自动写好下载源配置。

前置：需要一个 Gitee 私人令牌（设置 → 私人令牌，勾选 projects / releases 权限）。
      令牌只从环境变量读，不写进任何文件。

用法：
    set GITEE_TOKEN=xxxxxxxx
    python publish_engine.py --dist "D:\\大肥鱼缓存\\engine_dist" ^
        --owner liu-xingyuec --repo dafeiyu-ai-local --tag engine-v0.2.2-rc.2

它会：
  1. 创建（或复用）一个 Release
  2. 逐个上传分卷 + manifest.json
  3. 把可用的 manifest 地址写进 packaging/engine_manifest_url.txt

注意：Gitee Release 附件单文件上限通常 100 MB，所以分卷要切在 95 MB 以下。
如果平台限制了总附件数或体积，把 --part-mb 调小重跑 pack_engine.py。
"""
import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://gitee.com/api/v5"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api_json(path, params=None, method="GET", data=None, token=""):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = None
    headers = {"User-Agent": "dfy-publish"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json;charset=UTF-8"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with OPENER.open(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def upload_asset(owner, repo, release_id, token, file_path, display_name=None):
    """multipart/form-data 上传单个附件。"""
    name = display_name or os.path.basename(file_path)
    boundary = "----dfy" + str(int(time.time() * 1000))
    ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    parts = []
    def add_field(k, v):
        parts.append(("--%s\r\n" % boundary).encode())
        parts.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % k).encode())
        parts.append(str(v).encode("utf-8"))
        parts.append(b"\r\n")

    add_field("access_token", token)
    parts.append(("--%s\r\n" % boundary).encode())
    parts.append(('Content-Disposition: form-data; name="file"; filename="%s"\r\n' % name).encode())
    parts.append(("Content-Type: %s\r\n\r\n" % ctype).encode())
    with open(file_path, "rb") as f:
        parts.append(f.read())
    parts.append(b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode())
    payload = b"".join(parts)

    url = "%s/repos/%s/%s/releases/%s/attach_files" % (API, owner, repo, release_id)
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "multipart/form-data; boundary=%s" % boundary,
        "Content-Length": str(len(payload)),
        "User-Agent": "dfy-publish",
    }, method="POST")
    with OPENER.open(req, timeout=600) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", required=True, help="pack_engine.py 的输出目录")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--body", default="大肥鱼AI 内置插件引擎（分卷压缩包）")
    ap.add_argument("--config-out", default=r"D:\大肥鱼AI\packaging\engine_manifest_url.txt")
    a = ap.parse_args()

    token = (os.environ.get("GITEE_TOKEN") or "").strip()
    if not token:
        raise SystemExit("未设置 GITEE_TOKEN 环境变量（Gitee 私人令牌）")

    mp = os.path.join(a.dist, "manifest.json")
    if not os.path.isfile(mp):
        raise SystemExit("找不到 %s，先跑 pack_engine.py" % mp)
    man = json.load(open(mp, encoding="utf-8"))
    files = [os.path.join(a.dist, p["file"]) for p in man["parts"]]
    for f in files:
        if not os.path.isfile(f):
            raise SystemExit("缺分卷文件: %s" % f)

    print("准备发布 %d 个文件，共 %.1f MB"
          % (len(files), man["total_bytes"] / 1048576.0))

    # 1) 创建或复用 release
    try:
        rel = api_json("/repos/%s/%s/releases/tags/%s" % (a.owner, a.repo, a.tag),
                       params={"access_token": token})
        print("复用已有 release id=%s" % rel.get("id"))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        print("创建 release tag=%s" % a.tag)
        rel = api_json("/repos/%s/%s/releases" % (a.owner, a.repo),
                       method="POST", token=token, data={
                           "access_token": token, "tag_name": a.tag,
                           "name": a.name or ("插件引擎 " + man.get("version", "")),
                           "body": a.body, "target_commitish": "master"})
        print("新 release id=%s" % rel.get("id"))

    rid = rel.get("id")
    if not rid:
        raise SystemExit("拿不到 release id：%s" % rel)

    # 2) 逐个上传
    uploaded = {}
    for f in files:
        base = os.path.basename(f)
        print("上传 %s （%.1f MB）…" % (base, os.path.getsize(f) / 1048576.0))
        try:
            res = upload_asset(a.owner, a.repo, rid, token, f, base)
            url = res.get("browser_download_url") or res.get("down_url") or ""
            uploaded[base] = url
            print("   -> %s" % url)
        except urllib.error.HTTPError as e:
            print("   失败 HTTP %s: %s" % (e.code, e.read()[:200]))
            raise

    # manifest 也传上去，但下载源用的是 release 里的 manifest
    print("上传 manifest.json")
    res = upload_asset(a.owner, a.repo, rid, token, mp, "manifest.json")
    manifest_url = res.get("browser_download_url") or res.get("down_url") or ""

    # 3) 把清单里的 URL 改成真实地址，重新上传一次（覆盖同名）
    print("\n回填真实下载地址到 manifest…")
    for p in man["parts"]:
        p["url"] = uploaded.get(p["file"], p["url"])
    tmp_man = os.path.join(a.dist, "manifest_final.json")
    with open(tmp_man, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    print("注意：分卷 URL 已写进 manifest_final.json，请把它作为下载清单上传/覆盖。")
    print("  临时文件:", tmp_man)

    if manifest_url:
        os.makedirs(os.path.dirname(a.config_out), exist_ok=True)
        with open(a.config_out, "w", encoding="utf-8") as f:
            f.write(manifest_url + "\n")
        print("\n下载源已写入 %s" % a.config_out)
        print(manifest_url)
    else:
        print("\n没拿到 manifest 下载地址，请手动把 release 里的 manifest 地址填进 %s"
              % a.config_out)


if __name__ == "__main__":
    sys.exit(main())
