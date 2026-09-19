#!/usr/bin/env python3
"""F-R4/AC4：真实浏览器 + 真实传输链路验收（隔离栈，零真实模型费用）。

与 `run_controlled_acceptance.py` 是同一套受控环境的浏览器面补充。差别在可见性
链路：本脚本启动**真实 sidecar 进程** + 真实 FastAPI + 真实 Vite 生产构建，用
headless Chrome 观察首帧→提交→等待→增量正文→终态，以及刷新与断线恢复。

证明什么：
- 公共 SSE 事件真的到达浏览器（不是 route mock），Vue 真的渲染出正文；
- 09-18 增量显示合同在真实页面上成立：临时正文先出现、权威消息到达后**整体替换**
  而非重复追加；
- 刷新后不重复、重连不重、失败不伪装成功。

不证明什么：真实 Provider 的模型质量与线上延迟（属 G）；跨机时钟精度（本机
monotonic + 单机 RTT，不做毫秒级跨机声明）。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
AGENT = ROOT / "agent"
FRONTEND = ROOT / "frontend"
VENV_PY = BACKEND / ".venv" / "bin" / "python"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

EXIT_PASS = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2


def free_ports(count: int) -> list[int]:
    with ThreadPoolExecutor(max_workers=count) as pool:
        handles = list(pool.map(_free_port, range(count)))
    # Sequential bind to guarantee the ports stay distinct and free at return.
    socks = [socket.socket() for _ in range(count)]
    try:
        for sock in socks:
            sock.bind(("127.0.0.1", 0))
        return [int(sock.getsockname()[1]) for sock in socks]
    finally:
        for sock in socks:
            sock.close()


def _free_port(_: int) -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ---- scripted upstream -----------------------------------------------------


@dataclass
class UpstreamState:
    parts: list[str] = field(default_factory=lambda: ["蓝", "色", "茶", "叶", "罐", "在", "厨房"])
    chunk_delay_ms: int = 700
    status: int = 200
    requests: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"count": len(self.requests), "requests": list(self.requests)}


def _sse(parts: list[str]) -> list[bytes]:
    frames: list[bytes] = []
    frames.append(
        (
            "data: "
            + json.dumps(
                {
                    "id": "chatcmpl-browser",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "synthetic-model",
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}],
                }
            )
            + "\n\n"
        ).encode()
    )
    for part in parts:
        frames.append(
            (
                "data: "
                + json.dumps(
                    {
                        "id": "chatcmpl-browser",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": "synthetic-model",
                        "choices": [{"index": 0, "delta": {"content": part}}],
                    }
                )
                + "\n\n"
            ).encode()
        )
    frames.append(
        (
            "data: "
            + json.dumps(
                {
                    "id": "chatcmpl-browser",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "synthetic-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 60, "completion_tokens": 12, "total_tokens": 72},
                }
            )
            + "\n\n"
        ).encode()
    )
    frames.append(b"data: [DONE]\n\n")
    return frames


def start_upstream(state: UpstreamState, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802 - stdlib API
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b""
            with state.lock:
                state.requests.append(
                    {
                        "path": self.path,
                        "status": state.status,
                        "bytes": len(raw),
                        "has_authorization": bool(self.headers.get("authorization")),
                    }
                )
            if state.status != 200:
                body = json.dumps({"error": {"message": "synthetic"}}).encode()
                self.send_response(state.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for frame in _sse(state.parts):
                if state.chunk_delay_ms:
                    time.sleep(state.chunk_delay_ms / 1000)
                self.wfile.write(b"%x\r\n" % len(frame) + frame + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---- CDP -------------------------------------------------------------------


class CDP:
    def __init__(self, socket_: Any) -> None:
        self.socket = socket_
        self.serial = 0

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.serial += 1
        await self.socket.send(
            json.dumps({"id": self.serial, "method": method, "params": params or {}})
        )
        while True:
            response = json.loads(await asyncio.wait_for(self.socket.recv(), 60))
            if response.get("id") != self.serial:
                continue
            if "error" in response:
                raise RuntimeError(f"CDP {method} failed: {response['error']}")
            return response.get("result", {})

    async def evaluate(self, expression: str) -> Any:
        result = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            description = (
                details.get("exception", {}).get("description")
                or details.get("text")
                or "unknown"
            )
            raise RuntimeError(
                f"browser evaluation failed: {description[:300]} | expr={expression[:120]!r}"
            )
        return result.get("result", {}).get("value")


# ---- probe script (runs inside the page) -----------------------------------

CROSS_SPACE_PROBE = r"""(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const visiblePicker = () => {
    const nodes = [...document.querySelectorAll('[data-test=space-picker], [data-test=space-picker-mobile]')];
    return nodes.find((n) => n.getBoundingClientRect().width > 0) ?? null;
  };
  const items = () => [...document.querySelectorAll('[data-test=message-item]')];
  const picker = visiblePicker();
  if (!picker) return {error: 'no visible space picker'};
  const before = items().length;
  const beforeTexts = items().map((n) => (n.textContent ?? '').trim());
  // naive-ui NSelect opens from its rendered selection trigger.
  const trigger = picker.querySelector('.n-base-selection') ?? picker;
  trigger.click();
  await sleep(500);
  const options = [...document.querySelectorAll('.n-base-select-option')];
  const labels = options.map((o) => (o.textContent ?? '').trim());
  if (options.length < 2) return {error: 'no second option', labels, before, beforeTexts};
  const unselected = options.find((o) => !o.className.includes('--selected'));
  const target = unselected ?? options[options.length - 1];
  const chosen = (target.textContent ?? '').trim();
  target.click();
  await sleep(2500);
  const afterTexts = items().map((n) => (n.textContent ?? '').trim());
  return {
    before, beforeTexts, chosen, labels,
    afterCount: afterTexts.length, afterTexts,
    path: location.pathname,
  };
})()"""


CANCEL_PROBE = r"""(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const items = () => [...document.querySelectorAll('[data-test=message-item]')];
  const types = () => window.__fgProbe.events.map((e) => e.type);
  const input = document.querySelector('[data-test=composer-input]');
  const sendBtn = document.querySelector('[data-test=send-btn]');
  if (!input || !sendBtn) return {error: 'no composer'};
  // Mark the attempt's leading edge, then send.
  window.__fgProbe.t0 = performance.now();
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(input, '这个问题的回答请取消掉');
  input.dispatchEvent(new Event('input', {bubbles: true}));
  await sleep(200);
  sendBtn.click();

  // Wait until the run is actually generating (cancel button offered) and at
  // least one provisional frame has been painted, so the cancel lands
  // mid-stream rather than before the model starts.
  let sawCancelBtn = false;
  let provisionalBefore = 0;
  for (let i = 0; i < 300; i += 1) {
    const btn = document.querySelector('[data-test=cancel-run-btn]');
    const provisional = items().filter(
      (n) => n.querySelector('[data-test=provisional-mark]')).length;
    if (btn && provisional > 0) {
      sawCancelBtn = true;
      provisionalBefore = provisional;
      break;
    }
    await sleep(50);
  }
  if (!sawCancelBtn) {
    return {error: 'run never became cancellable', types: types()};
  }
  const cancelledAt = performance.now() - window.__fgProbe.t0;
  document.querySelector('[data-test=cancel-run-btn]').click();

  // Observe until a terminal event arrives.
  let terminalAt = null;
  for (let i = 0; i < 400; i += 1) {
    const t = types();
    if (t.includes('run.settled') || t.includes('run.failed') || t.includes('run.cancelled')) {
      terminalAt = performance.now() - window.__fgProbe.t0;
      break;
    }
    await sleep(50);
  }
  await sleep(800);
  const finalItems = items();
  return {
    sawCancelBtn,
    provisionalBefore,
    cancelledAt: Math.round(cancelledAt),
    terminalAt: terminalAt === null ? null : Math.round(terminalAt),
    eventTypes: types(),
    pendingIndicator: !!document.querySelector('[data-test=message-list] .pending'),
    errorNotice: document.querySelector('[data-test=error-notice]')?.textContent?.trim() ?? null,
    provisionalAfter: finalItems.filter(
      (n) => n.querySelector('[data-test=provisional-mark]')).length,
    assistantCount: finalItems.filter((n) => n.getAttribute('data-role') === 'assistant').length,
  };
})()"""


REVOKE_START_PROBE = r"""(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const items = () => [...document.querySelectorAll('[data-test=message-item]')];
  const types = () => window.__fgProbe.events.map((e) => e.type);
  const input = document.querySelector('[data-test=composer-input]');
  const sendBtn = document.querySelector('[data-test=send-btn]');
  if (!input || !sendBtn) return {error: 'no composer'};
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(input, '撤权测试的提问');
  input.dispatchEvent(new Event('input', {bubbles: true}));
  await sleep(200);
  sendBtn.click();
  // Wait until the run is genuinely streaming: the cancel button is offered
  // and at least one provisional frame is painted. Revoking before that would
  // test nothing about an in-flight stream.
  for (let i = 0; i < 300; i += 1) {
    const provisional = items().filter(
      (n) => n.querySelector('[data-test=provisional-mark]')).length;
    if (document.querySelector('[data-test=cancel-run-btn]') && provisional > 0) {
      return {
        started: true,
        runId: window.__fgProbe.lastRunId,
        provisionalBefore: provisional,
        itemCountBefore: items().length,
        textsBefore: items().map((n) => (n.textContent ?? '').trim()),
        typesBefore: types(),
      };
    }
    await sleep(50);
  }
  return {started: false, error: 'run never streamed', types: types()};
})()"""


REVOKE_OBSERVE_PROBE = r"""(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const items = () => [...document.querySelectorAll('[data-test=message-item]')];
  const types = () => window.__fgProbe.events.map((e) => e.type);
  const runId = window.__fgProbe.lastRunId;
  // Let the revocation propagate through the app's own space-context refresh.
  await sleep(6000);
  const afterRevoke = {
    types: types(),
    itemCount: items().length,
    texts: items().map((n) => (n.textContent ?? '').trim()),
    provisional: items().filter(
      (n) => n.querySelector('[data-test=provisional-mark]')).length,
    pendingIndicator: !!document.querySelector('[data-test=message-list] .pending'),
    errorNotice: document.querySelector('[data-test=error-notice]')?.textContent?.trim() ?? null,
    path: location.pathname,
  };
  // Raw read attempt with the app's own credentials: records what the current
  // API actually answers for a revoked account. Reported as an explicit
  // evidence boundary rather than folded into a pass/fail boolean.
  let rawReadStatus = null;
  if (runId) {
    try {
      const response = await fetch(`/api/agent/runs/${runId}/events`, {
        headers: window.__fgProbe.authHeaders(),
      });
      rawReadStatus = response.status;
    } catch (err) {
      rawReadStatus = String(err);
    }
  }
  // Hard refresh + reopen: revoked content must not come back.
  return {afterRevoke, rawReadStatus, runId};
})()"""


REVOKE_RELOAD_PROBE = r"""(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const items = () => [...document.querySelectorAll('[data-test=message-item]')];
  for (let i = 0; i < 600; i += 1) {
    if (document.querySelector('[data-test=assistant-launcher]')) break;
    await sleep(50);
  }
  // Reopen the assistant drawer and, if a conversation list exists, try to
  // select the revoked conversation again.
  for (let i = 0; i < 60; i += 1) {
    if (document.querySelector('[data-test=composer-input]')) break;
    const launcher = document.querySelector('[data-test=assistant-launcher]');
    if (!launcher) break;
    launcher.click();
    await sleep(250);
  }
  const toggle = document.querySelector('[data-test=session-toggle]');
  if (toggle?.getAttribute('aria-expanded') === 'false') toggle.click();
  await sleep(400);
  const sessionItems = [...document.querySelectorAll('[data-test=session-item]')];
  if (sessionItems.length > 0) sessionItems[0].click();
  await sleep(1500);
  return {
    sessionCount: sessionItems.length,
    itemCount: items().length,
    texts: items().map((n) => (n.textContent ?? '').trim()),
    pendingIndicator: !!document.querySelector('[data-test=message-list] .pending'),
    errorNotice: document.querySelector('[data-test=error-notice]')?.textContent?.trim() ?? null,
    path: location.pathname,
  };
})()"""


RESUME_PROBE = r"""(async () => {
                 const runId = window.__fgProbe.lastRunId;
                 if (!runId) return {error: 'no run'};
                 const readWith = async (lastEventId) => {
                   const controller = new AbortController();
                   const headers = window.__fgProbe.authHeaders();
                   if (lastEventId !== null) headers['Last-Event-ID'] = String(lastEventId);
                   const response = await fetch(`/api/agent/runs/${runId}/events`, {
                     headers, signal: controller.signal,
                   });
                   const reader = response.body.getReader();
                   const decoder = new TextDecoder();
                   let buffer = '';
                   const out = [];
                   for (;;) {
                     const {done, value} = await reader.read();
                     if (done) break;
                     buffer += decoder.decode(value, {stream: true});
                     const NEWLINE = String.fromCharCode(10);
                     const frames = buffer.split(NEWLINE + NEWLINE);
                     buffer = frames.pop() ?? '';
                     for (const frame of frames) {
                       const idLine = frame.split(NEWLINE).find((l) => l.startsWith('id:'));
                       const dataLine = frame.split(NEWLINE).find((l) => l.startsWith('data:'));
                       if (!dataLine) continue;
                       let parsed = null;
                       try { parsed = JSON.parse(dataLine.slice(5).trim()); } catch { continue; }
                       const seq = idLine ? Number(idLine.slice(3).trim()) : parsed.seq;
                       out.push({seq, type: parsed.type});
                       if (out.length >= 4 && lastEventId === null) {
                         controller.abort();
                         return {first: out, abortedAt: seq};
                       }
                     }
                   }
                   return {first: out, abortedAt: out.at(-1)?.seq ?? null};
                 };
                 let firstPart;
                 try { firstPart = await readWith(null); } catch { return {error: 'first read failed'}; }
                 const second = await readWith(firstPart.abortedAt);
                 return {first: firstPart.first, abortedAt: firstPart.abortedAt, second: second.first};
               })()"""


PROBE = r"""
window.__fgProbe = {
  events: [],
  renders: [],
  rewinds: 0,
  provisionalSeen: 0,
  t0: performance.now(),
  spaceId: null,
  otherSpaceId: null,
  lastRunId: null,
  authHeaders: () => ({}),
};
(() => {
  const origFetch = window.fetch;
  window.fetch = function(input, init) {
    const url = typeof input === 'string' ? input : (input?.url ?? '');
    // Capture the app's own Authorization header so the resume probe replays the
    // SAME real authenticated request instead of minting a second session
    // (refreshing here would rotate the app's refresh token).
    const rawHeaders = init?.headers ?? (typeof input === 'object' ? input?.headers : null);
    const auth = rawHeaders instanceof Headers
      ? rawHeaders.get('Authorization')
      : (rawHeaders?.Authorization ?? rawHeaders?.authorization ?? null);
    if (auth) {
      window.__fgProbe.authHeaders = () => ({ Authorization: auth });
    }
    const promise = origFetch.apply(this, arguments);
    const runMatch = String(url).match(/\/api\/agent\/runs\/(\d+)\/events/);
    if (runMatch) window.__fgProbe.lastRunId = Number(runMatch[1]);
    if (/\/api\/agent\/runs\/\d+\/events/.test(url)) {
      promise.then((response) => {
        const clone = response.clone();
        const reader = clone.body?.getReader?.();
        if (!reader) return;
        const decoder = new TextDecoder();
        let buffer = '';
        const pump = () => reader.read().then(({done, value}) => {
          if (done) return;
          buffer += decoder.decode(value, {stream: true});
          const NEWLINE = String.fromCharCode(10);
                     const frames = buffer.split(NEWLINE + NEWLINE);
          buffer = frames.pop() ?? '';
          for (const frame of frames) {
            for (const line of frame.split('\n')) {
              if (!line.startsWith('data:')) continue;
              const payload = line.slice(5).trim();
              if (!payload || payload === '[DONE]') continue;
              try {
                const parsed = JSON.parse(payload);
                window.__fgProbe.events.push({
                  type: parsed.type,
                  mark: performance.now() - window.__fgProbe.t0,
                  text: parsed.payload?.text ?? null,
                  delta: parsed.payload?.delta ?? null,
                });
              } catch { /* keep only frames we can read */ }
            }
          }
          return pump();
        }).catch(() => undefined);
        pump();
      }).catch(() => undefined);
    }
    return promise;
  };
})();
(() => {
  // The message list renders interleaved bodies; expose the author label so the
  // probe can tell a user turn from an assistant turn.
  const snapshot = () => {
    const items = [...document.querySelectorAll('[data-test=message-item]')];
    const assistant = items.filter((n) => n.getAttribute('data-role') === 'assistant'
                                        || n.classList.contains('assistant'));
    return items.map((n) => ({
      text: (n.querySelector('.body')?.textContent ?? n.textContent ?? '').trim().slice(0, 200),
      provisional: n.querySelector('[data-test=provisional-mark]') !== null
        || n.classList.contains('provisional'),
    }));
  };
  const record = () => {
    const rows = snapshot();
    const provisional = rows.filter((r) => r.provisional).length;
    if (provisional > 0) window.__fgProbe.provisionalSeen += 1;
    window.__fgProbe.renders.push({
      mark: performance.now() - window.__fgProbe.t0,
      count: rows.length,
      provisional,
      chars: rows.reduce((sum, r) => sum + r.text.length, 0),
    });
  };
  new MutationObserver(record).observe(document, {
    subtree: true, childList: true, characterData: true, attributes: true,
  });
})();
"""


async def drive(
    debug_port: int,
    base: str,
    refresh_token: str,
    screenshot: Path | None,
    width: int = 1365,
    height: int = 900,
) -> dict[str, Any]:
    import websockets

    targets = json.loads(
        subprocess.run(
            ["curl", "-s", f"http://127.0.0.1:{debug_port}/json/list"],
            capture_output=True,
            text=True,
        ).stdout
    )
    page = next(t for t in targets if t["type"] == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024) as ws:
        cdp = CDP(ws)
        await cdp.call("Page.enable")
        await cdp.call("Runtime.enable")
        await cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
        )
        # Bootstrap the app's own refresh-token path in an isolated profile.
        await cdp.call("Page.navigate", {"url": base + "/login"})
        for _ in range(400):
            if await cdp.evaluate("document.readyState === 'complete'"):
                break
            await asyncio.sleep(0.05)
        await cdp.evaluate(
            "localStorage.setItem('fg.refresh_token', " + json.dumps(refresh_token) + ")"
        )
        await cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": PROBE})
        await cdp.call("Page.navigate", {"url": base + "/family-tree"})
        # Wait for the shell + launcher.
        for _ in range(600):
            if await cdp.evaluate("!!document.querySelector('[data-test=assistant-launcher]')"):
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("assistant launcher never appeared")

        # The drawer mounts lazily; re-click until the composer is present.
        for attempt in range(60):
            if await cdp.evaluate("!!document.querySelector('[data-test=composer-input]')"):
                break
            await cdp.evaluate("document.querySelector('[data-test=assistant-launcher]').click()")
            await asyncio.sleep(0.25)
        else:
            diagnosed = await cdp.evaluate(
                """(() => ({
                     path: location.pathname,
                     hasPanel: !!document.querySelector('[data-test=assistant-panel]'),
                     hasContent: !!document.querySelector('[data-test=assistant-panel-content]'),
                     emptyBtn: !!document.querySelector('[data-test=new-session-btn-empty]'),
                     launcherDisabled: document.querySelector('[data-test=assistant-launcher]')?.disabled ?? null,
                     bodyText: (document.body?.innerText ?? '').slice(0, 400),
                   }))()"""
            )
            raise RuntimeError(f"composer never appeared: {diagnosed}")

        # Focus the run: mark the leading edge of the user-visible attempt.
        await cdp.evaluate("window.__fgProbe.t0 = performance.now()")
        await cdp.evaluate(
            """(() => {
                 const el = document.querySelector('[data-test=composer-input]');
                 const setter = Object.getOwnPropertyDescriptor(
                   window.HTMLTextAreaElement.prototype, 'value').set;
                 setter.call(el, '蓝罐茶叶在哪里');
                 el.dispatchEvent(new Event('input', {bubbles: true}));
                 return el.value;
               })()"""
        )
        await asyncio.sleep(0.2)
        await cdp.evaluate("document.querySelector('[data-test=send-btn]').click()")

        # Observe until the run reaches a terminal state (send button back, no
        # pending indicator) or the budget expires.
        deadline = time.monotonic() + 90
        terminal = False
        while time.monotonic() < deadline:
            state = await cdp.evaluate(
                """(() => {
                     const probe = window.__fgProbe;
                     const types = probe.events.map((e) => e.type);
                     return {
                       terminal: types.includes('run.settled') || types.includes('run.failed')
                         || types.includes('run.cancelled'),
                       count: probe.events.length,
                     };
                   })()"""
            )
            if state["terminal"]:
                terminal = True
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.6)

        probe = await cdp.evaluate("JSON.parse(JSON.stringify(window.__fgProbe))")
        dom = await cdp.evaluate(
            """(() => {
                 const items = [...document.querySelectorAll('[data-test=message-item]')];
                 return {
                   count: items.length,
                   provisional: items.filter((n) => n.querySelector('[data-test=provisional-mark]')).length,
                   texts: items.map((n) => (n.textContent ?? '').trim()),
                   pendingIndicator: !!document.querySelector('[data-test=message-list] .pending'),
                   errorNotice: document.querySelector('[data-test=error-notice]')?.textContent?.trim() ?? null,
                 };
               })()"""
        )
        if screenshot is not None:
            png = await cdp.call("Page.captureScreenshot", {"format": "png"})
            screenshot.write_bytes(base64.b64decode(png["data"]))

        # Refresh: the authoritative message must survive once, not duplicate.
        await cdp.call("Page.navigate", {"url": base + "/family-tree"})
        for _ in range(600):
            if await cdp.evaluate("!!document.querySelector('[data-test=assistant-launcher]')"):
                break
            await asyncio.sleep(0.05)
        # After a hard refresh no session is selected yet (the store only loads
        # the list), so the user must pick the conversation again. Reproduce that
        # path rather than asserting on an empty panel.
        for _ in range(60):
            if await cdp.evaluate("!!document.querySelector('[data-test=composer-input]')"):
                break
            await cdp.evaluate("document.querySelector('[data-test=assistant-launcher]').click()")
            await asyncio.sleep(0.25)
        await cdp.evaluate(
            """(() => {
                 const toggle = document.querySelector('[data-test=session-toggle]');
                 if (toggle?.getAttribute('aria-expanded') === 'false') toggle.click();
                 return true;
               })()"""
        )
        await asyncio.sleep(0.4)
        await cdp.evaluate(
            """(() => {
                 const item = document.querySelector('[data-test=session-item]');
                 if (item) item.click();
                 return !!item;
               })()"""
        )
        # Wait for the replayed history to render.
        for _ in range(120):
            count = await cdp.evaluate(
                "document.querySelectorAll('[data-test=message-item]').length"
            )
            if count and count > 0:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(1.0)
        after_refresh = await cdp.evaluate(
            """(() => {
                 const items = [...document.querySelectorAll('[data-test=message-item]')];
                 return {
                   count: items.length,
                   provisional: items.filter((n) => n.querySelector('[data-test=provisional-mark]')).length,
                   texts: items.map((n) => (n.textContent ?? '').trim()),
                 };
               })()"""
        )
        # F-R4 取消：在真实浏览器里点「取消回答」，验证服务端裁决的取消在页面上
        # 呈现为「已取消」（无错误横幅、无残留临时气泡、无无限等待）。
        # 必须真的在流中取消（等取消按钮出现且已有临时正文），否则证明不了中断语义。
        cancel = await cdp.evaluate(CANCEL_PROBE)

        # F-R4 断线续传：中止在飞的 SSE 读取，再带 Last-Event-ID 重新请求，
        # 拼接结果必须无重复、无缺失（真实 API/SSE，非 route mock）。

        # F-R4 断线续传必须在页面上有活跃 Run 时进行；刷新后 lastRunId 已经失效，
        # 所以先重新发一条消息，拿到真实的重连目标。
        await cdp.evaluate("window.__fgProbe.lastRunId = null")
        for attempt in range(60):
            if await cdp.evaluate("!!document.querySelector('[data-test=composer-input]')"):
                break
            await asyncio.sleep(0.2)
        await cdp.evaluate(
            """(() => {
                 const el = document.querySelector('[data-test=composer-input]');
                 const setter = Object.getOwnPropertyDescriptor(
                   window.HTMLTextAreaElement.prototype, 'value').set;
                 setter.call(el, '再问一次');
                 el.dispatchEvent(new Event('input', {bubbles: true}));
                 return true;
               })()"""
        )
        await asyncio.sleep(0.2)
        await cdp.evaluate("document.querySelector('[data-test=send-btn]').click()")
        for _ in range(400):
            if await cdp.evaluate("window.__fgProbe.lastRunId !== null"):
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)
        resume = await cdp.evaluate(RESUME_PROBE)

        # F-R4 跨空间：用真实的 topbar 空间选择器切换到另一个空间，面板不得残留旧消息。
        cross_space = await cdp.evaluate(CROSS_SPACE_PROBE)

        return {
            "terminal": terminal,
            "probe": probe,
            "dom": dom,
            "after_refresh": after_refresh,
            "cancel": cancel,
            "resume": resume,
            "cross_space": cross_space,
        }


async def drive_revocation(
    debug_port: int,
    base: str,
    member_refresh_token: str,
    member_access_token: str,
    owner_access_token: str,
    member_name: str,
    screenshot: Path | None = None,
    width: int = 1365,
    height: int = 900,
) -> dict[str, Any]:
    """F-R4 失权：被撤权者本人正在收流时，另一有权主体撤销其成员资格。

    与 `drive` 分开，因为它需要**两个主体**：浏览器以成员身份登录并触发真实流，
    移除动作由 owner token 经同一个真实 listener 发起。原 F 把这格记为“属另一
    轮环境搭建”而略过，但 F-R4 明确要求覆盖失权。
    """
    import websockets

    # Read-only calls on behalf of the member (run/session lookups). The browser
    # holds the refresh token; this access token is used only for the harness'
    # own observations so we never refresh inside the page (which would rotate
    # the token the app is using).
    member_headers = {"Authorization": f"Bearer {member_access_token}"}
    owner_headers = {"Authorization": f"Bearer {owner_access_token}"}

    targets = json.loads(
        subprocess.run(
            ["curl", "-s", f"http://127.0.0.1:{debug_port}/json/list"],
            capture_output=True,
            text=True,
        ).stdout
    )
    page = next(t for t in targets if t["type"] == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024) as ws:
        cdp = CDP(ws)
        await cdp.call("Page.enable")
        await cdp.call("Runtime.enable")
        await cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
        )
        await cdp.call("Page.navigate", {"url": base + "/login"})
        for _ in range(400):
            if await cdp.evaluate("document.readyState === 'complete'"):
                break
            await asyncio.sleep(0.05)
        # Start from a clean profile: the previous phase left another account's
        # refresh token in localStorage.
        await cdp.evaluate("localStorage.clear()")
        await cdp.evaluate(
            "localStorage.setItem('fg.refresh_token', " + json.dumps(member_refresh_token) + ")"
        )
        await cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": PROBE})
        await cdp.call("Page.navigate", {"url": base + "/family-tree"})
        for _ in range(600):
            if await cdp.evaluate("!!document.querySelector('[data-test=assistant-launcher]')"):
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("revocation: assistant launcher never appeared")
        for _ in range(60):
            if await cdp.evaluate("!!document.querySelector('[data-test=composer-input]')"):
                break
            await cdp.evaluate("document.querySelector('[data-test=assistant-launcher]').click()")
            await asyncio.sleep(0.25)
        else:
            raise RuntimeError("revocation: composer never appeared")

        await cdp.evaluate("window.__fgProbe.t0 = performance.now()")
        started = await cdp.evaluate(REVOKE_START_PROBE)
        if not started.get("started"):
            return {"started": started, "error": started.get("error", "stream never started")}

        # Resolve the space the stream actually runs in, then revoke THAT
        # membership. Revoking a different space would test nothing: the first
        # version of this harness revoked space 1 while the stream lived in
        # space 2, and the run happily completed.
        run_id = int(started["runId"])
        run_info = _get_json(f"{base}/api/agent/runs/{run_id}", member_headers)
        session_id = int(run_info["session_id"])
        sessions = _get_json(f"{base}/api/agent/sessions", member_headers)
        stream_space_id = next(
            int(row["space_id"]) for row in sessions if int(row["id"]) == session_id
        )
        members = _get_json(f"{base}/api/spaces/{stream_space_id}/members", owner_headers)
        target = next(
            (row for row in members if row.get("user_name") == member_name), None
        )
        if target is None or target.get("status") != "active":
            return {
                "started": started,
                "error": f"{member_name} is not an active member of space {stream_space_id}",
                "stream_space_id": stream_space_id,
                "members": [
                    {"user_name": r.get("user_name"), "status": r.get("status")} for r in members
                ],
            }
        messages_before = _get_json(
            f"{base}/api/agent/sessions/{session_id}/messages", member_headers
        )

        # Revoke through the real API while the member's stream is in flight.
        # A real authorized call, not a DB poke: the FSM, audit row and domain
        # event all happen exactly as they would for a user action.
        revoked = await asyncio.to_thread(
            _revoke_membership, base, owner_access_token, int(target["id"])
        )

        observed = await cdp.evaluate(REVOKE_OBSERVE_PROBE)
        if screenshot is not None:
            png = await cdp.call("Page.captureScreenshot", {"format": "png"})
            screenshot.write_bytes(base64.b64decode(png["data"]))

        # Hard refresh: revoked content must not reappear from history/replay.
        await cdp.call("Page.navigate", {"url": base + "/family-tree"})
        reloaded = await cdp.evaluate(REVOKE_RELOAD_PROBE)
        return {
            "started": started,
            "stream_space_id": stream_space_id,
            "membership": {"id": target["id"], "status": target["status"]},
            "revoked": revoked,
            "observed": observed,
            "reloaded": reloaded,
            "run_after_revoke": _await_terminal(
                base, member_headers, run_id, timeout_s=float(os.environ.get("FG_REVOKE_WAIT_S", "90"))
            ),
            "messages_before": [row.get("role") for row in messages_before],
            "messages_after": [
                row.get("role")
                for row in _get_json(
                    f"{base}/api/agent/sessions/{session_id}/messages", member_headers
                )
            ],
        }


def _get_json(url: str, headers: dict[str, str]) -> Any:
    import urllib.request

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def _await_terminal(
    base: str, headers: dict[str, str], run_id: int, *, timeout_s: float
) -> dict[str, Any]:
    """轮询 Run 终态；超时如实返回当时的 status（不伪装成终态）。"""
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            last = _get_json(f"{base}/api/agent/runs/{run_id}", headers)
        except Exception as exc:  # noqa: BLE001 - reported as an explicit outcome
            return {"status": None, "error_type": type(exc).__name__}
        if last.get("status") in ("succeeded", "failed", "cancelled", "expired"):
            return last
        time.sleep(0.5)
    return {**last, "timed_out": True}


def _revoke_membership(base: str, owner_token: str, member_id: int) -> dict[str, Any]:
    """owner 移除成员：真实 DELETE，返回状态码与错误码（不泄露响应正文）。"""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{base}/api/space-memberships/{member_id}",
        method="DELETE",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return {"status": response.status}
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        try:
            code = json.loads(body).get("error", {}).get("code")
        except json.JSONDecodeError:
            code = None
        return {"status": error.code, "error_code": code}
    except Exception as exc:  # noqa: BLE001 - reported as an explicit outcome
        return {"status": None, "error_type": type(exc).__name__}


def _post_expect_error(
    url: str, payload: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any]:
    """POST 并返回状态码 + 错误码（用于断言拒绝，不泄露响应正文）。"""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return {"status": response.status}
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        try:
            code = json.loads(body).get("error", {}).get("code")
        except json.JSONDecodeError:
            code = None
        return {"status": error.code, "error_code": code}
    except Exception as exc:  # noqa: BLE001
        return {"status": None, "error_type": type(exc).__name__}


def _grade_revocation(grid: Grid, revocation: dict[str, Any]) -> None:
    """F-R4 失权的逐格判定。

    只有服务端授权边界是合同固定的；已渲染内容的去留、Run 收敛时刻与重连后的
    读取能力如实记录为观察值，不折算成安全结论（见 design.md 的证据边界）。
    """
    started = revocation.get("started") or {}
    revoked = revocation.get("revoked") or {}
    observed = revocation.get("observed") or {}
    after = observed.get("afterRevoke") or {}
    reloaded = revocation.get("reloaded") or {}
    new_session = revocation.get("new_session_after_revoke") or {}
    run_after = revocation.get("run_after_revoke") or {}
    after_texts = after.get("texts") or []
    reloaded_texts = reloaded.get("texts") or []
    before_texts = started.get("textsBefore") or []
    stream_space_id = revocation.get("stream_space_id")

    grid.cell(
        "UI2-8",
        "UI2",
        "F-R4 失权：撤权后不得再在该空间创建新会话（服务端授权边界）",
        "pass"
        if revoked.get("status") == 204
        and new_session.get("status") == 403
        and new_session.get("error_code") == "SPACE_FORBIDDEN_ACTOR"
        else "fail",
        f"space={stream_space_id} revoke_http={revoked.get('status')} "
        f"new_session_http={new_session.get('status')} code={new_session.get('error_code')} "
        f"run={run_after.get('status')}",
        {
            "revoked": revoked,
            "membership": revocation.get("membership"),
            "new_session_after_revoke": new_session,
            "stream_space_id": stream_space_id,
            "stream_started": started,
            "note": (
                "撤权经真实 API（DELETE /api/space-memberships/{id}）由 owner 发起，"
                "且移除的是**该流实际所在空间**的成员资格（早期版本误移了另一个空间，"
                "使该格变成空转，已修正）；断言撤权后成员不再能创建 Agent 会话。"
            ),
        },
    )

    grid.cell(
        "UI2-9",
        "UI2",
        "F-R4 失权：撤权后 Run 必须收敛为终态且不再声称仍在生成",
        "pass"
        if run_after.get("status") in ("succeeded", "failed", "cancelled", "expired")
        and after.get("pendingIndicator") is False
        and after.get("provisional") == 0
        else "fail",
        f"run={run_after.get('status')} timed_out={run_after.get('timed_out')} "
        f"pending={after.get('pendingIndicator')} provisional={after.get('provisional')} "
        f"items={after.get('itemCount')} error={after.get('errorNotice')}",
        {
            "item_count_before": started.get("itemCountBefore"),
            "item_count_after": after.get("itemCount"),
            "provisional_after": after.get("provisional"),
            "texts_before": before_texts,
            "texts_after": after_texts,
            "error_notice": after.get("errorNotice"),
            "path": after.get("path"),
            "run_after_revoke": run_after,
            "note": (
                "断言两件事：Run 收敛为终态，且临时气泡不再声称仍在生成。"
                "只查 pendingIndicator 是不够的——撤权后 Run 若停在 leased，"
                "气泡会继续渲染「生成中…」而 pending 指示器已经消失。"
            ),
        },
    )

    grid.cell(
        "UI2-10",
        "UI2",
        "F-R4 失权：刷新后不复活旧内容、不越权读到新内容",
        "pass"
        if not any(
            "撤权测试" in text or "蓝" in text
            for text in reloaded_texts
            if text not in before_texts
        )
        else "fail",
        f"reload_items={reloaded.get('itemCount')} sessions={reloaded.get('sessionCount')} "
        f"pending={reloaded.get('pendingIndicator')} error={reloaded.get('errorNotice')} "
        f"msg_before={revocation.get('messages_before')} "
        f"msg_after={revocation.get('messages_after')}",
        {
            "session_count": reloaded.get("sessionCount"),
            "item_count": reloaded.get("itemCount"),
            "texts": reloaded_texts,
            "texts_before": before_texts,
            "pending_indicator": reloaded.get("pendingIndicator"),
            "error_notice": reloaded.get("errorNotice"),
            "raw_read_status_after_revoke": observed.get("rawReadStatus"),
            "messages_before": revocation.get("messages_before"),
            "messages_after": revocation.get("messages_after"),
            "path": reloaded.get("path"),
            "note": (
                "`raw_read_status_after_revoke` 与 `messages_after` 记录现状：历史投影端点"
                "只复核账号归属，不逐事件复核空间成员资格。这些值如实上报，"
                "不声称撤权即时切断已持久化历史的读取。"
            ),
        },
    )


SEED = """
import os
from app.db import SessionLocal
from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.utils.timeutil import utcnow
with SessionLocal() as db:
    now = utcnow()
    # Provider name is unique: reuse the existing row when the harness runs this
    # seed more than once (a second family space needs its own setting row).
    provider = db.query(AgentProvider).filter_by(name='browser-acceptance').one_or_none()
    if provider is None:
        provider = AgentProvider(name='browser-acceptance', kind='local',
            api='openai-completions', base_url=os.environ['FG_FAKE_UPSTREAM'],
            allowed_models_json=['synthetic-model'], context_window=272000,
            max_tokens=2048, reasoning=False, input_modalities_json=['text'],
            thinking_levels_json=[], enabled=True, created_at=now, updated_at=now)
        db.add(provider)
        db.flush()
    for space in os.environ['FG_SPACE_IDS'].split(','):
        space_id = int(space)
        existing = db.query(AgentSpaceProviderSetting).filter_by(
            space_id=space_id, agent_kind='assistant').one_or_none()
        if existing is not None:
            continue
        db.add(AgentSpaceProviderSetting(space_id=space_id, agent_kind='assistant',
            provider_id=provider.id, model='synthetic-model',
            cloud_allowed=False, local_required=False, enabled=True))
    db.commit()
