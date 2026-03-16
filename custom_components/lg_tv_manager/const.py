"""Constants for HA LG Manager."""

from __future__ import annotations

DOMAIN = "lg_tv_manager"

PLATFORMS = ["sensor", "button"]

CONF_INVENTORY_PATH = "inventory_path"
CONF_FIREWALL_CLIENTS_PATH = "firewall_clients_path"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_INVENTORY_PATH = "lg_tv_manager.yaml"
DEFAULT_SCAN_INTERVAL = 300

CLASSIFICATION_UNCHANGED = "unchanged"
CLASSIFICATION_IP_CHANGED = "ip_changed"
CLASSIFICATION_REPLACEMENT = "replacement_candidate"
CLASSIFICATION_MISSING = "missing"
