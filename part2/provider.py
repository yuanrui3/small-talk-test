"""Cloudflare Workers AI client for an owner-confirmed Workers Free account."""

from contextlib import closing, contextmanager
import json
import os
import re
import sqlite3
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


API_ORIGIN = "https://api.cloudflare.com"
MODEL = "@cf/openai/gpt-oss-120b"
SEED = 41393
DAILY_FREE_NEURONS = 10_000
DAILY_QUOTA_CODE = 3036
PURPOSES = {"chat", "reader_cold", "reader_history"}
RESPONSE_BYTES_LIMIT = 1_048_576
PAGE_SIZE = 50

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY,
    endpoint TEXT NOT NULL,
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at TEXT,
    elapsed_ms INTEGER,
    status TEXT NOT NULL,
    http_status INTEGER,
    provider_code INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    max_output_tokens INTEGER,
    charged_microusd INTEGER NOT NULL DEFAULT 0
)
"""

QUOTA_MESSAGE = "Cloudflare's daily free allocation is exhausted until 00:00 UTC."


class ProviderError(RuntimeError):
    """A provider or ledger error that omits the contents of the request and response."""


class ModelUnavailable(ProviderError):
    """The exact configured model was absent from the authenticated catalog."""


class BudgetExceeded(ProviderError):
    """Cloudflare rejected further requests under its daily free allocation."""


class _CredentialError(ProviderError):
    """Required Cloudflare environment variables are missing or invalid."""


class _RequestFailure(ProviderError):
    def __init__(self, status=None, code=None):
        message = "The Cloudflare request failed or returned an unreadable response."
        if status is not None:
            message = f"Cloudflare returned HTTP {status}."
        if status == 429 and code != DAILY_QUOTA_CODE:
            message = "Cloudflare rate-limited the request. Try again later."
        super().__init__(message)
        self.status = status
        self.code = code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _positive_integer(value):
    return type(value) is int and value > 0


def _encode(body):
    return json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _decode(response):
    raw = response.read(RESPONSE_BYTES_LIMIT + 1)
    if len(raw) > RESPONSE_BYTES_LIMIT:
        raise ValueError
    return json.loads(raw)


def _error_code(body):
    if not isinstance(body, dict):
        return None
    errors = body.get("errors")
    if isinstance(errors, list):
        codes = [error.get("code") for error in errors if isinstance(error, dict)]
        if DAILY_QUOTA_CODE in codes:
            return DAILY_QUOTA_CODE
        return next((code for code in codes if type(code) is int), None)
    error = body.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return code if type(code) is int else None


def _result(response):
    return response.get("result", response)


def _tokens(response):
    result = _result(response)
    usage = result.get("usage") if isinstance(result, dict) else None
    if not isinstance(usage, dict):
        usage = response.get("usage")
    if not isinstance(usage, dict):
        return None, None
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    return tuple(value if type(value) is int and value >= 0 else None for value in (input_tokens, output_tokens))


def _visible_text(result):
    if not isinstance(result, dict):
        raise ProviderError("Cloudflare returned an unreadable reply.")
    if "choices" in result:
        choices = result["choices"]
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError("Cloudflare returned an unreadable reply.")
        choice = choices[0]
        if choice.get("finish_reason") != "stop":
            raise ProviderError("Cloudflare did not finish a usable reply within the output limit.")
        message = choice.get("message")
        text = message.get("content") if isinstance(message, dict) else None
    else:
        text = result.get("response")
    if isinstance(text, dict):
        try:
            text = _encode(text).decode("utf-8")
        except (TypeError, ValueError):
            raise ProviderError("Cloudflare returned an unreadable reply.") from None
    if not isinstance(text, str) or not text.strip():
        raise ProviderError("Cloudflare returned an empty visible reply.")
    return text


class Client:
    def __init__(self, db_path, config, *, transport=None):
        self.db_path = db_path
        self.model = config.get("model")
        self.max_input_bytes = config.get("max_input_bytes", 115_000)
        self.max_output_tokens = config.get("max_output_tokens", 768)
        self.timeout_seconds = config.get("timeout_seconds", 25)
        self.free_neurons_per_day = config.get("free_neurons_per_day", DAILY_FREE_NEURONS)
        if self.model != MODEL or self.free_neurons_per_day != DAILY_FREE_NEURONS or not all(
            _positive_integer(value)
            for value in (self.max_input_bytes, self.max_output_tokens, self.timeout_seconds)
        ):
            raise ProviderError("The Workers AI pilot configuration is invalid.")

        self._transport = transport if transport is not None else self._http_request
        self._opener = build_opener(_NoRedirect())
        self._verify_lock = threading.Lock()
        self._verified = False
        with self._database() as connection:
            connection.execute(LEDGER_SCHEMA)

    @contextmanager
    def _database(self):
        try:
            with closing(sqlite3.connect(self.db_path, timeout=10)) as connection, connection:
                yield connection
        except sqlite3.Error:
            raise ProviderError("The local API usage ledger is unavailable.") from None

    def _http_request(self, method, path, body):
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{32}", account_id):
            raise _CredentialError("CLOUDFLARE_ACCOUNT_ID must be a 32-character hexadecimal account ID.")
        if not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token):
            raise _CredentialError("CLOUDFLARE_API_TOKEN is missing or invalid.")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{API_ORIGIN}/client/v4/accounts/{account_id}{path}"
        try:
            request = Request(url, data=_encode(body) if body is not None else None, headers=headers, method=method)
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                return _decode(response)
        except HTTPError as error:
            with error:
                try:
                    code = _error_code(_decode(error))
                except (OSError, TypeError, ValueError):
                    code = None
            raise _RequestFailure(error.code, code) from None
        except Exception:
            raise _RequestFailure() from None

    def _start_call(self, endpoint, purpose, max_tokens):
        with self._database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if endpoint == "messages" and connection.execute(
                """SELECT 1 FROM api_calls
                    WHERE endpoint = 'messages' AND model = ? AND status = 'quota'
                      AND date(finished_at) = date('now')
                    LIMIT 1""",
                (self.model,),
            ).fetchone():
                raise BudgetExceeded(QUOTA_MESSAGE)
            row = connection.execute(
                """INSERT INTO api_calls
                   (endpoint, purpose, model, status, max_output_tokens)
                   VALUES (?, ?, ?, 'pending', ?)""",
                (endpoint, purpose, self.model, max_tokens),
            )
            return row.lastrowid

    def _finish(self, call_id, status, start, *, http_status=None, code=None, tokens=(None, None)):
        elapsed_ms = round((time.monotonic() - start) * 1_000)
        with self._database() as connection:
            connection.execute(
                """UPDATE api_calls
                      SET status = ?, finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                          elapsed_ms = ?, http_status = ?, provider_code = ?,
                          input_tokens = ?, output_tokens = ?
                    WHERE id = ?""",
                (status, elapsed_ms, http_status, code, *tokens, call_id),
            )

    def _call(self, endpoint, purpose, method, path, body=None, max_tokens=None):
        call_id = self._start_call(endpoint, purpose, max_tokens)
        start = time.monotonic()
        try:
            response = self._transport(method, path, body)
            if not isinstance(response, dict):
                raise _RequestFailure()
            if response.get("success") is False or response.get("errors") or response.get("error"):
                raise _RequestFailure(200, _error_code(response))
        except _RequestFailure as error:
            if error.code == DAILY_QUOTA_CODE:
                self._finish(call_id, "quota", start, http_status=error.status, code=error.code)
                raise BudgetExceeded(QUOTA_MESSAGE) from None
            if error.status == 429:
                status = "rate_limited"
            elif error.status is not None and error.status < 500:
                status = "failed"
            else:
                status = "ambiguous"
            self._finish(call_id, status, start, http_status=error.status, code=error.code)
            raise
        except _CredentialError:
            self._finish(call_id, "failed", start)
            raise
        except Exception:
            self._finish(call_id, "ambiguous", start)
            raise _RequestFailure() from None
        self._finish(
            call_id, "complete", start, http_status=200,
            tokens=_tokens(response) if endpoint == "messages" else (None, None),
        )
        return response

    def verify_model(self) -> None:
        """Verify that the exact configured ID is in the account's Workers AI catalog."""
        with self._verify_lock:
            if self._verified:
                return
            page = 1
            seen_pages = set()
            while True:
                query = urlencode({"search": self.model, "page": page, "per_page": PAGE_SIZE})
                response = self._call("models", "verification", "GET", "/ai/models/search?" + query)
                results = response.get("result")
                if not isinstance(results, list):
                    raise ProviderError("Cloudflare returned an unreadable model catalog.")
                names = tuple(item.get("name") for item in results if isinstance(item, dict))
                if self.model in names:
                    self._verified = True
                    return
                info = response.get("result_info") or {}
                if not isinstance(info, dict):
                    raise ProviderError("Cloudflare returned unusable model pagination.")
                total_pages = info.get("total_pages")
                total_count = info.get("total_count")
                per_page = info.get("per_page") or PAGE_SIZE
                if not _positive_integer(per_page):
                    raise ProviderError("Cloudflare returned unusable model pagination.")
                if _positive_integer(total_pages):
                    more = page < total_pages
                elif type(total_count) is int:
                    more = page * per_page < total_count
                else:
                    more = len(results) >= per_page
                if not more:
                    raise ModelUnavailable("The exact pilot model is absent from the Cloudflare catalog.")
                if not results or names in seen_pages:
                    raise ProviderError("Cloudflare returned unusable model pagination.")
                seen_pages.add(names)
                page += 1

    def complete(self, purpose, system, messages, max_tokens, *, json_schema=None) -> str:
        """Run one stateless text completion without automatic retries."""
        if purpose not in PURPOSES or not isinstance(system, str):
            raise ProviderError("The pilot request has an invalid purpose or system message.")
        if not _positive_integer(max_tokens) or max_tokens > self.max_output_tokens:
            raise ProviderError("The requested output token cap is invalid.")
        if not isinstance(messages, list) or not messages or any(
            not isinstance(message, dict)
            or message.get("role") not in ("user", "assistant")
            or not isinstance(message.get("content"), str)
            or not message["content"].strip()
            for message in messages
        ):
            raise ProviderError("The pilot accepts only plain text user and assistant messages.")
        if messages[-1]["role"] != "user":
            raise ProviderError("The final pilot message must be a user message.")
        body = {
            "messages": [
                {"role": "system", "content": system},
                *({"role": message["role"], "content": message["content"]} for message in messages),
            ],
            "max_tokens": max_tokens,
            "seed": SEED,
            "stream": False,
        }
        if json_schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": json_schema}
        try:
            body_size = len(_encode(body))
        except (TypeError, ValueError):
            raise ProviderError("The pilot request cannot be encoded as JSON.") from None
        if body_size > self.max_input_bytes:
            raise ProviderError("The pilot request exceeds the local UTF-8 size limit.")

        self.verify_model()
        response = self._call("messages", purpose, "POST", "/ai/run/" + self.model, body, max_tokens)
        return _visible_text(_result(response))
