#!/usr/bin/env python3
"""Check the public tree without printing matched private values."""

import ipaddress
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "__pycache__", ".pytest_cache"}
RULES = {
    "private-home-path": re.compile(r"/(?:Users|home)/[a-zA-Z][^\s`\"']+"),
    "private-vault-path": re.compile(r"80-" r"private/"),
    "private-share-link": re.compile(r"https://chatgpt\.com/share/[a-zA-Z0-9-]+"),
    "assigned-secret": re.compile(r"(?i)(?:api[_-]?(?:key|secret)|password|token)\s*[=:]\s*[\"']?[A-Za-z0-9+/]{32,}"),
    "signed-url": re.compile(r"[?&](?:signature|token|apiKey)=[A-Za-z0-9+/]{16,}"),
}
IP = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
EXAMPLE_NETWORKS = tuple(ipaddress.ip_network(value) for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "127.0.0.0/8"))


def main():
    git = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=False)
    names = [value.decode() for value in git.stdout.split(b"\0") if value]
    if not names:
        names = [str(path.relative_to(ROOT)) for path in ROOT.rglob("*") if path.is_file() and not any(part in SKIP for part in path.relative_to(ROOT).parts) and path.relative_to(ROOT).parts[0] not in {"data", "reports"}]
    issues = []
    manifest = json.loads((ROOT / "records/manifest.json").read_text())
    for entry in manifest["entries"]:
        published = entry.get("published")
        if published and published not in names:
            issues.append((published, "manifest-file-not-published"))
    for name in names:
        path = ROOT / name
        if path.is_symlink():
            issues.append((name, "symlink"))
            continue
        if path.name.startswith('.env') and path.name != '.env.example':
            issues.append((name, "credential-file"))
        if path.parts[len(ROOT.parts)] in {"data", "reports"}:
            issues.append((name, "raw-runtime-file"))
        text = path.read_text(encoding="utf-8")
        for label, pattern in RULES.items():
            if pattern.search(text):
                issues.append((name, label))
        for candidate in IP.findall(text):
            try:
                address = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if not any(address in network for network in EXAMPLE_NETWORKS):
                issues.append((name, "non-example-ip"))
    for name, label in sorted(set(issues)):
        print(f"FAIL {name}: {label}")
    if issues:
        raise SystemExit(1)
    print(f"Public-content checks passed: {len(names)} files")


if __name__ == "__main__":
    main()
