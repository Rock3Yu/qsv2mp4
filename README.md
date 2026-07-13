# qsv2mp4

Convert iQIYI `.qsv` offline video files to standard `.mp4` — no re-encoding, lossless, pure Python + ffmpeg.

---

## English

### Background

iQIYI (爱奇艺) stores offline-downloaded videos in a proprietary container format with the `.qsv` extension.  
The file begins with a `QIYI VIDEO` magic header (version 2) and embeds a series of MPEG-TS segments whose **first 1 024 bytes are scrambled** by a proprietary shuffle cipher.  
Standard tools like ffmpeg cannot open `.qsv` files directly — this script reverses the cipher, reconstructs a clean MPEG-TS stream, and muxes it into MP4 via ffmpeg (stream copy, **no quality loss**).

### Requirements

| Dependency | Install |
|---|---|
| Python ≥ 3.8 | <https://python.org> |
| ffmpeg | `brew install ffmpeg` (macOS) · `apt install ffmpeg` (Ubuntu) · [ffmpeg.org](https://ffmpeg.org) (Windows) |

No third-party Python packages are needed — only the standard library.

### Installation

```bash
git clone https://github.com/<your-username>/qsv2mp4.git
cd qsv2mp4
```

### Usage

```
python qsv2mp4.py [INPUT ...] [options]
```

`INPUT` can be one or more `.qsv` files **or** directories.  
When no input is given the current directory is scanned.

#### Options

| Flag | Description |
|---|---|
| `-o DIR` / `--output DIR` | Write `.mp4` files into `DIR` instead of next to each input |
| `-r` / `--recursive` | Scan directories recursively for `.qsv` files |
| `--skip-existing` | Skip conversion if the `.mp4` already exists |
| `--keep-ts` | Keep the intermediate MPEG-TS file alongside the output |
| `--dry-run` | Print what would be converted — do nothing |
| `-v` / `--verbose` | Show extraction progress and ffmpeg output |
| `-h` / `--help` | Show the help message |

#### Examples

```bash
# Convert a single file (output sits next to the input)
python qsv2mp4.py "何以为家-蓝光1080P.qsv"

# Convert all .qsv files in a folder
python qsv2mp4.py "/path/to/iQIYI Downloads"

# Recursively find every .qsv under a tree and save MP4s to ~/Movies
python qsv2mp4.py -r "/path/to/iQIYI Downloads" -o ~/Movies

# Preview what would happen without writing anything
python qsv2mp4.py --dry-run -r "/path/to/iQIYI Downloads"

# Re-run a batch and skip files already converted
python qsv2mp4.py -r --skip-existing "/path/to/iQIYI Downloads"

# Keep the intermediate .ts alongside the .mp4
python qsv2mp4.py --keep-ts "movie.qsv"
```

### How it works

```
QSV file
  └─ 90-byte header        → parse version, segment count, XML offset
  └─ flag bitmap           → skipped
  └─ segment index table   → each 28-byte entry decrypted with shuffle cipher
  └─ embedded MPEG-TS      → copied verbatim; first 1 024 B of each segment
                              decrypted with the same shuffle cipher
                           → valid MPEG-TS stream
                           → ffmpeg stream-copy → .mp4 (H.264 + AAC, no loss)
```

The cipher (`_decrypt_2`) is a stateful byte-shuffle keyed on the constant `0x62677079` ("bgpy"). It is applied to:

1. Each 28-byte segment-index entry (to recover the true file offset and size).
2. The first 1 024 bytes of every MPEG-TS segment (to restore the sync bytes).

### Output quality

ffmpeg is invoked with `-c copy` — the video and audio streams are remuxed without re-encoding.  
The output MP4 is **bit-for-bit identical** to the original stream; there is no quality degradation.

### Supported formats

| Property | Value |
|---|---|
| QSV version | **2** (the current iQIYI offline format) |
| Inner container | MPEG-TS |
| Typical video codec | H.264 (AVC) |
| Typical audio codec | AAC |
| Output container | MP4 (`.mp4`) |

### Disclaimer

This tool is intended for **personal, educational use only** — to let you watch content you legitimately downloaded on devices and players of your choice.  
Circumventing DRM on content you do not own, or distributing converted files, may violate iQIYI's Terms of Service and applicable copyright law.  
Use responsibly.

---

## 中文

### 背景

爱奇艺将离线下载的视频保存为专有的 `.qsv` 格式文件。  
文件以 `QIYI VIDEO` 魔数开头（版本 2），内部包含若干 MPEG-TS 分段，每个分段的**前 1 024 字节**经过专有的字节混淆算法加密。  
ffmpeg 等标准工具无法直接打开 `.qsv` 文件。本脚本逆向还原该算法，重建完整的 MPEG-TS 流，再通过 ffmpeg 流复制（**无损**）封装为 MP4。

### 依赖

| 依赖 | 安装方式 |
|---|---|
| Python ≥ 3.8 | <https://python.org> |
| ffmpeg | `brew install ffmpeg`（macOS）· `apt install ffmpeg`（Ubuntu）· [ffmpeg.org](https://ffmpeg.org)（Windows）|

无需任何第三方 Python 包，仅依赖标准库。

### 安装

```bash
git clone https://github.com/<your-username>/qsv2mp4.git
cd qsv2mp4
```

### 用法

```
python qsv2mp4.py [INPUT ...] [选项]
```

`INPUT` 可以是一个或多个 `.qsv` 文件，也可以是目录。  
不传入参数时，默认扫描当前目录。

#### 选项说明

| 参数 | 说明 |
|---|---|
| `-o DIR` / `--output DIR` | 将 `.mp4` 输出到指定目录，而非与输入文件同目录 |
| `-r` / `--recursive` | 递归扫描目录下所有 `.qsv` 文件 |
| `--skip-existing` | 若目标 `.mp4` 已存在则跳过 |
| `--keep-ts` | 保留中间产生的 MPEG-TS 文件 |
| `--dry-run` | 仅打印将要转换的文件，不实际执行 |
| `-v` / `--verbose` | 显示提取进度和 ffmpeg 详细输出 |
| `-h` / `--help` | 显示帮助信息 |

#### 使用示例

```bash
# 转换单个文件（输出与输入同目录）
python qsv2mp4.py "何以为家-蓝光1080P.qsv"

# 转换某目录下所有 .qsv 文件
python qsv2mp4.py "/path/to/爱奇艺下载"

# 递归扫描目录，将所有 MP4 保存到 ~/Movies
python qsv2mp4.py -r "/path/to/爱奇艺下载" -o ~/Movies

# 预览将要转换的文件，不实际写入
python qsv2mp4.py --dry-run -r "/path/to/爱奇艺下载"

# 批量重跑时跳过已完成的文件
python qsv2mp4.py -r --skip-existing "/path/to/爱奇艺下载"

# 保留中间 .ts 文件
python qsv2mp4.py --keep-ts "movie.qsv"
```

### 工作原理

```
QSV 文件
  └─ 90 字节头部         → 解析版本号、分段数量、XML 偏移
  └─ 标志位图            → 跳过
  └─ 分段索引表          → 每条 28 字节记录用混淆算法解密，还原真实偏移和大小
  └─ 内嵌 MPEG-TS        → 原样复制；每个分段前 1 024 字节用同一算法解密
                         → 得到合法的 MPEG-TS 流
                         → ffmpeg 流复制 → .mp4（H.264 + AAC，无损）
```

混淆算法（`_decrypt_2`）是一种基于常量 `0x62677079`（即 ASCII `"bgpy"`）的有状态字节置换，作用于：

1. 每条 28 字节的分段索引（还原真实文件偏移和大小）。
2. 每个 MPEG-TS 分段的前 1 024 字节（恢复同步字节）。

### 输出质量

ffmpeg 以 `-c copy` 调用，视频和音频流直接封装，**不经过重新编码**。  
输出 MP4 与原始流**逐字节一致**，无任何质量损失。

### 支持规格

| 属性 | 值 |
|---|---|
| QSV 版本 | **2**（当前爱奇艺离线格式） |
| 内部容器 | MPEG-TS |
| 常见视频编码 | H.264（AVC） |
| 常见音频编码 | AAC |
| 输出容器 | MP4（`.mp4`） |

### 免责声明

本工具仅供**个人学习研究**使用，旨在让您在自己的设备和播放器上观看已合法下载的内容。  
对您不拥有版权的内容破解 DRM，或传播转换后的文件，可能违反爱奇艺服务条款及相关版权法律。  
请合法、合理地使用本工具。

---

## License

MIT
