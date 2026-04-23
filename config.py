import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

APP_ID = os.getenv("APP_ID", "")
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")

