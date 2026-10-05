"""Committed, policy-authorised transfers with portable archive boundaries."""
from __future__ import annotations

import fnmatch
import io
import re
import subprocess
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

MAX_BYTES = 64 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024
MAX_FILES = 10000
STRIPPED = {'.git', '.mcp.json', '.claude', '.codex', '.kimi-code', '.agents',
            '.ssh', '.gnupg', '.aws', '.kube', '.docker', 'node_modules', '__pycache__'}
SECRET_NAME = re.compile(
    r'(?i)^(?:\.env(?:\..*)?|.*\.env|id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?|'
    r'.*\.(?:pem|key|p12|pfx|jks|keystore|tfstate|tfvars)(?:\..*)?|'
    r'credentials(?:\..*)?|secrets?(?:\..*)?|.*kubeconfig.*|'
    r'\.netrc|\.git-credentials|\.npmrc|\.pypirc|\.pgpass)$')
TOKEN = re.compile(
    rb'-----BEGIN [A-Z ]*PRIVATE KEY|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|'
    rb'\bgh[pousr]_[A-Za-z0-9]{36,}|\bgithub_pat_[A-Za-z0-9_]{50,}|'
    rb'\bsk-[A-Za-z0-9_-]{20,}|\bxox[baprs]-[A-Za-z0-9-]{10,}|'
    rb'\bglpat-[A-Za-z0-9_-]{20,}|\bAIza[A-Za-z0-9_-]{35}|'
    rb'\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}')
ASSIGN = re.compile(
    rb'(?i)(?:password|secret|token|api[_-]?key|private[_-]?key)'
    rb'[\w-]*[\"\x27]?\s*[:=]\s*[\"\x27]?([^\s\"\x27,;}]{16,200})')


class TransferError(ValueError):
    """An authorization or archive boundary refused a transfer."""


def _git(repo, *args):
    proc = subprocess.run(['git', '-C', str(repo), *args], capture_output=True)
    if proc.returncode:
        raise TransferError('git operation failed; check repository and committed HEAD')
    return proc.stdout


def normalize_origin(value: str) -> str:
    value = value.strip()
    if '://' in value:
        parsed = urlsplit(value)
        if parsed.scheme not in {'https', 'ssh', 'git'} or not parsed.hostname:
            raise TransferError('unsupported repository origin')
        result = parsed.hostname + parsed.path
    else:
        result = re.sub(r'^[^/@]+@', '', value).replace(':', '/', 1)
    result = result.rstrip('/')
    if result.endswith('.git'):
        result = result[:-4]
    if not re.fullmatch(r'[A-Za-z0-9.-]+/[A-Za-z0-9_./-]+', result):
        raise TransferError('invalid repository origin')
    if any(part in {'', '.', '..'} for part in result.split('/')):
        raise TransferError('invalid repository origin')
    return result.casefold()


def authorize(policy, estate_id, repo_path, tool, vendor):
    estates = policy.get('estates', []) if isinstance(policy, dict) else policy
    if not isinstance(estates, list):
        raise TransferError('context-policy missing or malformed')
    matches = [e for e in estates if isinstance(e, dict) and e.get('id') == estate_id]
    if len(matches) != 1:
        raise TransferError('estate is not authorised')
    estate = dict(matches[0])
    if tool not in estate.get('snapshot_targets', []):
        raise TransferError('tool is not an allowed estate target')
    expected = {'claude': 'anthropic', 'codex': 'openai', 'kimi': 'moonshot'}
    if expected.get(tool) != vendor:
        raise TransferError('tool vendor does not match directory')
    vendors = estate.get('vendors_allowed')
    if vendors is None and isinstance(policy, dict):
        vendors = policy.get('vendors_allowed', {})
    if not isinstance(vendors, dict) or vendor not in vendors.get(estate.get('data_class'), []):
        raise TransferError('vendor is not allowed for estate data class')
    origin = normalize_origin(_git(repo_path, 'config', '--get', 'remote.origin.url').decode().strip())
    allowed = [normalize_origin(v) for v in estate.get('repos', [])]
    if origin not in allowed:
        raise TransferError('repository origin is not allowed for estate')
    deny = estate.get('deny_globs', [])
    if not isinstance(deny, list) or not all(isinstance(v, str) for v in deny):
        raise TransferError('malformed estate deny rules')
    for pattern in deny:
        if any(fnmatch.fnmatchcase(v.casefold(), pattern.casefold())
               for v in (origin, str(Path(repo_path).resolve()).replace('\\', '/'))):
            raise TransferError('repository matches estate deny rule')
    estate['_origin'] = origin
    return estate


