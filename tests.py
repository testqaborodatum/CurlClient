#!/usr/bin/env python3
"""
Comprehensive test suite for curl_client.py
Covers: parser, execute_request (mocked), history, utilities
"""

import json
import os
import sys
import tempfile
import time
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch, call

# curl_client uses tkinter at import time only for the app class; we mock it
# so the tests run headless.
sys.modules.setdefault('tkinter', MagicMock())
sys.modules.setdefault('tkinter.ttk', MagicMock())
sys.modules.setdefault('tkinter.scrolledtext', MagicMock())
sys.modules.setdefault('tkinter.messagebox', MagicMock())

import types as _types

from curl_client import (
    _unescape_windows,
    _unescape_mac,
    _is_windows_format,
    parse_curl,
    execute_request,
    load_history,
    save_history,
    _relative_time,
    HISTORY_MAX,
    CurlApp,
    ACCENT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status=200, reason='OK', headers=None, body=b'{}', url='https://example.com'):
    """Build a minimal mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status
    resp.reason = reason
    resp.headers = headers or {'Content-Type': 'application/json'}
    resp.content = body
    resp.text = body.decode('utf-8', errors='replace')
    resp.url = url
    resp.cookies = {}
    return resp


# ===========================================================================
# 1. Utility functions
# ===========================================================================

class TestUnescape(unittest.TestCase):

    def test_windows_removes_caret_escapes(self):
        result = _unescape_windows('^"hello^"')
        self.assertEqual(result, '"hello"')

    def test_windows_joins_continuation_lines(self):
        raw = 'curl ^\n  -H "Accept: json"'
        result = _unescape_windows(raw)
        self.assertNotIn('^', result)
        self.assertNotIn('\n', result)

    def test_windows_continuation_with_spaces(self):
        raw = 'curl ^\r\n  -X POST'
        result = _unescape_windows(raw)
        self.assertIn('POST', result)
        self.assertNotIn('^', result)

    def test_mac_joins_continuation_lines(self):
        raw = 'curl \\\n  -H "Accept: json"'
        result = _unescape_mac(raw)
        self.assertNotIn('\\', result)
        self.assertIn('Accept: json', result)

    def test_mac_no_change_without_continuation(self):
        raw = 'curl -X POST https://example.com'
        self.assertEqual(_unescape_mac(raw), raw)


class TestIsWindowsFormat(unittest.TestCase):

    def test_detects_caret_newline(self):
        self.assertTrue(_is_windows_format('curl ^\n-X POST'))

    def test_detects_caret_quote(self):
        self.assertTrue(_is_windows_format('curl ^"header^"'))

    def test_mac_format_not_windows(self):
        self.assertFalse(_is_windows_format('curl \\\n-X POST'))

    def test_plain_is_not_windows(self):
        self.assertFalse(_is_windows_format('curl https://example.com'))


class TestRelativeTime(unittest.TestCase):

    def test_just_now(self):
        self.assertEqual(_relative_time(time.time() - 5), 'just now')

    def test_minutes_ago(self):
        self.assertEqual(_relative_time(time.time() - 120), '2m ago')

    def test_hours_ago(self):
        self.assertEqual(_relative_time(time.time() - 7200), '2h ago')

    def test_days_ago_returns_formatted(self):
        ts = time.time() - 86400 * 2
        result = _relative_time(ts)
        # Should contain month abbreviation
        self.assertRegex(result, r'[A-Z][a-z]{2} \d+')


# ===========================================================================
# 2. Parser — basics
# ===========================================================================

class TestParseBasics(unittest.TestCase):

    def test_simple_get(self):
        p = parse_curl('curl https://api.example.com/v1/users')
        self.assertEqual(p['url'], 'https://api.example.com/v1/users')
        self.assertEqual(p['method'], 'GET')

    def test_url_with_query_string(self):
        p = parse_curl('curl https://api.example.com/search?q=hello&limit=10')
        self.assertIn('q=hello', p['url'])
        self.assertIn('limit=10', p['url'])

    def test_explicit_get(self):
        p = parse_curl('curl -X GET https://example.com')
        self.assertEqual(p['method'], 'GET')

    def test_explicit_post(self):
        p = parse_curl('curl -X POST https://example.com')
        self.assertEqual(p['method'], 'POST')

    def test_explicit_put(self):
        p = parse_curl('curl -X PUT https://example.com')
        self.assertEqual(p['method'], 'PUT')

    def test_explicit_patch(self):
        p = parse_curl('curl -X PATCH https://example.com')
        self.assertEqual(p['method'], 'PATCH')

    def test_explicit_delete(self):
        p = parse_curl('curl -X DELETE https://example.com')
        self.assertEqual(p['method'], 'DELETE')

    def test_head_flag(self):
        p = parse_curl('curl -I https://example.com')
        self.assertEqual(p['method'], 'HEAD')

    def test_long_request_flag(self):
        p = parse_curl('curl --request DELETE https://example.com')
        self.assertEqual(p['method'], 'DELETE')

    def test_defaults(self):
        p = parse_curl('curl https://example.com')
        self.assertFalse(p['allow_redirects'])
        self.assertTrue(p['verify'])
        self.assertEqual(p['headers'], {})
        self.assertIsNone(p['data'])
        self.assertEqual(p['form'], {})
        self.assertIsNone(p['auth'])
        self.assertEqual(p['cookies'], {})
        self.assertFalse(p['compressed'])

    def test_empty_string_returns_none_url(self):
        p = parse_curl('curl')
        self.assertIsNone(p['url'])

    def test_no_curl_prefix(self):
        p = parse_curl('https://example.com')
        self.assertEqual(p['url'], 'https://example.com')

    def test_http_url(self):
        p = parse_curl('curl http://insecure.example.com')
        self.assertEqual(p['url'], 'http://insecure.example.com')


# ===========================================================================
# 3. Parser — headers
# ===========================================================================

class TestParseHeaders(unittest.TestCase):

    def test_single_header(self):
        p = parse_curl('curl -H "Content-Type: application/json" https://example.com')
        self.assertEqual(p['headers']['Content-Type'], 'application/json')

    def test_multiple_headers(self):
        p = parse_curl(
            'curl -H "Content-Type: application/json" '
            '-H "Authorization: Bearer tok123" '
            'https://example.com'
        )
        self.assertEqual(p['headers']['Content-Type'], 'application/json')
        self.assertEqual(p['headers']['Authorization'], 'Bearer tok123')

    def test_header_with_colon_in_value(self):
        p = parse_curl('curl -H "X-Time: 12:30:00" https://example.com')
        self.assertEqual(p['headers']['X-Time'], '12:30:00')

    def test_header_value_leading_space_stripped(self):
        p = parse_curl('curl -H "Accept:  application/json" https://example.com')
        self.assertEqual(p['headers']['Accept'], 'application/json')

    def test_long_header_flag(self):
        p = parse_curl('curl --header "X-Custom: value" https://example.com')
        self.assertEqual(p['headers']['X-Custom'], 'value')

    def test_bearer_auth_via_header(self):
        p = parse_curl('curl -H "Authorization: Bearer eyJhbGci.eyJzdWI.SflKxwRJ" https://api.example.com')
        self.assertIn('Authorization', p['headers'])
        self.assertTrue(p['headers']['Authorization'].startswith('Bearer '))


# ===========================================================================
# 4. Parser — body data
# ===========================================================================

class TestParseBodyData(unittest.TestCase):

    def test_data_flag_sets_post(self):
        p = parse_curl('curl -d "name=test" https://example.com')
        self.assertEqual(p['data'], 'name=test')
        self.assertEqual(p['method'], 'POST')

    def test_data_raw(self):
        p = parse_curl('curl --data-raw \'{"key":"val"}\' https://example.com')
        self.assertEqual(p['data'], '{"key":"val"}')

    def test_data_binary(self):
        p = parse_curl('curl --data-binary @- https://example.com')
        self.assertEqual(p['data'], '@-')

    def test_data_does_not_override_explicit_method(self):
        p = parse_curl('curl -X PUT -d "body" https://example.com')
        self.assertEqual(p['method'], 'PUT')

    def test_data_with_json(self):
        payload = '{"user":"alice","age":30}'
        p = parse_curl(f'curl -d \'{payload}\' https://api.example.com/users')
        self.assertEqual(p['data'], payload)

    def test_data_with_content_type_header(self):
        p = parse_curl(
            'curl -H "Content-Type: application/json" '
            '-d \'{"x":1}\' https://example.com'
        )
        self.assertEqual(p['headers']['Content-Type'], 'application/json')
        self.assertIsNotNone(p['data'])

    def test_get_flag_overrides_data_method(self):
        # -G after -d wins (parser is left-to-right; -d sets POST, -G resets to GET)
        p = parse_curl('curl -d "q=test" -G https://example.com/search')
        self.assertEqual(p['method'], 'GET')


# ===========================================================================
# 5. Parser — form data
# ===========================================================================

class TestParseFormData(unittest.TestCase):

    def test_single_form_field(self):
        p = parse_curl('curl -F "name=Alice" https://example.com/upload')
        self.assertEqual(p['form']['name'], 'Alice')
        self.assertEqual(p['method'], 'POST')

    def test_multiple_form_fields(self):
        p = parse_curl(
            'curl -F "first=Alice" -F "last=Smith" -F "age=30" '
            'https://example.com/upload'
        )
        self.assertEqual(p['form']['first'], 'Alice')
        self.assertEqual(p['form']['last'], 'Smith')
        self.assertEqual(p['form']['age'], '30')

    def test_file_upload_field(self):
        p = parse_curl('curl -F "photo=@/tmp/avatar.png" https://example.com/upload')
        self.assertEqual(p['form']['photo'], '@/tmp/avatar.png')

    def test_mixed_fields_and_files(self):
        p = parse_curl(
            'curl -F "title=Report" -F "file=@report.pdf" '
            'https://example.com/upload'
        )
        self.assertEqual(p['form']['title'], 'Report')
        self.assertEqual(p['form']['file'], '@report.pdf')

    def test_form_string_flag(self):
        p = parse_curl('curl --form-string "data=<html>" https://example.com')
        self.assertEqual(p['form']['data'], '<html>')

    def test_form_with_equals_in_value(self):
        p = parse_curl('curl -F "token=abc=def=ghi" https://example.com')
        # only first = is separator
        self.assertEqual(p['form']['token'], 'abc=def=ghi')


# ===========================================================================
# 6. Parser — auth
# ===========================================================================

class TestParseAuth(unittest.TestCase):

    def test_basic_auth(self):
        p = parse_curl('curl -u admin:s3cr3t https://example.com')
        self.assertEqual(p['auth'], ('admin', 's3cr3t'))

    def test_auth_no_password(self):
        p = parse_curl('curl -u alice https://example.com')
        self.assertEqual(p['auth'], ('alice', ''))

    def test_long_user_flag(self):
        p = parse_curl('curl --user bob:pass123 https://example.com')
        self.assertEqual(p['auth'], ('bob', 'pass123'))

    def test_password_with_special_chars(self):
        p = parse_curl('curl -u "user:p@$$w0rd!" https://example.com')
        self.assertEqual(p['auth'][0], 'user')
        self.assertIn('p@', p['auth'][1])

    def test_password_with_colon(self):
        # Only the FIRST colon splits user:pass
        p = parse_curl('curl -u "user:pass:extra" https://example.com')
        self.assertEqual(p['auth'], ('user', 'pass:extra'))

    def test_auth_empty_username(self):
        p = parse_curl('curl -u :secrettoken https://example.com')
        self.assertEqual(p['auth'], ('', 'secrettoken'))


# ===========================================================================
# 7. Parser — cookies
# ===========================================================================

class TestParseCookies(unittest.TestCase):

    def test_single_cookie(self):
        p = parse_curl('curl -b "session=abc123" https://example.com')
        self.assertEqual(p['cookies']['session'], 'abc123')

    def test_multiple_cookies(self):
        p = parse_curl('curl -b "a=1; b=2; c=3" https://example.com')
        self.assertEqual(p['cookies']['a'], '1')
        self.assertEqual(p['cookies']['b'], '2')
        self.assertEqual(p['cookies']['c'], '3')

    def test_long_cookie_flag(self):
        p = parse_curl('curl --cookie "token=xyz" https://example.com')
        self.assertEqual(p['cookies']['token'], 'xyz')

    def test_cookie_jar_path(self):
        p = parse_curl('curl -c /tmp/cookies.txt https://example.com')
        self.assertEqual(p['cookie_jar'], '/tmp/cookies.txt')

    def test_long_cookie_jar_flag(self):
        p = parse_curl('curl --cookie-jar /var/cookies.json https://example.com')
        self.assertEqual(p['cookie_jar'], '/var/cookies.json')

    def test_send_and_save_cookies(self):
        p = parse_curl(
            'curl -b "session=abc" -c /tmp/out.txt https://example.com'
        )
        self.assertEqual(p['cookies']['session'], 'abc')
        self.assertEqual(p['cookie_jar'], '/tmp/out.txt')

    def test_cookie_value_with_equals(self):
        p = parse_curl('curl -b "jwt=hdr.payload.sig==" https://example.com')
        self.assertEqual(p['cookies']['jwt'], 'hdr.payload.sig==')


# ===========================================================================
# 8. Parser — proxy
# ===========================================================================

class TestParseProxy(unittest.TestCase):

    def test_proxy_long(self):
        p = parse_curl('curl --proxy http://proxy.corp:8080 https://example.com')
        self.assertEqual(p['proxy'], 'http://proxy.corp:8080')

    def test_proxy_short(self):
        p = parse_curl('curl -x socks5://127.0.0.1:1080 https://example.com')
        self.assertEqual(p['proxy'], 'socks5://127.0.0.1:1080')

    def test_https_proxy(self):
        p = parse_curl('curl --proxy https://secure-proxy:443 https://example.com')
        self.assertEqual(p['proxy'], 'https://secure-proxy:443')

    def test_no_proxy_by_default(self):
        p = parse_curl('curl https://example.com')
        self.assertIsNone(p['proxy'])


# ===========================================================================
# 9. Parser — timeout
# ===========================================================================

class TestParseTimeout(unittest.TestCase):

    def test_max_time(self):
        p = parse_curl('curl --max-time 60 https://example.com')
        self.assertEqual(p['timeout_total'], 60.0)

    def test_max_time_short(self):
        p = parse_curl('curl -m 15 https://example.com')
        self.assertEqual(p['timeout_total'], 15.0)

    def test_connect_timeout(self):
        p = parse_curl('curl --connect-timeout 5 https://example.com')
        self.assertEqual(p['timeout_connect'], 5.0)

    def test_both_timeouts(self):
        p = parse_curl('curl --connect-timeout 3 --max-time 30 https://example.com')
        self.assertEqual(p['timeout_connect'], 3.0)
        self.assertEqual(p['timeout_total'], 30.0)

    def test_fractional_timeout(self):
        p = parse_curl('curl --max-time 0.5 https://example.com')
        self.assertAlmostEqual(p['timeout_total'], 0.5)

    def test_no_timeout_by_default(self):
        p = parse_curl('curl https://example.com')
        self.assertIsNone(p['timeout_connect'])
        self.assertIsNone(p['timeout_total'])

    def test_invalid_timeout_ignored(self):
        p = parse_curl('curl --max-time notanumber https://example.com')
        self.assertIsNone(p['timeout_total'])


# ===========================================================================
# 10. Parser — redirects & SSL
# ===========================================================================

class TestParseRedirectSSL(unittest.TestCase):

    def test_redirects_off_by_default(self):
        p = parse_curl('curl https://example.com')
        self.assertFalse(p['allow_redirects'])

    def test_L_enables_redirects(self):
        p = parse_curl('curl -L https://example.com')
        self.assertTrue(p['allow_redirects'])

    def test_location_long_flag(self):
        p = parse_curl('curl --location https://example.com')
        self.assertTrue(p['allow_redirects'])

    def test_location_trusted(self):
        p = parse_curl('curl --location-trusted https://example.com')
        self.assertTrue(p['allow_redirects'])

    def test_ssl_verify_on_by_default(self):
        p = parse_curl('curl https://example.com')
        self.assertTrue(p['verify'])

    def test_insecure_flag(self):
        p = parse_curl('curl -k https://self-signed.example.com')
        self.assertFalse(p['verify'])

    def test_insecure_long_flag(self):
        p = parse_curl('curl --insecure https://self-signed.example.com')
        self.assertFalse(p['verify'])


# ===========================================================================
# 11. Parser — compression
# ===========================================================================

class TestParseCompressed(unittest.TestCase):

    def test_compressed_flag(self):
        p = parse_curl('curl --compressed https://example.com')
        self.assertTrue(p['compressed'])

    def test_compressed_off_by_default(self):
        p = parse_curl('curl https://example.com')
        self.assertFalse(p['compressed'])


# ===========================================================================
# 12. Parser — multiline / Windows format
# ===========================================================================

class TestParseMultiline(unittest.TestCase):

    def test_windows_multiline(self):
        raw = (
            'curl ^\n'
            '  -X POST ^\n'
            '  -H "Content-Type: application/json" ^\n'
            '  -d "{\\"key\\":\\"val\\"}" ^\n'
            '  https://api.example.com/data'
        )
        p = parse_curl(raw)
        self.assertEqual(p['method'], 'POST')
        self.assertEqual(p['url'], 'https://api.example.com/data')
        self.assertIn('Content-Type', p['headers'])

    def test_mac_multiline(self):
        raw = (
            'curl \\\n'
            '  -X POST \\\n'
            '  -H "Authorization: Bearer tok" \\\n'
            '  -d \'{"x":1}\' \\\n'
            '  https://api.example.com/items'
        )
        p = parse_curl(raw)
        self.assertEqual(p['method'], 'POST')
        self.assertEqual(p['url'], 'https://api.example.com/items')
        self.assertEqual(p['headers']['Authorization'], 'Bearer tok')

    def test_windows_quoted_header(self):
        raw = 'curl ^"https://example.com^"'
        # Just checks it doesn't crash and returns something
        p = parse_curl(raw)
        self.assertIsNotNone(p)

    def test_complex_windows_real_world(self):
        raw = (
            'curl "https://api.example.com/v2/items" ^\n'
            '  -H "accept: application/json" ^\n'
            '  -H "authorization: Bearer mytoken" ^\n'
            '  -H "content-type: application/json" ^\n'
            '  --data-raw "{""name"":""test""}"'
        )
        p = parse_curl(raw)
        self.assertEqual(p['url'], 'https://api.example.com/v2/items')
        self.assertIn('accept', p['headers'])


# ===========================================================================
# 13. Parser — skipped flags don't break parsing
# ===========================================================================

class TestParseSkippedFlags(unittest.TestCase):

    def test_silent_verbose_ignored(self):
        p = parse_curl('curl -s -v https://example.com')
        self.assertEqual(p['url'], 'https://example.com')

    def test_output_flag_skipped(self):
        p = parse_curl('curl -o /dev/null https://example.com')
        self.assertEqual(p['url'], 'https://example.com')

    def test_user_agent_skipped(self):
        p = parse_curl('curl -A "MyAgent/1.0" https://example.com')
        self.assertEqual(p['url'], 'https://example.com')

    def test_write_out_skipped(self):
        p = parse_curl('curl -w "%{http_code}" https://example.com')
        self.assertEqual(p['url'], 'https://example.com')

    def test_compressed_with_silent(self):
        p = parse_curl('curl -s --compressed https://example.com')
        self.assertTrue(p['compressed'])
        self.assertEqual(p['url'], 'https://example.com')


# ===========================================================================
# 14. Parser — realistic full curl commands
# ===========================================================================

class TestParseRealistic(unittest.TestCase):

    def test_github_api_call(self):
        raw = (
            'curl -L '
            '-H "Accept: application/vnd.github+json" '
            '-H "Authorization: Bearer ghp_token123" '
            '-H "X-GitHub-Api-Version: 2022-11-28" '
            'https://api.github.com/repos/owner/repo/issues'
        )
        p = parse_curl(raw)
        self.assertEqual(p['url'], 'https://api.github.com/repos/owner/repo/issues')
        self.assertTrue(p['allow_redirects'])
        self.assertEqual(p['headers']['Accept'], 'application/vnd.github+json')
        self.assertIn('Bearer', p['headers']['Authorization'])

    def test_post_json_with_auth(self):
        raw = (
            'curl -X POST https://api.example.com/login '
            '-u admin:password '
            '-H "Content-Type: application/json" '
            '-d \'{"remember":true}\' '
            '--connect-timeout 5 --max-time 30'
        )
        p = parse_curl(raw)
        self.assertEqual(p['method'], 'POST')
        self.assertEqual(p['auth'], ('admin', 'password'))
        self.assertIsNotNone(p['data'])
        self.assertEqual(p['timeout_connect'], 5.0)
        self.assertEqual(p['timeout_total'], 30.0)

    def test_file_upload_multipart(self):
        raw = (
            'curl -X POST https://upload.example.com/files '
            '-F "description=My report" '
            '-F "file=@/home/user/report.pdf" '
            '-b "session=abc123" '
            '-L -k'
        )
        p = parse_curl(raw)
        self.assertEqual(p['method'], 'POST')
        self.assertEqual(p['form']['description'], 'My report')
        self.assertEqual(p['form']['file'], '@/home/user/report.pdf')
        self.assertEqual(p['cookies']['session'], 'abc123')
        self.assertTrue(p['allow_redirects'])
        self.assertFalse(p['verify'])

    def test_proxy_with_auth_and_timeout(self):
        raw = (
            'curl --proxy http://corp-proxy:3128 '
            '--connect-timeout 10 -m 60 '
            '-u svc_user:svc_pass '
            '--compressed -L '
            'https://internal.corp.example/api'
        )
        p = parse_curl(raw)
        self.assertEqual(p['proxy'], 'http://corp-proxy:3128')
        self.assertEqual(p['timeout_connect'], 10.0)
        self.assertEqual(p['timeout_total'], 60.0)
        self.assertEqual(p['auth'], ('svc_user', 'svc_pass'))
        self.assertTrue(p['compressed'])
        self.assertTrue(p['allow_redirects'])


# ===========================================================================
# 15. execute_request — via mocked session
# ===========================================================================

class TestExecuteRequest(unittest.TestCase):

    def _mock_session(self, resp=None):
        """Return a patcher that mocks requests.Session and its response."""
        if resp is None:
            resp = _make_response()
        mock_session = MagicMock()
        mock_session.request.return_value = resp
        patcher = patch('curl_client.requests.Session', return_value=mock_session)
        return patcher, mock_session

    # --- basic ---

    def test_raises_on_missing_url(self):
        with self.assertRaises(ValueError):
            execute_request({'url': None, 'method': 'GET', 'headers': {},
                             'data': None, 'form': {}, 'allow_redirects': False,
                             'verify': True, 'cookies': {}, 'cookie_jar': None,
                             'auth': None, 'proxy': None,
                             'timeout_connect': None, 'timeout_total': None,
                             'compressed': False})

    def test_simple_get_returns_correct_shape(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl https://httpbin.org/get')
            result = execute_request(parsed)
        self.assertIn('status_code', result)
        self.assertIn('headers', result)
        self.assertIn('body', result)
        self.assertIn('elapsed_ms', result)
        self.assertIn('final_url', result)
        self.assertIn('size', result)

    def test_default_timeout_tuple(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertEqual(kwargs['timeout'], (30, 30))

    def test_custom_timeout_passed(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl --connect-timeout 3 --max-time 20 https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertEqual(kwargs['timeout'], (3.0, 20.0))

    def test_partial_timeout_total_only(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl -m 45 https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        tc, tt = kwargs['timeout']
        self.assertEqual(tt, 45.0)
        self.assertEqual(tc, 30)  # connect falls back to default

    # --- auth ---

    def test_basic_auth_forwarded(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl -u alice:wonderland https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertEqual(kwargs['auth'], ('alice', 'wonderland'))

    def test_no_auth_key_when_absent(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertNotIn('auth', kwargs)

    # --- cookies ---

    def test_cookies_forwarded(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl -b "a=1; b=2" https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertEqual(kwargs['cookies'], {'a': '1', 'b': '2'})

    def test_no_cookies_key_when_absent(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertNotIn('cookies', kwargs)

    # --- cookie-jar ---

    def test_cookie_jar_written_on_response(self):
        mock_resp = _make_response()
        mock_resp.cookies = {'session': 'newsession'}
        patcher, session = self._mock_session(mock_resp)

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            jar_path = f.name
        # Use forward slashes so shlex doesn't mangle backslashes on Windows
        jar_path_fwd = jar_path.replace('\\', '/')

        try:
            with patcher:
                parsed = parse_curl(f'curl -c {jar_path_fwd} https://example.com')
                execute_request(parsed)
            with open(jar_path, 'r') as f:
                saved = json.load(f)
            self.assertEqual(saved['session'], 'newsession')
        finally:
            os.unlink(jar_path)

    def test_cookie_jar_invalid_path_no_crash(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl -c /nonexistent/dir/cookies.txt https://example.com')
            # Should not raise
            execute_request(parsed)

    # --- proxy ---

    def test_proxy_forwarded_for_both_schemes(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl --proxy http://proxy:8080 https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertEqual(kwargs['proxies']['http'], 'http://proxy:8080')
        self.assertEqual(kwargs['proxies']['https'], 'http://proxy:8080')

    def test_no_proxy_key_when_absent(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertNotIn('proxies', kwargs)

    # --- compressed / gzip ---

    def test_compressed_adds_accept_encoding(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl --compressed https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertIn('Accept-Encoding', kwargs['headers'])
        self.assertIn('gzip', kwargs['headers']['Accept-Encoding'])

    def test_compressed_does_not_override_existing_accept_encoding(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl(
                'curl --compressed '
                '-H "Accept-Encoding: identity" '
                'https://example.com'
            )
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertEqual(kwargs['headers']['Accept-Encoding'], 'identity')

    def test_no_accept_encoding_without_compressed(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertNotIn('Accept-Encoding', kwargs['headers'])

    # --- redirects ---

    def test_redirects_off_by_default(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertFalse(kwargs['allow_redirects'])

    def test_redirects_enabled_with_L(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl -L https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertTrue(kwargs['allow_redirects'])

    # --- SSL ---

    def test_ssl_verify_on_by_default(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertTrue(kwargs['verify'])

    def test_ssl_verify_off_with_k(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl -k https://self-signed.example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertFalse(kwargs['verify'])

    # --- body data ---

    def test_raw_data_encoded_to_bytes(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl -d "hello=world" https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertIsInstance(kwargs['data'], bytes)
        self.assertIn(b'hello', kwargs['data'])

    def test_data_sets_post_method(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl -d "x=1" https://example.com')
            execute_request(parsed)
        args, _ = session.request.call_args
        self.assertEqual(args[0], 'POST')

    # --- multipart form ---

    def test_form_data_uses_files_kwarg(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl -F "field=value" https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        self.assertIn('files', kwargs)
        self.assertNotIn('data', kwargs)

    def test_form_text_field_has_none_filename(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl -F "name=Alice" https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        filename, value = kwargs['files']['name']
        self.assertIsNone(filename)
        self.assertEqual(value, 'Alice')

    def test_form_file_upload(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as f:
            f.write(b'file content')
            fpath = f.name
        try:
            patcher, session = self._mock_session()
            with patcher:
                parsed = parse_curl(f'curl -F "doc=@{fpath}" https://example.com')
                execute_request(parsed)
            _, kwargs = session.request.call_args
            fname, fobj = kwargs['files']['doc']
            self.assertEqual(fname, os.path.basename(fpath))
            fobj.close()
        finally:
            os.unlink(fpath)

    def test_form_missing_file_fallback(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl -F "f=@/no/such/file.bin" https://example.com')
            execute_request(parsed)
        _, kwargs = session.request.call_args
        # Should not raise; fallback puts raw value
        self.assertIn('files', kwargs)

    # --- response parsing ---

    def test_json_body_returned_as_text(self):
        body = b'{"status": "ok", "count": 42}'
        resp = _make_response(body=body, headers={'Content-Type': 'application/json'})
        patcher, session = self._mock_session(resp)
        with patcher:
            parsed = parse_curl('curl https://example.com')
            result = execute_request(parsed)
        self.assertIn('status', result['body'])
        self.assertEqual(result['status_code'], 200)

    def test_non_json_body(self):
        body = b'<html><body>Hello</body></html>'
        resp = _make_response(body=body, headers={'Content-Type': 'text/html'})
        patcher, session = self._mock_session(resp)
        with patcher:
            parsed = parse_curl('curl https://example.com')
            result = execute_request(parsed)
        self.assertIn('<html>', result['body'])

    def test_404_status_returned(self):
        resp = _make_response(status=404, reason='Not Found', body=b'Not Found')
        patcher, session = self._mock_session(resp)
        with patcher:
            parsed = parse_curl('curl https://example.com/missing')
            result = execute_request(parsed)
        self.assertEqual(result['status_code'], 404)
        self.assertEqual(result['status_text'], 'Not Found')

    def test_elapsed_ms_is_non_negative(self):
        patcher, session = self._mock_session()
        with patcher:
            parsed = parse_curl('curl https://example.com')
            result = execute_request(parsed)
        self.assertGreaterEqual(result['elapsed_ms'], 0)

    def test_size_matches_content_length(self):
        body = b'x' * 1024
        resp = _make_response(body=body)
        patcher, session = self._mock_session(resp)
        with patcher:
            parsed = parse_curl('curl https://example.com')
            result = execute_request(parsed)
        self.assertEqual(result['size'], 1024)


# ===========================================================================
# 16. History persistence
# ===========================================================================

class TestHistory(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False
        )
        self._tmp.close()
        self._path = self._tmp.name
        self._patcher = patch('curl_client._history_path', return_value=self._path)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        try:
            os.unlink(self._path)
        except FileNotFoundError:
            pass

    def _entry(self, n=1):
        return {
            'method': 'GET',
            'url': f'https://example.com/{n}',
            'curl': f'curl https://example.com/{n}',
            'status': 200,
            'ts': time.time(),
        }

    def test_save_and_load_roundtrip(self):
        entries = [self._entry(i) for i in range(3)]
        save_history(entries)
        loaded = load_history()
        self.assertEqual(len(loaded), 3)
        self.assertEqual(loaded[0]['url'], 'https://example.com/0')

    def test_load_returns_empty_on_missing_file(self):
        os.unlink(self._path)
        result = load_history()
        self.assertEqual(result, [])

    def test_load_returns_empty_on_corrupted_json(self):
        with open(self._path, 'w') as f:
            f.write('NOT JSON {{{{')
        result = load_history()
        self.assertEqual(result, [])

    def test_load_returns_empty_on_non_list_json(self):
        with open(self._path, 'w') as f:
            json.dump({'key': 'val'}, f)
        result = load_history()
        self.assertEqual(result, [])

    def test_max_entries_enforced(self):
        entries = [self._entry(i) for i in range(HISTORY_MAX + 5)]
        save_history(entries)
        loaded = load_history()
        self.assertEqual(len(loaded), HISTORY_MAX)

    def test_order_preserved(self):
        entries = [self._entry(i) for i in range(5)]
        save_history(entries)
        loaded = load_history()
        urls = [e['url'] for e in loaded]
        self.assertEqual(urls, [f'https://example.com/{i}' for i in range(5)])

    def test_unicode_preserved(self):
        entry = self._entry(1)
        entry['url'] = 'https://example.com/данные'
        save_history([entry])
        loaded = load_history()
        self.assertEqual(loaded[0]['url'], 'https://example.com/данные')

    def test_empty_list_saves_and_loads(self):
        save_history([])
        self.assertEqual(load_history(), [])


# ===========================================================================
# 17. Edge cases / regression guards
# ===========================================================================

class TestEdgeCases(unittest.TestCase):

    def test_parse_empty_string(self):
        p = parse_curl('')
        self.assertIsNone(p['url'])

    def test_parse_only_whitespace(self):
        p = parse_curl('   \n  ')
        self.assertIsNone(p['url'])

    def test_parse_malformed_shlex(self):
        # Unclosed quote — should not raise
        p = parse_curl('curl -H "Broken: value https://example.com')
        self.assertIsNotNone(p)

    def test_url_with_fragment(self):
        p = parse_curl('curl https://example.com/page#section')
        self.assertIn('example.com', p['url'])

    def test_url_with_encoded_chars(self):
        p = parse_curl('curl "https://example.com/search?q=hello%20world&lang=en"')
        self.assertIn('%20', p['url'])

    def test_multiple_data_flags_last_wins(self):
        p = parse_curl('curl -d "first=1" -d "second=2" https://example.com')
        self.assertEqual(p['data'], 'second=2')

    def test_form_without_equals_ignored(self):
        p = parse_curl('curl -F "noequals" https://example.com')
        # key without = should not be added
        self.assertNotIn('noequals', p['form'])

    def test_data_and_form_data_wins(self):
        # -d comes before -F; form overrides body in execute_request
        p = parse_curl('curl -d "raw" https://example.com')
        self.assertEqual(p['data'], 'raw')
        self.assertEqual(p['form'], {})

    def test_method_lowercased_normalized(self):
        p = parse_curl('curl -X post https://example.com')
        self.assertEqual(p['method'], 'POST')

    def test_cookie_single_no_semicolon(self):
        p = parse_curl('curl -b "token=abc" https://example.com')
        self.assertEqual(p['cookies']['token'], 'abc')

    def test_timeout_zero_treated_as_default(self):
        # 0 is falsy → falls back to 30 in execute_request
        patcher = patch('curl_client.requests.Session')
        with patcher as mock_sess:
            mock_sess.return_value.request.return_value = _make_response()
            parsed = parse_curl('curl --max-time 0 https://example.com')
            execute_request(parsed)
        _, kwargs = mock_sess.return_value.request.call_args
        _, tt = kwargs['timeout']
        self.assertEqual(tt, 30)


# ===========================================================================
# 18. Ignored flags tracking
# ===========================================================================

class TestIgnoredFlags(unittest.TestCase):

    def test_no_ignored_by_default(self):
        p = parse_curl('curl https://example.com')
        self.assertEqual(p['ignored'], [])

    def test_skip_flag_recorded(self):
        p = parse_curl('curl -s https://example.com')
        self.assertIn('-s', p['ignored'])

    def test_verbose_flag_recorded(self):
        p = parse_curl('curl -v https://example.com')
        self.assertIn('-v', p['ignored'])

    def test_http2_flag_recorded(self):
        p = parse_curl('curl --http2 https://example.com')
        self.assertIn('--http2', p['ignored'])

    def test_skip_with_value_recorded_with_value(self):
        p = parse_curl('curl -o /tmp/out.txt https://example.com')
        self.assertIn('-o /tmp/out.txt', p['ignored'])

    def test_user_agent_recorded_with_value(self):
        p = parse_curl('curl -A "MyAgent/1.0" https://example.com')
        self.assertIn('-A MyAgent/1.0', p['ignored'])

    def test_cert_recorded_with_value(self):
        p = parse_curl('curl --cert /path/to/cert.pem https://example.com')
        self.assertIn('--cert /path/to/cert.pem', p['ignored'])

    def test_multiple_skip_flags_all_recorded(self):
        p = parse_curl('curl -s -v -i https://example.com')
        self.assertIn('-s', p['ignored'])
        self.assertIn('-v', p['ignored'])
        self.assertIn('-i', p['ignored'])

    def test_unknown_flag_recorded(self):
        p = parse_curl('curl --some-unknown-flag https://example.com')
        self.assertTrue(any('--some-unknown-flag' in f for f in p['ignored']))

    def test_unknown_flag_with_value_recorded(self):
        p = parse_curl('curl --future-option somevalue https://example.com')
        self.assertTrue(any('--future-option somevalue' in f for f in p['ignored']))

    def test_known_flags_not_in_ignored(self):
        p = parse_curl(
            'curl -X POST -H "Content-Type: application/json" '
            '-d "{}" -u user:pass -L -k --compressed https://example.com'
        )
        self.assertEqual(p['ignored'], [])

    def test_mixed_known_and_unknown(self):
        p = parse_curl('curl -v -L -s https://example.com')
        self.assertIn('-v', p['ignored'])
        self.assertIn('-s', p['ignored'])
        self.assertNotIn('-L', p['ignored'])

    def test_retry_recorded_with_value(self):
        p = parse_curl('curl --retry 3 https://example.com')
        self.assertIn('--retry 3', p['ignored'])

    def test_write_out_recorded_with_value(self):
        p = parse_curl('curl -w "%{http_code}" https://example.com')
        self.assertTrue(any('-w' in f for f in p['ignored']))

    def test_order_preserved(self):
        p = parse_curl('curl -s -v -i https://example.com')
        self.assertEqual(p['ignored'], ['-s', '-v', '-i'])

    def test_real_world_devtools_command(self):
        raw = (
            'curl "https://api.example.com/data" '
            '-H "accept: application/json" '
            '-v -s --compressed -L '
            '--cert /etc/ssl/client.pem'
        )
        p = parse_curl(raw)
        self.assertIn('-v', p['ignored'])
        self.assertIn('-s', p['ignored'])
        self.assertNotIn('--compressed', p['ignored'])
        self.assertNotIn('-L', p['ignored'])
        self.assertTrue(any('--cert' in f for f in p['ignored']))


# ===========================================================================
# 20. Search UI — FakeTextWidget simulation
# ===========================================================================

class FakeTextWidget:
    """
    Minimal simulation of tkinter.scrolledtext.ScrolledText that supports
    the subset used by CurlApp's search methods:
      config(), tag_remove(), tag_add(), tag_config(), see(), search()
    """

    def __init__(self, content: str = ''):
        self.content = content
        self._state = None
        self.tags: dict = {}        # tag -> [(start, end), ...]
        self.tag_configs: dict = {}
        self.seen: list = []        # positions passed to .see()

    def config(self, state=None, **_):
        if state is not None:
            self._state = state

    def tag_remove(self, tag, *_):
        self.tags.pop(tag, None)

    def tag_add(self, tag, start, end):
        self.tags.setdefault(tag, []).append((start, end))

    def tag_config(self, tag, **kw):
        self.tag_configs[tag] = kw

    def see(self, pos):
        self.seen.append(pos)

    def search(self, pattern, start, stop=None, nocase: bool = False):
        """Return first match position after `start` (line.char), or ''."""
        txt = self.content
        pat = pattern
        if nocase:
            txt = txt.lower()
            pat = pat.lower()
        off = self._to_offset(str(start))
        idx = txt.find(pat, off)
        return '' if idx == -1 else self._from_offset(idx)

    # ---- position helpers ----

    def _to_offset(self, pos: str) -> int:
        if '+' in pos:
            base, rest = pos.split('+', 1)
            return self._to_offset(base) + int(rest.rstrip('c'))
        parts = pos.split('.')
        if len(parts) != 2:
            return 0
        line, char = int(parts[0]), int(parts[1])
        lines = self.content.split('\n')
        off = sum(len(lines[i]) + 1 for i in range(min(line - 1, len(lines))))
        return off + char

    def _from_offset(self, off: int) -> str:
        before = self.content[:off]
        line = before.count('\n') + 1
        char = off - (before.rfind('\n') + 1) if '\n' in before else off
        return f'{line}.{char}'


def _make_search_stub(body: str = '', headers: str = '', req: str = '',
                      active_tab: int = 0, term: str = ''):
    """
    Create a SimpleNamespace that mimics a CurlApp instance for search tests.
    CurlApp's unbound search methods are bound to it so they run against
    FakeTextWidget instances instead of real tkinter widgets.
    """
    stub = _types.SimpleNamespace()
    stub.body_text    = FakeTextWidget(body)
    stub.headers_text = FakeTextWidget(headers)
    stub.req_text     = FakeTextWidget(req)

    stub._notebook = MagicMock()
    stub._notebook.select.return_value = 'tab_id'
    stub._notebook.index.return_value  = active_tab

    stub._search_matches: list = []
    stub._search_idx: int = 0
    stub._search_var = MagicMock()
    stub._search_var.get.return_value = term
    stub._search_count_lbl = MagicMock()

    for name in ('_search_update', '_search_next', '_search_prev',
                 '_search_jump', '_search_clear_tags', '_active_tab_widget'):
        setattr(stub, name, _types.MethodType(getattr(CurlApp, name), stub))

    return stub


# ===========================================================================
# 19. FakeTextWidget correctness
# ===========================================================================

class TestFakeTextWidget(unittest.TestCase):

    def test_search_single_line_found(self):
        w = FakeTextWidget('hello world')
        self.assertEqual(w.search('world', '1.0'), '1.6')

    def test_search_single_line_not_found(self):
        w = FakeTextWidget('hello world')
        self.assertEqual(w.search('foo', '1.0'), '')

    def test_search_case_insensitive(self):
        w = FakeTextWidget('Hello World')
        self.assertEqual(w.search('hello', '1.0', nocase=True), '1.0')

    def test_search_case_sensitive_miss(self):
        w = FakeTextWidget('Hello World')
        self.assertEqual(w.search('hello', '1.0', nocase=False), '')

    def test_search_start_after_first_match(self):
        w = FakeTextWidget('aa bb aa')
        first = w.search('aa', '1.0')
        self.assertEqual(first, '1.0')
        second = w.search('aa', f'{first}+2c')
        self.assertEqual(second, '1.6')

    def test_search_multiline_second_line(self):
        w = FakeTextWidget('first\nsecond')
        self.assertEqual(w.search('second', '1.0'), '2.0')

    def test_search_multiline_mid_line(self):
        w = FakeTextWidget('foo bar\nbaz qux')
        self.assertEqual(w.search('qux', '1.0'), '2.4')

    def test_tag_add_and_retrieve(self):
        w = FakeTextWidget()
        w.tag_add('found', '1.0', '1.5')
        self.assertIn('found', w.tags)
        self.assertEqual(w.tags['found'], [('1.0', '1.5')])

    def test_tag_remove_clears(self):
        w = FakeTextWidget()
        w.tag_add('found', '1.0', '1.5')
        w.tag_remove('found', '1.0', '1.5')
        self.assertNotIn('found', w.tags)

    def test_see_recorded(self):
        w = FakeTextWidget()
        w.see('3.0')
        self.assertEqual(w.seen, ['3.0'])

    def test_offset_plus_format(self):
        w = FakeTextWidget('hello world')
        # '1.0+5c' should point to offset 5 → 'w' in 'world'
        self.assertEqual(w.search('world', '1.0+5c'), '1.6')

    def test_multiline_to_offset(self):
        w = FakeTextWidget('ab\ncd\nef')
        # line 3, char 0 → offset 6
        self.assertEqual(w._to_offset('3.0'), 6)

    def test_multiline_from_offset(self):
        w = FakeTextWidget('ab\ncd\nef')
        self.assertEqual(w._from_offset(6), '3.0')


# ===========================================================================
# 20. _active_tab_widget
# ===========================================================================

class TestActiveTabWidget(unittest.TestCase):

    def _stub(self, idx):
        return _make_search_stub(active_tab=idx)

    def test_index_0_returns_body(self):
        s = self._stub(0)
        self.assertIs(s._active_tab_widget(), s.body_text)

    def test_index_1_returns_headers(self):
        s = self._stub(1)
        self.assertIs(s._active_tab_widget(), s.headers_text)

    def test_index_2_returns_req(self):
        s = self._stub(2)
        self.assertIs(s._active_tab_widget(), s.req_text)

    def test_exception_falls_back_to_body(self):
        s = _make_search_stub()
        s._notebook.index.side_effect = Exception("no tab")
        self.assertIs(s._active_tab_widget(), s.body_text)


# ===========================================================================
# 21. _search_clear_tags
# ===========================================================================

class TestSearchClearTags(unittest.TestCase):

    def test_removes_search_match_tag(self):
        s = _make_search_stub()
        s.body_text.tag_add('search_match', '1.0', '1.5')
        s._search_clear_tags(s.body_text)
        self.assertNotIn('search_match', s.body_text.tags)

    def test_removes_search_current_tag(self):
        s = _make_search_stub()
        s.body_text.tag_add('search_current', '2.3', '2.8')
        s._search_clear_tags(s.body_text)
        self.assertNotIn('search_current', s.body_text.tags)

    def test_unrelated_tags_preserved(self):
        s = _make_search_stub()
        s.body_text.tag_add('custom', '1.0', '1.3')
        s.body_text.tag_add('search_match', '1.0', '1.3')
        s._search_clear_tags(s.body_text)
        self.assertIn('custom', s.body_text.tags)


# ===========================================================================
# 22. _search_update
# ===========================================================================

class TestSearchUpdate(unittest.TestCase):

    def test_empty_term_clears_matches(self):
        s = _make_search_stub(body='hello world', term='')
        s._search_matches = ['1.0', '1.6']
        s._search_update()
        self.assertEqual(s._search_matches, [])

    def test_empty_term_resets_idx(self):
        s = _make_search_stub(body='hello world', term='')
        s._search_idx = 3
        s._search_update()
        self.assertEqual(s._search_idx, 0)

    def test_empty_term_clears_count_label(self):
        s = _make_search_stub(body='hello', term='')
        s._search_update()
        s._search_count_lbl.config.assert_called_with(text='')

    def test_finds_single_match(self):
        s = _make_search_stub(body='hello world', term='hello')
        s._search_update()
        self.assertEqual(len(s._search_matches), 1)

    def test_finds_multiple_matches(self):
        s = _make_search_stub(body='aa bb aa cc aa', term='aa')
        s._search_update()
        self.assertEqual(len(s._search_matches), 3)

    def test_no_match_shows_label(self):
        s = _make_search_stub(body='hello world', term='xyz')
        s._search_update()
        s._search_count_lbl.config.assert_called_with(text='No match')

    def test_match_count_label_format(self):
        s = _make_search_stub(body='a a a', term='a')
        s._search_update()
        # Called with '1/3' (first jump triggers this)
        calls = [str(c) for c in s._search_count_lbl.config.call_args_list]
        self.assertTrue(any('1/3' in c for c in calls))

    def test_match_tag_added_for_each_occurrence(self):
        s = _make_search_stub(body='hi bye hi', term='hi')
        s._search_update()
        self.assertEqual(len(s.body_text.tags.get('search_match', [])), 2)

    def test_current_tag_set_on_first_match(self):
        s = _make_search_stub(body='foo bar foo', term='foo')
        s._search_update()
        self.assertIn('search_current', s.body_text.tags)

    def test_case_insensitive_search(self):
        s = _make_search_stub(body='Hello HELLO hello', term='hello')
        s._search_update()
        self.assertEqual(len(s._search_matches), 3)

    def test_clears_all_three_widgets_before_search(self):
        s = _make_search_stub(body='x', headers='x', req='x', term='')
        for w in (s.body_text, s.headers_text, s.req_text):
            w.tag_add('search_match', '1.0', '1.1')
        s._search_update()
        for w in (s.body_text, s.headers_text, s.req_text):
            self.assertNotIn('search_match', w.tags)

    def test_searches_only_active_tab(self):
        s = _make_search_stub(body='needle', headers='needle', req='needle',
                              active_tab=1, term='needle')
        s._search_update()
        # Only headers_text (tab 1) should have search tags
        self.assertIn('search_match', s.headers_text.tags)
        self.assertNotIn('search_match', s.body_text.tags)
        self.assertNotIn('search_match', s.req_text.tags)

    def test_multiline_text_finds_all(self):
        body = 'line1\nfoo here\nline3\nfoo again\nend'
        s = _make_search_stub(body=body, term='foo')
        s._search_update()
        self.assertEqual(len(s._search_matches), 2)

    def test_match_positions_in_order(self):
        s = _make_search_stub(body='ab cd ab', term='ab')
        s._search_update()
        pos0, pos1 = s._search_matches
        # First match must come before second (line.char comparison by string)
        self.assertLess(pos0, pos1)

    def test_highlight_config_colors_applied(self):
        s = _make_search_stub(body='test text', term='test')
        s._search_update()
        self.assertIn('search_match',   s.body_text.tag_configs)
        self.assertIn('search_current', s.body_text.tag_configs)
        self.assertEqual(s.body_text.tag_configs['search_current']['background'], ACCENT)


# ===========================================================================
# 23. _search_jump
# ===========================================================================

class TestSearchJump(unittest.TestCase):

    def _stub_with_matches(self, positions, term='hi'):
        s = _make_search_stub(body='hi there hi', term=term)
        s._search_matches = positions
        return s

    def test_see_called_with_match_position(self):
        s = self._stub_with_matches(['1.0', '1.9'])
        s._search_jump(0)
        self.assertIn('1.0', s.body_text.seen)

    def test_see_called_for_second_match(self):
        s = self._stub_with_matches(['1.0', '1.9'])
        s._search_jump(1)
        self.assertIn('1.9', s.body_text.seen)

    def test_current_tag_added_at_correct_position(self):
        s = self._stub_with_matches(['1.3'])
        s._search_jump(0)
        self.assertIn('search_current', s.body_text.tags)
        start, _ = s.body_text.tags['search_current'][0]
        self.assertEqual(start, '1.3')

    def test_count_label_shows_1_indexed(self):
        s = self._stub_with_matches(['1.0', '1.3', '1.6'])
        s._search_jump(1)
        s._search_count_lbl.config.assert_called_with(text='2/3')

    def test_noop_when_no_matches(self):
        s = _make_search_stub(body='hello')
        s._search_jump(0)
        self.assertEqual(s.body_text.seen, [])

    def test_previous_current_tag_removed_before_new(self):
        s = self._stub_with_matches(['1.0', '1.6'])
        # Pre-add current tag at first pos
        s.body_text.tag_add('search_current', '1.0', '1.2')
        s._search_jump(1)
        # After jump to idx=1, tags should only contain the new position
        current_positions = [start for start, _ in s.body_text.tags.get('search_current', [])]
        self.assertNotIn('1.0', current_positions)
        self.assertIn('1.6', current_positions)


# ===========================================================================
# 24. _search_next / _search_prev navigation
# ===========================================================================

class TestSearchNavigation(unittest.TestCase):

    def _stub_3matches(self):
        s = _make_search_stub(body='x x x', term='x')
        s._search_matches = ['1.0', '1.2', '1.4']
        s._search_idx = 0
        return s

    # ---- next ----

    def test_next_advances_index(self):
        s = self._stub_3matches()
        s._search_next()
        self.assertEqual(s._search_idx, 1)

    def test_next_advances_twice(self):
        s = self._stub_3matches()
        s._search_next()
        s._search_next()
        self.assertEqual(s._search_idx, 2)

    def test_next_wraps_to_zero(self):
        s = self._stub_3matches()
        s._search_idx = 2
        s._search_next()
        self.assertEqual(s._search_idx, 0)

    def test_next_noop_when_no_matches(self):
        s = _make_search_stub()
        s._search_next()
        self.assertEqual(s._search_idx, 0)

    def test_next_calls_jump(self):
        s = self._stub_3matches()
        s._search_next()
        self.assertIn('1.2', s.body_text.seen)

    def test_next_updates_count_label(self):
        s = self._stub_3matches()
        s._search_next()
        s._search_count_lbl.config.assert_called_with(text='2/3')

    # ---- prev ----

    def test_prev_decrements_index(self):
        s = self._stub_3matches()
        s._search_idx = 2
        s._search_prev()
        self.assertEqual(s._search_idx, 1)

    def test_prev_wraps_to_last(self):
        s = self._stub_3matches()
        s._search_idx = 0
        s._search_prev()
        self.assertEqual(s._search_idx, 2)

    def test_prev_noop_when_no_matches(self):
        s = _make_search_stub()
        s._search_prev()
        self.assertEqual(s._search_idx, 0)

    def test_prev_calls_jump(self):
        s = self._stub_3matches()
        s._search_idx = 1
        s._search_prev()
        self.assertIn('1.0', s.body_text.seen)

    def test_prev_updates_count_label(self):
        s = self._stub_3matches()
        s._search_idx = 2
        s._search_prev()
        s._search_count_lbl.config.assert_called_with(text='2/3')

    # ---- combined ----

    def test_next_prev_roundtrip(self):
        s = self._stub_3matches()
        s._search_next()
        s._search_next()
        s._search_prev()
        self.assertEqual(s._search_idx, 1)

    def test_single_match_next_wraps_to_itself(self):
        s = _make_search_stub(body='hi', term='hi')
        s._search_matches = ['1.0']
        s._search_idx = 0
        s._search_next()
        self.assertEqual(s._search_idx, 0)

    def test_single_match_prev_wraps_to_itself(self):
        s = _make_search_stub(body='hi', term='hi')
        s._search_matches = ['1.0']
        s._search_idx = 0
        s._search_prev()
        self.assertEqual(s._search_idx, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
