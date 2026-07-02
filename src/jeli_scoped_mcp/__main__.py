"""Entry point for Jeli Scoped MCP Server."""

import asyncio
import sys

from .config import get_settings


async def main():
    """Initialize and run the Scoped MCP server."""
    try:
        settings = get_settings()

        # Import server components
        from .database.pool import AsyncPostgresPool
        from .embedding.provider import EmbeddingProvider
        from .server.mcp_server import ScopedMCPServer

        # Initialize components
        db = AsyncPostgresPool(
            db_url=settings.db_url,
            min_size=settings.db_min_size,
            max_size=settings.db_max_size,
        )
        await db.connect()

        embedder = EmbeddingProvider.from_settings(settings)

        # Create MCP server
        mcp = ScopedMCPServer(db=db, embedder=embedder, settings=settings)

        # Run based on transport
        if settings.transport == "stdio":
            await mcp.run_stdio()
        else:
            await mcp.run_http()

    except Exception as e:
        print(f"Error starting Scoped MCP server: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
