/**
 * Health endpoint (node:http only, no new dependencies).
 *
 * GET /healthz →
 *   { process: "ok", fastapi: "reachable"|"unreachable",
 *     provider: { cloud: "configured"|"missing"|"unknown",
 *                 local: "configured"|"missing"|"unknown" } }
 *
 * Provider readiness reports configuration presence only; no real inference
 * probe is performed. FastAPI unavailability does not crash the sidecar.
 * `/readyz` is the orchestration readiness contract (503 while FastAPI is
 * unreachable); `/healthz` remains a liveness probe and stays 200.
 */

import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { describeProviderReadiness, type AgentConfig } from "./config.js";
import type { InternalClient } from "./client.js";

export function createHealthServer(
  config: AgentConfig,
  client: InternalClient,
): Server {
  return createServer((req: IncomingMessage, res: ServerResponse) => {
    if (req.method !== "GET" || !req.url || !["/healthz", "/readyz"].includes(req.url)) {
      res.writeHead(404).end();
      return;
    }
    void client.probeFastAPI().then((fastapi) => {
      const body = JSON.stringify({
        process: "ok",
        fastapi,
        provider: describeProviderReadiness(config),
      });
      res.writeHead(req.url === "/readyz" && fastapi !== "reachable" ? 503 : 200, {
        "Content-Type": "application/json",
      });
      res.end(body);
    });
  });
}
