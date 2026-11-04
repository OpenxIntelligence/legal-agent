"""Secret-free status and atomic local configuration updates; never execute .env."""
import os
import re
import secrets
import stat
from urllib.parse import urlsplit

KEY_RULE = "Use 8–4096 letters, digits, dots, underscores, plus signs, slashes, equals signs or hyphens; no spaces or shell characters."
_KEY = re.compile(r"[A-Za-z0-9._+/=\-]{8,4096}\Z", re.ASCII)
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _parent_fd(path):
    """Walk by directory descriptors, refusing symlinks even in ancestors."""
    path = path.absolute()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(fd, name):
    try:
        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return ""
    try:
        metadata = os.fstat(source)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > 1_000_000:
            raise ValueError("Unsafe configuration file.")
        with os.fdopen(source, "r", encoding="utf-8", newline="") as stream:
            source = None
            text = stream.read(1_000_001)
        if len(text) > 1_000_000:
            raise ValueError("Unsafe configuration file.")
        return text
    finally:
        if source is not None:
            os.close(source)


def _values(text):
    values = {}
    for line in text.splitlines():
        match = _ASSIGNMENT.match(line)
        if match:
            values[match[1]] = match[2].strip().strip("\"'")
    return values


def status(path):
    """Only selected, bounded non-secret fields leave this function."""
    fd = None
    try:
        fd = _parent_fd(path)
        values = _values(_read(fd, path.name))
        model = values.get("MODEL_API_MODEL", "muse-spark-1.3")
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,120}", model):
            model = "custom model"
        try:
            host = urlsplit(values.get("MODEL_API_BASE_URL", "https://api.meta.ai/v1")).hostname or "custom provider"
            if not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", host):
                host = "custom provider"
        except ValueError:
            host = "custom provider"
        return {"api_key_configured": bool(values.get("MODEL_API_KEY")), "model": model, "provider_host": host}
    except (OSError, UnicodeError, ValueError):
        raise RuntimeError("Local configuration cannot be accessed safely.") from None
    finally:
        if fd is not None:
            os.close(fd)


def update_key(path, body):
    if not isinstance(body, dict) or not (set(body) == {"api_key"} or (set(body) == {"remove"} and body["remove"] is True)):
        raise ValueError("Send either an api_key or remove: true.")
    key = body.get("api_key")
    if key is not None and (not isinstance(key, str) or not _KEY.fullmatch(key)):
        raise ValueError(KEY_RULE)
    if "api_key" in body and key is None:
        raise ValueError(KEY_RULE)
    fd = None
    temporary = None
    try:
        fd = _parent_fd(path)
        text = _read(fd, path.name)
        lines = [line for line in text.splitlines(keepends=True)
                 if not ((match := _ASSIGNMENT.match(line)) and match[1] == "MODEL_API_KEY")]
        if key is not None:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += "\n"
            lines.append("MODEL_API_KEY='" + key + "'\n")
        temporary = ".legal-agent-env-" + secrets.token_hex(16)
        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out, "w", encoding="utf-8", newline="") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write("".join(lines))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd)
        temporary = None
        os.fsync(fd)
    except (OSError, UnicodeError):
        raise RuntimeError("Local configuration could not be updated safely.") from None
    finally:
        if temporary is not None and fd is not None:
            try:
                os.unlink(temporary, dir_fd=fd)
            except OSError:
                pass
        if fd is not None:
            os.close(fd)
