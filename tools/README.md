# Tools — dingwave 解密工具

本目录需要放置 **dingwave** 二进制文件，用于解密钉钉数据库。

## dingwave 是什么？

[dingwave](https://github.com/p1g3/dingwave) 是一个开源工具（Go 语言编写），用于解密钉钉桌面客户端的加密 SQLite 数据库（V2 格式，AES ECB + XXTEA）。它使用你的钉钉用户 UID 作为解密密钥。

钉钉的本地数据库文件（`dingtalk.db`）是加密的，无法直接读取 — dingwave 将其转换为标准的 SQLite 数据库。

## 下载

从 GitHub Releases 下载最新版本：**https://github.com/p1g3/dingwave/releases**

根据你的操作系统选择对应文件：

| 平台 | 文件 |
|------|------|
| Windows (64位) | `dingwave_windows_amd64.zip` |
| Windows (32位) | `dingwave_windows_386.zip` |
| Linux (64位) | `dingwave_linux_amd64.tar.gz` |
| Linux (ARM) | `dingwave_linux_arm64.tar.gz` |
| macOS (Intel) | `dingwave_darwin_amd64.tar.gz` |
| macOS (Apple Silicon) | `dingwave_darwin_arm64.tar.gz` |

## 安装

解压下载的压缩包，将二进制文件放入本目录：

```
dingtalk-exporter/
├── tools/
│   └── dingwave.exe    ← Windows 放这里
│   └── dingwave        ← Linux/Mac 放这里
```

建议文件名为 `dingwave.exe`（Windows）或 `dingwave`（Linux/Mac）。工具也支持自动识别 `tools/` 目录中包含 "dingwave" 的文件。

## 验证

手动运行确认可用：

```bash
# Windows
tools\dingwave.exe --help

# Linux/Mac
./tools/dingwave --help
```
