"""Data model and discovery helpers for HA LG Manager."""

from __future__ import annotations

import csv
import json
import ipaddress
import re
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SSDP_MULTICAST_IP = "239.255.255.250"
SSDP_PORT = 1900
SSDP_SEARCH_TARGETS = (
    "urn:lge-com:service:webos-second-screen:1",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
)


@dataclass
class InventoryTv:
    slug: str
    title: str
    room: str
    entity_id: str | None
    expected_source: str | None
    friendly_name_hints: list[str] = field(default_factory=list)
    wol_automation_aliases: list[str] = field(default_factory=list)


@dataclass
class ConfiguredTv:
    title: str
    entry_id: str
    unique_id: str | None
    ssdp_uuid: str | None
    host: str | None
    entity_id: str | None
    inventory: InventoryTv | None


@dataclass
class DiscoveredTv:
    ip: str
    mac: str | None
    uuid: str | None
    friendly_name: str | None
    manufacturer: str | None
    model_name: str | None
    source: str
    note: str | None = None
    ssdp_st: str | None = None


@dataclass
class ReconcileResult:
    title: str
    room: str
    entity_id: str | None
    classification: str
    confidence: str
    configured_host: str | None
    configured_uuid: str | None
    discovered_ip: str | None
    discovered_mac: str | None
    discovered_uuid: str | None
    notes: list[str] = field(default_factory=list)


@dataclass
class WolActionRecord:
    alias: str
    source_type: str
    mac: str | None
    broadcast_address: str | None
    broadcast_port: int | None


@dataclass
class WakeTarget:
    ip: str
    mac: str | None
    broadcast_address: str | None
    source: str
    label: str


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def normalize_uuid(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if text.startswith("uuid:"):
        text = text[5:]
    if "::" in text:
        text = text.split("::", 1)[0]
    return text.lower() or None


def normalize_mac(value: str | None) -> str | None:
    if not value:
        return None
    raw = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(raw) != 12:
        return None
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2)).upper()


def network_broadcast_for_ip(ip_address: str, adapter_networks: list[str]) -> str | None:
    """Find a broadcast address for an IP based on known local adapter networks."""
    try:
        ip_obj = ipaddress.ip_address(ip_address)
    except ValueError:
        return None
    for network_text in adapter_networks:
        try:
            network = ipaddress.ip_network(network_text, strict=False)
        except ValueError:
            continue
        if ip_obj in network and isinstance(network, ipaddress.IPv4Network):
            return str(network.broadcast_address)
    return None


