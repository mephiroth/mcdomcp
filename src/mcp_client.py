"""麦当劳 MCP 最小客户端（Streamable HTTP / JSON-RPC）。

Token 只从环境变量 MCD_TOKEN 读取，绝不写入代码或仓库。
"""

from __future__ import annotations

import json
import os
import urllib.request

MCP_URL = os.environ.get("MCD_MCP_URL", "https://mcp.mcd.cn")


class McdError(RuntimeError):
    pass


def _post(payload: dict, token: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")

    # 服务端可能返回 SSE 帧
    if raw.lstrip().startswith(("event:", "data:")):
        for line in raw.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                break
    return json.loads(raw)


def _extract_json(text: str) -> dict | None:
    """MCP 的 text content 里混了字段说明和原始响应，只取原始响应段。"""
    marker = "## Original Response"
    tail = text.split(marker, 1)[1].strip() if marker in text else text
    start = tail.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(tail[start:])
        return obj
    except json.JSONDecodeError:
        return None


class McdClient:
    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("MCD_TOKEN", "")
        if not self.token:
            raise McdError("缺少 MCD_TOKEN 环境变量")
        self._id = 0

    def _call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        res = _post(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}},
            self.token,
        )
        if "error" in res:
            raise McdError(f"{method} 失败: {res['error']}")
        return res["result"]

    def tool(self, name: str, arguments: dict | None = None) -> dict:
        result = self._call("tools/call", {"name": name, "arguments": arguments or {}})
        text = "\n".join(
            c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"
        )
        if result.get("isError"):
            raise McdError(f"{name} 返回错误: {text[:200]}")
        parsed = _extract_json(text)
        if parsed is None:
            raise McdError(f"{name} 返回无法解析: {text[:200]}")
        if parsed.get("success") is False:
            raise McdError(f"{name} 业务失败: {parsed.get('code')} {parsed.get('message')}")
        return parsed
