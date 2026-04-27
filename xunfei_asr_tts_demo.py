"""
[已废弃] 此文件仅做兼容，原代码已拆分到 providers/xunfei/ 目录下。
请使用:
    from providers.xunfei import XunFeiASR, XunFeiTTS
"""
from providers.xunfei.asr import XunFeiASR
from providers.xunfei.tts import XunFeiTTS
from providers.xunfei.auth import create_auth_url
from utils.audio import estimate_mp3_duration, get_audio_duration