def load_inventory(path: Path) -> tuple[dict[str, InventoryTv], dict[str, InventoryTv]]:
    if not path.exists():
        return {}, {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = payload.get("defaults", {})
    inventory_by_slug: dict[str, InventoryTv] = {}
    inventory_by_title: dict[str, InventoryTv] = {}
    for slug, raw in payload.get("tvs", {}).items():
        tv = InventoryTv(
            slug=slug,
            title=raw["title"],
            room=raw.get("room", raw["title"]),
            entity_id=raw.get("entity_id"),
            expected_source=raw.get("expected_source", defaults.get("expected_source")),
            friendly_name_hints=list(raw.get("friendly_name_hints", [])),
            wol_automation_aliases=list(raw.get("wol_automation_aliases", [])),
        )
        inventory_by_slug[slug] = tv
        inventory_by_title[tv.title] = tv
    return inventory_by_slug, inventory_by_title


def load_yaml_file(path: Path) -> Any:
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_firewall_clients(path: Path | None) -> list[DiscoveredTv]:
    if path is None or not path.exists():
        return []
    devices: list[DiscoveredTv] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            manufacturer = (row.get("Manufacturer") or "").strip()
            description = (row.get("Description") or "").strip()
            notes = (row.get("Notes") or "").strip()
            searchable = " ".join([manufacturer, description, notes]).lower()
            if "lg" not in searchable and "webos" not in searchable:
                continue
            ip_address = (row.get("IPv4 address") or "").strip()
            if not ip_address:
                continue
            devices.append(
                DiscoveredTv(
                    ip=ip_address,
                    mac=normalize_mac(row.get("MAC address")),
                    uuid=None,
                    friendly_name=notes or description or None,
                    manufacturer=manufacturer or None,
                    model_name=None,
                    source="firewall_csv",
                    note=row.get("Status") or None,
                )
            )
    return devices


def iter_meraki_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "clients", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def load_meraki_clients(api_url: str | None, api_key: str | None) -> list[DiscoveredTv]:
    if not api_url or not api_key:
        return []
    request = urllib.request.Request(
        api_url,
        headers={
            "X-Cisco-Meraki-API-Key": api_key,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
        return []

    devices: list[DiscoveredTv] = []
    for row in iter_meraki_items(payload):
        manufacturer = str(row.get("manufacturer") or "").strip()
        description = str(
            row.get("description")
            or row.get("name")
            or row.get("dhcpHostname")
            or row.get("recentDeviceName")
            or ""
        ).strip()
        notes = str(row.get("notes") or row.get("deviceName") or "").strip()
        searchable = " ".join([manufacturer, description, notes]).lower()
        if "lg" not in searchable and "webos" not in searchable:
            continue
        ip_address = str(row.get("ip") or row.get("ip6") or "").strip()
        if not ip_address or ":" in ip_address:
            continue
        devices.append(
            DiscoveredTv(
                ip=ip_address,
                mac=normalize_mac(str(row.get("mac") or row.get("clientMac") or "")),
                uuid=None,
                friendly_name=notes or description or None,
                manufacturer=manufacturer or None,
                model_name=None,
                source="meraki_api",
                note=str(row.get("vlan") or row.get("status") or "").strip() or None,
            )
        )
    return devices


def parse_ssdp_headers(payload: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in payload.decode("utf-8", errors="ignore").split("\r\n"):
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers


def send_ssdp_probe(sock: socket.socket, search_target: str) -> None:
    message = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_MULTICAST_IP}:{SSDP_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        f"ST: {search_target}\r\n"
        "\r\n"
    )
    sock.sendto(message.encode("ascii"), (SSDP_MULTICAST_IP, SSDP_PORT))


def _iter_wol_actions(actions: Any) -> list[dict[str, Any]]:
    if not isinstance(actions, list):
        return []
    matches: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        service = action.get("action") or action.get("service")
        if service == "wake_on_lan.send_magic_packet":
            matches.append(action)
            continue
        for nested_key in ("sequence", "then", "else", "default", "parallel"):
            nested_value = action.get(nested_key)
            if isinstance(nested_value, list):
                matches.extend(_iter_wol_actions(nested_value))
        if isinstance(action.get("choose"), list):
            for choose_item in action["choose"]:
                if isinstance(choose_item, dict):
                    matches.extend(_iter_wol_actions(choose_item.get("sequence")))
    return matches


def load_wol_action_records(automations_path: Path, scripts_path: Path) -> dict[str, WolActionRecord]:
    records: dict[str, WolActionRecord] = {}

    automations_payload = load_yaml_file(automations_path)
    if isinstance(automations_payload, list):
        for item in automations_payload:
            if not isinstance(item, dict):
                continue
            alias = item.get("alias")
            if not alias:
                continue
            for action in _iter_wol_actions(item.get("actions", item.get("action"))):
                data = action.get("data") or {}
                records[alias] = WolActionRecord(
                    alias=alias,
                    source_type="automation",
                    mac=normalize_mac(data.get("mac")),
                    broadcast_address=data.get("broadcast_address"),
                    broadcast_port=int(data["broadcast_port"]) if str(data.get("broadcast_port", "")).isdigit() else None,
                )
                break

    scripts_payload = load_yaml_file(scripts_path)
    if isinstance(scripts_payload, dict):
        for script_key, item in scripts_payload.items():
            if not isinstance(item, dict):
                continue
            alias = item.get("alias") or script_key
            for action in _iter_wol_actions(item.get("sequence")):
                data = action.get("data") or {}
                records[alias] = WolActionRecord(
                    alias=alias,
                    source_type="script",
                    mac=normalize_mac(data.get("mac")),
                    broadcast_address=data.get("broadcast_address"),
                    broadcast_port=int(data["broadcast_port"]) if str(data.get("broadcast_port", "")).isdigit() else None,
                )
                break

    return records


def create_ssdp_socket(source_ip: str, timeout_seconds: float) -> socket.socket:
    """Create a UDP socket that sends SSDP from a specific source address."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(timeout_seconds)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(source_ip))
    sock.bind((source_ip, 0))
    return sock


def fetch_device_description(location: str | None) -> dict[str, str | None]:
    if not location:
        return {}
    try:
        with urllib.request.urlopen(location, timeout=3) as response:
            xml_payload = response.read()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return {}
    try:
        root = ET.fromstring(xml_payload)
    except ET.ParseError:
        return {}

    def text(path: str) -> str | None:
        node = root.find(path)
        return node.text.strip() if node is not None and node.text else None

    return {
        "friendly_name": text(".//{*}friendlyName"),
        "manufacturer": text(".//{*}manufacturer"),
        "model_name": text(".//{*}modelName"),
        "udn": text(".//{*}UDN"),
    }


def resolve_mac(ip_address: str) -> str | None:
    for command in (["ip", "neigh", "show", ip_address], ["arp", "-n", ip_address]):
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=3,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            continue
        output = "\n".join(chunk for chunk in (completed.stdout, completed.stderr) if chunk)
        match = re.search(r"\b([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\b", output)
        if match:
            return normalize_mac(match.group(1))
    return None


def discover_ssdp_devices(
    source_ips: list[str] | None = None,
    timeout_seconds: float = 2.0,
    attempts: int = 2,
) -> list[DiscoveredTv]:
    source_ips = source_ips or ["0.0.0.0"]
    responses: dict[tuple[str, str | None], DiscoveredTv] = {}
    for source_ip in source_ips:
        try:
            sock = create_ssdp_socket(source_ip, timeout_seconds)
        except OSError:
            continue
        try:
            for _ in range(attempts):
                for search_target in SSDP_SEARCH_TARGETS:
                    send_ssdp_probe(sock, search_target)
                while True:
                    try:
                        payload, address = sock.recvfrom(8192)
                    except TimeoutError:
                        break
                    headers = parse_ssdp_headers(payload)
                    location = headers.get("location")
                    parsed_location = urllib.parse.urlparse(location) if location else None
                    ip_address = parsed_location.hostname if parsed_location and parsed_location.hostname else address[0]
                    device_xml = fetch_device_description(location)
                    manufacturer = device_xml.get("manufacturer")
                    friendly_name = device_xml.get("friendly_name")
                    model_name = device_xml.get("model_name")
                    searchable = " ".join(
                        value for value in (manufacturer, friendly_name, model_name, headers.get("server")) if value
                    ).lower()
                    if not any(token in searchable for token in ("lg", "webos", "lge")):
                        continue
                    tv = DiscoveredTv(
                        ip=ip_address,
                        mac=resolve_mac(ip_address),
                        uuid=normalize_uuid(device_xml.get("udn") or headers.get("usn")),
                        friendly_name=friendly_name,
                        manufacturer=manufacturer,
                        model_name=model_name,
                        source="ssdp",
                        note=location,
                        ssdp_st=headers.get("st"),
                    )
                    responses[(tv.ip, tv.uuid)] = tv
        finally:
            sock.close()
    return list(responses.values())


def dedupe_discovered(devices: list[DiscoveredTv]) -> list[DiscoveredTv]:
    deduped: dict[str, DiscoveredTv] = {}
    for device in devices:
        existing = deduped.get(device.ip)
        if existing is None:
            deduped[device.ip] = device
            continue

        existing_is_webos = bool(existing.ssdp_st and "webos-second-screen" in existing.ssdp_st)
        device_is_webos = bool(device.ssdp_st and "webos-second-screen" in device.ssdp_st)

        if device.source == "ssdp" and existing.source != "ssdp":
            deduped[device.ip] = device
            continue
        if device_is_webos and not existing_is_webos:
            deduped[device.ip] = device
            continue

        # Merge missing details from additional records seen on the same IP.
        if not existing.uuid and device.uuid:
            existing.uuid = device.uuid
        if not existing.mac and device.mac:
            existing.mac = device.mac
        if not existing.friendly_name and device.friendly_name:
            existing.friendly_name = device.friendly_name
        if not existing.manufacturer and device.manufacturer:
            existing.manufacturer = device.manufacturer
        if not existing.model_name and device.model_name:
            existing.model_name = device.model_name
        if not existing.note and device.note:
            existing.note = device.note
        if not existing.ssdp_st and device.ssdp_st:
            existing.ssdp_st = device.ssdp_st
    return list(deduped.values())


def score_candidate(configured: ConfiguredTv, candidate: DiscoveredTv) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    hints = configured.inventory.friendly_name_hints if configured.inventory else []
    if configured.unique_id and candidate.uuid and configured.unique_id == candidate.uuid:
        score += 100
        reasons.append("matched unique_id")
    if configured.ssdp_uuid and candidate.uuid and configured.ssdp_uuid == candidate.uuid:
        score += 90
        reasons.append("matched ssdp uuid")
    if configured.host and candidate.ip == configured.host:
        score += 40
        reasons.append("matched configured host")
    candidate_name = normalize_text(candidate.friendly_name or "")
    configured_name = normalize_text(configured.title)
    if candidate_name and candidate_name == configured_name:
        score += 50
        reasons.append("matched title")
    for hint in hints:
        normalized_hint = normalize_text(hint)
        if normalized_hint and normalized_hint in candidate_name:
            score += 30
            reasons.append(f"matched hint {hint}")
    if candidate.note and configured_name in normalize_text(candidate.note):
        score += 30
        reasons.append("matched firewall note")
    return score, reasons


def classify_candidate(configured: ConfiguredTv, candidate: DiscoveredTv, reasons: list[str]) -> tuple[str, str]:
    confidence = "low"
    if any(reason.startswith("matched unique_id") or reason.startswith("matched ssdp uuid") for reason in reasons):
        confidence = "high"
    elif any(reason.startswith("matched title") or reason.startswith("matched firewall note") for reason in reasons):
        confidence = "medium"

    if candidate.uuid and configured.unique_id and candidate.uuid != configured.unique_id:
        return "replacement_candidate", confidence
    if candidate.uuid and configured.ssdp_uuid and candidate.uuid != configured.ssdp_uuid:
        return "replacement_candidate", confidence
    if configured.host != candidate.ip:
        return "ip_changed", confidence
    return "unchanged", confidence


def reconcile_tvs(configured_tvs: list[ConfiguredTv], discovered_tvs: list[DiscoveredTv]) -> list[ReconcileResult]:
    results: list[ReconcileResult] = []
    for configured in configured_tvs:
        ranked: list[tuple[int, list[str], DiscoveredTv]] = []
        for candidate in discovered_tvs:
            score, reasons = score_candidate(configured, candidate)
            if score > 0:
                ranked.append((score, reasons, candidate))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            results.append(
                ReconcileResult(
                    title=configured.title,
                    room=configured.inventory.room if configured.inventory else configured.title,
                    entity_id=configured.entity_id,
                    classification="missing",
                    confidence="low",
                    configured_host=configured.host,
                    configured_uuid=configured.unique_id or configured.ssdp_uuid,
                    discovered_ip=None,
                    discovered_mac=None,
                    discovered_uuid=None,
                    notes=["No candidate device discovered"],
                )
            )
            continue

        _, reasons, best = ranked[0]
        classification, confidence = classify_candidate(configured, best, reasons)
        results.append(
            ReconcileResult(
                title=configured.title,
                room=configured.inventory.room if configured.inventory else configured.title,
                entity_id=configured.entity_id,
                classification=classification,
                confidence=confidence,
                configured_host=configured.host,
                configured_uuid=configured.unique_id or configured.ssdp_uuid,
                discovered_ip=best.ip,
                discovered_mac=best.mac,
                discovered_uuid=best.uuid,
                notes=reasons,
            )
        )
    return results
