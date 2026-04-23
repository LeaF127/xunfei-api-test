import subprocess
import io

def resample_mp3(input_file, output_file, target_sr=16000, target_bit=16, target_channels=1):
    """
    将MP3音频重采样
    
    参数:
        input_file:   输入MP3文件路径
        output_file:  输出文件路径
        target_sr:    目标采样率 (默认16000Hz)
        target_bit:   目标位长 (默认16bit)
        target_channels: 目标声道数 (1=单声道, 2=立体声)
    """
    cmd = [
        'ffmpeg',
        '-i', input_file,           # 输入文件
        '-ar', str(target_sr),      # 采样率 16k
        '-ac', str(target_channels),# 单声道
        '-acodec', 'pcm_s16le',     # 16bit PCM编码
        '-y',                        # 覆盖输出
        output_file
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print(f"重采样成功！保存到: {output_file}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"出错啦: {e.stderr}")
        return False
    except FileNotFoundError:
        print("找不到ffmpeg，请先安装ffmpeg哦！")
        return False

def resample_streaming(input_file, target_sr=16000, target_channels=1):
    """
    直接流式重采样，不创建临时文件！最省空间的方式！
    """
    # 使用管道，直接将ffmpeg输出重定向到内存
    cmd = [
        'ffmpeg', '-nostdin', '-y',
        '-i', input_file,
        '-ar', str(target_sr),
        '-ac', str(target_channels),
        '-acodec', 'pcm_s16le',
        '-f', 'wav',  # 输出WAV格式
        'pipe:1'      # 输出到标准输出
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, check=True)
        audio_data = result.stdout  # 直接获取重采样后的音频数据
        return audio_data  # 返回音频bytes

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"重采样失败: {e.stderr}")

def main():
    mp3_file = "test.mp3"
    output_file = "16k.wav"

    resample_mp3(mp3_file, output_file)

if __name__ == "__main__":
    main()
