"""Read-only MCP server for StockPilot paper trading, deployed on Vercel.

Meta Muse (or any MCP client) POSTs JSON-RPC messages here using the
Streamable HTTP transport. Every response is a single JSON body (no streaming),
and no session state is kept, which suits serverless hosting.

Security
--------
* Every request must carry the key from the MUSE_API_KEY environment variable,
  as "Authorization: Bearer <key>" or "X-API-Key: <key>". If the variable is
  not set, the server refuses everything (fail closed).
* All tools only READ data/latest.json. Nothing here can place an order or
  change any file.

Data
----
data/latest.json is written by scripts/paper.py after each daily run. The
GitHub Actions workflow commits it, and Vercel redeploys on every commit, so
the deployed copy is always the latest daily snapshot.
"""

from __future__ import annotations

import hmac
import json
import os
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "latest.json"
SUPPORTED_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
SERVER_INFO = {"name": "stockpilot-paper", "version": "0.1.0"}
INSTRUCTIONS = (
    "StockPilot is a personal US-stock paper-trading agent using a rule-based breakout "
    "strategy with SIMULATED money (not real money). Data updates once per trading day "
    "after the close; 'asof' is the market date of the latest data. These tools are "
    "read-only."
)

NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}
TOOLS = [
    {
        "name": "get_summary",
        "description": "Start here. Today's status: DATA ERROR (data failed checks, no new orders), "
                       "ORDERS QUEUED (simulated orders for the open named in orders_valid_for_open_of), "
                       "or NO SIGNAL (with no_signal_reasons: how many stocks met each rule and which "
                       "were closest). Also: data date, simulated account value and return in "
                       "`currency` (CAD for an account started from real holdings; equity_usd is the "
                       "USD book value) against `benchmark`, market filter, risk summary, notes about "
                       "how the account started, and this month's buyable stocks.",
        "inputSchema": NO_ARGS,
    },
    {
        "name": "get_positions",
        "description": "Open simulated positions with entry date/price, latest close, unrealized "
                       "return and stop-loss price.",
        "inputSchema": NO_ARGS,
    },
    {
        "name": "get_next_orders",
        "description": "Simulated buy/sell orders queued for the next market open, with the session "
                       "they are valid for, candidate rank, sizing reason and planned stop.",
        "inputSchema": NO_ARGS,
    },
    {
        "name": "get_closed_trades",
        "description": "Closed simulated trades, newest first, with profit/loss and exit reason.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200,
                                     "description": "Max trades to return (default 20)."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_daily_report",
        "description": "The full daily report as Markdown text.",
        "inputSchema": NO_ARGS,
    },
]


def load() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def call_tool(name: str, args: dict) -> dict:
    d = load()
    orders = d["next_open_orders"]
    cutoff = d.get("orders_expire_at")
    expired = bool(orders) and (not cutoff or datetime.now(timezone.utc) >= datetime.fromisoformat(cutoff))
    if expired:
        d["status"] = "ORDERS EXPIRED"
        d["status_detail"] = "The scheduled opening time has passed or could not be verified."
        d["next_open_orders"] = []
        section = r"(?m)^## Orders for [^\n]+\n(?:[\s\S]*?)(?=^## |\Z)"
        d["report_markdown"] = re.sub(section, "## Orders\nNo active orders.\n\n", d["report_markdown"])
        d["report_markdown"] = re.sub(r"(?m)^\*\*Status: ORDERS QUEUED\*\*[^\n]*",
                                      "**Status: ORDERS EXPIRED**. " + d["status_detail"],
                                      d["report_markdown"])
    if name == "get_summary":
        return {k: d.get(k) for k in ("status", "status_detail", "data_problems", "asof",
                                      "latest_completed_session", "orders_valid_for_open_of", "start",
                                      "strategy", "universe", "currency", "account_opened",
                                      "initial_capital", "equity",
                                      "equity_usd", "usdcad", "return_pct", "benchmark",
                                      "benchmark_return_pct", "notes", "market_filter_on", "fees_paid",
                                      "risk", "no_signal_reasons")} | {
            "buyable_this_month": d.get("buyable_this_month", []),
            "open_positions": len(d["positions"]),
            "next_open_orders": len(d["next_open_orders"]),
            "closed_trades": len(d["closed_trades"]),
            "note": "Simulated money. Percent fields are fractions (0.012 = +1.2%).",
        }
    if name == "get_positions":
        return {"asof": d["asof"], "positions": d["positions"]}
    if name == "get_next_orders":
        return {"asof": d["asof"], "status": d.get("status"),
                "valid_for_open_of": d.get("orders_valid_for_open_of"),
                "orders_for_next_open": d["next_open_orders"]}
    if name == "get_closed_trades":
        limit = int(args.get("limit", 20))
        limit = max(1, min(limit, 200))
        return {"asof": d["asof"], "trades": list(reversed(d["closed_trades"]))[:limit],
                "total": len(d["closed_trades"])}
    if name == "get_daily_report":
        return {"asof": d["asof"], "report_markdown": d["report_markdown"]}
    raise KeyError(name)


