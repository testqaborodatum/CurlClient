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
- Right-click context menu (Cut / Copy / Paste / Select All)
- Keyboard shortcuts:
  | Shortcut | Action |
  |---|---|
  | `Ctrl/Cmd + Enter` | Send request |
  | `Ctrl/Cmd + A` | Select all |
  | `Ctrl/Cmd + C` | Copy |
  | `Ctrl/Cmd + V` | Paste |
  | `Ctrl/Cmd + X` | Cut |
  | `Ctrl/Cmd + Z` | Undo |
  | `Ctrl/Cmd + Y` / `Cmd+Shift+Z` | Redo |

---

## Supported curl formats

**Windows (CMD)**
```
curl ^"https://api.example.com/data^" ^
  -H ^"accept: application/json^" ^
  -H ^"authorization: Bearer TOKEN^"
```

**Mac / Linux**
```bash
curl 'https://api.example.com/data' \
  -H 'accept: application/json' \
  -H 'authorization: Bearer TOKEN'
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
pyinstaller --onefile --windowed --name "CurlClient" curl_client.py
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
- `tkinter` (included with standard Python on Windows; on macOS install via `brew install python-tk`)
