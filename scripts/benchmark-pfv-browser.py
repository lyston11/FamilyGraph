#!/usr/bin/env python3
"""Measure a cold, interactive Ming family skeleton in a real isolated Chrome.

Requires the backend virtualenv, an existing frontend production build, Chrome,
and websockets (already in the backend environment). Uses fresh migrated SQLite
and a temporary browser profile per trial. No real user's session is accessed.

  backend/.venv/bin/python scripts/benchmark-pfv-browser.py --trials 5 --report /tmp/pfv-browser.json

The observed viewer belongs to the unchanged 30-person Ming graph; all 30 people
have accounts. The worker becomes enabled immediately before browser navigation,
so the first skeleton cannot be a projection computed during authentication.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import json
import logging
import math
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class CDP:
    def __init__(self, socket: Any) -> None:
        self.socket = socket
        self.serial = 0

    async def call(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.serial += 1
        await self.socket.send(
            json.dumps({"id": self.serial, "method": method, "params": params or {}})
        )
        while True:
            response = json.loads(await asyncio.wait_for(self.socket.recv(), 30))
            if response.get("id") != self.serial:
                continue
            if "error" in response:
                raise RuntimeError(f"CDP {method} failed")
            return response.get("result", {})

    async def evaluate(self, expression: str) -> Any:
        result = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        if result.get("exceptionDetails"):
            raise RuntimeError("browser evaluation failed")
        return result.get("result", {}).get("value")


def load_benchmark() -> Any:
    spec = importlib.util.spec_from_file_location(
        "steward_benchmark", ROOT / "scripts/benchmark-steward-recompute.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_json(
    url: str, data: dict[str, Any] | None = None
) -> dict[str, Any] | list[Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(data).encode() if data else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def measure_page(
    debug_port: int,
    base: str,
    refresh_token: str,
    expected: int,
    screenshot: Path | None,
    width: int,
    height: int,
    theme: str,
) -> dict[str, Any]:
    import websockets

    targets = get_json(f"http://127.0.0.1:{debug_port}/json/list")
    target = next(target for target in targets if target["type"] == "page")
    async with websockets.connect(
        target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024
    ) as socket:
        cdp = CDP(socket)
        await cdp.call("Page.enable")
        await cdp.call("Runtime.enable")
        browser_version = await cdp.call("Browser.getVersion")
        await cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
        )
        # Bootstrap the app's normal refresh-token path in this isolated profile.
        await cdp.call("Page.navigate", {"url": base + "/login"})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if await cdp.evaluate(
                "location.pathname === '/login' && document.readyState === 'complete'"
            ):
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("isolated login page did not finish loading")
        await cdp.evaluate(
            "localStorage.setItem('fg.refresh_token', "
            + json.dumps(refresh_token)
            + ")"
        )
        await cdp.evaluate(
            "localStorage.setItem('fg-theme', " + json.dumps(theme) + ")"
        )
        source = """
          window.__pfvBench = { first: null, responses: [] };
          const open = XMLHttpRequest.prototype.open;
          XMLHttpRequest.prototype.open = function(method, url, ...args) {
            if (new URL(url, location.href).pathname === '/api/personal-family-view') {
              this.addEventListener('loadend', () => {
                let payload = null;
                try { payload = JSON.parse(this.responseText); } catch { /* record headers only */ }
                const view = payload?.data ?? payload;
                window.__pfvBench.responses.push({
                  status: this.status,
                  date: this.getResponseHeader('date'),
                  validatedAt: this.getResponseHeader('x-pfv-validated-at'),
                  displayUntil: this.getResponseHeader('x-pfv-display-until'),
                  hasEtag: this.getResponseHeader('etag') !== null,
                  nodes: view?.nodes?.length ?? null,
                  phase: view?.progress?.phase ?? null,
                  completed: view?.progress?.completed_count ?? null,
                  total: view?.progress?.total_count ?? null
                });
              }, {once: true});
            }
            return open.call(this, method, url, ...args);
          };
          const observe = () => {
            const nodes = [...document.querySelectorAll('.vue-flow__node-member')];
            const visible = nodes.filter(n => n.getBoundingClientRect().width > 0).length;
            if (visible >= EXPECTED && window.__pfvBench.first === null) {
              requestAnimationFrame(() => requestAnimationFrame(() => {
                if (window.__pfvBench.first !== null) return;
                const pane = document.querySelector('.vue-flow__pane');
                if (!pane || getComputedStyle(pane).pointerEvents === 'none') return;
                const clickable = nodes.some(n => {
                  const r = n.getBoundingClientRect();
                  const x = r.left + r.width / 2, y = r.top + r.height / 2;
                  if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return false;
                  const hit = document.elementFromPoint(x, y);
                  return hit !== null && n.contains(hit);
                });
                if (!clickable) return;
                window.__pfvBench.first = performance.now();
                window.__pfvBench.nodes = visible;
                window.__pfvBench.pending = document.querySelectorAll('[data-test=term-pending-chip]').length;
              }));
            }
          };
          new MutationObserver(observe).observe(document, {subtree: true, childList: true, attributes: true});
        """.replace("EXPECTED", str(expected))
        await cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": source})
        from app import config

        config.STEWARD_WORKER_ENABLED = True
        await cdp.call("Page.navigate", {"url": base + "/family-tree"})
        deadline = time.monotonic() + 60
        first = None
        while time.monotonic() < deadline:
            first = await cdp.evaluate("window.__pfvBench?.first ?? null")
            if first is not None:
                break
            await asyncio.sleep(0.05)
        if first is None:
            state = await cdp.evaluate(
                """({
                  path: location.pathname,
                  nodes: document.querySelectorAll('.vue-flow__node-member').length,
                  error: document.querySelector('[data-test=family-tree-error]')?.textContent?.trim(),
                  notice: document.querySelector('[data-test=update-notice]')?.textContent?.trim(),
                  progress: document.querySelector('[data-test=family-tree-progress]')?.textContent?.trim(),
                  responses: window.__pfvBench?.responses ?? []
                })"""
            )
            if screenshot:
                png = await cdp.call("Page.captureScreenshot", {"format": "png"})
                screenshot.write_bytes(base64.b64decode(png["data"]))
            return {
                "passed": False,
                "error": "interactive_skeleton_timeout",
                "state": state,
            }
        result = await cdp.evaluate("""(() => {
          const calls = performance.getEntriesByType('resource').filter(e => {
            const path = new URL(e.name).pathname;
            return path === '/api/personal-family-view';
          });
          return { ...window.__pfvBench, first_request_start: calls[0]?.startTime ?? null,
            first_response_end: calls[0]?.responseEnd ?? null,
            request_count: calls.length, viewport: document.querySelector('.vue-flow__transformationpane')?.style.transform,
            page_width: document.documentElement.scrollWidth, viewport_width: innerWidth,
            theme: document.documentElement.dataset.theme };
        })()""")
        if screenshot:
            png = await cdp.call("Page.captureScreenshot", {"format": "png"})
            screenshot.write_bytes(base64.b64decode(png["data"]))
        # Dragging is intentionally enabled only in the product's free-canvas
        # mode. Switch through the actual radio control after measuring the
        # default tree's first render.
        mode = await cdp.evaluate("""(() => {
          const radio = [...document.querySelectorAll('[data-test=layout-switch] .n-radio-button')]
            .find(n => n.textContent.trim() === '自由画布');
          if (!radio) return null;
          const r = radio.getBoundingClientRect();
          return {x: r.left + r.width / 2, y: r.top + r.height / 2};
        })()""")
        if mode:
            for kind in ("mousePressed", "mouseReleased"):
                await cdp.call(
                    "Input.dispatchMouseEvent",
                    {
                        "type": kind,
                        **mode,
                        "button": "left",
                        "clickCount": 1,
                        "buttons": int(kind == "mousePressed"),
                    },
                )
            await asyncio.sleep(0.15)
        # Confirm pan handling, then observe whether a later label update resets it.
        # This check occurs after the first-render timestamp, so measurement does
        # not include automation round trips.
        position = await cdp.evaluate("""(() => {
          const pane = document.querySelector('.vue-flow__pane');
          if (!pane) return null;
          const r = pane.getBoundingClientRect();
          for (const yf of [0.2, 0.4, 0.6, 0.8]) {
            for (const xf of [0.2, 0.4, 0.6, 0.8]) {
              const x = r.left + r.width * xf, y = r.top + r.height * yf;
              if (x + 50 >= innerWidth || y + 40 >= innerHeight) continue;
              if (document.elementFromPoint(x, y) === pane) {
                return {x, y, before: document.querySelector('.vue-flow__transformationpane')?.style.transform};
              }
            }
          }
          return null;
        })()""")
        pan_supported = False
        zoom_supported = False
        pan_retained = None
        drag_supported = False
        drag_retained = None
        selection_supported = False
        selection_retained = None
        if position:
            await cdp.call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed",
                    "x": position["x"],
                    "y": position["y"],
                    "button": "left",
                    "buttons": 1,
                    "clickCount": 1,
                },
            )
            await cdp.call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseMoved",
                    "x": position["x"] + 40,
                    "y": position["y"] + 25,
                    "button": "left",
                    "buttons": 1,
                },
            )
            await cdp.call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": position["x"] + 40,
                    "y": position["y"] + 25,
                    "button": "left",
                    "buttons": 0,
                    "clickCount": 1,
                },
            )
            await asyncio.sleep(0.1)
            after = await cdp.evaluate("""({
              viewport: document.querySelector('.vue-flow__transformationpane')?.style.transform,
              labels: document.querySelectorAll('[data-test=view-label]').length,
              pending: document.querySelectorAll('[data-test=term-pending-chip]').length
            })""")
            pan_supported = (
                bool(position["before"]) and after["viewport"] != position["before"]
            )
            await cdp.call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseWheel",
                    "x": position["x"],
                    "y": position["y"],
                    "deltaX": 0,
                    "deltaY": -160,
                },
            )
            await asyncio.sleep(0.2)
            zoomed = await cdp.evaluate("""({
              viewport: document.querySelector('.vue-flow__transformationpane')?.style.transform,
              labels: document.querySelectorAll('[data-test=view-label]').length,
              pending: document.querySelectorAll('[data-test=term-pending-chip]').length
            })""")
            zoom_supported = (
                bool(after["viewport"]) and zoomed["viewport"] != after["viewport"]
            )
            after = zoomed
            dragged = await cdp.evaluate("""(() => {
              for (const node of document.querySelectorAll('.vue-flow__node-member.draggable')) {
                const r = node.getBoundingClientRect();
                const x = r.left + r.width / 2, y = r.top + Math.min(12, r.height / 3);
                if (x < 30 || y < 10 || x + 35 >= innerWidth || y + 20 >= innerHeight) continue;
                if (!node.contains(document.elementFromPoint(x, y))) continue;
                return {x, y, id: node.dataset.id, before: node.style.transform};
              }
              return null;
            })()""")
            if dragged:
                await cdp.call(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mousePressed",
                        "x": dragged["x"],
                        "y": dragged["y"],
                        "button": "left",
                        "buttons": 1,
                        "clickCount": 1,
                    },
                )
                for step in range(1, 5):
                    await cdp.call(
                        "Input.dispatchMouseEvent",
                        {
                            "type": "mouseMoved",
                            "x": dragged["x"] + step * 7,
                            "y": dragged["y"] + step * 4,
                            "button": "left",
                            "buttons": 1,
                        },
                    )
                await cdp.call(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseReleased",
                        "x": dragged["x"] + 28,
                        "y": dragged["y"] + 16,
                        "button": "left",
                        "buttons": 0,
                        "clickCount": 1,
                    },
                )
                await asyncio.sleep(0.05)
                await cdp.evaluate(
                    "window.__pfvBench.draggedId = " + json.dumps(dragged["id"])
                )
                dragged[
                    "after"
                ] = await cdp.evaluate("""[...document.querySelectorAll('.vue-flow__node-member')]
                  .find(n => n.dataset.id === window.__pfvBench.draggedId)?.style.transform ?? null""")
                drag_supported = (
                    bool(dragged["after"]) and dragged["after"] != dragged["before"]
                )

            edge_position = await cdp.evaluate("""(() => {
              for (const path of document.querySelectorAll('.fg-struct-edge .vue-flow__edge-path')) {
                const matrix = path.getScreenCTM();
                if (!matrix) continue;
                for (const ratio of [0.5, 0.25, 0.75]) {
                  const p = path.getPointAtLength(path.getTotalLength() * ratio).matrixTransform(matrix);
                  if (p.x < 5 || p.y < 5 || p.x >= innerWidth - 5 || p.y >= innerHeight - 5) continue;
                  const edge = path.closest('.vue-flow__edge');
                  if (document.elementFromPoint(p.x, p.y)?.closest('.vue-flow__edge') === edge) {
                    return {x: p.x, y: p.y};
                  }
                }
              }
              return null;
            })()""")
            if edge_position:
                for kind in ("mousePressed", "mouseReleased"):
                    await cdp.call(
                        "Input.dispatchMouseEvent",
                        {
                            "type": kind,
                            **edge_position,
                            "button": "left",
                            "clickCount": 1,
                            "buttons": int(kind == "mousePressed"),
                        },
                    )
                await asyncio.sleep(0.05)
                selection_supported = await cdp.evaluate("""(() => {
                  window.__pfvBench.selectedEndpoints = document.querySelector('[data-test=structural-endpoints]')?.textContent;
                  return !!window.__pfvBench.selectedEndpoints;
                })()""")
            if pan_supported and after["pending"]:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    current = await cdp.evaluate("""({
                      viewport: document.querySelector('.vue-flow__transformationpane')?.style.transform,
                      labels: document.querySelectorAll('[data-test=view-label]').length,
                      dragged: [...document.querySelectorAll('.vue-flow__node-member')]
                        .find(n => n.dataset.id === window.__pfvBench.draggedId)?.style.transform ?? null,
                      selection_retained: !!window.__pfvBench.selectedEndpoints &&
                        document.querySelector('[data-test=structural-endpoints]')?.textContent === window.__pfvBench.selectedEndpoints
                    })""")
                    if current["labels"] > after["labels"]:
                        pan_retained = current["viewport"] == after["viewport"]
                        drag_retained = (
                            drag_supported and current["dragged"] == dragged["after"]
                        )
                        selection_retained = (
                            selection_supported and current["selection_retained"]
                        )
                        break
                    await asyncio.sleep(0.1)
        start = result.get("first_request_start")
        elapsed = result["first"] - start if start is not None else None
        return {
            "passed": elapsed is not None
            and elapsed <= 1000
            and pan_supported
            and zoom_supported
            and pan_retained is True
            and drag_supported
            and drag_retained is True
            and selection_supported
            and selection_retained is True,
            "interactive_from_request_ms": round(elapsed, 3)
            if elapsed is not None
            else None,
            "interactive_from_navigation_ms": round(result["first"], 3),
            "first_http_ms": round(result["first_response_end"] - start, 3)
            if start is not None
            else None,
            "nodes": result.get("nodes"),
            "pending_terms_at_skeleton": result.get("pending"),
            "request_count": result["request_count"],
            "responses": result["responses"],
            "pan_interaction_passed": pan_supported,
            "zoom_interaction_passed": zoom_supported,
            "pan_retained_after_term_update": pan_retained,
            "drag_interaction_passed": drag_supported,
            "drag_retained_after_term_update": drag_retained,
            "selection_interaction_passed": selection_supported,
            "selection_retained_after_term_update": selection_retained,
            "no_page_horizontal_overflow": result["page_width"]
            <= result["viewport_width"],
            "theme": result["theme"],
            "browser_version": browser_version.get("product"),
        }


def single(args: argparse.Namespace) -> dict[str, Any]:
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise RuntimeError("build frontend before browser measurement")
    benchmark = load_benchmark()
    with tempfile.TemporaryDirectory(
        prefix="fg-pfv-browser-", delete=False
    ) as directory:
        temporary = Path(directory)
        benchmark.prepare_environment(temporary / "data", args.bcrypt_rounds)
        fixture = benchmark.seed_case("ming", 30, None)
        from app import config
        from app.main import app
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
        import uvicorn

        server_timings: list[float] = []

        @app.middleware("http")
        async def measure_pfv_server(request: Any, call_next: Any) -> Any:
            started = time.perf_counter()
            response = await call_next(request)
            if request.url.path == "/api/personal-family-view":
                elapsed = (time.perf_counter() - started) * 1000
                server_timings.append(elapsed)
                response.headers["Server-Timing"] = f"pfv;dur={elapsed:.3f}"
            return response

        # Serve the production frontend and the unchanged API on the same local
        # listener; this avoids depending on another task's development ports.
        app.mount("/assets", StaticFiles(directory=ROOT / "frontend/dist/assets"))

        @app.get("/family-tree", include_in_schema=False)
        @app.get("/login", include_in_schema=False)
        @app.get("/", include_in_schema=False)
        def page() -> FileResponse:
            return FileResponse(ROOT / "frontend/dist/index.html")

        port = free_port()
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(
            uvicorn.Config(
                app, host="127.0.0.1", port=port, log_level="critical", access_log=False
            )
        )
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        browser = None
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if server.started:
                    break
                time.sleep(0.05)
            if not server.started:
                raise RuntimeError("isolated API startup failed")
            tokens = get_json(
                base + "/api/auth/login",
                {"name": fixture["name"], "pin": fixture["pin"]},
            )
            profile = temporary / "chrome"
            browser = subprocess.Popen(
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
            deadline = time.monotonic() + 20
            while (
                not devtools.exists()
                and time.monotonic() < deadline
                and browser.poll() is None
            ):
                time.sleep(0.05)
            if not devtools.exists():
                raise RuntimeError("isolated Chrome startup failed")
            debug_port = int(devtools.read_text().splitlines()[0])
            result = asyncio.run(
                measure_page(
                    debug_port,
                    base,
                    tokens["refresh_token"],
                    fixture["people"],
                    args.screenshot,
                    args.width,
                    args.height,
                    args.theme,
                )
            )
            result["server_response_ms"] = benchmark.distribution(server_timings)
            result["first_server_response_ms"] = (
                round(server_timings[0], 3) if server_timings else None
            )
            result["environment"] = {
                "people": fixture["people"],
                "accounts": fixture["accounts"],
                "facts": fixture["facts"],
                "bcrypt_rounds": args.bcrypt_rounds,
                "maintenance_interval_seconds": config.MAINTENANCE_INTERVAL_SECONDS,
                "scan_interval_seconds": config.STEWARD_SCAN_INTERVAL_SECONDS,
                "frontend": "production build",
                "browser": "Chrome headless",
                "viewport": f"{args.width}x{args.height}",
            }
            return result
        finally:
            config.STEWARD_WORKER_ENABLED = False
            if browser is not None:
                browser.terminate()
                try:
                    browser.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    browser.kill()
                    browser.wait(timeout=10)
            server.should_exit = True
            server_thread.join(timeout=20)
            from app.db import engine
            from app.services.steward_runtime import shutdown_runtime

            stopped = shutdown_runtime(timeout_seconds=10)
            logging.disable(logging.CRITICAL)
            if not stopped or server_thread.is_alive():
                raise RuntimeError(
                    f"shutdown incomplete; isolated data retained at {temporary}"
                )
            engine.dispose()
            shutil.rmtree(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--bcrypt-rounds", type=int, default=12)
    parser.add_argument("--chrome", default=CHROME)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--width", type=int, default=1365)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--theme", choices=("paper", "modern"), default="paper")
    parser.add_argument("--single", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.single:
        result = single(args)
        args.report.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
        return 0 if result["passed"] else 1
    results = []
    with tempfile.TemporaryDirectory(prefix="fg-pfv-browser-results-") as directory:
        for trial in range(args.trials):
            report = Path(directory) / f"trial-{trial}.json"
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--single",
                "--chrome",
                args.chrome,
                "--bcrypt-rounds",
                str(args.bcrypt_rounds),
                "--report",
                str(report),
                "--width",
                str(args.width),
                "--height",
                str(args.height),
                "--theme",
                args.theme,
            ]
            if args.screenshot and trial == 0:
                command += ["--screenshot", str(args.screenshot)]
            process = subprocess.run(command, cwd=ROOT)
            results.append(
                json.loads(report.read_text())
                if report.exists()
                else {
                    "passed": False,
                    "error": "trial_process_failed",
                    "exit_code": process.returncode,
                }
            )
            samples = sorted(
                row["interactive_from_request_ms"]
                for row in results
                if row.get("interactive_from_request_ms") is not None
            )
            p95 = (
                samples[max(0, math.ceil(0.95 * len(samples)) - 1)] if samples else None
            )
            args.report.write_text(
                json.dumps(
                    {"trials": results, "interactive_from_request_ms_p95": p95},
                    indent=2,
                )
                + "\n"
            )
    return 0 if results and all(row["passed"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
