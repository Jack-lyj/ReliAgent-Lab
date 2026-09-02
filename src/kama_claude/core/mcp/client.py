from __future__ import annotations

import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError

_CLAUDE_IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


class McpServerUnavailableError(Exception):
    pass


class McpToolError(Exception):
    """MCP server 返回的应用层错误（连接正常，但工具调用失败）"""


@dataclass(frozen=True)
class McpToolDef:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class McpCallResult:
    content: str
    is_error: bool = False
    llm_content: list[dict[str, object]] | None = None
    structured_content: dict[str, Any] | None = None


# 将 MCP 富内容转换为事件摘要和 Anthropic 可接受的工具结果内容
def _convert_call_result(result: types.CallToolResult) -> McpCallResult:
    summary: list[str] = []
    llm_content: list[dict[str, object]] = []

    for block in result.content:
        if isinstance(block, types.TextContent):
            summary.append(block.text)
            llm_content.append({"type": "text", "text": block.text})
        elif isinstance(block, types.ImageContent):
            summary.append(
                f"[image mime_type={block.mimeType} base64_chars={len(block.data)}]"
            )
            if block.mimeType in _CLAUDE_IMAGE_MEDIA_TYPES:
                llm_content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": block.mimeType,
                            "data": block.data,
                        },
                    }
                )
            else:
                llm_content.append(
                    {
                        "type": "text",
                        "text": f"MCP returned an unsupported image type: {block.mimeType}",
                    }
                )
        elif isinstance(block, types.AudioContent):
            marker = f"[audio mime_type={block.mimeType} base64_chars={len(block.data)}]"
            summary.append(marker)
            llm_content.append({"type": "text", "text": marker})
        elif isinstance(block, types.EmbeddedResource):
            resource = block.resource
            uri = str(resource.uri)
            if isinstance(resource, types.TextResourceContents):
                rendered = f"[resource uri={uri}]\n{resource.text}"
            else:
                rendered = (
                    f"[resource uri={uri} mime_type={resource.mimeType or 'unknown'} "
                    f"base64_chars={len(resource.blob)}]"
                )
            summary.append(rendered)
            llm_content.append({"type": "text", "text": rendered})
        elif isinstance(block, types.ResourceLink):
            rendered = (
                f"[resource_link name={block.name} uri={block.uri} "
                f"mime_type={block.mimeType or 'unknown'}]"
            )
            summary.append(rendered)
            llm_content.append({"type": "text", "text": rendered})
        else:  # pragma: no cover - official SDK currently exhausts ContentBlock here
            rendered = json.dumps(
                block.model_dump(by_alias=True, mode="json", exclude_none=True),
                ensure_ascii=False,
            )
            summary.append(rendered)
            llm_content.append({"type": "text", "text": rendered})

    structured = result.structuredContent
    if structured is not None:
        rendered = json.dumps(structured, ensure_ascii=False, sort_keys=True)
        structured_text = f"[structured_content]\n{rendered}"
        summary.append(structured_text)
        llm_content.append({"type": "text", "text": structured_text})

    if not summary:
        summary.append("[MCP tool returned no content]")
    if not llm_content:
        llm_content.append({"type": "text", "text": summary[0]})

    return McpCallResult(
        content="\n".join(summary),
        is_error=result.isError,
        llm_content=llm_content,
        structured_content=structured,
    )


# 使用官方 SDK 管理 MCP 协议协商、请求并发与标准传输生命周期
class McpClient:
    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    # 通过官方 stdio transport 启动子进程并完成能力协商
    async def connect_stdio(
        self,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
    ) -> None:
        stack = AsyncExitStack()
        try:
            params = StdioServerParameters(command=command, args=args, env=env)
            read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
        except Exception as exc:
            await stack.aclose()
            raise McpServerUnavailableError(f"stdio MCP connection failed: {exc}") from exc
        self._stack = stack
        self._session = session

    # 通过标准 Streamable HTTP transport 连接并完成能力协商
    async def connect_streamable_http(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        stack = AsyncExitStack()
        try:
            http_client: httpx.AsyncClient | None = None
            if headers:
                http_client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=headers,
                        follow_redirects=True,
                        timeout=httpx.Timeout(30.0, read=300.0),
                    )
                )
            streams = await stack.enter_async_context(
                streamable_http_client(url, http_client=http_client)
            )
            read_stream, write_stream, _ = streams
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
        except Exception as exc:
            await stack.aclose()
            raise McpServerUnavailableError(
                f"Streamable HTTP MCP connection failed: {exc}"
            ) from exc
        self._stack = stack
        self._session = session

    # 返回已初始化的 SDK session，未连接时抛出可诊断异常
    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise McpServerUnavailableError("MCP client is not connected")
        return self._session

    # 分页获取 server 暴露的全部 MCP 工具定义
    async def list_tools(self) -> list[McpToolDef]:
        session = self._require_session()
        definitions: list[McpToolDef] = []
        cursor: str | None = None
        try:
            while True:
                if cursor is None:
                    page = await session.list_tools()
                else:
                    page = await session.list_tools(
                        params=types.PaginatedRequestParams(cursor=cursor)
                    )
                definitions.extend(
                    McpToolDef(
                        name=tool.name,
                        description=tool.description or "",
                        input_schema=tool.inputSchema,
                    )
                    for tool in page.tools
                )
                cursor = page.nextCursor
                if cursor is None:
                    return definitions
        except Exception as exc:
            raise McpServerUnavailableError(f"MCP tools/list failed: {exc}") from exc

    # 调用 MCP 工具并保留 isError、结构化内容及受支持的多媒体结果
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpCallResult:
        session = self._require_session()
        try:
            result = await session.call_tool(name, arguments)
        except McpError as exc:
            raise McpToolError(str(exc)) from exc
        except Exception as exc:
            raise McpServerUnavailableError(f"MCP tools/call failed: {exc}") from exc
        return _convert_call_result(result)

    # 按 transport → session 的逆序安全关闭 HTTP/stdio 资源与进程树
    async def close(self) -> None:
        stack = self._stack
        self._session = None
        self._stack = None
        if stack is not None:
            await stack.aclose()
