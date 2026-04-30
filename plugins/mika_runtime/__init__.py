"""Bundled Hermes plugin that exposes Mika-managed integrations as stable tools."""

from plugins.mika_runtime.tools import (
    CALCOM_API_SCHEMA,
    INTEGRATIONS_STATUS_SCHEMA,
    NOTION_API_SCHEMA,
    TODOIST_API_SCHEMA,
    handle_calcom_api,
    handle_integrations_status,
    handle_notion_api,
    handle_todoist_api,
)


def register(ctx) -> None:
    """Register Mika integration bridge tools."""
    ctx.register_tool(
        name="mika_integrations_status",
        toolset="mika_integrations",
        schema=INTEGRATIONS_STATUS_SCHEMA,
        handler=handle_integrations_status,
        emoji="🔌",
    )
    ctx.register_tool(
        name="mika_notion_api",
        toolset="mika_integrations",
        schema=NOTION_API_SCHEMA,
        handler=handle_notion_api,
        emoji="🧠",
    )
    ctx.register_tool(
        name="mika_todoist_api",
        toolset="mika_integrations",
        schema=TODOIST_API_SCHEMA,
        handler=handle_todoist_api,
        emoji="✅",
    )
    ctx.register_tool(
        name="mika_calcom_api",
        toolset="mika_integrations",
        schema=CALCOM_API_SCHEMA,
        handler=handle_calcom_api,
        emoji="📅",
    )
