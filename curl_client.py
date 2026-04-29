#!/usr/bin/env python3
"""
Curl Client - Desktop App
Accepts curl commands in Windows (^) and Mac (backslash) formats
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import requests
import re
import json
import shlex
import time
import os
import sys
from urllib.parse import quote_plus


# ---------------------------------------------------------------------------
# Curl parser
# ---------------------------------------------------------------------------

_SKIP_WITH_VALUE = {
    '--limit-rate',
    '-o', '--output', '-A', '--user-agent',
    '--url', '-e', '--referer',
    '--cert', '--key', '--cacert', '--capath',
    '--dns-servers', '--resolve', '--interface',
    '-T', '--upload-file', '--retry', '--retry-delay',
    '--write-out', '-w', '--max-redirs',
}

_SKIP_FLAG = {
    '-s', '-S', '--silent', '--show-error', '-v', '--verbose',
    '-i', '--include', '--http1.0', '--http1.1',
    '--http2', '--http2-prior-knowledge', '--no-keepalive',
    '-4', '--ipv4', '-6', '--ipv6', '-n', '--netrc',
    '--no-buffer', '-N',
}

def _unescape_windows(raw: str) -> str:
    """Remove Windows CMD ^ escape characters and join continuation lines."""
    joined = re.sub(r'\^\s*\r?\n\s*', ' ', raw)
    result = []
    i = 0
    while i < len(joined):
        if joined[i] == '^' and i + 1 < len(joined):
            result.append(joined[i + 1])
            i += 2
        else:
            result.append(joined[i])
            i += 1
    return ''.join(result)


def _unescape_mac(raw: str) -> str:
    """Remove Mac/Unix shell backslash continuation characters."""
    return re.sub(r'\\\s*\r?\n\s*', ' ', raw).strip()


def _is_windows_format(raw: str) -> bool:
    return bool(re.search(r'\^\s*\r?\n', raw) or '^"' in raw or "^'" in raw)


def parse_curl(raw: str) -> dict:
    raw = raw.strip()

    if _is_windows_format(raw):
        normalized = _unescape_windows(raw)
    else:
        normalized = _unescape_mac(raw)

    try:
        tokens = shlex.split(normalized, posix=True)
    except ValueError:
        safe = normalized.replace('\x00', '')
        tokens = safe.split()

    result = {
        'url': None,
        'method': 'GET',
        'headers': {},
        'data': None,
        'form': {},
        'allow_redirects': False,  # curl default: no redirects unless -L
        'verify': True,
        'cookies': {},
        'cookie_jar': None,
        'auth': None,
        'proxy': None,
        'timeout_connect': None,
        'timeout_total': None,
        'compressed': False,
        'ignored': [],
    }

    i = 0
    while i < len(tokens):
        tok = tokens[i]

        if tok == 'curl':
            i += 1
        elif tok in ('-H', '--header'):
            if i + 1 < len(tokens):
                hdr = tokens[i + 1]
                sep = hdr.find(':')
                if sep != -1:
                    name = hdr[:sep].strip()
                    value = hdr[sep + 1:].lstrip()
                    result['headers'][name] = value
                i += 2
            else:
                i += 1
        elif tok in ('-X', '--request'):
            if i + 1 < len(tokens):
                result['method'] = tokens[i + 1].upper()
                i += 2
            else:
                i += 1
        elif tok in ('-d', '--data', '--data-raw', '--data-binary', '--data-ascii'):
            if i + 1 < len(tokens):
                result['data'] = tokens[i + 1]
                if result['method'] == 'GET':
                    result['method'] = 'POST'
                i += 2
            else:
                i += 1
        elif tok == '--data-urlencode':
            if i + 1 < len(tokens):
                val = tokens[i + 1]
                eq = val.find('=')
                if eq != -1:
                    result['data'] = f"{val[:eq]}={quote_plus(val[eq + 1:])}"
                else:
                    result['data'] = quote_plus(val)
                if result['method'] == 'GET':
                    result['method'] = 'POST'
                i += 2
            else:
                i += 1
        elif tok in ('-F', '--form', '--form-string'):
            if i + 1 < len(tokens):
                kv = tokens[i + 1]
                eq = kv.find('=')
                if eq != -1:
                    result['form'][kv[:eq]] = kv[eq + 1:]
                if result['method'] == 'GET':
                    result['method'] = 'POST'
                i += 2
            else:
                i += 1
        elif tok in ('-G', '--get'):
            result['method'] = 'GET'
            i += 1
        elif tok in ('-L', '--location', '--location-trusted'):
            result['allow_redirects'] = True
            i += 1
        elif tok in ('-u', '--user'):
            if i + 1 < len(tokens):
                creds = tokens[i + 1]
                colon = creds.find(':')
                if colon != -1:
                    result['auth'] = (creds[:colon], creds[colon + 1:])
                else:
                    result['auth'] = (creds, '')
                i += 2
            else:
                i += 1
        elif tok in ('-b', '--cookie'):
            if i + 1 < len(tokens):
                for part in tokens[i + 1].split(';'):
                    part = part.strip()
                    eq = part.find('=')
                    if eq != -1:
                        result['cookies'][part[:eq].strip()] = part[eq + 1:].strip()
                i += 2
            else:
                i += 1
        elif tok in ('-c', '--cookie-jar'):
            if i + 1 < len(tokens):
                result['cookie_jar'] = tokens[i + 1]
                i += 2
            else:
                i += 1
        elif tok in ('--proxy', '-x'):
            if i + 1 < len(tokens):
                result['proxy'] = tokens[i + 1]
                i += 2
            else:
                i += 1
        elif tok == '--connect-timeout':
            if i + 1 < len(tokens):
                try:
                    result['timeout_connect'] = float(tokens[i + 1])
                except ValueError:
                    pass
                i += 2
            else:
                i += 1
        elif tok in ('--max-time', '-m'):
            if i + 1 < len(tokens):
                try:
                    result['timeout_total'] = float(tokens[i + 1])
                except ValueError:
                    pass
                i += 2
            else:
                i += 1
        elif tok == '--compressed':
            result['compressed'] = True
            i += 1
        elif tok in ('-k', '--insecure'):
            result['verify'] = False
            i += 1
        elif tok in ('-I', '--head'):
            result['method'] = 'HEAD'
            i += 1
        elif tok in _SKIP_FLAG:
            result['ignored'].append(tok)
            i += 1
        elif tok in _SKIP_WITH_VALUE:
            if i + 1 < len(tokens):
                result['ignored'].append(f'{tok} {tokens[i + 1]}')
                i += 2
            else:
                result['ignored'].append(tok)
                i += 1
        elif tok.startswith('http://') or tok.startswith('https://'):
            result['url'] = tok
            i += 1
        elif tok.startswith('--') or (tok.startswith('-') and len(tok) == 2):
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ''
            if (nxt and not nxt.startswith('-')
                    and not nxt.startswith('http://') and not nxt.startswith('https://')):
                result['ignored'].append(f'{tok} {nxt}')
                i += 2
            else:
                result['ignored'].append(tok)
                i += 1
        else:
            if result['url'] is None:
                result['url'] = tok
            i += 1

    return result


# ---------------------------------------------------------------------------
# HTTP execution
# ---------------------------------------------------------------------------

def execute_request(parsed: dict) -> dict:
    if not parsed['url']:
        raise ValueError("No URL found in curl command")

    tc = parsed.get('timeout_connect') or 30
    tt = parsed.get('timeout_total') or 30

    headers = dict(parsed['headers'])
    if parsed.get('compressed') and 'Accept-Encoding' not in headers:
        headers['Accept-Encoding'] = 'gzip, deflate, br'

    kwargs = {
        'headers': headers,
        'allow_redirects': parsed['allow_redirects'],
        'verify': parsed['verify'],
        'timeout': (tc, tt),
    }

    if parsed.get('auth'):
        kwargs['auth'] = parsed['auth']

    if parsed.get('cookies'):
        kwargs['cookies'] = parsed['cookies']

    if parsed.get('proxy'):
        kwargs['proxies'] = {'http': parsed['proxy'], 'https': parsed['proxy']}

    opened_files = []
    if parsed['data'] is not None:
        kwargs['data'] = parsed['data'].encode('utf-8') if isinstance(parsed['data'], str) else parsed['data']
    elif parsed['form']:
        files = {}
        for k, v in parsed['form'].items():
            if isinstance(v, str) and v.startswith('@'):
                filepath = v[1:]
                try:
                    fh = open(filepath, 'rb')
                    opened_files.append(fh)
                    files[k] = (os.path.basename(filepath), fh)
                except OSError:
                    files[k] = (None, v)
            else:
                files[k] = (None, v)
        kwargs['files'] = files

    with requests.Session() as session:
        start = time.time()
        try:
            resp = session.request(parsed['method'], parsed['url'], **kwargs)
        finally:
            for fh in opened_files:
                fh.close()
        elapsed = time.time() - start

    if parsed.get('cookie_jar'):
        try:
            with open(parsed['cookie_jar'], 'w', encoding='utf-8') as f:
                json.dump(dict(resp.cookies), f, indent=2)
        except Exception:
            pass

    content_type = resp.headers.get('content-type', '')
    try:
        body = resp.text
    except Exception:
        body = resp.content.decode('utf-8', errors='replace')

    return {
        'status_code': resp.status_code,
        'status_text': resp.reason or '',
        'elapsed_ms': round(elapsed * 1000),
        'headers': dict(resp.headers),
        'body': body,
        'content_type': content_type,
        'final_url': str(resp.url),
        'size': len(resp.content),
    }


# ---------------------------------------------------------------------------
# History persistence
# ---------------------------------------------------------------------------

HISTORY_MAX = 12


def _history_path() -> str:
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'history.json')


def load_history() -> list:
    try:
        with open(_history_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(entries: list):
    try:
        with open(_history_path(), 'w', encoding='utf-8') as f:
            json.dump(entries[:HISTORY_MAX], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _relative_time(ts: float) -> str:
    diff = time.time() - ts
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff // 60)}m ago"
    if diff < 86400:
        return f"{int(diff // 3600)}h ago"
    return time.strftime('%b %d', time.localtime(ts))


# ---------------------------------------------------------------------------
# Tooltip
# ---------------------------------------------------------------------------

class Tooltip:
    """Hover tooltip.

    Key design: moving the mouse between child widgets inside the same card
    fires Leave+Enter pairs rapidly. We avoid resetting the show timer on
    every such pair by:
      - Only starting the timer once per hover session (guarded by _show_id).
      - On Leave, deferring the hide by 40 ms and checking whether the mouse
        is still inside the item bounding box before actually cancelling.
    """

    DELAY_MS = 550

    def __init__(self, text: str, item: tk.Widget):
        self._text = text
        self._item = item   # outermost frame — used as bounds reference
        self._tip: tk.Toplevel | None = None
        self._show_id = None
        self._hide_id = None

    def bind_all(self, widgets):
        for w in widgets:
            w.bind('<Enter>',  self._on_enter,  add='+')
            w.bind('<Leave>',  self._on_leave,  add='+')
            w.bind('<Motion>', self._on_motion, add='+')

    # ---- event handlers ----

    def _on_enter(self, _event):
        self._cancel_hide()
        if self._show_id is None:           # don't restart if already counting
            self._show_id = self._item.after(self.DELAY_MS, self._show)

    def _on_motion(self, event):
        if self._tip:
            self._tip.wm_geometry(f'+{event.x_root + 14}+{event.y_root + 20}')

    def _on_leave(self, _event):
        # Defer: if mouse moved to a sibling child, an Enter fires within ~1 ms
        # and _cancel_hide() prevents this from doing anything.
        self._hide_id = self._item.after(40, self._deferred_hide)

    # ---- internals ----

    def _deferred_hide(self):
        self._hide_id = None
        if not self._mouse_in_item():
            self._cancel_show()
            self._hide()

    def _mouse_in_item(self) -> bool:
        try:
            rx = self._item.winfo_rootx()
            ry = self._item.winfo_rooty()
            rw = self._item.winfo_width()
            rh = self._item.winfo_height()
            mx = self._item.winfo_pointerx()
            my = self._item.winfo_pointery()
            return rx <= mx <= rx + rw and ry <= my <= ry + rh
        except tk.TclError:
            return False

    def _show(self):
        self._show_id = None
        if not self._mouse_in_item():
            return
        self._hide()
        mx = self._item.winfo_pointerx()
        my = self._item.winfo_pointery()
        self._tip = tk.Toplevel()
        self._tip.wm_overrideredirect(True)
        self._tip.wm_attributes('-topmost', True)
        self._tip.wm_geometry(f'+{mx + 14}+{my + 20}')
        outer = tk.Frame(self._tip, bg='#45475a', padx=1, pady=1)
        outer.pack()
        tk.Label(outer, text=self._text, bg='#313244', fg=TEXT,
                 font=FONT_SM, padx=10, pady=6,
                 justify=tk.LEFT, wraplength=540).pack()

    def _hide(self):
        if self._tip:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None

    def _cancel_show(self):
        if self._show_id:
            try:
                self._item.after_cancel(self._show_id)
            except tk.TclError:
                pass
            self._show_id = None

    def _cancel_hide(self):
        if self._hide_id:
            try:
                self._item.after_cancel(self._hide_id)
            except tk.TclError:
                pass
            self._hide_id = None

    def close(self):
        """Call when the owner widget is destroyed so the popup doesn't linger."""
        self._cancel_show()
        self._cancel_hide()
        self._hide()


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

