"""音频工具函数 — 时长估算 + 重采样"""

import subprocess


# ===================== MP3 时长估算 =====================

def estimate_mp3_duration(data: bytes) -> float:
    """通过解析 MP3 帧头估算音频时长（秒）"""
    if len(data) < 4:
        return 0.0

    # 跳过 ID3v2 标签
    i = 0
    if data[:3] == b"ID3":
        if len(data) >= 10:
            tag_size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | \
                       ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
            i = 10 + tag_size

    total_samples = 0
    sample_rate = 0

    bitrate_v1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
    bitrate_v2 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0]
    sr_v1  = [44100, 48000, 32000]
    sr_v2  = [22050, 24000, 16000]
    sr_v25 = [11025, 12000, 8000]

    while i <= len(data) - 4:
        if data[i] != 0xFF or (data[i + 1] & 0xE0) != 0xE0:
            i += 1
            continue

        b1, b2 = data[i + 1], data[i + 2]
        version_bits = (b1 >> 3) & 3
        layer_bits   = (b1 >> 1) & 3
        br_idx       = (b2 >> 4) & 0xF
        sr_idx       = (b2 >> 2) & 3
        pad          = (b2 >> 1) & 1

        if version_bits == 1 or layer_bits == 0 or sr_idx == 3 or br_idx in (0, 15):
            i += 1
            continue

        if version_bits == 3:
            bitrate     = bitrate_v1[br_idx] * 1000
            sample_rate = sr_v1[sr_idx]
            spf         = 1152 if layer_bits in (1, 2) else 384
        else:
            bitrate     = bitrate_v2[br_idx] * 1000
            sample_rate = (sr_v2 if version_bits == 2 else sr_v25)[sr_idx]
            spf         = 576 if layer_bits == 1 else (1152 if layer_bits == 2 else 384)

        if bitrate == 0 or sample_rate == 0:
            i += 1
            continue

        frame_size = (spf * bitrate // (8 * sample_rate)) + pad
        total_samples += spf
        i += frame_size

    return total_samples / sample_rate if sample_rate > 0 else 0.0


def get_audio_duration(audio_data: bytes, encoding: str, sample_rate: int) -> float:
    """根据音频数据和编码方式计算音频时长（秒）"""
    if encoding == "raw":
        return len(audio_data) / (sample_rate * 2)
    elif encoding in ("lame", "mp3"):
        return estimate_mp3_duration(audio_data)
    return 0.0


# ===================== 音频重采样 =====================

def resample_mp3(input_file, output_file, target_sr=16000, target_bit=16, target_channels=1):
    """
    将音频重采样并保存为文件

    参数:
        input_file:   输入音频文件路径
        output_file:  输出文件路径
        target_sr:    目标采样率 (默认16000Hz)
        target_bit:   目标位长 (默认16bit)
        target_channels: 目标声道数 (1=单声道, 2=立体声)
    """
    cmd = [
        'ffmpeg',
        '-i', input_file,
        '-ar', str(target_sr),
        '-ac', str(target_channels),
        '-acodec', 'pcm_s16le',
        '-y',
        output_file
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
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
    流式重采样，不创建临时文件，直接返回 bytes
    """
    cmd = [
        'ffmpeg', '-nostdin', '-y',
        '-i', input_file,
        '-ar', str(target_sr),
        '-ac', str(target_channels),
        '-acodec', 'pcm_s16le',
        '-f', 'wav',
        'pipe:1'
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"重采样失败: {e.stderr}")
