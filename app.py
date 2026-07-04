#!/usr/bin/env python3
"""百度网盘上传助手 - Web UI"""
import os, sys, json, time, subprocess, threading, sqlite3, requests
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, render_template, request, jsonify, session, redirect, url_for

import config

# ===== 配置 =====
APP_PORT = config.APP_PORT
APP_PASSWORD = config.APP_PASSWORD
BAIDU_TOKEN_FILE = config.BAIDU_TOKEN_FILE
BASE_DIRS = config.BASE_DIRS
DB_PATH = config.DB_PATH

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 * 1024  # 16GB

# ===== 数据库 =====
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS uploads
        (id INTEGER PRIMARY KEY AUTOINCREMENT,
         local_path TEXT NOT NULL,
         remote_path TEXT NOT NULL,
         size INTEGER DEFAULT 0,
         status TEXT DEFAULT 'pending',
         progress INTEGER DEFAULT 0,
         source_deleted INTEGER DEFAULT 0,
         dir_path TEXT,
         started_at TIMESTAMP,
         completed_at TIMESTAMP,
         error_msg TEXT)''')
    try:
        c.execute("ALTER TABLE uploads ADD COLUMN progress INTEGER DEFAULT 0")
    except: pass
    try:
        c.execute("ALTER TABLE uploads ADD COLUMN dir_path TEXT")
    except: pass
    c.execute('''CREATE TABLE IF NOT EXISTS dir_progress
        (dir_path TEXT PRIMARY KEY,
         total_files INTEGER DEFAULT 0,
         completed_files INTEGER DEFAULT 0,
         total_size INTEGER DEFAULT 0,
         status TEXT DEFAULT 'pending',
         started_at TIMESTAMP,
         completed_at TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

# ===== 上传并发控制 =====
UPLOAD_SEMAPHORE = threading.Semaphore(5)

# ===== 百度 Token =====
def get_baidu_token():
    try:
        with open(BAIDU_TOKEN_FILE) as f:
            data = json.load(f)
        return data.get("access_token", "")
    except:
        return ""

# ===== 认证装饰器 =====
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            if request.path.startswith('/api/'):
                return jsonify({"error": "未登录"}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ===== 路由 =====
@app.route('/')
def login():
    if session.get('authenticated'):
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def do_login():
    pwd = request.form.get('password', '')
    if pwd == APP_PASSWORD:
        session['authenticated'] = True
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "密码错误"}), 401

@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('login'))

@app.route('/main')
@login_required
def index():
    return render_template('index.html')

# ===== 目录大小缓存 =====
_dir_size_cache = {}
_DIR_SIZE_CACHE_TTL = 60

