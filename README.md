# 钉钉聊天记录导出工具 (DingTalk Chat Exporter)

从钉钉桌面客户端的本地加密数据库中导出聊天记录，提供 Web 界面浏览、搜索和导出。

> **声明**：本工具仅供个人数据备份使用，请遵守公司数据政策和相关法规。

## 功能特性

- **解密**钉钉桌面端 V2 加密 SQLite 数据库（AES ECB + XXTEA）
- **Web 界面**浏览全部会话和消息，支持搜索、筛选、分页
- **20+ 种消息类型**：文本、图片、文件、语音、富文本、引用、审批、互动卡片等
- **图片预览** — 包括引用消息和富文本中内嵌的图片
- **附件导出** — 图片、文档（docx/pdf/xlsx）一并打包到自包含目录
- **时间范围筛选** — 支持按近3个月/6个月/1年/2年/全部导出
- **ZIP 打包下载** — 一键下载包含所有附件的压缩包
- **AI 友好格式** — 导出 JSON 包含统一的 `content` 字段，内联附件路径，方便 AI 工具处理
- **自动同步** — 每4小时自动增量同步新消息
- **完全离线** — 无需云端 API、无需钉钉开放平台 Token、无需网络请求

## 快速开始

### 环境要求

- Python 3.10+
- 钉钉桌面客户端已安装并登录过（用于生成本地数据）
- [dingwave](https://github.com/p1g3/dingwave) 解密工具（放入 `tools/` 目录）

### 安装

**方式一：一键安装（推荐）**

Windows 用户双击 `setup.bat`，Linux/Mac 用户运行 `./setup.sh`。

**方式二：手动安装**

```bash
# 克隆仓库
git clone https://github.com/abbr530/dingtalk-exporter.git
cd dingtalk-exporter

# 安装 Python 依赖
pip install -r requirements.txt

# 下载 dingwave 解密工具
# 从 https://github.com/p1g3/dingwave/releases 下载对应平台的二进制文件
# 放入 tools/ 目录（Windows: dingwave.exe，Linux/Mac: dingwave）
```

### 运行

```bash
python main.py
```

浏览器访问 http://localhost:8090

> **无需配置** — 工具自动扫描 `%APPDATA%\DingTalk\`、`%LOCALAPPDATA%\DingTalk\` 等多个目录查找 `*_v2` 用户数据。如果同一台电脑有多个钉钉账号，会自动选择最近使用的那个。

### 手动配置（仅在自动检测失败时需要）

如果工具无法自动找到钉钉数据，可以设置环境变量：

```bash
set DINGTALK_UID=123456789
set DINGTALK_DATA_DIR=C:\Users\用户名\AppData\Roaming\DingTalk\123456789_v2
python main.py
```

或直接修改 `config.py` 中的默认值。

## 工作原理

1. 复制加密数据库（`dingtalk.db`）到临时目录，避免锁冲突
2. 使用 [dingwave](https://github.com/p1g3/dingwave) 以用户 UID 为密钥解密数据库
3. 读取解密后的 SQLite 数据库（128 个分片消息表：`tbmsg_000`–`tbmsg_127`）
4. 通过 `im_image_info` 表和 `resource_cache/` 目录解析本地图片路径
5. 启动 FastAPI Web 应用，提供浏览、搜索和导出功能

## 使用说明

| 功能 | 操作 |
|------|------|
| 浏览会话 | 左侧会话列表向下滚动自动加载更多 |
| 查看消息 | 点击会话名，右侧显示消息流，最新消息在底部 |
| 搜索消息 | 顶部搜索栏输入关键词回车 |
| 搜索会话 | 左侧搜索栏按名称搜索 |
| 筛选会话 | 左侧"全部/群聊/单聊"Tab切换 |
| 查看图片 | 消息中的图片可直接预览，点击放大 |
| 手动同步 | 点击右上角"手动同步"按钮 |
| 勾选导出 | 点击"导出" → 勾选会话 → 选择时间范围 → "导出选中会话" |
| 全量导出 | 点击"导出" → "已导出文件"Tab → "全量导出所有会话" |
| 下载导出 | "已导出文件"Tab中点击"下载 ZIP" |

## 导出格式

每次导出生成一个自包含目录：

```
export_20260417_191234/
├── export.json            # 消息数据，附件使用相对路径引用
├── images/                # 所有导出的图片
│   ├── 12345_67890.jpg
│   └── 12345_67891_0.webp
└── files/                 # 所有导出的文档
    ├── report_v1.0.docx
    └── datasheet.pdf
```

`export.json` 中的 `content` 字段整合了文本和附件引用：

```json
{
  "sender_name": "用户A",
  "content": "[图片: images/12345_67890.jpg]\n一些描述文字",
  "image_export_paths": ["images/12345_67890.jpg"]
}
```

此格式专为 AI 工具设计 — `content` 字段提供统一可读字符串，附件路径为相对路径。

## 项目结构

```
dingtalk-exporter/
├── config.py          # 配置（自动检测 + 路径、常量）
├── main.py            # 入口文件（uvicorn 服务器）
├── decrypt.py         # 数据库解密（复制 + dingwave）
├── parser.py          # 消息解析（SQLite → 结构化数据）
├── exporter.py        # 导出为 JSON + 附件打包
├── attachment.py      # 附件文件管理
├── scheduler.py       # 自动同步定时任务（APScheduler）
├── setup.bat          # Windows 一键安装脚本
├── setup.sh           # Linux/Mac 一键安装脚本
├── requirements.txt   # Python 依赖
├── tools/
│   └── dingwave.exe   # 解密工具（需单独下载）
└── web/
    ├── api.py         # FastAPI 路由
    └── static/        # 前端页面（HTML/CSS/JS）
```

## 技术细节

- **数据库加密**：AES ECB + XXTEA，密钥为用户 UID
- **消息分片**：128 个表（`tbmsg_000`–`tbmsg_127`），按哈希路由
- **图片解析**：`im_image_info` 表将消息 ID 映射到 `resource_cache/` 中的本地文件路径
- **引用/富文本图片**：通过 `im_image_info` 多行查找，解析每条消息的多张图片
- **文件附件**：从消息 JSON 的 `content.attachments[].filepath` 中提取本地路径
- **并发访问**：解密前将数据库复制到临时目录，避免 WAL 锁冲突

## 已知限制

- 仅支持 V2 加密格式的数据库（`_v2` 文件夹后缀）
- 仅包含钉钉桌面客户端缓存的数据 — 从未在该设备上查看过的消息可能缺失
- 未缓存到本地的图片会显示占位符
- 需要在使用过钉钉桌面客户端的电脑上运行

## 致谢

- [dingwave](https://github.com/p1g3/dingwave) — 钉钉数据库解密工具
- [FastAPI](https://fastapi.tiangolo.com/) — Web 框架
- [APScheduler](https://apscheduler.readreadocs.io/) — 定时任务

## 许可证

[MIT](LICENSE)
