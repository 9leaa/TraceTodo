"""MCP Server package."""

from trace_memory.mcp_server.server import (
    MCPToolDispatcher,
    TOOL_DEFINITIONS,
    handle_jsonrpc_message,
    serve_mcp_stdio,
)

__all__ = [
    "MCPToolDispatcher",
    "TOOL_DEFINITIONS",
    "handle_jsonrpc_message",
    "serve_mcp_stdio",
]
