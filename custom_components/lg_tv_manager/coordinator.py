"""Coordinator for HA LG Manager."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_FIREWALL_CLIENTS_PATH,
    CONF_INVENTORY_PATH,
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
    normalize_uuid,
    reconcile_tvs,
)


@dataclass
class LgManagerData:
    results: list[Any]
    discovered_count: int
    configured_count: int
    inventory_count: int


class LgTvManagerCoordinator(DataUpdateCoordinator[LgManagerData]):
    """Manage LG discovery and reconciliation updates."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.config_entry = entry
        scan_interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
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
            _, inventory_by_title = await self.hass.async_add_executor_job(load_inventory, inventory_path)
            configured_tvs = await self._async_collect_configured_tvs(inventory_by_title)
            discovered_tvs = await self.hass.async_add_executor_job(
                self._discover_devices,
                firewall_clients_path,
            )
            results = await self.hass.async_add_executor_job(reconcile_tvs, configured_tvs, discovered_tvs)
            return LgManagerData(
                results=results,
                discovered_count=len(discovered_tvs),
                configured_count=len(configured_tvs),
                inventory_count=len(inventory_by_title),
            )
        except Exception as err:  # pragma: no cover - HA handles UpdateFailed
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
            ssdp_items = (getattr(entry, "discovery_keys", {}) or {}).get("ssdp") or []
            ssdp_uuid = normalize_uuid(ssdp_items[0]["key"]) if ssdp_items else None
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
        return configured

    def _discover_devices(self, firewall_clients_path: Path | None) -> list[Any]:
        discovered = discover_ssdp_devices()
        discovered.extend(load_firewall_clients(firewall_clients_path))
        return dedupe_discovered(discovered)
