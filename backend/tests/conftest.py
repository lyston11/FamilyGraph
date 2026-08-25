"""测试全局准备：必须在任何 app 模块导入前注入测试环境变量。"""

import os
import tempfile

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="familygraph-tests-")

# 环境变量就绪后才能导入 config（其路径/密钥在导入时读取）
from app import config  # noqa: E402

config.ensure_ready()
