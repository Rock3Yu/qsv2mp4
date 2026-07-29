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
| `--mux-mode` | `auto` (default) · `single` · `concat` · `split` — how to join segments |
| `--salvage-clear` | On a DRM-protected file, convert the segments that aren't encrypted |
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
| *segment(s) … lose TS sync partway* | Those segments are still encrypted past their head — a format change this tool does not yet handle |
| *carry codec parameter sets that segment 1 never had* | The shards are separate encodes — handled automatically, see below |
| *this file is DRM-protected* | The video is encrypted with a key iQIYI never wrote to the file — see below |
| *hold TS sync end-to-end* | The container unpacked correctly; if playback is still wrong the fault is inside the video stream, not in the unpacking |

`--mux-mode concat` extracts each segment to its own `.ts` and joins them with
ffmpeg's concat demuxer, which re-bases every segment's timestamps. Byte-splicing
(`single`) is faster but assumes all segments share one clock.

### Only the first part plays, the rest is macroblock soup

An MP4 track carries **one** set of codec parameters, fixed by whatever the
first frames declared. Some `.qsv` files are assembled from shards that are
separate encodes — a different resolution or profile per shard — and MPEG-TS
allows that because it repeats the parameters in-band. Copy such a stream into
a single MP4 and every shard after the first decodes against the wrong
parameters: the opening minutes look fine, the remainder is garbage.

There is no lossless way to put that in one MP4, so `--mux-mode split` writes
one file per parameter set instead:

```
movie.part1.mp4    segments 1–3
movie.part2.mp4    segments 4–7
```

This is **opt-in**, and worth understanding before reaching for it. Splitting
only helps when every shard is independently decodable; a shard that carries no
parameter set of its own becomes an MP4 that will not open at all. Run
`--inspect` first — its per-segment decode check says which case you are in —
and confirm each part plays before discarding the source.

### DRM-protected downloads

Some downloads are encrypted, and this tool cannot convert them. `--inspect`
names the case outright:

```
  DRM          : iQIYI DRM (version 3.1.0, dr=4, edrtype=cuva, licence ticket 2048 chars)
    #       codec      dimensions                       verdict
    1        hevc       3840x1608                            ok
    2        hevc               —     no codec parameters found
```

The container still unpacks perfectly — TS framing, PIDs, PCR and PTS are all
intact — but the video inside the protected segments is encrypted under a key
that lives on iQIYI's licence server and was never written to the file. There
is nothing to reverse: no unpacker, this one included, can produce clear video
from it. Converting anyway just yields a file that plays as macroblock soup,
so those files are refused rather than silently written.

Typically only the opening segment is left in the clear (the free preview).
`--salvage-clear` converts that part on its own, to `NAME.clear.mp4`:

```bash
python qsv2mp4.py --salvage-clear movie.qsv
```

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
| `--mux-mode` | `auto`（默认）· `single` · `concat` · `split` — 分段的拼接方式 |
| `--salvage-clear` | 对有 DRM 保护的文件，只转换其中未加密的分段 |
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
| *segment(s) … lose TS sync partway* | 这些分段在头部之后仍是密文，属于本工具尚未支持的格式变化 |
| *carry codec parameter sets that segment 1 never had* | 各分段是彼此独立的编码，已自动处理，见下 |
| *this file is DRM-protected* | 视频被 DRM 加密，密钥根本不在文件里，见下 |
| *hold TS sync end-to-end* | 容器解包正确；若播放仍有问题，原因在视频流内部，不在解包环节 |

`--mux-mode concat` 会把每个分段单独提取，再用 ffmpeg 的 concat 解复用器拼接，
从而重建每段的时间戳；`single` 是直接按字节拼接，更快，但前提是所有分段共用同一条时间轴。

### 只有开头一段能正常播放，后面全是花屏

一条 MP4 轨道只能保存**一份**编码参数集，由最开始的那几帧确定。有些 `.qsv`
的各个分段是彼此独立的编码——分辨率或 profile 逐段不同——MPEG-TS 允许这样，
因为它把参数集随流反复内嵌。可一旦把这种流复制进单个 MP4，第一段之后的所有
分段都会按错误的参数去解码：开头几分钟正常，后面全是花屏。

这种情况没有无损塞进单个 MP4 的办法，可以改用 `--mux-mode split` 按参数集分文件输出：

```
movie.part1.mp4    分段 1–3
movie.part2.mp4    分段 4–7
```

这是**手动开关**，用之前先了解清楚：只有当每个分段都能独立解码时分文件才有意义；
若某个分段本身不带参数集，拆出来的 MP4 会**完全打不开**。请先跑 `--inspect`，
其中的逐段解码检查会告诉你属于哪种情况，并在删掉源文件前确认每个分片都能播放。

### 带 DRM 保护的下载文件

有些下载文件是加密的，本工具无法转换。`--inspect` 会直接点明：

```
  DRM          : iQIYI DRM (version 3.1.0, dr=4, edrtype=cuva, licence ticket 2048 chars)
    #       codec      dimensions                       verdict
    1        hevc       3840x1608                            ok
    2        hevc               —     no codec parameters found
```

此时容器本身解包完全正常——TS 帧结构、PID、PCR、PTS 全部完好——但受保护分段里的
视频码流是用爱奇艺授权服务器上的密钥加密的，**该密钥从未写进这个文件**。
所以这里没有什么可以「逆向」的：任何解包工具（包括本工具）都不可能还原出明文视频。
强行转换只会得到一个满屏花屏的文件，因此本工具会直接拒绝，而不是悄悄写出一个坏文件。

通常只有开头第一个分段是明文的（也就是免费试看的部分）。
`--salvage-clear` 可以只把这部分单独转换出来，输出为 `NAME.clear.mp4`：

```bash
python qsv2mp4.py --salvage-clear movie.qsv
```

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
