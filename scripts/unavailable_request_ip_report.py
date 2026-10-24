#!/usr/bin/env python3
"""Build a dedicated report for unavailable Binance request IPs."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CST = timezone(timedelta(hours=8))
DEFAULT_EVENTS = Path("data/processed/paper-signal-service/events.jsonl")
DEFAULT_EXECUTIONS = Path("data/processed/live-execution/executions.jsonl")
DEFAULT_OUTPUT = Path("reports/service/ip-stats/unavailable-request-ips.md")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"raw": line})
    return rows


def extract_request_ip(message: str) -> str | None:
    match = re.search(r"request ip: ([^\"}]+)", message)
    if not match:
        return None
    return match.group(1).strip().rstrip("\\")


def extract_endpoint(message: str) -> str | None:
    match = re.search(r"(/fapi/v\d+/[A-Za-z]+)", message)
    if not match:
        return None
    return match.group(1)


def extract_error_code(message: str) -> int | None:
    match = re.search(r'"code":\s*(-?\d+)', message)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def collect_messages(row: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    for value in (
        row.get("error"),
        row.get("result", {}).get("error") if isinstance(row.get("result"), dict) else None,
        row.get("message"),
        row.get("raw"),
    ):
        if value:
            messages.append(str(value))
    return messages


def row_ips(row: dict[str, Any], messages: list[str]) -> list[str]:
    direct = row.get("request_ip")
    ips: list[str] = []
    if isinstance(direct, str) and direct.strip():
        ips.append(direct.strip())
    for message in messages:
        request_ip = extract_request_ip(message)
        if request_ip:
            ips.append(request_ip)
    return list(dict.fromkeys(ips))


def row_unavailable(messages: list[str], ips: list[str]) -> bool:
    if ips:
        return True
    text = " ".join(messages).lower()
    return "invalid api-key, ip, or permissions" in text or '"code":-2015' in text


def fmt_time(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S %z")


def render_counter(counter: Counter[str], limit: int | None = None) -> str:
    items = counter.most_common(limit)
    if not items:
        return "-"
    return ", ".join(f"{key} x{count}" for key, count in items)


def build_report(events_path: Path, executions_path: Path, output_path: Path) -> Path:
    sources = {
        "events": read_jsonl(events_path),
        "executions": read_jsonl(executions_path),
    }
    now = datetime.now(CST)
    recent_cutoff = now - timedelta(hours=1)
    records: list[dict[str, Any]] = []
    ip_totals: Counter[str] = Counter()
    ip_recent_totals: Counter[str] = Counter()
    ip_event_types: dict[str, Counter[str]] = defaultdict(Counter)
    ip_endpoints: dict[str, Counter[str]] = defaultdict(Counter)
    ip_codes: dict[str, Counter[str]] = defaultdict(Counter)
    ip_sources: dict[str, Counter[str]] = defaultdict(Counter)
    ip_samples: dict[str, list[dict[str, str]]] = defaultdict(list)
    ip_first_seen: dict[str, datetime] = {}
    ip_last_seen: dict[str, datetime] = {}

    for source_name, rows in sources.items():
        for row in rows:
            messages = collect_messages(row)
            ips = row_ips(row, messages)
            if not row_unavailable(messages, ips):
                continue
            ts = parse_time(row.get("time"))
            endpoint = "-"
            error_code = "-"
            for message in messages:
                endpoint = extract_endpoint(message) or endpoint
                code = extract_error_code(message)
                if code is not None:
                    error_code = str(code)
                    break
            event_type = str(
                row.get("type")
                or row.get("event")
                or row.get("status")
                or row.get("mode")
                or "-"
            )
            for ip in ips:
                record = {
                    "time": fmt_time(ts),
                    "source": source_name,
                    "event_type": event_type,
                    "endpoint": endpoint,
                    "error_code": error_code,
                    "request_ip": ip,
                    "message": messages[0] if messages else "-",
                }
                records.append(record)
                ip_totals[ip] += 1
                ip_event_types[ip][event_type] += 1
                ip_endpoints[ip][endpoint] += 1
                ip_codes[ip][error_code] += 1
                ip_sources[ip][source_name] += 1
                if ts is not None and ts >= recent_cutoff:
                    ip_recent_totals[ip] += 1
                if ts is not None and (ip not in ip_first_seen or ts < ip_first_seen[ip]):
                    ip_first_seen[ip] = ts
                if ts is not None and (ip not in ip_last_seen or ts > ip_last_seen[ip]):
                    ip_last_seen[ip] = ts
                if len(ip_samples[ip]) < 5:
                    ip_samples[ip].append(record)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Unavailable Request IP Report",
        "",
        f"- Generated: {fmt_time(now)}",
        f"- Sources: `{events_path}`, `{executions_path}`",
        f"- Distinct unavailable IPs: {len(ip_totals)}",
        f"- Total unavailable records: {sum(ip_totals.values())}",
        f"- Last 1 hour unavailable records: {sum(ip_recent_totals.values())}",
        "",
        "## Summary",
        "",
        "| Request IP | Count | Last 1h | Endpoints | First Seen | Last Seen |",
        "|---|---:|---:|---|---|---|",
    ]
    for ip, count in ip_totals.most_common():
        lines.append(
            f"| `{ip}` | {count} | {ip_recent_totals.get(ip, 0)} | "
            f"{render_counter(ip_endpoints[ip], limit=3)} | "
            f"{fmt_time(ip_first_seen.get(ip))} | {fmt_time(ip_last_seen.get(ip))} |"
        )

    lines += [
        "",
        "## By IP",
        "",
    ]
    for ip, count in ip_totals.most_common():
        lines += [
            f"### `{ip}`",
            "",
            f"- Count: {count}",
            f"- Last 1 hour: {ip_recent_totals.get(ip, 0)}",
            f"- Sources: {render_counter(ip_sources[ip])}",
            f"- Event types: {render_counter(ip_event_types[ip], limit=8)}",
            f"- Endpoints: {render_counter(ip_endpoints[ip], limit=8)}",
            f"- Error codes: {render_counter(ip_codes[ip], limit=8)}",
            f"- First seen: {fmt_time(ip_first_seen.get(ip))}",
            f"- Last seen: {fmt_time(ip_last_seen.get(ip))}",
            "",
            "Sample records:",
            "",
        ]
        for sample in ip_samples[ip]:
            lines.append(
                f"- {sample['time']} [{sample['source']}] {sample['event_type']} "
                f"endpoint={sample['endpoint']} code={sample['error_code']}"
            )
        lines += [
            "",
            "---",
            "",
        ]

    if not ip_totals:
        lines += [
            "- No unavailable request IP records were found.",
            "",
        ]

    output_path.write_text("\n".join(lines))
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", default=str(DEFAULT_EVENTS))
    parser.add_argument("--executions", default=str(DEFAULT_EXECUTIONS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    print(
        build_report(
            events_path=Path(args.events),
            executions_path=Path(args.executions),
            output_path=Path(args.output),
        )
    )


if __name__ == "__main__":
    main()
