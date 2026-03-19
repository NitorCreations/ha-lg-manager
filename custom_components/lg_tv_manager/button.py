"""Button platform for HA LG Manager."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LgTvManagerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LgTvManagerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LgTvRefreshButton(coordinator, entry), LgTvDiscoverySweepButton(coordinator, entry)])


class LgTvRefreshButton(CoordinatorEntity, ButtonEntity):
    """Refresh button for rediscovery."""

    _attr_name = "LG TV Manager Refresh"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: LgTvManagerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


class LgTvDiscoverySweepButton(CoordinatorEntity, ButtonEntity):
    """Run a wake-and-refresh discovery sweep."""

    _attr_name = "LG TV Manager Discovery Sweep"
    _attr_icon = "mdi:television-play"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: LgTvManagerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_discovery_sweep"

    async def async_press(self) -> None:
        await self.coordinator.async_run_discovery_sweep()
