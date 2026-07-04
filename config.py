"""百度网盘上传助手 - 配置文件"""
import os

# 服务端口
APP_PORT = int(os.getenv("APP_PORT", "3456"))

# Web 登录密码
APP_PASSWORD = os.getenv("APP_PASSWORD", "admin123")

# Flask 密钥
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-to-a-random-secret")

# 百度网盘 Token 文件路径
BAIDU_TOKEN_FILE = os.path.expanduser(
    os.getenv("BAIDU_TOKEN_FILE", "~/.bypy/bypy.json")
)

# 允许浏览的根目录
BASE_DIRS = os.getenv("BASE_DIRS", "/mnt").split(",")

# Docker 删除镜像（用于权限不足时的 fallback）
DOCKER_RM_IMAGE = os.getenv("DOCKER_RM_IMAGE", "busybox:latest")

# 数据库路径
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "uploads.db"))