BG       = '#1e1e2e'
PANEL    = '#24273a'
INPUT_BG = '#181825'
ACCENT   = '#89b4fa'
TEXT     = '#cdd6f4'
SUBTEXT  = '#a6adc8'
GREEN    = '#a6e3a1'
RED      = '#f38ba8'
YELLOW   = '#f9e2af'
FONT_MONO = ('Cascadia Code', 10)
FONT_UI   = ('Segoe UI', 10)
FONT_UI_B = ('Segoe UI', 10, 'bold')
FONT_H    = ('Segoe UI', 11, 'bold')
FONT_SM   = ('Segoe UI', 9)

METHOD_COLORS = {
    'GET':    '#74c7ec',  # sky blue — distinct from dark background
    'POST':   '#a6e3a1',
    'PUT':    '#fab387',
    'PATCH':  '#f9e2af',
    'DELETE': '#f38ba8',
    'HEAD':   '#cba6f7',
}


class CurlApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Curl Client")
        self.root.geometry("1400x860")
        self.root.configure(bg=BG)
        self.root.minsize(900, 600)

        self._history: list = load_history()
        self._history_frames: list = []
        self._has_placeholder: bool = False

        self._setup_styles()
        self._build_ui()
        self._insert_placeholder()
        self._refresh_history_ui()

    # ------------------------------------------------------------------
    # Styles
    # ------------------------------------------------------------------

    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use('clam')

        s.configure('TFrame',         background=BG)
        s.configure('Panel.TFrame',   background=PANEL)
        s.configure('TLabel',         background=BG,    foreground=TEXT,    font=FONT_UI)
        s.configure('Sub.TLabel',     background=BG,    foreground=SUBTEXT, font=FONT_UI)
        s.configure('Panel.TLabel',   background=PANEL, foreground=TEXT,    font=FONT_UI)
        s.configure('Header.TLabel',  background=PANEL, foreground=ACCENT,  font=FONT_H)

        s.configure('Send.TButton',
                    background=ACCENT, foreground='#1e1e2e',
                    font=FONT_UI_B, padding=(16, 8), relief='flat',
                    focuscolor=ACCENT)
        s.map('Send.TButton',
              background=[('active', '#b4befe'), ('pressed', '#7287fd'), ('disabled', '#45475a')],
              focuscolor=[('disabled', '#45475a'), ('pressed', '#7287fd'),
                          ('active', '#b4befe'), ('!focus', ACCENT)])

        s.configure('Clear.TButton',
                    background='#45475a', foreground=TEXT,
                    font=FONT_UI, padding=(12, 8), relief='flat',
                    focuscolor='#45475a')
        s.map('Clear.TButton',
              background=[('active', '#585b70'), ('pressed', '#313244')],
              focuscolor=[('pressed', '#313244'), ('active', '#585b70'), ('!focus', '#45475a')])

        s.configure('TNotebook',     background=BG, borderwidth=0)
        s.configure('TNotebook.Tab', background=BG, foreground=SUBTEXT,
                    padding=(10, 4), font=FONT_SM, focuscolor=BG)
        s.map('TNotebook.Tab',
              background=[('selected', ACCENT)],
              foreground=[('selected', '#1e1e2e')],
              padding=[('selected', (16, 9))],
              font=[('selected', FONT_UI_B)],
              focuscolor=[('selected', ACCENT), ('!selected', BG)])

        s.configure('TScrollbar',
                    background='#313244', troughcolor=INPUT_BG,
                    arrowcolor=SUBTEXT, relief='flat')

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        outer = ttk.Frame(self.root)
        outer.pack(fill=tk.BOTH, expand=True)

        # ---- Header bar ----
        header = tk.Frame(outer, bg='#11111b', height=44)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="  Curl Client", bg='#11111b', fg=ACCENT,
                 font=('Segoe UI', 13, 'bold')).pack(side=tk.LEFT, padx=8)
        tk.Label(header, text="Supports Windows (^) and Mac (\\) formats",
                 bg='#11111b', fg=SUBTEXT, font=FONT_UI).pack(side=tk.LEFT, padx=4)

        # ---- Body: history sidebar + main ----
        body = tk.Frame(outer, bg=BG)
        body.pack(fill=tk.BOTH, expand=True)

        # History sidebar (left, fixed width)
        self._build_history_panel(body)

        # Thin separator
        tk.Frame(body, bg='#313244', width=1).pack(side=tk.LEFT, fill=tk.Y)

        # Main content (right)
        self._build_main(body)

    def _build_history_panel(self, parent: tk.Frame):
        sidebar = tk.Frame(parent, bg=PANEL, width=230)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        # Header row
        hdr = tk.Frame(sidebar, bg=PANEL)
        hdr.pack(fill=tk.X, padx=12, pady=(12, 6))
        tk.Label(hdr, text="History", bg=PANEL, fg=ACCENT, font=FONT_H).pack(side=tk.LEFT)

        clear_all = tk.Label(hdr, text="Clear all", bg=PANEL, fg=SUBTEXT,
                             font=FONT_SM, cursor='hand2')
        clear_all.pack(side=tk.RIGHT, pady=2)
        clear_all.bind('<Enter>', lambda e: clear_all.config(fg=RED))
        clear_all.bind('<Leave>', lambda e: clear_all.config(fg=SUBTEXT))
        clear_all.bind('<Button-1>', lambda e: self._clear_history_all())

        tk.Frame(sidebar, bg='#313244', height=1).pack(fill=tk.X, padx=8)

        # Scrollable container
        wrap = tk.Frame(sidebar, bg=PANEL)
        wrap.pack(fill=tk.BOTH, expand=True, pady=4)

        self._hist_canvas = tk.Canvas(wrap, bg=PANEL, highlightthickness=0, bd=0)
        self._hist_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._hist_container = tk.Frame(self._hist_canvas, bg=PANEL)
        self._hist_win = self._hist_canvas.create_window(
            (0, 0), window=self._hist_container, anchor='nw')

        def _on_inner_resize(_e):
            self._hist_canvas.update_idletasks()
            bbox = self._hist_canvas.bbox('all')
            if bbox:
                self._hist_canvas.configure(scrollregion=(0, 0, bbox[2], bbox[3]))

        def _on_canvas_resize(e):
            self._hist_canvas.itemconfig(self._hist_win, width=e.width)

        self._hist_container.bind('<Configure>', _on_inner_resize)
        self._hist_canvas.bind('<Configure>', _on_canvas_resize)

        # Mousewheel scroll — check bounds first to prevent overscroll
        def _wheel(e):
            delta = int(-1 * (e.delta / 120))
            top, bottom = self._hist_canvas.yview()
            if delta < 0 and top <= 0.0:
                return
            if delta > 0 and bottom >= 1.0:
                return
            self._hist_canvas.yview_scroll(delta, 'units')

        self._hist_canvas.bind('<MouseWheel>', _wheel)
        self._hist_container.bind('<MouseWheel>', _wheel)
        self._hist_wheel_fn = _wheel   # saved so history items can bind it too

    def _build_main(self, parent: tk.Frame):
        content = tk.Frame(parent, bg=BG)
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=14, pady=10)

        # Input section
        in_frame = tk.Frame(content, bg=PANEL)
        in_frame.pack(fill=tk.X, pady=(0, 8))

        top_row = tk.Frame(in_frame, bg=PANEL)
        top_row.pack(fill=tk.X, padx=12, pady=(10, 4))
        tk.Label(top_row, text="Curl Command", bg=PANEL, fg=ACCENT, font=FONT_H).pack(side=tk.LEFT)
        tk.Label(top_row, text="Ctrl+Enter to send", bg=PANEL, fg=SUBTEXT, font=FONT_UI).pack(side=tk.RIGHT)

        self.curl_input = scrolledtext.ScrolledText(
            in_frame, height=10, wrap=tk.NONE,
            bg=INPUT_BG, fg=TEXT, insertbackground=ACCENT,
            font=FONT_MONO, relief=tk.FLAT,
            selectbackground=ACCENT, selectforeground='#1e1e2e',
            borderwidth=0, padx=10, pady=8,
            undo=True, maxundo=-1,
        )
        self.curl_input.pack(fill=tk.BOTH, expand=True, padx=2, pady=(0, 2))
        self.curl_input.bind('<Control-Return>', lambda _e: self._send())
        self.curl_input.bind('<FocusIn>',  self._on_focus_in)
        self.curl_input.bind('<<Paste>>',  self._on_paste)
        self.curl_input.bind('<Button-3>', self._show_context_menu)
        # Select-all: Ctrl+A (Windows/Linux) and Cmd+A / Meta+A (macOS)
        for _seq in ('<Control-a>', '<Control-A>',
                     '<Command-a>', '<Command-A>',
                     '<Meta-a>',   '<Meta-A>'):
            self.curl_input.bind(_seq, lambda e: self._select_all(self.curl_input))
        # Redo: Ctrl+Y (Windows/Linux) and Cmd+Y / Cmd+Shift+Z (macOS)
        for _seq in ('<Control-y>', '<Control-Y>',
                     '<Command-y>', '<Command-Y>',
                     '<Meta-y>',   '<Meta-Y>',
                     '<Command-Shift-z>', '<Command-Shift-Z>',
                     '<Meta-Shift-z>',   '<Meta-Shift-Z>'):
            self.curl_input.bind(_seq, lambda e: (self.curl_input.edit_redo(), 'break')[-1])

        # Button row
        btn_row = tk.Frame(content, bg=BG)
        btn_row.pack(fill=tk.X, pady=(0, 8))

        self.send_btn = ttk.Button(btn_row, text="Send Request", style='Send.TButton',
                                   command=self._send)
        self.send_btn.pack(side=tk.LEFT)

        ttk.Button(btn_row, text="Clear", style='Clear.TButton',
                   command=self._clear).pack(side=tk.LEFT, padx=8)

        status_row = tk.Frame(btn_row, bg=BG)
        status_row.pack(side=tk.LEFT, padx=4)
        self.status_dot = tk.Label(status_row, text="●", bg=BG, fg=SUBTEXT, font=('Segoe UI', 12))
        self.status_dot.pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="Ready")
        self.status_label = tk.Label(status_row, textvariable=self.status_var,
                                     bg=BG, fg=SUBTEXT, font=FONT_UI)
        self.status_label.pack(side=tk.LEFT, padx=4)

        # Response section
        resp_outer = tk.Frame(content, bg=PANEL)
        resp_outer.pack(fill=tk.BOTH, expand=True)

        resp_hdr = tk.Frame(resp_outer, bg=PANEL)
        resp_hdr.pack(fill=tk.X, padx=12, pady=(10, 4))
        tk.Label(resp_hdr, text="Response", bg=PANEL, fg=ACCENT, font=FONT_H).pack(side=tk.LEFT)

        # Search bar
        self._search_matches: list = []
        self._search_idx: int = 0

        search_frame = tk.Frame(resp_hdr, bg=PANEL)
        search_frame.pack(side=tk.RIGHT)

        tk.Label(search_frame, text="Find:", bg=PANEL, fg=SUBTEXT, font=FONT_SM).pack(
            side=tk.LEFT, padx=(0, 4))

        self._search_var = tk.StringVar()
        self._search_var.trace_add('write', lambda *_: self._search_update())
        self._search_entry = tk.Entry(
            search_frame, textvariable=self._search_var,
            bg=INPUT_BG, fg=TEXT, insertbackground=ACCENT,
            font=FONT_SM, relief=tk.FLAT, width=22,
            selectbackground=ACCENT, selectforeground='#1e1e2e',
        )
        self._search_entry.pack(side=tk.LEFT, ipady=4, padx=(0, 4))

        for symbol, cmd in (('◀', '_search_prev'), ('▶', '_search_next')):
            btn = tk.Label(search_frame, text=symbol, bg=PANEL, fg=SUBTEXT,
                           font=FONT_SM, cursor='hand2')
            btn.pack(side=tk.LEFT, padx=1)
            btn.bind('<Button-1>', lambda _e, m=cmd: getattr(self, m)())
            btn.bind('<Enter>', lambda e, w=btn: w.config(fg=ACCENT))
            btn.bind('<Leave>', lambda e, w=btn: w.config(fg=SUBTEXT))

        self._search_count_lbl = tk.Label(search_frame, text="", bg=PANEL,
                                          fg=SUBTEXT, font=FONT_SM, width=8, anchor='w')
        self._search_count_lbl.pack(side=tk.LEFT, padx=(4, 0))

        self._notebook = ttk.Notebook(resp_outer)
        self._notebook.pack(fill=tk.BOTH, expand=True, padx=2, pady=(0, 2))
        self._notebook.bind('<<NotebookTabChanged>>', lambda _e: self._search_update())

        self.body_text    = self._make_tab(self._notebook, 'Body')
        self.headers_text = self._make_tab(self._notebook, 'Response Headers')
        self.req_text     = self._make_tab(self._notebook, 'Parsed Request')

        for widget in (self.body_text, self.headers_text, self.req_text):
            self._bind_readonly_keys(widget)

        # Ctrl+F focuses search entry
        for seq in ('<Control-f>', '<Control-F>'):
            self.root.bind(seq, lambda _e: self._search_entry.focus_set())

    # ------------------------------------------------------------------
    # Search in response
    # ------------------------------------------------------------------

    def _active_tab_widget(self):
        try:
            idx = self._notebook.index(self._notebook.select())
            return (self.body_text, self.headers_text, self.req_text)[idx]
        except Exception:
            return self.body_text

    def _search_clear_tags(self, widget):
        widget.config(state=tk.NORMAL)
        widget.tag_remove('search_match',   '1.0', tk.END)
        widget.tag_remove('search_current', '1.0', tk.END)
        widget.config(state=tk.DISABLED)

    def _search_update(self):
        term = self._search_var.get()
        self._search_matches = []
        self._search_idx = 0

        for w in (self.body_text, self.headers_text, self.req_text):
            self._search_clear_tags(w)

        if not term:
            self._search_count_lbl.config(text='')
            return

        widget = self._active_tab_widget()
        widget.config(state=tk.NORMAL)
        widget.tag_config('search_match',   background='#f9e2af', foreground='#1e1e2e')
        widget.tag_config('search_current', background=ACCENT,    foreground='#1e1e2e')

        start = '1.0'
        while True:
            pos = widget.search(term, start, tk.END, nocase=True)
            if not pos:
                break
            end = f'{pos}+{len(term)}c'
            widget.tag_add('search_match', pos, end)
            self._search_matches.append(pos)
            start = end

        widget.config(state=tk.DISABLED)

        if self._search_matches:
            self._search_count_lbl.config(text=f'1/{len(self._search_matches)}')
            self._search_jump(0)
        else:
            self._search_count_lbl.config(text='No match')

    def _search_jump(self, idx: int):
        widget = self._active_tab_widget()
        if not self._search_matches:
            return
        term = self._search_var.get()
        widget.config(state=tk.NORMAL)
        widget.tag_remove('search_current', '1.0', tk.END)
        pos = self._search_matches[idx]
        widget.tag_add('search_current', pos, f'{pos}+{len(term)}c')
        widget.see(pos)
        widget.config(state=tk.DISABLED)
        self._search_count_lbl.config(text=f'{idx + 1}/{len(self._search_matches)}')

    def _search_next(self):
        if not self._search_matches:
            return
        self._search_idx = (self._search_idx + 1) % len(self._search_matches)
        self._search_jump(self._search_idx)

    def _search_prev(self):
        if not self._search_matches:
            return
        self._search_idx = (self._search_idx - 1) % len(self._search_matches)
        self._search_jump(self._search_idx)

    def _make_tab(self, notebook: ttk.Notebook, title: str) -> scrolledtext.ScrolledText:
        frame = tk.Frame(notebook, bg=INPUT_BG)
        notebook.add(frame, text=f'  {title}  ')
        widget = scrolledtext.ScrolledText(
            frame, wrap=tk.NONE,
            bg=INPUT_BG, fg=TEXT, insertbackground=ACCENT,
            font=FONT_MONO, relief=tk.FLAT,
            state=tk.DISABLED, borderwidth=0, padx=10, pady=8,
            selectbackground=ACCENT, selectforeground='#1e1e2e',
        )
        widget.pack(fill=tk.BOTH, expand=True)
        return widget

    # ------------------------------------------------------------------
    # History UI
    # ------------------------------------------------------------------

    def _refresh_history_ui(self):
        for f in self._history_frames:
            f.destroy()
        self._history_frames.clear()

        if not self._history:
            empty = tk.Label(self._hist_container, text="No history yet",
                             bg=PANEL, fg=SUBTEXT, font=FONT_SM)
            empty.pack(pady=20)
            self._history_frames.append(empty)
            return

        for idx, entry in enumerate(self._history):
            self._history_frames.append(self._make_history_item(entry, idx))

    def _make_history_item(self, entry: dict, idx: int) -> tk.Frame:
        method      = entry.get('method', 'GET')
        url         = entry.get('url', '')
        status      = entry.get('status_code')
        ts          = entry.get('timestamp', 0)
        curl_text   = entry.get('curl', '')

        badge_color = METHOD_COLORS.get(method, SUBTEXT)

        if status is None:
            status_color, status_str = RED, 'error'
        elif 200 <= status < 300:
            status_color, status_str = GREEN, str(status)
        elif 300 <= status < 400:
            status_color, status_str = YELLOW, str(status)
        else:
            status_color, status_str = RED, str(status)

        # Truncate URL: keep path + query, drop scheme+host if long
        display_url = url
        try:
            from urllib.parse import urlparse
            p = urlparse(url)
            path_qs = p.path + ('?' + p.query if p.query else '')
            display_url = path_qs if len(path_qs) > 4 else p.netloc + path_qs
        except Exception:
            pass
        if len(display_url) > 28:
            display_url = display_url[:25] + '…'

        NORMAL_BG = '#2a2a3d'
        HOVER_BG  = '#353550'

        # Outer wrapper (provides padding / gap between items)
        item = tk.Frame(self._hist_container, bg=PANEL, cursor='hand2')
        item.pack(fill=tk.X, padx=6, pady=2)

        # Inner card
        inner = tk.Frame(item, bg=NORMAL_BG, padx=8, pady=6)
        inner.pack(fill=tk.X)

        # Row 1: method badge + url
        row1 = tk.Frame(inner, bg=NORMAL_BG)
        row1.pack(fill=tk.X)

        badge = tk.Label(row1, text=method, bg=badge_color, fg='#11111b',
                         font=('Segoe UI', 8, 'bold'), padx=5, pady=2)
        badge.pack(side=tk.LEFT)

        url_lbl = tk.Label(row1, text=f'  {display_url}', bg=NORMAL_BG, fg=TEXT,
                           font=FONT_SM, anchor=tk.W)
        # Packed later — del_btn must be packed RIGHT first or expand=True leaves no room

        # Row 2: status code + relative time
        row2 = tk.Frame(inner, bg=NORMAL_BG)
        row2.pack(fill=tk.X, pady=(3, 0))

        tk.Label(row2, text=status_str, bg=NORMAL_BG, fg=status_color,
                 font=('Segoe UI', 9, 'bold')).pack(side=tk.LEFT)
        tk.Label(row2, text=_relative_time(ts), bg=NORMAL_BG, fg=SUBTEXT,
                 font=FONT_SM).pack(side=tk.RIGHT)

        # --- Tooltip: show full URL on hover
        tip = Tooltip(url or '(no url)', item)
        tip.bind_all([item, inner] + _all_children(inner))
        item.bind('<Destroy>', lambda _e: tip.close(), add='+')

        # --- Forward mousewheel on items to the scrollable canvas
        for w in [item, inner] + _all_children(inner):
            w.bind('<MouseWheel>', self._hist_wheel_fn, add='+')

        # --- Click: bind on item, then add item to every child's bindtags so
        #     clicks on Labels propagate up to item's handler (tkinter doesn't
        #     bubble Button-1 from child to parent by default).
        item.bind('<Button-1>', lambda _e, c=curl_text: self._load_from_history(c))
        for w in [inner] + _all_children(inner):
            w.config(cursor='hand2')
            tags = list(w.bindtags())
            if str(item) not in tags:
                tags.insert(1, str(item))   # propagate to item after the widget itself
            w.bindtags(tags)

        # --- Hover: only change frame / label backgrounds, never the badge.
        #     Use a deferred leave check so moving between sub-widgets doesn't flicker.
        non_badge = [inner, row1, row2, url_lbl] + _all_children(row2)

        def _hl_on(_e=None):
            for w in non_badge:
                try:
                    w.config(bg=HOVER_BG)
                except tk.TclError:
                    pass

        def _hl_off_deferred():
            try:
                rx = item.winfo_rootx()
                ry = item.winfo_rooty()
                rw = item.winfo_width()
                rh = item.winfo_height()
                mx = item.winfo_pointerx()
                my = item.winfo_pointery()
                if not (rx <= mx <= rx + rw and ry <= my <= ry + rh):
                    for w in non_badge:
                        try:
                            w.config(bg=NORMAL_BG)
                        except tk.TclError:
                            pass
            except tk.TclError:
                pass

        for w in [item, inner] + _all_children(inner):
            w.bind('<Enter>', lambda _e: _hl_on(), add='+')
            w.bind('<Leave>', lambda _e: item.after(20, _hl_off_deferred), add='+')

        # Delete button — created AFTER all _all_children() calls so it is
        # excluded from click-propagation (bindtags) and tooltip bindings.
        # Pack RIGHT before url_lbl so expand=True doesn't steal its space.
        del_btn = tk.Label(row1, text='×', bg=NORMAL_BG, fg='#585b70',
                           font=('Segoe UI', 12, 'bold'), cursor='hand2', padx=3)
        del_btn.pack(side=tk.RIGHT)
        url_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def _on_del(e, i=idx):
            self._delete_history_entry(i)
            return 'break'   # stop propagation to item's load-handler

        del_btn.bind('<Button-1>', _on_del)
        del_btn.bind('<Enter>', lambda e: del_btn.config(fg=RED,      bg=HOVER_BG))
        del_btn.bind('<Leave>', lambda e: del_btn.config(fg='#585b70', bg=NORMAL_BG))
        # Keep del_btn in sync with card hover
        non_badge.append(del_btn)

        return item

    def _load_from_history(self, curl_text: str):
        self._has_placeholder = False
        self.curl_input.config(fg=TEXT)
        self.curl_input.delete('1.0', tk.END)
        self.curl_input.insert('1.0', curl_text)
        self.curl_input.focus_set()

    def _delete_history_entry(self, idx: int):
        if 0 <= idx < len(self._history):
            self._history.pop(idx)
            save_history(self._history)
            self._refresh_history_ui()

    def _clear_history_all(self):
        if not self._history:
            return
        if messagebox.askyesno("Clear History", "Delete all history entries?",
                               icon='warning'):
            self._history = []
            save_history(self._history)
            self._refresh_history_ui()

    # ------------------------------------------------------------------
    # Hotkey helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _select_all(widget) -> str:
        widget.tag_add(tk.SEL, '1.0', tk.END)
        widget.mark_set(tk.INSERT, tk.END)
        return 'break'

    @staticmethod
    def _bind_readonly_keys(widget: scrolledtext.ScrolledText):
        """Copy/SelectAll for disabled (read-only) response panes — Windows and Mac."""
        widget.bind('<Button-1>', lambda e: widget.focus_set())

        def _copy(_e):
            try:
                txt = widget.get(tk.SEL_FIRST, tk.SEL_LAST)
                widget.clipboard_clear()
                widget.clipboard_append(txt)
            except tk.TclError:
                pass
            return 'break'

        def _sel_all(_e):
            widget.tag_add(tk.SEL, '1.0', tk.END)
            return 'break'

        for seq in ('<Control-c>', '<Control-C>',
                    '<Command-c>', '<Command-C>',
                    '<Meta-c>',   '<Meta-C>'):
            widget.bind(seq, _copy)
        for seq in ('<Control-a>', '<Control-A>',
                    '<Command-a>', '<Command-A>',
                    '<Meta-a>',   '<Meta-A>'):
            widget.bind(seq, _sel_all)

    # ------------------------------------------------------------------
    # History data management
    # ------------------------------------------------------------------

    def _add_to_history(self, curl_text: str, parsed: dict, status_code):
        entry = {
            'curl':        curl_text,
            'method':      parsed['method'],
            'url':         parsed['url'] or '',
            'status_code': status_code,
            'timestamp':   time.time(),
        }
        self._history.insert(0, entry)
        self._history = self._history[:HISTORY_MAX]
        save_history(self._history)
        self.root.after(0, self._refresh_history_ui)

    # ------------------------------------------------------------------
    # Placeholder
    # ------------------------------------------------------------------

    PLACEHOLDER = (
        "Paste your curl command here (Windows ^ or Mac \\ format)...\n\n"
        "Example:\n"
        "  curl \"https://api.example.com/data\" -H \"Authorization: Bearer TOKEN\"\n\n"
        "  curl 'https://api.example.com/search?q=foo&page=2' \\\n"
        "    -H 'accept: application/json'"
    )

    def _insert_placeholder(self):
        self.curl_input.insert('1.0', self.PLACEHOLDER)
        self.curl_input.config(fg='#585b70')
        self._has_placeholder = True

    def _on_focus_in(self, _event):
        if self._has_placeholder:
            self.curl_input.delete('1.0', tk.END)
            self.curl_input.config(fg=TEXT)
            self._has_placeholder = False

    def _on_paste(self, _event):
        # Clear placeholder before paste lands so clipboard text isn't appended to it
        if self._has_placeholder:
            self.curl_input.delete('1.0', tk.END)
            self.curl_input.config(fg=TEXT)
            self._has_placeholder = False
        # Return None so tkinter's default paste handling still runs

    def _show_context_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0,
                       bg='#313244', fg=TEXT, activebackground=ACCENT,
                       activeforeground='#1e1e2e', bd=0, relief=tk.FLAT,
                       font=FONT_UI)
        menu.add_command(label="Cut",       command=lambda: self.curl_input.event_generate('<<Cut>>'))
        menu.add_command(label="Copy",      command=lambda: self.curl_input.event_generate('<<Copy>>'))
        menu.add_command(label="Paste",     command=self._paste_from_menu)
        menu.add_separator()
        menu.add_command(label="Select All", command=lambda: self.curl_input.tag_add(tk.SEL, '1.0', tk.END))
        menu.add_separator()
        menu.add_command(label="Clear",     command=self._clear)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _paste_from_menu(self):
        if self._has_placeholder:
            self.curl_input.delete('1.0', tk.END)
            self.curl_input.config(fg=TEXT)
            self._has_placeholder = False
        self.curl_input.event_generate('<<Paste>>')

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _send(self):
        if self._has_placeholder:
            return
        text = self.curl_input.get('1.0', tk.END).strip()
        if not text:
            messagebox.showwarning("Empty Input", "Please paste a curl command first.")
            return

        self.send_btn.config(state=tk.DISABLED)
        self._set_status("Sending…", YELLOW)
        self._set_text(self.body_text, '')
        self._set_text(self.headers_text, '')
        self._set_text(self.req_text, '')

        threading.Thread(target=self._worker, args=(text,), daemon=True).start()

    def _worker(self, curl_text: str):
        try:
            parsed = parse_curl(curl_text)

            req_info = {
                'method':  parsed['method'],
                'url':     parsed['url'],
                'headers': parsed['headers'],
                'data':    parsed['data'],
                'form':    parsed['form'] or None,
                'auth':    f"{parsed['auth'][0]}:***" if parsed.get('auth') else None,
                'cookies': parsed['cookies'] or None,
                'cookie_jar': parsed.get('cookie_jar'),
                'proxy':   parsed.get('proxy'),
                'timeout': {
                    'connect_s': parsed.get('timeout_connect'),
                    'total_s':   parsed.get('timeout_total'),
                } if parsed.get('timeout_connect') or parsed.get('timeout_total') else None,
                'allow_redirects': parsed['allow_redirects'],
                'verify':    parsed['verify'],
                'compressed': parsed.get('compressed', False),
                'ignored_flags': parsed['ignored'] or None,
            }
            req_display = json.dumps(req_info, indent=2, ensure_ascii=False)
            self.root.after(0, lambda: self._set_text(self.req_text, req_display))

            resp = execute_request(parsed)

            def _on_success(r=resp):
                self._add_to_history(curl_text, parsed, r['status_code'])
                self._show_response(r)
            self.root.after(0, _on_success)

        except requests.exceptions.SSLError as e:
            msg = f"SSL Error: {e}\n\nTip: try adding -k to skip SSL verification."
            self.root.after(0, lambda m=msg: [self._save_error_history(curl_text), self._show_error(m)])
        except requests.exceptions.ConnectionError as e:
            cause = e
            while cause.__cause__:
                cause = cause.__cause__
            msg = f"Connection Error: {str(cause).splitlines()[0]}"
            self.root.after(0, lambda m=msg: [self._save_error_history(curl_text), self._show_error(m)])
        except requests.exceptions.Timeout:
            tt = parsed.get('timeout_total') or 30
            tc = parsed.get('timeout_connect') or 30
            msg = f"Request timed out (total={tt}s, connect={tc}s)"
            self.root.after(0, lambda m=msg: [self._save_error_history(curl_text), self._show_error(m)])
        except Exception as e:
            msg = str(e)
            self.root.after(0, lambda m=msg: [self._save_error_history(curl_text), self._show_error(m)])

    def _save_error_history(self, curl_text: str):
        try:
            parsed = parse_curl(curl_text)
        except Exception:
            parsed = {'method': 'GET', 'url': ''}
        self._add_to_history(curl_text, parsed, None)

    def _show_response(self, resp: dict):
        self._search_var.set('')

        code = resp['status_code']
        color = GREEN if 200 <= code < 300 else (YELLOW if 300 <= code < 400 else RED)

        size_kb = resp['size'] / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb >= 1 else f"{resp['size']} B"

        self._set_status(
            f"HTTP {code} {resp['status_text']}  ·  {resp['elapsed_ms']} ms  ·  "
            f"{size_str}  ·  {resp['final_url']}",
            color,
        )

        body = resp['body']
        if 'json' in resp['content_type']:
            try:
                body = json.dumps(json.loads(body), indent=2, ensure_ascii=False)
            except Exception:
                pass

        self._set_text(self.body_text, body)
        self._set_text(self.headers_text,
                       '\n'.join(f"{k}: {v}" for k, v in resp['headers'].items()))
        self.send_btn.config(state=tk.NORMAL)

    def _show_error(self, msg: str):
        self._search_var.set('')
        self._set_status(f"Error: {msg.splitlines()[0]}", RED)
        self._set_text(self.body_text, f"Error:\n\n{msg}")
        self.send_btn.config(state=tk.NORMAL)

    def _clear(self):
        self.curl_input.delete('1.0', tk.END)
        self._insert_placeholder()
        for w in (self.body_text, self.headers_text, self.req_text):
            self._set_text(w, '')
        self._set_status("Ready", SUBTEXT)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_text(self, widget: scrolledtext.ScrolledText, text: str):
        widget.config(state=tk.NORMAL)
        widget.delete('1.0', tk.END)
        if text:
            widget.insert('1.0', text)
        widget.config(state=tk.DISABLED)

    def _set_status(self, msg: str, color: str):
        self.status_var.set(msg)
        self.status_label.config(fg=color)
        self.status_dot.config(fg=color)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _all_children(widget) -> list:
    result = []
    for child in widget.winfo_children():
        result.append(child)
        result.extend(_all_children(child))
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    root = tk.Tk()
    CurlApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
