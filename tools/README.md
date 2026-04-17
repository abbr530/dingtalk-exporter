# Tools — dingwave

This directory should contain the **dingwave** binary used to decrypt the DingTalk database.

## What is dingwave?

[dingwave](https://github.com/p1g3/dingwave) is an open-source tool (written in Go) that decrypts the DingTalk desktop client's encrypted SQLite database (V2 format, AES ECB + XXTEA). It uses your DingTalk user UID as the decryption key.

DingTalk's local database file (`dingtalk.db`) is encrypted and cannot be read directly — dingwave converts it into a standard SQLite database that this tool can then parse.

## Download

Get the latest release from: **https://github.com/p1g3/dingwave/releases**

Latest version: **v1.0.1** (2026-01-20)

Download the binary for your platform:

| Platform | File |
|----------|------|
| Windows (64-bit) | `dingwave_windows_amd64.zip` |
| Linux (64-bit) | `dingwave_linux_amd64.tar.gz` |
| macOS (Intel) | `dingwave_darwin_amd64.tar.gz` |
| macOS (Apple Silicon) | `dingwave_darwin_arm64.tar.gz` |
| Windows (32-bit) | `dingwave_windows_386.zip` |
| Linux (ARM) | `dingwave_linux_arm64.tar.gz` |

## Install

Extract the archive and place the binary in this directory:

```
dingtalk-exporter/
├── tools/
│   └── dingwave.exe    ← place it here (Windows)
│   └── dingwave        ← or here (Linux/Mac)
```

Make sure the filename is `dingwave.exe` (Windows) or `dingwave` (Linux/Mac).

## Verify

Run it manually to verify:

```bash
# Windows
tools\dingwave.exe --help

# Linux/Mac
./tools/dingwave --help
```
