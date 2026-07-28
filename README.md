# qsv2mp4

Convert `.qsv` offline video files to standard `.mp4` — lossless, no re-encoding.

**[English](#english)** · **[中文](#中文)**

---

## English

### Requirements

| | |
|---|---|
| Python ≥ 3.8 | https://python.org |
| ffmpeg | `brew install ffmpeg` · `apt install ffmpeg` · https://ffmpeg.org |

No third-party Python packages required.

### Usage

```
python qsv2mp4.py [INPUT ...] [options]
```

`INPUT` is one or more `.qsv` files or directories. Defaults to the current directory if omitted.

| Option | Description |
|---|---|
| `-o DIR` | Write `.mp4` output to `DIR` instead of alongside each input |
| `-r` | Scan directories recursively |
| `--skip-existing` | Skip if the output `.mp4` already exists |
| `--keep-ts` | Keep the intermediate `.ts` file |
| `--dry-run` | Print what would be converted, do nothing |
| `--inspect` | Diagnose a file: container layout, TS sync, per-segment clocks |
| `--mux-mode` | `auto` (default) · `single` · `concat` — how to join segments |
| `-v` | Verbose — show progress and ffmpeg output |

### Troubleshooting

Run `--inspect` on the source file:

```bash
python qsv2mp4.py --inspect movie.qsv
```

It prints one row per segment and a short verdict, which distinguishes the
possible causes:

| Finding | Meaning |
|---|---|
| *indices point past EOF* | The `.qsv` download is incomplete — re-download it |
| *N gap(s), segments not contiguous* | Layout issue — handled automatically since this version |
| *no TS sync after decryption* | The cipher or container layout does not match this file — please open an issue |
| *clock resets at segment(s) …* | Each shard has its own timeline — retry with `--mux-mode concat` |
| *all consistent* | Extraction is fine; the problem is elsewhere (player or ffmpeg version) |

`--mux-mode concat` extracts each segment to its own `.ts` and joins them with
ffmpeg's concat demuxer, which re-bases every segment's timestamps. Byte-splicing
(`single`) is faster but assumes all segments share one clock.

### Examples

```bash
# Single file
python qsv2mp4.py movie.qsv

# All .qsv files in a folder
python qsv2mp4.py /path/to/downloads

# Recursive scan, save MP4s to a specific folder
python qsv2mp4.py -r /path/to/downloads -o ~/Movies

# Preview without converting
python qsv2mp4.py --dry-run -r /path/to/downloads

# Batch re-run, skip already-converted files
python qsv2mp4.py -r --skip-existing /path/to/downloads
```

### Disclaimer & License

This tool is for **personal use only**. Do not use it to circumvent protections on content you do not own, or to distribute converted files. Use responsibly.

Released under the **MIT License**.

---

## 中文

### 依赖

| | |
|---|---|
| Python ≥ 3.8 | https://python.org |
| ffmpeg | `brew install ffmpeg` · `apt install ffmpeg` · https://ffmpeg.org |

无需任何第三方 Python 包。

### 用法

```
python qsv2mp4.py [INPUT ...] [选项]
```

`INPUT` 为一个或多个 `.qsv` 文件或目录，省略时默认扫描当前目录。

| 选项 | 说明 |
|---|---|
| `-o DIR` | 将 `.mp4` 输出到指定目录，而非与输入文件同目录 |
| `-r` | 递归扫描子目录 |
| `--skip-existing` | 若输出 `.mp4` 已存在则跳过 |
| `--keep-ts` | 保留中间 `.ts` 文件 |
| `--dry-run` | 仅预览将要转换的文件，不实际执行 |
| `--inspect` | 诊断模式：输出容器布局、TS 同步状态、各分段时钟 |
| `--mux-mode` | `auto`（默认）· `single` · `concat` — 分段的拼接方式 |
| `-v` | 显示详细进度和 ffmpeg 输出 |

### 疑难排查

先对源文件跑诊断：

```bash
python qsv2mp4.py --inspect movie.qsv
```

它会逐段输出一行信息并给出结论，用来区分几种可能的原因：

| 结论 | 含义 |
|---|---|
| *indices point past EOF* | `.qsv` 没下载完整，需要重新下载 |
| *N gap(s), segments not contiguous* | 分段不连续，本版本起已自动处理 |
| *no TS sync after decryption* | 解密算法或容器布局与该文件不匹配，请提 issue |
| *clock resets at segment(s) …* | 各分段时间轴独立，改用 `--mux-mode concat` |
| *all consistent* | 提取环节正常，问题在别处（播放器或 ffmpeg 版本） |

`--mux-mode concat` 会把每个分段单独提取，再用 ffmpeg 的 concat 解复用器拼接，
从而重建每段的时间戳；`single` 是直接按字节拼接，更快，但前提是所有分段共用同一条时间轴。

### 示例

```bash
# 转换单个文件
python qsv2mp4.py movie.qsv

# 转换目录下所有 .qsv 文件
python qsv2mp4.py /path/to/下载目录

# 递归扫描，将 MP4 保存到指定目录
python qsv2mp4.py -r /path/to/下载目录 -o ~/Movies

# 预览，不实际转换
python qsv2mp4.py --dry-run -r /path/to/下载目录

# 批量重跑，跳过已完成文件
python qsv2mp4.py -r --skip-existing /path/to/下载目录
```

### 免责声明 & 许可证

本工具仅供**个人使用**。请勿用于破解您不拥有版权的内容或传播转换后的文件，请合法合理使用。

基于 **MIT 许可证** 开源。
