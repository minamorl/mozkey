"""Capture child errors and emit only bounded, redacted diagnostics."""

from datetime import datetime, timezone
import json
import os
import re
import shlex
import subprocess
import sys
from urllib.parse import quote


PUBLIC_ENV = (
    "PATH", "HOME", "TMPDIR", "RUNNER_TEMP", "LANG", "LC_ALL", "GITHUB_SHA",
    "GITHUB_ACTIONS", "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
)
URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
AUTH = re.compile(r"(?i)\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*[:=]")
SECRET = re.compile(r"(?i)\b(?:[\w-]*(?:token|password|passwd|secret|credential|api[_-]?key)[\w-]*)\s*[:=]")
TOKEN = re.compile(r"(?i)\b(?:gh[pousr]_|github_pat_|bearer\s+|basic\s+)[\w+/=.-]+")
SECRET_OPTION = re.compile(r"(?i)(?:^|\s)--[\w-]*(?:token|password|passwd|secret|credential|api[_-]?key)[\w-]*\s+\S")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MAX_LINE_CHARS = 480


def redact_line(line, secrets):
    def clean_url(match):
        value = match[0].split("?", 1)[0].split("#", 1)[0]
        scheme, address = value.split("://", 1)
        authority, slash, path = address.partition("/")
        return f"{scheme}://{authority.rsplit('@', 1)[-1]}{slash}{path}"

    line = ANSI_ESCAPE.sub("", line)
    for secret in secrets:
        if secret:
            line = line.replace(secret, "[redacted]").replace(quote(secret, safe=""), "[redacted]")
    if AUTH.search(line) or SECRET.search(line) or SECRET_OPTION.search(line):
        return "[redacted credential-bearing line]"
    return TOKEN.sub("[redacted]", URL.sub(clean_url, line))


def redacted_tail(output, secrets, lines):
    if not output or not output.strip(b"\r\n"):
        return []
    return [redact_line(line.rstrip(b"\r").decode("utf-8", errors="replace"), secrets)[-MAX_LINE_CHARS:]
            for line in output.rstrip(b"\r\n").rsplit(b"\n", lines)[-lines:]]


class CommandFailure(ValueError):
    def __init__(self, result, secrets=(), *, include_stdout=False):
        super().__init__("A producer command failed")
        self.details = {
            "command": redact_line(shlex.join(result.args), secrets),
            "exit_code": result.returncode,
            "stderr_tail": redacted_tail(result.stderr, secrets, 6) if result.returncode else [],
        }
        if include_stdout and result.returncode:
            self.details["stdout_tail"] = redacted_tail(result.stdout, secrets, 16)


def run_command(argv, *, cwd=None, stdout=subprocess.PIPE, auth=None):
    environment = {name: os.environ[name] for name in PUBLIC_ENV if name in os.environ}
    if auth is not None:
        environment["GH_TOKEN"] = auth
    return subprocess.run(argv, cwd=cwd, env=environment, stdout=stdout,
                          stderr=subprocess.PIPE, check=False)


def checked_output(argv, *, cwd=None, stdout=subprocess.PIPE, auth=None):
    result = run_command(argv, cwd=cwd, stdout=stdout, auth=auth)
    if result.returncode:
        raise CommandFailure(result, (auth,) if auth else ())
    return result.stdout


def emit_error(trace, code, message, details):
    print(json.dumps({
        "code": code, "message": message, "details": details,
        "ts": datetime.now(timezone.utc).isoformat(), "level": "error", "trace_id": trace, "msg": message,
    }), file=sys.stderr)
