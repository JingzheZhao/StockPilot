"""The Muse connector: auth, MCP handshake and tools, over real HTTP."""

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

KEY = "test-key-0123456789-abcdefghij"
SNAP = {
    "asof": "2026-11-16", "start": "2026-11-16", "strategy": "breakout", "initial_cash": 3000,
    "equity": 3012.5, "return_pct": 0.0042, "spy_return_pct": 0.002, "market_filter_on": True,
    "fees_paid": 2.0, "universe": "S&P 500 top 50", "buyable_this_month": ["AAPL", "NVDA"],
    "status": "ORDERS QUEUED", "orders_valid_for_open_of": "2026-11-18",
    "next_open_orders": [{"side": "BUY", "symbol": "KO", "shares": 6.0, "approx_value": 600.0}],
    "positions": [{"symbol": "PG", "shares": 3.4, "entry_date": "2026-11-17", "entry_price": 173.0,
                   "last_close": 175.0, "pnl_pct": 0.0116, "stop": 166.1}],
    "closed_trades": [{"symbol": f"T{i}", "pnl": i} for i in range(30)],
    "report_markdown": "# Paper trading report",
}


@pytest.fixture()
def server(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "muse_index", Path(__file__).resolve().parents[1] / "muse" / "api" / "index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = tmp_path / "latest.json"
    data.write_text(json.dumps(SNAP, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "DATA", data)
    monkeypatch.setenv("MUSE_API_KEY", KEY)
    srv = HTTPServer(("127.0.0.1", 0), mod.handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/mcp", mod
    srv.shutdown()


def post(url, body, key=KEY):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, None


def call(url, i, method, params=None):
    return post(url, {"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}})


def test_rejects_missing_or_wrong_key(server):
    url, _ = server
    assert post(url, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, key=None)[0] == 401
    assert post(url, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, key="wrong" * 6)[0] == 401


def test_refuses_everything_when_key_not_configured(server, monkeypatch):
    url, _ = server
    monkeypatch.delenv("MUSE_API_KEY")
    assert post(url, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, key="")[0] == 401


def test_handshake_and_tool_list(server):
    url, _ = server
    status, r = call(url, 1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                            "clientInfo": {"name": "t", "version": "1"}})
    assert status == 200 and r["result"]["protocolVersion"] == "2025-06-18"
    status, r = post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert status == 202 and r is None
    _, r = call(url, 2, "tools/list")
    names = {t["name"] for t in r["result"]["tools"]}
    assert names == {"get_summary", "get_positions", "get_next_orders",
                     "get_closed_trades", "get_daily_report"}


def test_tools_return_snapshot_data(server):
    url, _ = server
    _, r = call(url, 1, "tools/call", {"name": "get_summary", "arguments": {}})
    s = r["result"]["structuredContent"]
    assert s["equity"] == 3012.5 and s["open_positions"] == 1 and s["next_open_orders"] == 1
    assert s["buyable_this_month"] == ["AAPL", "NVDA"]
    assert s["status"] == "ORDERS QUEUED"
    _, r = call(url, 4, "tools/call", {"name": "get_next_orders", "arguments": {}})
    assert r["result"]["structuredContent"]["valid_for_open_of"] == "2026-11-18"
    _, r = call(url, 2, "tools/call", {"name": "get_closed_trades", "arguments": {"limit": 3}})
    t = r["result"]["structuredContent"]["trades"]
    assert [x["symbol"] for x in t] == ["T29", "T28", "T27"]  # newest first
    _, r = call(url, 3, "tools/call", {"name": "get_daily_report", "arguments": {}})
    assert "Paper trading report" in r["result"]["content"][0]["text"]


def test_unknown_tool_and_method(server):
    url, _ = server
    _, r = call(url, 1, "tools/call", {"name": "place_order", "arguments": {}})
    assert r["error"]["code"] == -32602
    _, r = call(url, 2, "resources/list")
    assert r["error"]["code"] == -32601
