"""Search Providers under the D16 Provider Contract (规范 19 §3, AOP-P2-1).

RESEARCH capability providers registered into the shared
ExtensionRegistry. Consumers (WebResearchExecutor, Worker tool loop)
invoke through the registry — never import these classes directly
(P2-1 closure: third-party search joins by registering a provider).

Providers:
  ExaProvider    — keyless MCP (mcp.exa.ai), priority=50 (opencode 同款)
  ScrapeProvider — DDG/Bing HTML scraping fallback, priority=100
"""

from __future__ import annotations


def register_search_providers(registry) -> None:
    """组合根调用（规范 19 §3.4）——把搜索 provider 注册进共享 registry。

    幂等：重复注册同名 provider 直接覆盖（register 语义本就如此）。
    """
    from .exa_mcp import ExaProvider
    from .scrape import ScrapeProvider

    registry.register(ExaProvider())
    registry.register(ScrapeProvider())
