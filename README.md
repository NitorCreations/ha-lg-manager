# HA LG Manager

Home Assistant custom integration for managing LG webOS TVs during network migrations and ongoing office operations.

## What it does

- Discovers LG TVs over SSDP on the local network.
- Optionally reads a firewall/export CSV as extra discovery input.
- Optionally reads live Meraki client data from the Dashboard API.
- Reconciles discovered TVs against Home Assistant `webostv` config entries.
- Exposes Home Assistant entities for summary, per-TV reconciliation state, and manual refresh.
- Keeps live office state out of the repository by using a local inventory file in `/config`.

## Current scope

This first version focuses on discovery and reconciliation:

- one integration config entry
- one refresh button
- one summary sensor
- one sensor per configured LG TV

The integration does not modify Home Assistant `.storage` or automations.

## Installation

### HACS custom repository

1. Add this repository as a HACS custom integration repository.
2. Install `HA LG Manager`.
3. Restart Home Assistant.
4. Add the integration from `Settings -> Devices & Services`.

### Manual install

Copy `custom_components/lg_tv_manager` into `/config/custom_components/lg_tv_manager`.

## Local files

The integration expects local, non-public operator data in `/config`.

- Inventory file: default `/config/lg_tv_manager.yaml`
- Optional firewall CSV: configured in the integration options
- Optional Meraki API URL and API key: configured in the integration UI and stored privately in Home Assistant config storage

See [`examples/lg_tv_manager.example.yaml`](examples/lg_tv_manager.example.yaml) for the public-safe inventory format.

## Inventory model

Each TV entry can define:

- canonical room title
- expected Home Assistant entity ID
- name hints for matching
- expected source

No IPs, MACs, or office-specific live data need to be committed.

## Meraki support

If you use Cisco Meraki for client visibility, you can provide:

- a full clients API URL, for example `https://api.meraki.com/api/v1/networks/<network_id>/clients?timespan=86400&perPage=1000`
- an API key in the integration options

The API key is intended to be stored in Home Assistant's config entry storage, not in this repository.
