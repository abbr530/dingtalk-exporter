# DingTalk Chat Exporter

Export and browse DingTalk (钉钉) desktop chat history from the local encrypted database.

> **Disclaimer**: This tool is for personal data backup only. Please comply with your company's data policies and local regulations.

## Features

- **Decrypt** the DingTalk desktop V2 encrypted SQLite database (AES ECB + XXTEA)
- **Web UI** to browse all conversations and messages with search, filter, and pagination
- **20+ message types** supported: text, image, file, voice, rich text, quote, approval, interactive card, etc.
- **Image preview** — including images embedded in quote/rich-text messages
- **Export with attachments** — images, documents (docx/pdf/xlsx) are copied into a self-contained directory
- **Time range filter** — export messages from the last 3 months, 6 months, 1 year, 2 years, or all time
- **ZIP download** — one-click download of exported data with all attachments
- **AI-friendly format** — exported JSON includes a unified `content` field with inline attachment paths, ready for AI tools to process
- **Auto-sync** — periodic incremental sync every 4 hours via APScheduler
- **Fully offline** — no cloud API, no DingTalk open platform token, no network requests required

## How It Works

1. Copies the encrypted database (`dingtalk.db`) from the DingTalk data directory to avoid lock conflicts
2. Uses [dingwave](https://github.com/p1g3/dingwave) to decrypt the database with the user's UID as the key
3. Reads the decrypted SQLite database (128 sharded message tables: `tbmsg_000`–`tbmsg_127`)
4. Resolves local image paths via the `im_image_info` table and `resource_cache/` directory
5. Serves a FastAPI web application for browsing, searching, and exporting

## Quick Start

### Prerequisites

- Python 3.10+
- DingTalk desktop client installed and logged in (to generate local data)
- [dingwave](https://github.com/p1g3/dingwave) binary in `tools/` directory

### Setup

```bash
# Clone the repository
git clone https://github.com/<your-username>/dingtalk-exporter.git
cd dingtalk-exporter

# Install dependencies
pip install -r requirements.txt

# Download dingwave
# Get it from https://github.com/p1g3/dingwave/releases
# Place dingwave.exe (Windows) or dingwave (Linux/Mac) in tools/
```

### Run

```bash
python main.py
```

Open http://localhost:8090 in your browser.

> **No configuration needed** — the tool auto-detects your DingTalk user data by scanning `%APPDATA%\DingTalk\` for `*_v2` directories. If you have multiple DingTalk accounts on this machine, it picks the one with the most recently modified database.

### Configure (only if auto-detection fails)

If the tool cannot find your DingTalk data automatically, set environment variables:

```bash
set DINGTALK_UID=123456789
set DINGTALK_DATA_DIR=C:\Users\YourName\AppData\Roaming\DingTalk\123456789_v2
python main.py
```

Or edit the defaults in `config.py`.

## Usage

| Feature | How |
|---------|-----|
| Browse conversations | Scroll the sidebar to load more |
| View messages | Click a conversation — messages shown chronologically, newest at bottom |
| Search messages | Use the search bar at the top |
| Filter conversations | Use "All / Group / Single" tabs or search by name |
| View images | Click any image to enlarge (lightbox) |
| Manual sync | Click the "Sync" button |
| Export selected | Click "Export" → check conversations → select time range → "Export" |
| Full export | Click "Export" → "Exported Files" tab → "Full Export" |
| Download export | Click "Download ZIP" in the exported files list |

## Export Format

Each export is a self-contained directory:

```
export_20260417_191234/
├── export.json            # Message data with relative attachment paths
├── images/                # All exported images
│   ├── 12345_67890.jpg
│   └── 12345_67891_0.webp
└── files/                 # All exported documents
    ├── report_v1.0.docx
    └── datasheet.pdf
```

The `content` field in `export.json` integrates text and attachment references:

```json
{
  "sender_name": "UserA",
  "content": "[图片: images/12345_67890.jpg]\nsome description",
  "image_export_paths": ["images/12345_67890.jpg"]
}
```

This format is designed to be AI-agent-friendly — the `content` field provides a unified, readable string with inline attachment paths.

## Architecture

```
dingtalk-exporter/
├── config.py          # Configuration (auto-detection + paths, constants)
├── main.py            # Entry point (uvicorn server)
├── decrypt.py         # Database decryption (copy + dingwave)
├── parser.py          # Message parsing (SQLite → structured dicts)
├── exporter.py        # Export to JSON with attachments
├── attachment.py      # Attachment file management
├── scheduler.py       # Auto-sync scheduler (APScheduler)
├── tools/
│   └── dingwave.exe   # Decryption tool (not included — download separately)
└── web/
    ├── api.py         # FastAPI routes
    └── static/        # Frontend (HTML/CSS/JS)
```

## Technical Details

- **Database encryption**: AES ECB + XXTEA, key derived from user UID
- **Message sharding**: 128 tables (`tbmsg_000`–`tbmsg_127`), routed by hash
- **Image resolution**: `im_image_info` table maps message IDs to local file paths in `resource_cache/`
- **Quote/rich-text images**: Multiple images per message resolved via `im_image_info` multi-row lookup
- **File attachments**: Local paths extracted from `content.attachments[].filepath` in raw message JSON
- **Concurrent access**: Database copied to temp directory before decryption to avoid WAL lock conflicts

## Limitations

- Only V2 encrypted databases are supported (`_v2` folder suffix)
- Only data cached by the DingTalk desktop client is available — messages never viewed on this device may be missing
- Images not cached locally will show a placeholder
- The tool must run on a machine where DingTalk desktop has been used

## Credits

- [dingwave](https://github.com/p1g3/dingwave) — DingTalk database decryption tool
- [FastAPI](https://fastapi.tiangolo.com/) — Web framework
- [APScheduler](https://apscheduler.readthedocs.io/) — Job scheduling

## License

[MIT](LICENSE)
