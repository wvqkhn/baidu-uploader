# 百度网盘上传助手

一个带 Web 界面的百度网盘上传工具，支持目录递归上传、实时进度、断点恢复。

## 功能

- 📂 **文件浏览** — 浏览本地文件系统，可切换显示文件夹大小
- 📤 **上传到百度网盘** — 支持单个文件或整个目录递归上传
- 📊 **实时进度** — 文件级别和目录级别进度显示（百分比 + 进度条）
- ✅ **同步标记** — 已上传的文件/文件夹显示绿色勾 
- 🗑️ **删除源文件** — 上传完成后可选择删除本地文件（自动处理权限问题）
- 🔄 **断点恢复** — 服务重启后自动恢复未完成的上传
- 🔐 **密码保护** — Web 界面有登录密码
- 📱 **响应式** — 支持手机端访问

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

复制配置文件模板：

```bash
cp config.py config.local.py
# 编辑 config.local.py 修改配置
```

关键配置项：
- `APP_PASSWORD` — Web 登录密码
- `APP_PORT` — 监听端口（默认 3456）
- `BASE_DIRS` — 允许浏览的目录

### 3. 获取百度网盘授权

```bash
# 安装 bypy 并授权
pip install bypy
bypy info
# 按提示打开 URL，完成 OAuth 授权
```

### 4. 启动

```bash
python3 app.py
```

浏览器打开 `http://你的IP:3456` 即可访问。

## 配置项

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `APP_PORT` | 监听端口 | 3456 |
| `APP_PASSWORD` | 登录密码 | admin123 |
| `SECRET_KEY` | Flask 密钥 | (随机) |
| `BAIDU_TOKEN_FILE` | 百度 Token 路径 | ~/.bypy/bypy.json |
| `BASE_DIRS` | 浏览根目录 | /mnt |
| `DOCKER_RM_IMAGE` | 删除用 Docker 镜像 | busybox:latest |
| `DB_PATH` | 数据库路径 | ./uploads.db |

## API

提供 RESTful API，可用于自动化：

- `GET /api/list?path=/mnt` — 列出目录
- `GET /api/baidu/status` — 百度网盘使用情况
- `POST /api/upload/start` — 开始上传
- `GET /api/upload/queue` — 上传队列状态
- `POST /api/delete-synced` — 删除已同步文件

## 技术栈

- **后端**: Python Flask + SQLite
- **前端**: 原生 HTML/JS/CSS（无框架）
- **上传**: 百度 PCS API + requests-toolbelt（实时进度）
- **并发**: 最多 5 个文件同时上传

## License

MIT
