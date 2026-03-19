"""Sensor platform for HA LG Manager."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LgManagerData, LgTvManagerCoordinator


@dataclass(frozen=True, kw_only=True)
class LgManagerSensorDescription(SensorEntityDescription):
    pass


SUMMARY_DESCRIPTION = LgManagerSensorDescription(
    key="summary",
    name="LG TV Manager Summary",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LgTvManagerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [LgTvManagerSummarySensor(coordinator, entry)]
    entities.extend(LgTvReconcileSensor(coordinator, entry, index) for index in range(len(coordinator.data.results)))
    async_add_entities(entities)


class LgTvManagerSummarySensor(CoordinatorEntity[LgManagerData], SensorEntity):
    """Summary sensor for the manager."""

    entity_description = SUMMARY_DESCRIPTION

    def __init__(self, coordinator: LgTvManagerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_summary"

    @property
    def native_value(self) -> str:
        data = self.coordinator.data
        return (
            f"{data.configured_count} configured, "
            f"{data.discovered_count} discovered, "
            f"{sum(1 for item in data.results if item.classification != 'unchanged')} needing attention"
        )

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        data = self.coordinator.data
        return {
            "configured_count": data.configured_count,
            "discovered_count": data.discovered_count,
            "inventory_count": data.inventory_count,
            "meraki_candidate_count": data.meraki_candidate_count,
            "meraki_candidates": data.meraki_candidates,
            "configured_titles": data.configured_titles,
            "wol_action_count": len(data.wol_action_records),
            "wol_aliases_with_config": sorted(data.wol_action_records),
            "unchanged": sum(1 for item in data.results if item.classification == "unchanged"),
            "ip_changed": sum(1 for item in data.results if item.classification == "ip_changed"),
            "replacement_candidate": sum(
                1 for item in data.results if item.classification == "replacement_candidate"
            ),
            "missing": sum(1 for item in data.results if item.classification == "missing"),
        }


class LgTvReconcileSensor(CoordinatorEntity[LgManagerData], SensorEntity):
    """Per-TV reconciliation sensor."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: LgTvManagerCoordinator, entry: ConfigEntry, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        result = coordinator.data.results[index]
        slug = result.entity_id or result.title.lower().replace(" ", "_")
        self._attr_unique_id = f"{entry.entry_id}_{slug}_status"
        self._attr_name = f"{result.title} Status"

    @property
    def _result(self):
        return self.coordinator.data.results[self._index]

    @property
    def native_value(self) -> str:
        return self._result.classification

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        result = self._result
        expected_aliases = self.coordinator.data.expected_wol_aliases.get(result.title, [])
        wol_records = []
        for alias in expected_aliases:
            record = self.coordinator.data.wol_action_records.get(alias)
            if record:
                wol_records.append(
                    {
                        "alias": record.alias,
                        "source_type": record.source_type,
                        "mac": record.mac,
                        "broadcast_address": record.broadcast_address,
                        "broadcast_port": record.broadcast_port,
                    }
                )
        return {
            "room": result.room,
            "entity_id": result.entity_id,
            "confidence": result.confidence,
            "configured_host": result.configured_host,
            "configured_uuid": result.configured_uuid,
            "discovered_ip": result.discovered_ip,
            "discovered_mac": result.discovered_mac,
            "discovered_uuid": result.discovered_uuid,
            "expected_wol_aliases": expected_aliases,
            "current_wol_configurations": wol_records,
            "notes": result.notes,
        }
