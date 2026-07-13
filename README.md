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
| `-v` | Verbose — show progress and ffmpeg output |

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
| `-v` | 显示详细进度和 ffmpeg 输出 |

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