"""


def start_frontend_proxy(
    dist: Path, api_targets: dict[str, str], port: int
) -> ThreadingHTTPServer:
    """Serve the real production build and proxy /api to the isolated listeners.

    `vite preview` deliberately does not apply `server.proxy`, so the design's
    "指定测试 proxy" is this explicit shim: static files come from the real build
    and API paths are forwarded verbatim (no route mocking, no payload rewriting).
    """
    import http.client
    import urllib.parse

    index = (dist / "index.html").read_bytes()
    content_types = {
        ".js": "text/javascript",
        ".css": "text/css",
        ".html": "text/html",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".woff2": "font/woff2",
    }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:
            pass

        def _proxy(self, method: str) -> None:
            prefix = next((p for p in api_targets if self.path.startswith(p)), None)
            if prefix is None:
                self.send_error(404)
                return
            target = api_targets[prefix]
            parsed = urllib.parse.urlsplit(target)
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {
                header: value
                for header, value in self.headers.items()
                if header.lower() not in ("host", "content-length", "connection")
            }
            # Stream through with http.client rather than urllib: the SSE endpoint
            # must reach the browser chunk-by-chunk. Buffering here would hide the
            # very behaviour F-R4/F-R5 measure (incremental visibility + transport
            # timing), so this shim must relay each read immediately.
            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=180)
            try:
                connection.request(method, self.path, body=body, headers=headers)
                response = connection.getresponse()
                self.send_response(response.status)
                passthrough = [
                    (k, v)
                    for k, v in response.getheaders()
                    if k.lower() not in ("transfer-encoding", "connection", "content-length")
                ]
                for key, value in passthrough:
                    self.send_header(key, value)
                # Advertise chunked framing so Starlette's incrementally flushed
                # SSE chunks are forwarded as they arrive.
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                while True:
                    # read1() returns as soon as ONE chunk's bytes are available;
                    # read() would block until the connection ends and silently
                    # turn this SSE relay into a full-response buffer.
                    chunk = response.read1(4096)
                    if not chunk:
                        break
                    self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:  # noqa: BLE001 - upstream down surfaces as 502
                try:
                    self.send_error(502)
                except Exception:  # noqa: BLE001 - client already gone
                    pass
            finally:
                connection.close()

        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            if any(self.path.startswith(p) for p in api_targets):
                self._proxy("GET")
                return
            relative = self.path.split("?", 1)[0].lstrip("/")
            candidate = (dist / relative).resolve() if relative else dist / "index.html"
            if relative and candidate.is_file() and dist.resolve() in candidate.parents:
                payload = candidate.read_bytes()
                self.send_response(200)
                self.send_header(
                    "Content-Type", content_types.get(candidate.suffix, "application/octet-stream")
                )
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            # SPA fallback for client-side routes.
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(index)))
            self.end_headers()
            self.wfile.write(index)

        do_POST = lambda self: self._proxy("POST")  # noqa: E731 - stdlib API
        do_PATCH = lambda self: self._proxy("PATCH")  # noqa: E731 - stdlib API
        do_PUT = lambda self: self._proxy("PUT")  # noqa: E731 - stdlib API
        do_DELETE = lambda self: self._proxy("DELETE")  # noqa: E731 - stdlib API

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@dataclass
class Grid:
    cells: list[dict[str, Any]] = field(default_factory=list)

    def cell(
        self,
        cell_id: str,
        group: str,
        requirement: str,
        result: str,
        note: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.cells.append(
            {
                "cell": cell_id,
                "group": group,
                "requirement": requirement,
                "result": result,
                "note": note,
                "evidence": evidence or {},
            }
        )

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [c for c in self.cells if c["result"] == "fail"]


def wait_health(url: str, deadline_s: float = 90.0) -> bool:
    import urllib.request

    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - any failure means "not ready yet"
            time.sleep(0.2)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=None)
    parser.add_argument("--chrome", default=CHROME)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--keep", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if not VENV_PY.exists():
        print("BLOCKED: backend/.venv 不存在", file=sys.stderr)
        return EXIT_BLOCKED
    if not (AGENT / "dist" / "worker.js").exists():
        print("BLOCKED: agent/dist 未构建（cd agent && npm run build）", file=sys.stderr)
        return EXIT_BLOCKED
    if not (FRONTEND / "dist" / "index.html").is_file():
        print("BLOCKED: frontend/dist 未构建（cd frontend && npm run build）", file=sys.stderr)
        return EXIT_BLOCKED
    if not Path(args.chrome).exists():
        print(f"BLOCKED: Chrome 不存在 {args.chrome}", file=sys.stderr)
        return EXIT_BLOCKED

    data_dir = Path(tempfile.mkdtemp(prefix="fg-browser-"))
    public_port, internal_port, admin_port, agent_health_port, upstream_port, vite_port = (
        free_ports(6)
    )
    state = UpstreamState()
    upstream = start_upstream(state, upstream_port)
    grid = Grid()
    procs: list[subprocess.Popen] = []
    chrome: subprocess.Popen | None = None
    proxy: ThreadingHTTPServer | None = None
    env: dict[str, str] = {}

    try:
        env = os.environ.copy()
        env.update(
            {
                "DATA_DIR": str(data_dir),
                "PYTHONPATH": str(BACKEND),
                "PUBLIC_API_PORT": str(public_port),
                "PUBLIC_API_HOST": "127.0.0.1",
                "INTERNAL_AGENT_API_PORT": str(internal_port),
                "INTERNAL_AGENT_API_HOST": "127.0.0.1",
                "ADMIN_API_PORT": str(admin_port),
                "ADMIN_API_HOST": "127.0.0.1",
                "SECRET_KEY": secrets.token_hex(32),
                "ADMIN_JWT_SECRET": secrets.token_hex(32),
                "ADMIN_JWT_ISSUER": "fg-browser-admin-issuer",
                "ADMIN_JWT_AUDIENCE": "fg-browser-admin-audience",
                "DEV_SEED_DEMO_DATA": "1",
                "BCRYPT_ROUNDS": "4",
                "AGENT_RUNTIME_ENABLED": "1",
                "AGENT_SERVICE_SECRET": secrets.token_hex(32),
                "PERSONAL_FAMILY_VIEW_ENABLED": "1",
                "MEMORY_ENABLED": "1",
                "RAG_ENABLED": "1",
                "STEWARD_ENABLED": "",
                "STEWARD_WORKER_ENABLED": "",
                # sidecar reads the same environment
                "FG_API_BASE_URL": f"http://127.0.0.1:{public_port}",
                "FG_INTERNAL_API_BASE_URL": f"http://127.0.0.1:{internal_port}",
                "HEALTH_PORT": str(agent_health_port),
                "AGENT_SIDECAR_ID": "fg-browser-acceptance",
                "AGENT_LEASE_POLL_MS": "50",
                "FG_FAKE_UPSTREAM": f"http://127.0.0.1:{upstream_port}/v1",
            }
        )

        migrate = subprocess.run(
            [str(VENV_PY), "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if migrate.returncode != 0:
            print(f"BLOCKED: alembic upgrade head 失败\n{migrate.stderr[-1200:]}", file=sys.stderr)
            return EXIT_BLOCKED
        grid.cell("ENV-1", "env", "隔离库迁移到 head", "pass", "alembic upgrade head")

        serve_log = data_dir.parent / f"{data_dir.name}-serve.log"
        handle = serve_log.open("w", encoding="utf-8")
        serve = subprocess.Popen(
            [str(VENV_PY), "-m", "app.serve"],
            cwd=str(BACKEND),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        procs.append(serve)
        family = f"http://127.0.0.1:{public_port}"
        if not wait_health(f"{family}/api/health"):
            tail = "\n".join(serve_log.read_text(encoding="utf-8").splitlines()[-20:])
            print(f"BLOCKED: 隔离 listener 未就绪\n{tail}", file=sys.stderr)
            return EXIT_BLOCKED
        grid.cell("ENV-2", "env", "真实三 listener 就绪", "pass", "app.serve")

        sidecar_log = serve_log.with_name(f"{data_dir.name}-sidecar.log")
        sidecar_handle = sidecar_log.open("w", encoding="utf-8")
        sidecar = subprocess.Popen(
            ["node", "dist/main.js"],
            cwd=str(AGENT),
            env=env,
            stdout=sidecar_handle,
            stderr=subprocess.STDOUT,
        )
        procs.append(sidecar)
        if not wait_health(f"http://127.0.0.1:{agent_health_port}/readyz"):
            tail = "\n".join(sidecar_log.read_text(encoding="utf-8").splitlines()[-20:])
            print(f"BLOCKED: sidecar 未就绪\n{tail}", file=sys.stderr)
            return EXIT_BLOCKED
        grid.cell("ENV-3", "env", "真实 sidecar 就绪", "pass", "node dist/main.js")

        import urllib.request

        def post(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", **(headers or {})},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)

        tokens = post(
            f"{family}/api/auth/login", {"name": "朱元璋", "pin": "123456"}
        )
        auth = {"Authorization": f"Bearer {tokens['access_token']}"}
        request = urllib.request.Request(f"{family}/api/spaces", headers=auth)
        with urllib.request.urlopen(request, timeout=20) as response:
            spaces = json.load(response)
        space_ids = [str(s["id"]) for s in spaces]
        seeded = subprocess.run(
            [str(VENV_PY), "-c", SEED],
            cwd=str(BACKEND),
            env={**env, "FG_SPACE_IDS": ",".join(space_ids)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        if seeded.returncode != 0:
            print(f"BLOCKED: Provider 播种失败\n{seeded.stderr[-800:]}", file=sys.stderr)
            return EXIT_BLOCKED
        grid.cell("ENV-4", "env", "隔离 Provider 指向 loopback 假上游（无云 egress）", "pass", "local provider")

        # F-R4 跨空间切换需要一个真实存在的第二个家族空间：用真实 API 创建 lineage，
        # 并把同一 provider 设置挂到它上面（否则切换后助手无模型可解析）。
        def send(url: str, payload: dict[str, Any], extra: dict[str, str] | None = None) -> Any:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", **auth, **(extra or {})},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)

        second = send(
            f"{family}/api/spaces", {"name": "第二个家族", "kind": "lineage"}
        )
        second_space_id = second["id"]
        seeded_second = subprocess.run(
            [str(VENV_PY), "-c", SEED],
            cwd=str(BACKEND),
            env={**env, "FG_SPACE_IDS": str(second_space_id)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        if seeded_second.returncode != 0:
            print(
                f"BLOCKED: 第二空间 Provider 播种失败\n{seeded_second.stderr[-800:]}",
                file=sys.stderr,
            )
            return EXIT_BLOCKED
        grid.cell(
            "ENV-7",
            "env",
            "第二个家族空间已创建（跨空间对抗用）",
            "pass",
            f"space_id={second_space_id}",
        )

        # Real production frontend build served through the explicit test proxy
        # (no route mocking: the browser talks to the real FastAPI listener).
        proxy = start_frontend_proxy(
            FRONTEND / "dist",
            {
                "/api": f"http://127.0.0.1:{public_port}",
                "/admin-api": f"http://127.0.0.1:{admin_port}",
            },
            vite_port,
        )
        base = f"http://127.0.0.1:{vite_port}"
        if not wait_health(base + "/login"):
            print("BLOCKED: 前端测试 proxy 未就绪", file=sys.stderr)
            return EXIT_BLOCKED
        grid.cell(
            "ENV-5",
            "env",
            "真实前端构建经显式测试 proxy 提供（非 route mock）",
            "pass",
            "frontend/dist + /api → 隔离 listener",
        )

        profile = data_dir.parent / f"{data_dir.name}-chrome"
        chrome = subprocess.Popen(
            [
                args.chrome,
                "--headless=new",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-extensions",
                "--no-first-run",
                "--no-default-browser-check",
                "--remote-debugging-port=0",
                f"--user-data-dir={profile}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        devtools = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 25
        while not devtools.exists() and time.monotonic() < deadline and chrome.poll() is None:
            time.sleep(0.05)
        if not devtools.exists():
            print("BLOCKED: isolated Chrome startup failed", file=sys.stderr)
            return EXIT_BLOCKED
        debug_port = int(devtools.read_text().splitlines()[0])
        grid.cell("ENV-6", "env", "headless Chrome 就绪（独立 profile）", "pass", "CDP")

        result = asyncio.run(
            drive(
                debug_port,
                base,
                tokens["refresh_token"],
                args.screenshot,
            )
        )
        _grade(grid, result, state)

        # F-R4 失权：需要第二个主体（被撤权者本人收流 + owner 执行移除）。
        # 原 F 把这格记为“属另一轮环境搭建”而略过，但 F-R4 明确要求覆盖失权。
        member_tokens = post(
            f"{family}/api/auth/login", {"name": "朱标", "pin": "123456"}
        )
        member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}
        revocation = asyncio.run(
            drive_revocation(
                debug_port,
                base,
                member_tokens["refresh_token"],
                member_tokens["access_token"],
                tokens["access_token"],
                "朱标",
                args.screenshot.with_name("browser-revocation.png")
                if args.screenshot
                else None,
            )
        )
        if revocation.get("error"):
            grid.cell(
                "UI2-8",
                "UI2",
                "F-R4 失权：撤权后不得再在该空间创建新会话（服务端授权边界）",
                "blocked",
                str(revocation["error"])[:200],
                revocation,
            )
        else:
            # 硬安全边界（服务端，与 UI 无关）：撤权后不得再在该空间创建新会话。
            revocation["new_session_after_revoke"] = _post_expect_error(
                f"{family}/api/agent/sessions",
                {"space_id": int(revocation["stream_space_id"])},
                member_headers,
            )
            _grade_revocation(grid, revocation)
    except Exception as exc:  # noqa: BLE001 - harness failure is an environment block
        grid.cell("HARNESS", "env", "浏览器验收未中断完成", "fail", f"{type(exc).__name__}: {exc}")
        print(f"BLOCKED: harness 异常 {type(exc).__name__}: {exc}", file=sys.stderr)
        report = _report(grid, state)
        if args.report:
            Path(args.report).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return EXIT_BLOCKED
    finally:
        if chrome is not None:
            chrome.terminate()
            try:
                chrome.wait(timeout=10)
            except subprocess.TimeoutExpired:
                chrome.kill()
        for proc in reversed(procs):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
        upstream.shutdown()
        if proxy is not None:
            proxy.shutdown()
        for handle_name in ("serve", "sidecar"):
            path = data_dir.parent / f"{data_dir.name}-{handle_name}.log"
            path.unlink(missing_ok=True)
        if args.keep:
            print(f"kept DATA_DIR: {data_dir}", file=sys.stderr)
        else:
            shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(data_dir.parent / f"{data_dir.name}-chrome", ignore_errors=True)
        grid.cell(
            "ENV-9",
            "env",
            "临时 DATA_DIR 与浏览器 profile 已删除",
            "pass" if not data_dir.exists() else "fail",
            data_dir.name,
        )

    report = _report(grid, state)
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return EXIT_PASS if not grid.failed else EXIT_FAILED


def _grade(grid: Grid, result: dict[str, Any], state: UpstreamState) -> None:
    probe = result.get("probe") or {}
    events = probe.get("events") or []
    renders = probe.get("renders") or []
    dom = result.get("dom") or {}
    after_refresh = result.get("after_refresh") or {}
    types = [e["type"] for e in events]
    deltas = [e for e in events if e["type"] == "assistant.text_delta"]
    authoritative = [e for e in events if e["type"] == "message.assistant_added"]
    final_text = authoritative[0]["text"] if authoritative else None
    expected = "".join(state.parts)

    grid.cell(
        "UI1-1",
        "UI1",
        "F-R4 公共 SSE 真实到达浏览器（非 route mock）且 run 达终态",
        "pass" if result.get("terminal") and events else "fail",
        f"terminal={result.get('terminal')} frames={len(events)} types={types[:6]}",
        {
            "terminal": result.get("terminal"),
            "frame_count": len(events),
            "event_types": types,
            "upstream_requests": state.snapshot()["count"],
        },
    )
    # P0-2 增量显示：临时正文必须在权威消息之前到达并可见。
    first_delta = next((e["mark"] for e in deltas), None)
    authoritative_mark = next((e["mark"] for e in authoritative), None)
    grid.cell(
        "UI1-2",
        "UI1",
        "F-R4/09-18 临时正文先于权威消息到达浏览器",
        "pass"
        if first_delta is not None
        and authoritative_mark is not None
        and first_delta < authoritative_mark
        else "fail",
        f"first_delta_ms={None if first_delta is None else round(first_delta)} "
        f"authoritative_ms={None if authoritative_mark is None else round(authoritative_mark)}",
        {
            "delta_frames": len(deltas),
            "first_delta_ms": first_delta,
            "authoritative_ms": authoritative_mark,
            "delta_chars": sum(len(d.get("delta") or "") for d in deltas),
        },
    )
    # 临时正文真的渲染成可见节点（不是只到 store）。
    grid.cell(
        "UI1-3",
        "UI1",
        "F-R4 临时正文在真实 DOM 中可见（provisional 标记）",
        "pass" if probe.get("provisionalSeen", 0) > 0 and renders else "fail",
        f"provisional_frames={probe.get('provisionalSeen', 0)} renders={len(renders)}",
        {
            "provisional_frames": probe.get("provisionalSeen", 0),
            "render_samples": renders[:6],
            "dom_provisional_now": dom.get("provisional"),
        },
    )
    # 终态必须只剩一条权威助手消息，且正文等于完整脚本答案。
    grid.cell(
        "UI1-4",
        "UI1",
        "F-R4 权威正文整体替换临时投影：最终恰一条且内容完整",
        "pass"
        if len(authoritative) == 1
        and final_text == expected
        and dom.get("provisional") == 0
        else "fail",
        f"authoritative={len(authoritative)} dom_provisional={dom.get('provisional')} "
        f"final_match={final_text == expected}",
        {
            "authoritative_count": len(authoritative),
            "final_text": final_text,
            "expected_text": expected,
            "dom_texts": dom.get("texts"),
            "dom_count": dom.get("count"),
        },
    )
    # 刷新后不重复、不复活临时气泡。
    refresh_texts = after_refresh.get("texts") or []
    grid.cell(
        "UI2-1",
        "UI2",
        "F-R4 刷新后权威正文保留且不重复、无残留临时气泡",
        "pass"
        if after_refresh.get("provisional") == 0
        and sum(1 for t in refresh_texts if expected in t) == 1
        else "fail",
        f"refresh_count={after_refresh.get('count')} "
        f"provisional={after_refresh.get('provisional')}",
        {"after_refresh": after_refresh, "expected_text": expected},
    )
    grid.cell(
        "UI2-2",
        "UI2",
        "F-R4 终态后无无限等待指示、无错误横幅",
        "pass"
        if not dom.get("pendingIndicator") and not dom.get("errorNotice")
        else "fail",
        f"pending={dom.get('pendingIndicator')} error={dom.get('errorNotice')}",
        {"dom": dom},
    )
    # F-R4 断线续传：中止在飞读取后带 Last-Event-ID 重连，拼接不得重复或丢序。
    first = (result.get("resume") or {}).get("first") or []
    second = (result.get("resume") or {}).get("second") or []
    aborted_at = (result.get("resume") or {}).get("abortedAt")
    first_seqs = [f["seq"] for f in first]
    second_seqs = [f["seq"] for f in second]
    merged = first_seqs + second_seqs
    no_dupes = len(merged) == len(set(merged))
    contiguous = all(b - a == 1 for a, b in zip(merged, merged[1:]))
    grid.cell(
        "UI2-4",
        "UI2",
        "F-R4 断线后带 Last-Event-ID 续传：无重复、无缺序",
        "pass" if first_seqs and second_seqs and no_dupes and contiguous else "fail",
        f"first={len(first_seqs)} resume={len(second_seqs)} dupes={not no_dupes} "
        f"contiguous={contiguous} aborted_at={aborted_at}",
        {
            "first_seqs": first_seqs,
            "resume_seqs": second_seqs,
            "merged_seqs": merged,
            "aborted_at": aborted_at,
            "resume_error": (result.get("resume") or {}).get("error"),
            "note": "重连携带中止时最后收到的 seq；只允许出现中止点之后的序号",
        },
    )
    # F-R4 跨空间：切空间后面板不得残留上一空间的消息。
    cs = result.get("cross_space") or {}
    old_texts = cs.get("beforeTexts") or []
    after_texts = cs.get("afterTexts") or []
    leaked = [t for t in after_texts if expected in t]
    grid.cell(
        "UI2-5",
        "UI2",
        "F-R4 跨空间切换：旧空间消息不得残留或复活",
        "pass"
        if cs.get("chosen") and not leaked and after_texts != old_texts
        else "fail",
        f"chosen={cs.get('chosen')} before={cs.get('before')} after={cs.get('afterCount')} "
        f"leaked={len(leaked)}",
        {
            "chosen_space_label": cs.get("chosen"),
            "option_labels": cs.get("labels"),
            "before_texts": old_texts,
            "after_texts": after_texts,
            "error": cs.get("error"),
        },
    )
    # F-R4 取消的浏览器呈现。拆成两格：服务端裁决（已通过）与终态标记
    # （仍失败，见 G2）。合并成一格会让一个真实的展示缺陷藏在一个布尔里。
    cxl = result.get("cancel") or {}
    cxl_types = cxl.get("eventTypes") or []
    grid.cell(
        "UI2-6",
        "UI2",
        "F-R4 浏览器取消：终态由服务端裁决为 cancelled，不显示为失败",
        "pass"
        if cxl.get("sawCancelBtn")
        and "run.cancelled" in cxl_types
        and "run.failed" not in cxl_types
        and not cxl.get("errorNotice")
        and not cxl.get("pendingIndicator")
        else "fail",
        f"cancel_btn={cxl.get('sawCancelBtn')} cancelled_at_ms={cxl.get('cancelledAt')} "
        f"terminal_at_ms={cxl.get('terminalAt')} error={cxl.get('errorNotice')}",
        {
            "event_types": cxl_types,
            "provisional_before_cancel": cxl.get("provisionalBefore"),
            "provisional_after": cxl.get("provisionalAfter"),
            "assistant_count": cxl.get("assistantCount"),
            "pending_indicator": cxl.get("pendingIndicator"),
            "error_notice": cxl.get("errorNotice"),
            "error": cxl.get("error"),
            "note": (
                "取消必须打在流中（已出现临时正文），否则证明不了中断语义；"
                "终态由服务端裁决，页面不得自行显示失败。"
            ),
        },
    )
    # 09-18 design 的表格要求「failed / cancelled：保留已显示的安全部分并标终态」。
    # 现状保留了正文，但没有标终态：取消后仍渲染「生成中…」标记。
    # 这是展示缺陷（G2），本格如实记为 fail，不因 UI2-6 通过而掩盖。
    grid.cell(
        "UI2-7",
        "UI2",
        "F-R4 取消后临时正文标为终态（不再声称仍在生成）",
        "pass" if cxl.get("provisionalAfter") == 0 else "fail",
        f"provisional_after={cxl.get('provisionalAfter')} "
        f"assistant_count={cxl.get('assistantCount')}",
        {
            "event_types": cxl_types,
            "provisional_before_cancel": cxl.get("provisionalBefore"),
            "provisional_after": cxl.get("provisionalAfter"),
            "error": cxl.get("error"),
            "note": (
                "09-18 design：failed/cancelled 保留已显示的安全部分并标终态。"
                "现状保留了正文但未标终态，MessageList 仍渲染 生成中… 标记。"
            ),
        },
    )
    # F-R5：渲染间隔与传输间隔必须可分辨，且注入的分块延迟真的体现在渲染上。
    render_gaps = [
        round(renders[i]["mark"] - renders[i - 1]["mark"])
        for i in range(1, len(renders))
        if renders[i]["mark"] - renders[i - 1]["mark"] > 1
    ]
    # Browser-observed inter-chunk cadence: this is the transport-side quantity
    # F-R5 asks for, and it is directly comparable to the injected delay.
    delta_gaps = [
        round(deltas[i]["mark"] - deltas[i - 1]["mark"])
        for i in range(1, len(deltas))
    ]
    grid.cell(
        "UI2-3",
        "UI2",
        "F-R5 分片节奏可量化：浏览器收到的分片间隔与注入的上游节奏一致（时钟边界注明）",
        "pass" if final_text == expected and len(delta_gaps) >= 3 else "fail",
        f"delta_gaps_ms={delta_gaps[:8]} injected_chunk_ms={state.chunk_delay_ms}",
        {
            "delta_arrival_marks_ms": [round(d["mark"]) for d in deltas],
            "delta_gaps_ms": delta_gaps,
            "injected_chunk_delay_ms": state.chunk_delay_ms,
            "render_gaps_ms": render_gaps,
            "render_samples": renders[:8],
            "clock_note": (
                "浏览器 performance.now() 为单机 monotonic；与服务端 UTC 及 sidecar "
                "monotonic 不可直接相减，本格不报告跨机毫秒精度。以上间隔由本机"
                "注入的上游 chunk 延迟驱动，不声明跨机误差。"
            ),
        },
    )


def _report(grid: Grid, state: UpstreamState) -> dict[str, Any]:
    return {
        "suite": "familygraph-controlled-browser-acceptance",
        "verdict": "pass" if not grid.failed else "failed",
        "provenance": {
            "source_sha": _git("rev-parse", "HEAD"),
            "source_sha_short": _git("rev-parse", "--short", "HEAD"),
            "source_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "working_tree_dirty": bool(_git("status", "--porcelain")),
            "versions": _versions(),
            "fixture": {
                "kind": "synthetic-loopback-upstream + real sidecar + real frontend build",
                "real_provider": False,
                "browser": "Chrome headless (CDP)",
                "note": "真实传输链路成立不等于真实 Provider 质量或线上延迟成立",
            },
        },
        "upstream": state.snapshot(),
        "counts": {
            "total": len(grid.cells),
            "passed": len(grid.cells) - len(grid.failed),
            "failed": len(grid.failed),
        },
        "cells": grid.cells,
    }


def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else "unknown"


def _versions() -> dict[str, str]:
    node = subprocess.run(["node", "--version"], capture_output=True, text=True)
    return {
        "node": node.stdout.strip() if node.returncode == 0 else "unknown",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }


if __name__ == "__main__":
    raise SystemExit(main())