def _parts(name):
    if len(name) > 1000 or '\\' in name or name.startswith('/'):
        raise TransferError('unsafe archive path')
    parts = name.rstrip('/').split('/')
    for part in parts:
        if (part in {'', '.', '..'} or part.endswith(('.', ' '))
                or any(ord(c) < 32 or c in ':<>"|?*' or ord(c) == 127 for c in part)
                or re.fullmatch(r'(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part)):
            raise TransferError('unsafe archive path')
    return parts


def _strip(parts, deny):
    path = '/'.join(parts)
    return (any(p.casefold() in STRIPPED or SECRET_NAME.fullmatch(p) for p in parts)
            or any(fnmatch.fnmatchcase(path.casefold(), p.casefold())
                   or any(fnmatch.fnmatchcase(v.casefold(), p.casefold()) for v in parts)
                   for p in deny))


def _check_case(parts, spelling):
    for index in range(1, len(parts) + 1):
        name = '/'.join(parts[:index])
        previous = spelling.setdefault(name.casefold(), name)
        if previous != name:
            raise TransferError('case collision in archive')


def scan(data: bytes):
    if TOKEN.search(data):
        raise TransferError('secret content detected; transfer refused')
    for hit in ASSIGN.finditer(data):
        value = hit.group(1)
        if (not value.startswith((b'${', b'{{', b'<', b'example', b'placeholder'))
                and len(set(value)) > 8 and any(c in b'0123456789' for c in value)):
            raise TransferError('credential assignment detected; transfer refused')


def archive(repo_path, estate) -> bytes:
    origin = normalize_origin(_git(repo_path, 'config', '--get', 'remote.origin.url').decode().strip())
    if estate.get('_origin') != origin:
        raise TransferError('repository authorization changed')
    raw = _git(repo_path, 'archive', '--format=tar', 'HEAD')
    if len(raw) > MAX_BYTES:
        raise TransferError('archive exceeds transfer limit')
    target = io.BytesIO()
    count = total = 0
    seen = set()
    spelling = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as source:
        with tarfile.open(fileobj=target, mode='w', format=tarfile.PAX_FORMAT) as output:
            for member in source:
                parts = _parts(member.name)
                if _strip(parts, estate.get('deny_globs', [])):
                    continue
                _check_case(parts, spelling)
                if member.isdir():
                    continue
                if not member.isfile():
                    raise TransferError('links and special files are refused')
                key = member.name.casefold()
                if key in seen:
                    raise TransferError('case collision in archive')
                seen.add(key)
                count += 1
                total += member.size
                if member.size > MAX_FILE or total > MAX_BYTES or count > MAX_FILES:
                    raise TransferError('archive exceeds transfer limit')
                data = source.extractfile(member).read()
                scan(data)
                safe = tarfile.TarInfo(member.name)
                safe.size = len(data)
                safe.mode = 0o755 if member.mode & 0o111 else 0o644
                output.addfile(safe, io.BytesIO(data))
    return target.getvalue()


def extract_export(data: bytes, out) -> None:
    """Validate the entire export before writing into an empty directory."""
    if len(data) > MAX_BYTES:
        raise TransferError('export exceeds transfer limit')
    root = Path(out)
    if root.is_symlink() or (root.exists() and any(root.iterdir())):
        raise TransferError('export destination must be empty and not a link')
    parent = root.absolute()
    while parent != parent.parent:
        if parent.is_symlink():
            raise TransferError('export destination contains a link')
        parent = parent.parent
    files = []
    paths = {}
    spelling = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as source:
            for member in source:
                parts = _parts(member.name)
                _check_case(parts, spelling)
                if not (member.isfile() or member.isdir()):
                    raise TransferError('links and special files are refused')
                key = '/'.join(parts).casefold()
                if key in paths:
                    raise TransferError('duplicate or case collision in export')
                paths[key] = member.isdir()
                total += member.size
                if member.size > MAX_FILE or total > MAX_BYTES or len(paths) > MAX_FILES:
                    raise TransferError('export exceeds transfer limit')
                if member.isfile():
                    files.append((parts, source.extractfile(member).read()))
    except tarfile.TarError as exc:
        raise TransferError('invalid export archive') from exc
    for key in paths:
        components = key.split('/')
        if any(paths.get('/'.join(components[:i])) is False for i in range(1, len(components))):
            raise TransferError('file and directory collision in export')
    root.mkdir(parents=True, exist_ok=True)
    for parts, body in files:
        target = root.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(body)
