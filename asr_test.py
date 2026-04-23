import websocket

from xunfei_asr_tts_demo import XunFeiASR
from util import resample_streaming
from config import APP_ID, API_KEY, API_SECRET

def main():
    print("=" * 50)
    print("语音听写 (ASR) 示例")
    print("=" * 50)
    
    asr = XunFeiASR(APP_ID, API_KEY, API_SECRET)
    test_audio = "test.mp3"  # 替换为你的测试音频文件路径
    audio_bytes = resample_streaming(test_audio)
    # 支持格式: pcm(16k/8k 16bit 单声道) / mp3(仅中英文)
    result = asr.recognize(audio_bytes,
                           audio_format="audio/L16;rate=16000",
                           encoding="raw")
    print(f"识别结果: {result}")

if __name__ == "__main__":
    main()