def rpc_result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def rpc_error(msg_id, code: int, message: str):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_message(msg: dict) -> dict | None:
    """Return a JSON-RPC response, or None for notifications."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    method, msg_id, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if "id" not in msg:  # notification, e.g. notifications/initialized
        return None

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return rpc_result(msg_id, {"protocolVersion": version,
                                   "capabilities": {"tools": {"listChanged": False}},
                                   "serverInfo": SERVER_INFO,
                                   "instructions": INSTRUCTIONS})
    if method == "ping":
        return rpc_result(msg_id, {})
    if method == "tools/list":
        return rpc_result(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        if name not in {t["name"] for t in TOOLS}:
            return rpc_error(msg_id, -32602, f"Unknown tool: {name}")
        try:
            data = call_tool(name, params.get("arguments") or {})
        except FileNotFoundError:
            return rpc_result(msg_id, {"isError": True, "content": [
                {"type": "text", "text": "No paper-trading data yet: the daily job has not run."}]})
        except (ValueError, TypeError) as exc:
            return rpc_result(msg_id, {"isError": True, "content": [
                {"type": "text", "text": f"Bad arguments: {exc}"}]})
        text = json.dumps(data, ensure_ascii=False, indent=2)
        return rpc_result(msg_id, {"content": [{"type": "text", "text": text}],
                                   "structuredContent": data, "isError": False})
    return rpc_error(msg_id, -32601, f"Method not found: {method}")


def authorized(headers) -> bool:
    expected = os.environ.get("MUSE_API_KEY", "")
    if len(expected) < 20:  # unset or too weak: refuse everything
        return False
    auth = headers.get("Authorization", "")
    given = auth[7:].strip() if auth.lower().startswith("bearer ") else headers.get("X-API-Key", "")
    return hmac.compare_digest(given.encode(), expected.encode())


class handler(BaseHTTPRequestHandler):  # Vercel looks for a class named "handler"
    def _send(self, status: int, body: dict | None = None, extra: dict | None = None):
        payload = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = -1
        if n < 0 or n > 1_000_000:
            return self._send(413, {"error": "request too large"})
        body = self.rfile.read(n)  # read before replying, even when rejecting
        if not authorized(self.headers):
            return self._send(401, {"error": "unauthorized"}, {"WWW-Authenticate": "Bearer"})
        try:
            msg = json.loads(body or b"null")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, rpc_error(None, -32700, "Parse error"))
        if isinstance(msg, list):  # batches are not part of the current spec
            return self._send(400, rpc_error(None, -32600, "Batch requests are not supported"))
        resp = handle_message(msg)
        if resp is None:
            return self._send(202)
        return self._send(200, resp)

    def do_GET(self):  # no server-initiated stream; clients fall back to POST only
        self._send(405, {"error": "use POST"}, {"Allow": "POST"})

    do_DELETE = do_GET

    def log_message(self, *args):  # keep logs quiet; never log headers (they hold the key)
        pass
