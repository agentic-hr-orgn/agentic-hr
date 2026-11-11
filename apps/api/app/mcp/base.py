"""
Base FastMCP Server Definition & Factory for Agentic HR Core.
Aliases official MCPServer from mcp.server.mcpserver to FastMCP for seamless protocol compatibility.
"""

from mcp.server.mcpserver import MCPServer

# Expose official FastMCP server class
FastMCP = MCPServer


def create_fastmcp_server(name: str, instructions: str | None = None) -> FastMCP:
    """Creates a configured FastMCP server instance."""
    server = FastMCP(name=name, instructions=instructions)
    return server
