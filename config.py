import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# ===== 讯飞 =====
XUNFEI_APP_ID = os.getenv("XUNFEI_APP_ID", "")
XUNFEI_API_KEY = os.getenv("XUNFEI_API_KEY", "")
XUNFEI_API_SECRET = os.getenv("XUNFEI_API_SECRET", "")

# ===== 阿里云 =====
ALIYUN_ACCESS_KEY_ID = os.getenv("ALIYUN_ACCESS_KEY_ID", "")
ALIYUN_ACCESS_KEY_SECRET = os.getenv("ALIYUN_ACCESS_KEY_SECRET", "")
ALIYUN_APP_KEY = os.getenv("ALIYUN_APP_KEY", "")

# ===== 豆包 (火山引擎) =====
DOUBAO_APP_ID = os.getenv("DOUBAO_APP_ID", "")
DOUBAO_ACCESS_TOKEN = os.getenv("DOUBAO_ACCESS_TOKEN", "")
DOUBAO_SECRET_KEY = os.getenv("DOUBAO_SECRET_KEY", "")
DOUBAO_CLUSTER = os.getenv("DOUBAO_CLUSTER", "volcano_tts")
