# Curl Client

A lightweight desktop app for sending HTTP requests by pasting curl commands — supports both **Windows** (`^`) and **Mac** (`\`) curl formats.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS-lightgrey) ![License](https://img.shields.io/badge/License-MIT-green)

---

## Features

- Paste curl commands copied directly from browser DevTools (Windows or Mac format)
- Automatic format detection — no manual conversion needed
- URLs with `?`, `&`, `%`-encoded chars preserved as-is
- JSON responses are pretty-printed automatically
- Request history (last 12) with method badge, status code, and relative timestamp
  - Click any history entry to reload it into the input
  - Hover to see the full URL tooltip
  - `×` button to delete individual entries
  - **Clear all** to wipe history
- Response viewer with three tabs: **Body**, **Response Headers**, **Parsed Request**
- **Find bar** — search text inside any response tab (Ctrl+F, ◀ ▶ navigation, match counter)
- Right-click context menu (Cut / Copy / Paste / Select All)
- Keyboard shortcuts:
  | Shortcut | Action |
  |---|---|
  | `Ctrl + Enter` | Send request |
  | `Ctrl + F` | Focus find bar |
  | `Ctrl + A` | Select all |
  | `Ctrl + C` | Copy |
  | `Ctrl + V` | Paste |
  | `Ctrl + X` | Cut |
  | `Ctrl + Z / Y` | Undo / Redo |

---

## Supported curl options

| Option | Description |
|---|---|
| `-H / --header` | Custom request headers |
| `-X / --request` | HTTP method (GET, POST, PUT, PATCH, DELETE, HEAD) |
| `-d / --data` | Raw request body (`--data-raw`, `--data-binary`, etc.) |
| `-F / --form` | Multipart form-data fields; `@path` uploads a file |
| `-u / --user` | Basic auth (`user:password`) |
| `-b / --cookie` | Send cookies (`"name=value; name2=value2"`) |
| `-c / --cookie-jar` | Save response cookies to a file |
| `-L / --location` | Follow redirects (off by default, matching curl) |
| `-k / --insecure` | Skip SSL certificate verification |
| `--compressed` | Request gzip/deflate/br encoding |
| `--proxy / -x` | HTTP/HTTPS/SOCKS proxy URL |
| `--max-time / -m` | Total request timeout in seconds |
| `--connect-timeout` | Connection timeout in seconds |
| `-I / --head` | HEAD request |
| `-G / --get` | Force GET method |

---

## Supported curl formats

**Windows (CMD)**
```
curl "https://api.example.com/data" ^
  -H "accept: application/json" ^
  -H "authorization: Bearer TOKEN" ^
  -d "{\"key\": \"value\"}"
```

**Mac / Linux**
```bash
curl 'https://api.example.com/data' \
  -H 'accept: application/json' \
  -H 'authorization: Bearer TOKEN' \
  -d '{"key": "value"}'
```

---

## Getting started

### Run from source

```bash
pip install requests
python curl_client.py
```

### Build on Windows

```bash
pip install requests pyinstaller
pyinstaller --onefile --windowed --name CurlClient --clean curl_client.py
# Output: dist/CurlClient.exe
```

Or just double-click **`run.bat`** — it installs dependencies and launches the app.

### Build on macOS

```bash
bash build_mac.sh
# Output: dist/CurlClient
```

---

## Requirements

- Python 3.10+
- `requests` library (`pip install requests`)
- `tkinter` (included with standard Python on Windows; on macOS: `brew install python-tk`)
