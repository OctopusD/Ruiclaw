"""Resolve channel-owned setup contracts for settings consumers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ruiclaw.channels.contracts import ChannelSetupSpec

if TYPE_CHECKING:
    from ruiclaw.channels.plugin import ChannelPlugin


def channel_setup_spec(
    name: str,
    *,
    plugin: ChannelPlugin | None = None,
) -> ChannelSetupSpec | None:
    """Return the setup contract declared by one channel descriptor."""
    if plugin is None:
        from ruiclaw.channels.registry import load_channel_plugin

        plugin = load_channel_plugin(name)
    return plugin.setup
