"""Normalize official Ubuntu mirror URIs on the ephemeral amd64 Actions runner."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re

from corrected_diagnostics import emit_error
from corrected_evidence import trace_id


UBUNTU_MIRROR = re.compile(
    r"https?://(?:(?:[a-z0-9-]+\.)*archive\.ubuntu\.com|security\.ubuntu\.com)/ubuntu/?", re.IGNORECASE,
)
UBUNTU_ARCHIVE = "https://archive.ubuntu.com/ubuntu"
UBUNTU_SECURITY = "https://security.ubuntu.com/ubuntu"
FIELD = re.compile(r"^([a-zA-Z][\w-]*):([^\r\n]*(?:\r?\n[ \t]+[^\r\n]*)*)", re.MULTILINE)
LIST_ENTRY = re.compile(
    r"^[ \t]*deb(?:-src)?[ \t]+(?:\[(?P<options>[^\]\r\n]*)\][ \t]+)?"
    r"(?P<uri>[^\s#]+)[ \t]+(?P<suite>[^\s#]+)",
)


def supports_amd64(architectures, added, removed):
    return "amd64" not in removed and (not architectures or "amd64" in architectures or "amd64" in added)


def official_uri(uri, security):
    if not UBUNTU_MIRROR.fullmatch(uri):
        return uri
    return UBUNTU_SECURITY if security else UBUNTU_ARCHIVE


def normalize_list(text):
    rewritten = []
    for line in text.splitlines(keepends=True):
        entry = LIST_ENTRY.match(line)
        if entry:
            options = dict(option.split("=", 1) for option in (entry["options"] or "").split() if "=" in option)
            if supports_amd64(*(options.get(key, "").split(",") if options.get(key) else ()
                                for key in ("arch", "arch+", "arch-"))):
                start, end = entry.span("uri")
                uri = official_uri(entry["uri"], entry["suite"].endswith("-security"))
                line = line[:start] + uri + line[end:]
        rewritten.append(line)
    return "".join(rewritten)


def normalize_stanza(stanza):
    fields = {match[1].lower(): match[2].split() for match in FIELD.finditer(stanza)}
    uris, suites = fields.get("uris", ()), fields.get("suites", ())
    if (not suites or not any(UBUNTU_MIRROR.fullmatch(uri) for uri in uris)
            or (fields.get("enabled") or ["yes"])[0].lower() == "no"
            or not supports_amd64(*(fields.get(key, ()) for key in (
                "architectures", "architectures-add", "architectures-remove",
            )))):
        return stanza

    archive = [suite for suite in suites if not suite.endswith("-security")]
    security = [suite for suite in suites if suite.endswith("-security")]
    split = bool(archive and security)

    def rewrite(group, is_security):
        def field(match):
            name, value = match.groups()
            if name.lower() == "uris":
                value = re.sub(r"\S+", lambda token: official_uri(token[0], is_security), value)
            elif name.lower() == "suites" and split:
                value = " " + " ".join(group)
            return name + ":" + value

        return FIELD.sub(field, stanza)

    if not split:
        return rewrite(suites, bool(security))
    newline = "\r\n" if "\r\n" in stanza else "\n"
    return rewrite(archive, False).rstrip("\r\n") + newline * 2 + rewrite(security, True)


def normalize_apt_sources(apt_root, architecture):
    if architecture != "amd64":
        return
    sources_dir = apt_root / "sources.list.d"
    for path in (apt_root / "sources.list", sources_dir / "ubuntu.sources", *sorted(sources_dir.glob("*.list"))):
        if path.is_symlink() or not path.is_file():
            continue
        original = path.read_bytes().decode("utf-8")
        if path.suffix == ".sources":
            paragraphs = re.split(r"(\r?\n[ \t]*\r?\n)", original)
            paragraphs[::2] = map(normalize_stanza, paragraphs[::2])
            rewritten = "".join(paragraphs)
        else:
            rewritten = normalize_list(original)
        if rewritten != original:
            path.write_bytes(rewritten.encode("utf-8"))


def main():
    trace = trace_id()
    scope = {"scope": "amd64 Actions runner APT", "fields": ["URIs", "Suites"]}
    if (os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REPOSITORY") != "minamorl/mozkey"
            or platform.system() != "Linux" or platform.machine() != "x86_64"):
        emit_error(trace, "APT_RUNNER_SCOPE", "Expected the own-fork amd64 Actions runner", scope)
        return 1
    try:
        normalize_apt_sources(Path("/etc/apt"), "amd64")
    except (OSError, UnicodeError):
        emit_error(trace, "APT_SOURCE_NORMALIZATION", "Could not normalize runner APT sources", scope)
        return 1
    print(json.dumps({**scope, "ts": datetime.now(timezone.utc).isoformat(), "level": "info",
                      "trace_id": trace, "msg": "Official Ubuntu HTTPS source normalization checked"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
