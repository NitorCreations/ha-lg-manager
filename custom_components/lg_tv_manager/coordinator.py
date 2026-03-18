"""Coordinator for HA LG Manager."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.components.network import async_get_adapters
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_FIREWALL_CLIENTS_PATH,
    CONF_INVENTORY_PATH,
    CONF_MERAKI_API_KEY,
    CONF_MERAKI_API_URL,
    CONF_SCAN_INTERVAL,
    DEFAULT_INVENTORY_PATH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .model import (
    ConfiguredTv,
    dedupe_discovered,
    discover_ssdp_devices,
    load_firewall_clients,
    load_inventory,
    load_meraki_clients,
    normalize_uuid,
    reconcile_tvs,
)

LOGGER = logging.getLogger(__name__)


@dataclass
class LgManagerData:
    results: list[Any]
    discovered_count: int
    configured_count: int
    inventory_count: int


def _extract_ssdp_uuid(entry: ConfigEntry) -> str | None:
    """Extract an SSDP UUID from a Home Assistant config entry."""
    discovery_keys = getattr(entry, "discovery_keys", None) or {}
    ssdp_items = discovery_keys.get("ssdp") or []
    if not ssdp_items:
        return None

    first_item = ssdp_items[0]
    key_value = getattr(first_item, "key", None)
    if key_value is None and isinstance(first_item, dict):
        key_value = first_item.get("key")
    if key_value is None:
        key_value = str(first_item)
    return normalize_uuid(key_value)


class LgTvManagerCoordinator(DataUpdateCoordinator[LgManagerData]):
    """Manage LG discovery and reconciliation updates."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.config_entry = entry
        scan_interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> LgManagerData:
        try:
            inventory_path = Path(
                self.hass.config.path(
                    self.config_entry.options.get(CONF_INVENTORY_PATH, DEFAULT_INVENTORY_PATH)
                )
            )
            firewall_clients_path_value = self.config_entry.options.get(CONF_FIREWALL_CLIENTS_PATH, "")
            firewall_clients_path = (
                Path(self.hass.config.path(firewall_clients_path_value))
                if firewall_clients_path_value
                else None
            )
            meraki_api_url = (self.config_entry.options.get(CONF_MERAKI_API_URL) or "").strip()
            meraki_api_key = (self.config_entry.options.get(CONF_MERAKI_API_KEY) or "").strip()
            _, inventory_by_title = await self.hass.async_add_executor_job(load_inventory, inventory_path)
            source_ips = await self._async_get_source_ips()
            LOGGER.debug(
                "Loading LG TV inventory from %s, firewall CSV %s, Meraki %s, source IPs %s",
                inventory_path,
                firewall_clients_path if firewall_clients_path else "<disabled>",
                meraki_api_url if meraki_api_url and meraki_api_key else "<disabled>",
                source_ips,
            )
            configured_tvs = await self._async_collect_configured_tvs(inventory_by_title)
            discovered_tvs = await self.hass.async_add_executor_job(
                self._discover_devices,
                firewall_clients_path,
                meraki_api_url,
                meraki_api_key,
                source_ips,
            )
            results = await self.hass.async_add_executor_job(reconcile_tvs, configured_tvs, discovered_tvs)
            LOGGER.debug(
                "LG TV Manager update complete: inventory=%s configured=%s discovered=%s summary=%s",
                len(inventory_by_title),
                len(configured_tvs),
                len(discovered_tvs),
                {
                    "unchanged": sum(1 for item in results if item.classification == "unchanged"),
                    "ip_changed": sum(1 for item in results if item.classification == "ip_changed"),
                    "replacement_candidate": sum(
                        1 for item in results if item.classification == "replacement_candidate"
                    ),
                    "missing": sum(1 for item in results if item.classification == "missing"),
                },
            )
            for result in results:
                LOGGER.debug(
                    "Result %s: entity=%s class=%s confidence=%s configured_host=%s discovered_ip=%s discovered_uuid=%s notes=%s",
                    result.title,
                    result.entity_id,
                    result.classification,
                    result.confidence,
                    result.configured_host,
                    result.discovered_ip,
                    result.discovered_uuid,
                    result.notes,
                )
            return LgManagerData(
                results=results,
                discovered_count=len(discovered_tvs),
                configured_count=len(configured_tvs),
                inventory_count=len(inventory_by_title),
            )
        except Exception as err:  # pragma: no cover - HA handles UpdateFailed
            LOGGER.exception("LG TV Manager update failed")
            raise UpdateFailed(str(err)) from err

    async def _async_collect_configured_tvs(self, inventory_by_title: dict[str, Any]) -> list[ConfiguredTv]:
        entity_registry = er.async_get(self.hass)
        entities_by_config_entry = {
            entity.config_entry_id: entity.entity_id
            for entity in entity_registry.entities.values()
            if entity.platform == "webostv" and entity.config_entry_id
        }
        configured: list[ConfiguredTv] = []
        for entry in self.hass.config_entries.async_entries("webostv"):
            ssdp_uuid = _extract_ssdp_uuid(entry)
            inventory = inventory_by_title.get(entry.title)
            configured.append(
                ConfiguredTv(
                    title=entry.title,
                    entry_id=entry.entry_id,
                    unique_id=normalize_uuid(entry.unique_id),
                    ssdp_uuid=ssdp_uuid,
                    host=entry.data.get("host"),
                    entity_id=entities_by_config_entry.get(entry.entry_id) or (inventory.entity_id if inventory else None),
                    inventory=inventory,
                )
            )
        LOGGER.debug(
            "Collected %s configured webostv entries: %s",
            len(configured),
            [
                {
                    "title": item.title,
                    "entity_id": item.entity_id,
                    "host": item.host,
                    "unique_id": item.unique_id,
                    "ssdp_uuid": item.ssdp_uuid,
                    "has_inventory": item.inventory is not None,
                }
                for item in configured
            ],
        )
        return configured

    async def _async_get_source_ips(self) -> list[str]:
        """Collect usable IPv4 source addresses from Home Assistant adapters."""
        adapters = await async_get_adapters(self.hass)
        source_ips: list[str] = []
        for adapter in adapters:
            if not adapter.get("enabled", True):
                continue
            for ipv4 in adapter.get("ipv4", []):
                address = ipv4.get("address")
                if not address or address.startswith("127."):
                    continue
                source_ips.append(address)
        return sorted(set(source_ips))

    def _discover_devices(
        self,
        firewall_clients_path: Path | None,
        meraki_api_url: str,
        meraki_api_key: str,
        source_ips: list[str],
    ) -> list[Any]:
        discovered = discover_ssdp_devices(source_ips=source_ips)
        discovered.extend(load_firewall_clients(firewall_clients_path))
        discovered.extend(load_meraki_clients(meraki_api_url, meraki_api_key))
        deduped = dedupe_discovered(discovered)
        LOGGER.debug(
            "Discovered %s raw LG candidates and %s deduped candidates: %s",
            len(discovered),
            len(deduped),
            [
                {
                    "ip": item.ip,
                    "mac": item.mac,
                    "uuid": item.uuid,
                    "friendly_name": item.friendly_name,
                    "source": item.source,
                }
                for item in deduped
            ],
        )
        return deduped
