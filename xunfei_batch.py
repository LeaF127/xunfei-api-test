"""
[已废弃] 此文件仅做兼容，请使用统一入口:
    python batch.py --provider xunfei
"""
import subprocess
subprocess.run(["python", "batch.py", "--provider", "xunfei"] + __import__("sys").argv[1:])
