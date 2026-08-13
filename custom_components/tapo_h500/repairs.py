"""Conditions worth interrupting someone about, raised as Home Assistant issues.

The alternative is a debug log line nobody reads. Both of these are silent
until footage is already lost: a hub at 99% is overwriting the oldest
recordings to make room, and a hub that stopped answering is not recording
anything at all while every entity keeps showing its last known value.

Deliberately not raised: a single failed poll, which is normal on a busy
network and recovers by itself, and low free space in gigabytes, which means
nothing without knowing the disk size.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

# Percent used at which the hub is about to start overwriting. Loop recording
# does not fail at 100%, it silently discards the oldest footage, so the
# warning has to arrive before that rather than at it.
STORAGE_WARN_PERCENT = 95

STORAGE_ISSUE = "storage_nearly_full"
UNREACHABLE_ISSUE = "hub_unreachable"


def _issue_id(entry_id: str, kind: str) -> str:
    return f"{kind}_{entry_id}"


def async_check(hass: HomeAssistant, entry_id: str, coordinator) -> None:
    """Raise or clear both issues for one hub. Safe to call every poll."""
    _storage(hass, entry_id, coordinator)
    _reachable(hass, entry_id, coordinator)


def _storage(hass: HomeAssistant, entry_id: str, coordinator) -> None:
    total = coordinator.readings.get("storage_total")
    free = coordinator.readings.get("storage_free")
    issue_id = _issue_id(entry_id, STORAGE_ISSUE)
    # Unknown is not the same as fine. Say nothing rather than guessing.
    if not total or free is None:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    used_percent = round((total - free) / total * 100)
    if used_percent < STORAGE_WARN_PERCENT:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass, DOMAIN, issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=STORAGE_ISSUE,
        translation_placeholders={"used": str(used_percent)},
    )


def _reachable(hass: HomeAssistant, entry_id: str, coordinator) -> None:
    issue_id = _issue_id(entry_id, UNREACHABLE_ISSUE)
    if coordinator.last_update_success:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass, DOMAIN, issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=UNREACHABLE_ISSUE,
    )