def get_dir_sizes(parent_path, entries):
    now = time.time()
    dirs = [e for e in entries if os.path.isdir(os.path.join(parent_path, e))]
    if not dirs:
        return {}
    sizes = {}
    uncached = []
    for d in dirs:
        key = os.path.join(parent_path, d)
        if key in _dir_size_cache and now - _dir_size_cache[key][1] < _DIR_SIZE_CACHE_TTL:
            sizes[key] = _dir_size_cache[key][0]
        else:
            uncached.append(d)
    if not uncached:
        return sizes
    for i in range(0, len(uncached), 5):
        batch = uncached[i:i+5]
        globs = [os.path.join(parent_path, d) for d in batch]
        try:
            result = subprocess.run(
                ["du", "-sb", "--apparent-size", "--max-depth=0"] + globs,
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    parts = line.split('\t', 1)
                    if len(parts) == 2:
                        raw_path = parts[1].strip()
                        try:
                            sz = int(parts[0])
                            sizes[raw_path] = sz
                            _dir_size_cache[raw_path] = (sz, now)
                        except: pass
        except subprocess.TimeoutExpired:
            for d in batch:
                key = os.path.join(parent_path, d)
                sizes[key] = -1
                _dir_size_cache[key] = (-1, now)
        except: pass
    return sizes

# ===== API: 文件浏览器 =====
@app.route('/api/list')
@login_required
def list_dir():
    path = request.args.get('path', BASE_DIRS[0])
    show_sizes = request.args.get('sizes', '0') == '1'
    path = os.path.realpath(path)
    allowed = False
    for base in BASE_DIRS:
        if path.startswith(os.path.realpath(base)):
            allowed = True
            break
    if not allowed:
        path = BASE_DIRS[0]

    try:
        items = []
        entries = sorted(os.listdir(path), key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))
        for name in entries:
            full = os.path.join(path, name)
            try:
                st = os.stat(full)
                items.append({
                    "name": name, "path": full,
                    "is_dir": os.path.isdir(full),
                    "size": 0 if os.path.isdir(full) else st.st_size,
                    "mtime": st.st_mtime
                })
            except: pass

        if show_sizes:
            entry_names = [i['name'] for i in items if i['is_dir']]
            if entry_names:
                dir_sizes = get_dir_sizes(path, entry_names)
                for item in items:
                    if item['is_dir'] and item['path'] in dir_sizes:
                        item['size'] = dir_sizes[item['path']]

        parent = os.path.dirname(path) if path != '/' else None
        return jsonify({"path": path, "parent": parent, "items": items, "can_go_up": path != BASE_DIRS[0]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ===== API: 已同步状态 =====
@app.route('/api/synced')
@login_required
def get_synced():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT DISTINCT local_path FROM uploads WHERE status='completed'")
    synced = set(r[0] for r in c.fetchall())
    parent_synced = set()
    for p in synced:
        d = os.path.dirname(p)
        while d and d != '/':
            parent_synced.add(d)
            d = os.path.dirname(d)
    conn.close()
    return jsonify({
        "paths": list(synced | parent_synced),
        "dirs": dict(get_dir_progress_all())
    })

def get_dir_progress_all():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT dir_path, total_files, completed_files, total_size, status FROM dir_progress")
    rows = c.fetchall()
    conn.close()
    result = {}
    for r in rows:
        pct = int(r[1] * 100 / max(r[2], 1)) if r[4] in ('uploading','completed') else 0
        result[r[0]] = {"total": r[1], "completed": r[2], "size": r[3], "status": r[4], "progress": pct}
    return result

# ===== Docker 删除（权限不足时 fallback） =====
DOCKER_RM_IMAGE = config.DOCKER_RM_IMAGE

def docker_rm(path, image=None):
    img = image or DOCKER_RM_IMAGE
    mnt_points = sorted([m for m in os.listdir('/mnt') if os.path.isdir(os.path.join('/mnt', m))], key=len, reverse=True)
    mount_map = {}
    for mp in mnt_points:
        full = os.path.join('/mnt', mp)
        if path.startswith(full + '/') or path == full:
            mount_map[full] = '/data'
            inner_path = '/data' + path[len(full):]
            break
    else:
        mount_map[os.path.dirname(path)] = '/data'
        inner_path = '/data/' + os.path.basename(path)
    volumes = ' '.join([f"-v {host}:{guest}" for host, guest in mount_map.items()])
    cmd = f"docker run --rm {volumes} {img} rm -rf '{inner_path}'"
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return True, "已删除"
        return False, f"Docker 删除失败: {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, "Docker 删除超时"
    except Exception as e:
        return False, f"Docker 删除异常: {str(e)}"

def force_delete(path):
    if not os.path.exists(path):
        return False, "路径不存在"
    try:
        if os.path.isfile(path):
            os.remove(path)
        elif os.path.isdir(path):
            import shutil
            shutil.rmtree(path)
        return True, "已删除"
    except PermissionError:
        return docker_rm(path)
    except Exception as e:
        return False, str(e)

# ===== API: 删除已同步的文件 =====
@app.route('/api/delete-synced', methods=['POST'])
@login_required
def delete_synced():
    data = request.json
    path = data.get('path', '')
    if not path or not os.path.exists(path):
        return jsonify({"error": "路径不存在"}), 400
    success, msg = force_delete(path)
    if success:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE uploads SET source_deleted=1 WHERE local_path=? OR local_path LIKE ?", (path, path + '/%'))
        c.execute("DELETE FROM dir_progress WHERE dir_path=?", (path,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": msg})
    else:
        return jsonify({"error": msg}), 500

# ===== API: 百度网盘状态 =====
@app.route('/api/baidu/status')
@login_required
def baidu_status():
    token = get_baidu_token()
    if not token:
        return jsonify({"connected": False})
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://pan.baidu.com/api/quota?access_token={token}&check_expire=1",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data.get("errno") == 0:
            quota = data.get("quota", 0)
            used = data.get("used", 0)
            return jsonify({
                "connected": True, "quota": quota, "used": used,
                "quota_str": fmt_size(quota), "used_str": fmt_size(used),
                "free_str": fmt_size(quota - used),
                "percent": round(used / quota * 100, 1) if quota > 0 else 0
            })
        return jsonify({"connected": False, "error": "token 过期"})
    except Exception as e:
        return jsonify({"connected": False, "error": str(e)})

# ===== API: 上传历史 =====
@app.route('/api/uploads')
@login_required
def get_uploads():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, local_path, remote_path, size, status, progress, source_deleted, started_at, completed_at, error_msg FROM uploads ORDER BY id DESC LIMIT 50")
    rows = c.fetchall()
    conn.close()
    return jsonify([{
        "id": r[0], "local_path": r[1], "remote_path": r[2], "size": r[3],
        "status": r[4], "progress": r[5] or 0, "source_deleted": r[6],
        "started_at": r[7], "completed_at": r[8], "error_msg": r[9]
    } for r in rows])

# ===== 后台上传 =====
def do_upload(local_path, remote_dir, upload_id, delete_after=False, dir_path=None):
    acquired = False
    if not dir_path:
        acquired = UPLOAD_SEMAPHORE.acquire(blocking=True)
    try:
        token = get_baidu_token()
        if not token:
            update_upload_status(upload_id, "failed", error_msg="未授权")
            return
        filename = os.path.basename(local_path)
        remote_path = f"{remote_dir.rstrip('/')}/{filename}"
        file_size = os.path.getsize(local_path)
        start_time = time.time()
        update_upload_status(upload_id, "uploading", size=file_size, progress=0, started_at=datetime.now().isoformat())

        from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor
        def create_monitor_callback():
            last_update = [0, 0.0]
            def callback(monitor):
                pct = min(99, int(monitor.bytes_read * 100 / max(monitor.len, 1)))
                now = time.time()
                if pct != last_update[0] or now - last_update[1] > 2:
                    update_upload_status(upload_id, "uploading", progress=pct)
                    last_update[0] = pct; last_update[1] = now
            return callback

        with open(local_path, 'rb') as f:
            encoder = MultipartEncoder(fields={'file': (filename, f, 'application/octet-stream')})
            monitor = MultipartEncoderMonitor(encoder, create_monitor_callback())
            api_url = f"https://pcs.baidu.com/rest/2.0/pcs/file?method=upload&access_token={token}&path={remote_path}&ondup=newcopy"
            resp = requests.post(api_url, data=monitor, headers={'Content-Type': monitor.content_type}, timeout=600)
            data = resp.json()

        if "path" in data:
            elapsed = time.time() - start_time
            update_upload_status(upload_id, "completed", progress=100, completed_at=datetime.now().isoformat())
            if dir_path:
                try:
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("UPDATE dir_progress SET completed_files = completed_files + 1 WHERE dir_path=?", (dir_path,))
                    c.execute("SELECT total_files, completed_files FROM dir_progress WHERE dir_path=?", (dir_path,))
                    row = c.fetchone()
                    if row and row[0] == row[1]:
                        c.execute("UPDATE dir_progress SET status='completed', completed_at=? WHERE dir_path=?", (datetime.now().isoformat(), dir_path))
                    conn.commit(); conn.close()
                except Exception as e:
                    app.logger.warning(f"更新目录进度失败: {e}")
            if delete_after:
                try:
                    force_delete(local_path)
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("UPDATE uploads SET source_deleted=1 WHERE id=?", (upload_id,))
                    conn.commit(); conn.close()
                except Exception as e:
                    app.logger.warning(f"删除源文件失败: {local_path} - {e}")
            return {"success": True}
        else:
            err = json.dumps(data, ensure_ascii=False)[:200]
            update_upload_status(upload_id, "failed", progress=0, error_msg=err)
            return {"success": False, "error": err}
    except requests.exceptions.Timeout:
        update_upload_status(upload_id, "failed", error_msg="上传超时")
    except json.JSONDecodeError:
        update_upload_status(upload_id, "failed", error_msg="返回数据异常")
    except Exception as e:
        update_upload_status(upload_id, "failed", error_msg=str(e))
    finally:
        if acquired:
            UPLOAD_SEMAPHORE.release()

def update_upload_status(upload_id, status, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    fields = {"status": status}
    fields.update(kwargs)
    set_clause = ", ".join([f"{k}=?" for k in fields.keys()])
    values = list(fields.values()) + [upload_id]
    c.execute(f"UPDATE uploads SET {set_clause} WHERE id=?", values)
    conn.commit(); conn.close()

# ===== API: 开始上传 =====
@app.route('/api/upload/start', methods=['POST'])
@login_required
def start_upload():
    data = request.json
    files = data.get('files', [])
    remote_dir = data.get('remote_dir', '/apps/bypy')
    delete_after = data.get('delete_after', False)
    if not files:
        return jsonify({"error": "请选择文件"}), 400
    token = get_baidu_token()
    if not token:
        return jsonify({"error": "百度网盘未授权"}), 401

    upload_ids = []
    for f in files:
        if not os.path.exists(f):
            continue
        if os.path.isdir(f):
            dir_path = f
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO dir_progress (dir_path, total_files, completed_files, total_size, status, started_at) VALUES (?, 0, 0, 0, 'scanning', ?)",
                      (dir_path, datetime.now().isoformat()))
            conn.commit(); conn.close()

            def scan_and_upload(dir_path, remote_dir_base, delete_after):
                dir_name = os.path.basename(dir_path)
                dir_remote = f"{remote_dir_base.rstrip('/')}/{dir_name}"
                total_size = 0; file_count = 0
                try:
                    try: os.nice(19)
                    except: pass
                    for root, dirs, filenames in os.walk(dir_path):
                        for fn in filenames:
                            try:
                                fn.encode('utf-8'); root.encode('utf-8')
                            except (UnicodeEncodeError, UnicodeDecodeError):
                                continue
                            fp = os.path.join(root, fn)
                            try: total_size += os.path.getsize(fp)
                            except: pass
                            file_count += 1
                            conn = sqlite3.connect(DB_PATH)
                            c = conn.cursor()
                            rel = os.path.relpath(fp, dir_path)
                            remote_fp = f"{dir_remote}/{rel}"
                            c.execute("INSERT INTO uploads (local_path, remote_path, status, started_at, dir_path) VALUES (?, ?, 'queued', ?, ?)",
                                      (fp, remote_fp, datetime.now().isoformat(), dir_path))
                            uid = c.lastrowid
                            conn.commit(); conn.close()
                            if file_count % 10 == 0:
                                time.sleep(0.01)
                            if file_count % 50 == 0:
                                conn = sqlite3.connect(DB_PATH)
                                c = conn.cursor()
                                c.execute("UPDATE dir_progress SET total_files=?, total_size=? WHERE dir_path=?", (file_count, total_size, dir_path))
                                conn.commit(); conn.close()
                            t = threading.Thread(target=do_upload, args=(fp, os.path.dirname(remote_fp), uid, delete_after, dir_path), daemon=True)
                            t.start()
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("UPDATE dir_progress SET total_files=?, total_size=?, status='uploading' WHERE dir_path=?", (file_count, total_size, dir_path))
                    conn.commit(); conn.close()
                except Exception as e:
                    app.logger.error(f"扫描目录失败: {str(e)[:100]}")
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("UPDATE dir_progress SET status='failed' WHERE dir_path=?", (dir_path,))
                    conn.commit(); conn.close()

            t = threading.Thread(target=scan_and_upload, args=(dir_path, remote_dir, delete_after), daemon=True)
            t.start()
            return jsonify({"upload_ids": [], "message": "正在扫描目录...", "dir_scanning": dir_path})
        else:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("INSERT INTO uploads (local_path, remote_path, status, started_at) VALUES (?, ?, 'queued', ?)",
                      (f, f"{remote_dir.rstrip('/')}/{os.path.basename(f)}", datetime.now().isoformat()))
            uid = c.lastrowid
            conn.commit(); conn.close()
            upload_ids.append(uid)
            t = threading.Thread(target=do_upload, args=(f, remote_dir, uid, delete_after), daemon=True)
            t.start()

    return jsonify({"upload_ids": upload_ids, "message": f"已添加 {len(upload_ids)} 个文件到上传队列"})

# ===== API: 重试上传 =====
@app.route('/api/upload/retry', methods=['POST'])
@login_required
def retry_upload():
    data = request.json
    upload_ids = data.get('ids', [])
    if not upload_ids:
        return jsonify({"error": "未指定任务"}), 400
    token = get_baidu_token()
    if not token:
        return jsonify({"error": "百度网盘未授权"}), 401
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    retried = []
    for uid in upload_ids:
        c.execute("SELECT id, local_path, remote_path, status, progress FROM uploads WHERE id=?", (uid,))
        row = c.fetchone()
        if not row: continue
        uid, local_path, remote_path, status, _p = row
        if not os.path.exists(local_path): continue
        remote_dir = os.path.dirname(remote_path)
        c.execute("UPDATE uploads SET status='queued', error_msg=NULL WHERE id=?", (uid,))
        conn.commit()
        t = threading.Thread(target=do_upload, args=(local_path, remote_dir, uid, False), daemon=True)
        t.start()
        retried.append(uid)
    conn.close()
    return jsonify({"retried": retried, "message": f"已重试 {len(retried)} 个任务"})

# ===== API: 上传队列 =====
@app.route('/api/upload/queue')
@login_required
def upload_queue():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, local_path, remote_path, size, status, progress, source_deleted, started_at, completed_at, error_msg FROM uploads WHERE status IN ('queued','uploading') ORDER BY id")
    rows = c.fetchall()
    c.execute("SELECT COUNT(*) FROM uploads WHERE status='completed'")
    completed = c.fetchone()[0]
    conn.close()
    return jsonify({
        "queue": [{"id": r[0], "local_path": r[1], "status": r[4], "progress": r[5] or 0} for r in rows],
        "completed_count": completed
    })

# ===== API: 删除文件 =====
@app.route('/api/delete', methods=['POST'])
@login_required
def delete_files():
    data = request.json
    paths = data.get('paths', [])
    if not paths:
        return jsonify({"error": "未指定文件"}), 400
    results = []
    for p in paths:
        success, msg = force_delete(p)
        results.append({"path": p, "success": success, "error": None if success else msg})
    return jsonify({"results": results})

# ===== 工具 =====
def fmt_size(size):
    if size >= 1073741824: return f"{size/1073741824:.1f}GB"
    if size >= 1048576: return f"{size/1048576:.1f}MB"
    if size >= 1024: return f"{size/1024:.1f}KB"
    return f"{size}B"

# ===== 启动 =====
if __name__ == '__main__':
    print(f"🚀 百度网盘上传助手启动!")
    print(f"   地址: http://0.0.0.0:{APP_PORT}")
    print(f"   密码: {APP_PASSWORD}")
    app.run(host='0.0.0.0', port=APP_PORT, debug=False, threaded=True)
