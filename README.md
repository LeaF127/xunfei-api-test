# asr-tts-benchmark

> 多服务方 ASR（语音识别）+ TTS（语音合成）性能测试工具，使用 [CommonVoice](https://commonvoice.mozilla.org/) 中文数据集。

## 支持的服务方

| 服务方 | ASR | TTS | 鉴权方式 |
|--------|-----|-----|----------|
| 讯飞 (xunfei) | ✅ 在线语音听写 | ✅ 在线语音合成 | WebSocket HMAC |
| 阿里云 (aliyun) | ✅ 一句话识别 | ✅ 语音合成 | AccessKey Token |
| 豆包 (doubao) | ✅ 一句话识别 | ✅ 语音合成 | Bearer Token / HMAC256 |
| 豆包大模型 (doubao_bigmodel) | ✅ 大模型流式识别 | ✅ 语音合成（复用豆包TTS） | API Key |

## 项目结构

```
├── config.py                     # 统一配置（按服务方前缀读取 .env）
├── .env.example                  # 环境变量模板
├── batch.py                      # 统一批量测试入口（支持随机抽样、并发）
├── calculate_cer.py              # 批量 CER 计算脚本（独立使用）
├── providers/
│   ├── base.py                   # BaseASR / BaseTTS 抽象基类 + Metrics
│   ├── __init__.py               # 工厂函数 get_asr() / get_tts()
│   ├── xunfei/
│   │   ├── auth.py               # 讯飞 WebSocket 鉴权
│   │   ├── asr.py                # 讯飞 ASR
│   │   └── tts.py                # 讯飞 TTS
│   ├── aliyun/
│   │   ├── auth.py               # 阿里云 AccessKey 签名 → Token
│   │   ├── asr.py                # 阿里云 ASR
│   │   └── tts.py                # 阿里云 TTS
│   └── doubao/
│       ├── auth.py               # 豆包 Bearer Token / HMAC256 鉴权
│       ├── asr.py                # 豆包一句话识别 ASR (WebSocket)
│       ├── asr_bigmodel.py       # 豆包大模型流式识别 ASR (V3 二进制协议)
│       └── tts.py                # 豆包 TTS (HTTP)
├── utils/
│   ├── audio.py                  # 音频时长估算 + FFmpeg 重采样
│   └── metrics.py                # CER 计算（NIST 标准 + CJK 数字转换）
└── requirements.txt
```

## 快速开始

### 1. 环境准备

- **Python** >= 3.10
- **FFmpeg**（音频重采样依赖）

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 API 密钥

复制 `.env.example` 为 `.env`，填入对应服务方的密钥：

```env
# ===== 讯飞 =====
XUNFEI_APP_ID=你的APP_ID
XUNFEI_API_KEY=你的API_KEY
XUNFEI_API_SECRET=你的API_SECRET

# ===== 阿里云 =====
ALIYUN_ACCESS_KEY_ID=你的ACCESS_KEY_ID
ALIYUN_ACCESS_KEY_SECRET=你的ACCESS_KEY_SECRET
ALIYUN_APP_KEY=你的APP_KEY

# ===== 豆包 (火山引擎) =====
DOUBAO_APP_ID=你的APPID
DOUBAO_ACCESS_TOKEN=你的ACCESS_TOKEN
DOUBAO_SECRET_KEY=你的SECRET_KEY
DOUBAO_CLUSTER=volcano_tts

# ===== 豆包大模型流式识别 (新版控制台 API Key) =====
DOUBAO_API_KEY=你的API_KEY
DOUBAO_RESOURCE_ID=volc.bigasr.sauc.duration
```

> - 讯飞：前往 [讯飞开放平台](https://www.xfyun.cn/) 获取
> - 阿里云：前往 [阿里云智能语音交互](https://nls-portal.console.aliyun.com/) 获取
> - 豆包：前往 [火山引擎语音控制台](https://console.volcengine.com/speech/service/overview) 获取
> - 豆包大模型：新版控制台 → API Keys 页面获取 `DOUBAO_API_KEY`

### 4. 准备测试数据

从 [Mozilla CommonVoice](https://commonvoice.mozilla.org/zh-CN/datasets) 下载中文数据集，解压后目录结构如下：

```
cv-test/cv-corpus-25.0-2026-03-09/zh-CN/
├── test.tsv          # 测试集 TSV
├── clips/            # 音频文件目录
│   ├── common_voice_zh-CN_12345.mp3
│   └── ...
└── ...
```

## 使用方法

### 批量测试（推荐）

从 `test.tsv` 随机抽样测试：

```bash
# 从 test.tsv 随机抽 2000 条，2 并发
python batch.py --provider xunfei --mode all --sample 2000 --workers 2

# 豆包大模型 ASR，指定种子保证可复现
python batch.py --provider doubao_bigmodel --mode asr --sample 2000 --seed 42

# 阿里云 ASR
python batch.py --provider aliyun --mode asr --sample 2000 --workers 2

# 豆包一句话识别 + TTS
python batch.py --provider doubao --mode all --sample 500
```

### 按顺序取前 N 条

```bash
python batch.py --provider xunfei --mode asr --limit 100
```

### 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--provider` | 服务方 xunfei / aliyun / doubao / doubao_bigmodel | `xunfei` |
| `--mode` | 测试模式 asr / tts / all | `all` |
| `--data_root` | CommonVoice 数据目录 | `cv-test/.../zh-CN` |
| `--sample` | 从 test.tsv 随机抽样的条数 | 不抽样，使用全部 |
| `--seed` | 随机种子（保证可复现） | `42` |
| `--limit` | 按顺序取前 N 条（与 --sample 可独立使用） | 不限制 |
| `--output_asr` | ASR 结果目录 | `outputs/<provider>/asr` |
| `--output_tts` | TTS 音频目录 | `outputs/<provider>/tts` |
| `--voice` | TTS 发音人 | 服务方默认值 |
| `--workers` | 并发路数 | `1` |

### 增量模式

程序支持增量续跑，已处理的结果会自动跳过。中断后重新运行同一命令即可继续。

### 豆包大模型 ASR Debug 模式

```bash
DOUBAO_ASR_DEBUG=1 python batch.py --provider doubao_bigmodel --mode asr --limit 5
```

开启后会打印完整的 WebSocket 帧交互日志，便于排查问题。

### 批量计算 CER

对已有的测试结果重新计算 CER：

```bash
# JSON 输出
python calculate_cer.py --dir outputs/xunfei/asr

# CSV 输出
python calculate_cer.py --dir outputs/aliyun/asr --format csv

# 忽略 CER >= 1.0 的结果（fuzzy 模式）
python calculate_cer.py --dir outputs/doubao_bigmodel/asr --fuzzy

# 指定服务方（使用默认目录）
python calculate_cer.py --provider doubao_bigmodel
```

### 在代码中使用

```python
from providers import get_asr, get_tts

# ASR
asr = get_asr("doubao_bigmodel")
text, metrics = asr.recognize("test.wav", audio_format="wav")
print(text, metrics.ttft, metrics.total_time, metrics.rtf)

# TTS
tts = get_tts("doubao")
audio, metrics = tts.synthesize("你好世界", output_file="output.mp3")
```

## 添加新的服务方

1. 在 `providers/` 下创建新目录（如 `providers/tencent/`）
2. 实现 `auth.py`、`asr.py`、`tts.py`，继承 `BaseASR` / `BaseTTS`
3. 在 `providers/__init__.py` 的工厂函数中注册
4. 在 `config.py` 和 `.env.example` 中添加对应的环境变量
5. 在 `batch.py` 的 `--provider` choices 中添加

## 输出指标

每次请求会记录以下性能指标：

| 指标 | 说明 | 单位 |
|------|------|------|
| `ttft` | 首字节延迟：从发送请求到收到首个结果 | 秒 |
| `total_time` | 总处理时间 | 秒 |
| `rtf` | 实时率：总耗时 / 音频时长 | 无量纲 |
| `cer` | 字错误率 (Character Error Rate) | 0.0 ~ ∞ |

结果保存在 `outputs/<provider>/asr/` 下：
- `result_0001.json` - 单条结果（含识别文本、CER、性能指标）
- `summary.json` / `summary.csv` - 汇总统计（平均 CER、平均 TTFT 等）

### CER 计算方法

符合 NIST / HuggingFace 标准 ASR 评测规范：

1. Unicode NFC 归一化
2. 去除标注标记（`<UNK>`, `[NOISE]` 等）
3. 大小写归一化（转小写）
4. 去除标点（基于 Unicode Category）
5. CJK 数字转换（`2014年` → `二零一四年`，统一数字表示形式）
6. CJK 语种去空格

## 参考文档

- [讯飞语音听写 API](https://www.xfyun.cn/doc/asr/voicedictation/API.html)
- [讯飞在线语音合成 API](https://www.xfyun.cn/doc/tts/online_tts/API.html)
- [阿里云一句话识别](https://help.aliyun.com/document_detail/84428.html)
- [阿里云语音合成](https://help.aliyun.com/document_detail/84435.html)
- [豆包语音合成 HTTP 接口](https://www.volcengine.com/docs/6561/79820)
- [豆包一句话识别](https://www.volcengine.com/docs/6561/80816)
- [豆包大模型流式语音识别](https://www.volcengine.com/docs/6561/1354869)
- [豆包语音鉴权方法](https://www.volcengine.com/docs/6561/107789)

## License

MIT
