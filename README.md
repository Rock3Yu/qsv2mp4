# qsv2mp4

Convert iQIYI `.qsv` offline video files to standard `.mp4` — lossless, no re-encoding.

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

```bash
python qsv2mp4.py movie.qsv              # one file
python qsv2mp4.py /path/to/downloads     # every .qsv in a folder
python qsv2mp4.py -r /downloads -o ~/Movies   # recursive, output elsewhere
python qsv2mp4.py --inspect movie.qsv    # diagnose without converting
```

`INPUT` is one or more `.qsv` files or directories. Defaults to the current
directory if omitted.

| Option | Description |
|---|---|
| `-o DIR` | Write `.mp4` output to `DIR` instead of alongside each input |
| `-r` | Scan directories recursively |
| `--skip-existing` | Skip if the output `.mp4` already exists |
| `--keep-ts` | Keep the intermediate `.ts` file |
| `--dry-run` | Print what would be converted, do nothing |
| `--inspect` | Diagnose a file instead of converting it |
| `--mux-mode` | `auto` (default) · `single` · `concat` — how to join segments |
| `--salvage-clear` | On a DRM-protected file, convert the segments that aren't encrypted |
| `-v` | Verbose — show progress and ffmpeg output |

### Limitations

**DRM-protected downloads cannot be converted.** This is the one case worth
understanding before opening an issue.

Some downloads — typically 4K / HDR or members-only titles — have their video
encrypted with a key that lives on iQIYI's licence server and was never written
into the file. The container still unpacks perfectly, but there is nothing to
reverse: no tool can produce clear video without that key, and this project
does not attempt to obtain it.

`--inspect` says so outright:

```
  DRM          : iQIYI DRM (version 3.1.0, dr=4, edrtype=cuva, licence ticket 2048 chars)
    #       codec      dimensions                       verdict
    1        hevc       3840x1608                            ok
    2        hevc               —     no codec parameters found
```

Such files are refused rather than converted into an unplayable result.
Usually the opening segment is left in the clear (the free preview);
`--salvage-clear` converts just that, to `NAME.clear.mp4`.

Files with no DRM convert normally — `--inspect` prints `DRM : none declared`.

Other known limits: QSV **v2** containers only, and ffmpeg must be on `PATH`.

### Reporting a problem

Run:

```bash
python qsv2mp4.py --inspect movie.qsv
```

and paste the **whole** output into the issue. It reports the container
layout, per-segment TS sync, the clocks, whether DRM is declared, and whether
a decoder can read each segment — which is enough to tell an incomplete
download from a layout problem, a cipher mismatch, or DRM. Please also say
what player you used and what you saw.

Do not attach the `.qsv` file unless asked.

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

```bash
python qsv2mp4.py movie.qsv              # 转换单个文件
python qsv2mp4.py /path/to/下载目录       # 转换目录下所有 .qsv
python qsv2mp4.py -r /下载目录 -o ~/Movies # 递归扫描，输出到指定目录
python qsv2mp4.py --inspect movie.qsv    # 只诊断，不转换
```

`INPUT` 为一个或多个 `.qsv` 文件或目录，省略时默认扫描当前目录。

| 选项 | 说明 |
|---|---|
| `-o DIR` | 将 `.mp4` 输出到指定目录，而非与输入文件同目录 |
| `-r` | 递归扫描子目录 |
| `--skip-existing` | 若输出 `.mp4` 已存在则跳过 |
| `--keep-ts` | 保留中间 `.ts` 文件 |
| `--dry-run` | 仅预览将要转换的文件，不实际执行 |
| `--inspect` | 只诊断文件，不做转换 |
| `--mux-mode` | `auto`（默认）· `single` · `concat` — 分段的拼接方式 |
| `--salvage-clear` | 对有 DRM 保护的文件，只转换其中未加密的分段 |
| `-v` | 显示详细进度和 ffmpeg 输出 |

### 局限

**带 DRM 保护的文件无法转换。** 提 issue 之前，这一条值得先了解清楚。

有些下载文件——通常是 4K / HDR 或会员专享的片源——其视频码流是用爱奇艺授权服务器上的
密钥加密的，**而那个密钥从未写进文件里**。此时容器本身依然能完美解包，但这里没有任何
可以「逆向」的东西：没有密钥，任何工具都不可能还原出明文视频，本项目也不会去尝试获取它。

`--inspect` 会直接点明：

```
  DRM          : iQIYI DRM (version 3.1.0, dr=4, edrtype=cuva, licence ticket 2048 chars)
    #       codec      dimensions                       verdict
    1        hevc       3840x1608                            ok
    2        hevc               —     no codec parameters found
```

遇到这种文件，本工具会直接拒绝，而不是悄悄写出一个满屏花屏的坏文件。
通常只有开头第一段是明文的（免费试看部分），`--salvage-clear` 可以把这部分
单独转出来，输出为 `NAME.clear.mp4`。

没有 DRM 的文件转换一切正常 —— `--inspect` 会显示 `DRM : none declared`。

其他已知限制：仅支持 QSV **v2** 容器；ffmpeg 必须在 `PATH` 中。

### 如何反馈问题

请运行：

```bash
python qsv2mp4.py --inspect movie.qsv
```

并把**完整输出**贴进 issue。它会报告容器布局、各分段的 TS 同步状态、时间轴、
是否声明了 DRM，以及解码器能否读出每一段——这些信息足以区分「文件没下载完」、
「布局异常」、「解密算法不匹配」还是「DRM 加密」。同时也请说明你用的播放器和具体现象。

除非被要求，请不要直接上传 `.qsv` 文件。

### 免责声明 & 许可证

本工具仅供**个人使用**。请勿用于破解您不拥有版权的内容或传播转换后的文件，请合法合理使用。

基于 **MIT 许可证** 开源。
