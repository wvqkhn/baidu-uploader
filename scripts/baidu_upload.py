#!/usr/bin/env python3
"""百度网盘 CLI 上传工具"""
import os, sys, json, subprocess

TOKEN_FILE = os.path.expanduser("~/.bypy/bypy.json")

def get_token():
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f).get("access_token", "")
    except:
        return ""

def upload_file(local_path, remote_dir="/apps/bypy"):
    """上传文件到百度网盘"""
    token = get_token()
    if not token:
        print("❌ 未授权，请先运行: bypy info")
        return False

    filename = os.path.basename(local_path)
    remote_path = f"{remote_dir.rstrip('/')}/{filename}"
    api_url = f"https://pcs.baidu.com/rest/2.0/pcs/file?method=upload&access_token={token}&path={remote_path}&ondup=newcopy"

    print(f"📤 上传: {local_path} -> {remote_path}")
    result = subprocess.run(
        ["curl", "-4", "-s", "--connect-timeout", "10", "-m", "600",
         api_url, "-F", f"file=@{local_path}"],
        capture_output=True, text=True, timeout=600
    )
    data = json.loads(result.stdout) if result.stdout else {}
    if "path" in data:
        print(f"✅ 上传成功!")
        return True
    else:
        print(f"❌ 失败: {json.dumps(data, ensure_ascii=False)[:200]}")
        return False

def list_remote(remote_dir="/apps/bypy"):
    """列出百度网盘目录"""
    token = get_token()
    if not token:
        print("❌ 未授权")
        return
    import urllib.request, urllib.parse
    url = f"https://pan.baidu.com/api/list?dir={urllib.parse.quote(remote_dir)}&access_token={token}&order=time&desc=1&limit=30"
    resp = urllib.request.urlopen(urllib.request.Request(url), timeout=15)
    data = json.loads(resp.read())
    if data.get("errno") == 0:
        items = data.get("list", [])
        print(f"📁 {remote_dir} ({len(items)} 项):")
        for f in items:
            icon = "📁" if f["isdir"] else "📄"
            sz = f.get("size", 0)
            sz_str = f"{sz/1024:.0f}KB" if sz < 1048576 else f"{sz/1048576:.1f}MB"
            print(f"  {icon} {f['server_filename']}")
    else:
        print(f"❌ 列表失败: {data.get('errno')}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法:")
        print("  上传: python3 baidu_upload.py <文件路径> [远程目录]")
        print("  列表: python3 baidu_upload.py --list [远程目录]")
        sys.exit(1)

    if sys.argv[1] == "--list":
        list_remote(sys.argv[2] if len(sys.argv) > 2 else "/apps/bypy")
    else:
        local_path = sys.argv[1]
        if not os.path.exists(local_path):
            print(f"❌ 文件不存在: {local_path}")
            sys.exit(1)
        remote_dir = sys.argv[2] if len(sys.argv) > 2 else "/apps/bypy"
        sys.exit(0 if upload_file(local_path, remote_dir) else 1)
