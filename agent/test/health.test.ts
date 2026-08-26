import { createServer, type Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { InternalClient } from "../src/client.js";
import { createHealthServer } from "../src/health.js";
import { makeAgentConfig } from "./helpers.js";

describe("health endpoint", () => {
  let upstream: Server;
  let upstreamPort = 0;
  let upstreamUp = true;

  beforeAll(async () => {
    upstream = createServer((_req, res) => {
      if (!upstreamUp) {
        res.destroy();
        return;
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "ok" }));
    });
    await new Promise<void>((resolve) => upstream.listen(0, "127.0.0.1", resolve));
    upstreamPort = (upstream.address() as { port: number }).port;
  });

  afterAll(async () => {
    await new Promise<void>((resolve) => upstream.close(() => resolve()));
  });

  function startHealth(): Promise<{ server: Server; url: string }> {
    const config = makeAgentConfig(upstreamPort);
    const client = new InternalClient(config);
    const server = createHealthServer(config, client);
    return new Promise((resolve) => {
      server.listen(0, "127.0.0.1", () => {
        const port = (server.address() as { port: number }).port;
        resolve({ server, url: `http://127.0.0.1:${port}/healthz` });
      });
    });
  }

  it("reports process/fastapi/provider readiness", async () => {
    const { server, url } = await startHealth();
    try {
      const response = await fetch(url);
      expect(response.status).toBe(200);
      const body = (await response.json()) as {
        process: string;
        fastapi: string;
        provider: { cloud: string; local: string };
      };
      expect(body.process).toBe("ok");
      expect(body.fastapi).toBe("reachable");
      // makeAgentConfig sets cloud fully; local lacks api key but has base+model.
      expect(body.provider.cloud).toBe("configured");
      expect(body.provider.local).toBe("configured");
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });

  it("stays healthy when FastAPI is unreachable (no crash)", async () => {
    const { server, url } = await startHealth();
    upstreamUp = false;
    try {
      const response = await fetch(url);
      const body = (await response.json()) as { fastapi: string };
      expect(body.fastapi).toBe("unreachable");
    } finally {
      upstreamUp = true;
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });

  it("404s other paths", async () => {
    const { server, url } = await startHealth();
    try {
      const response = await fetch(url.replace("healthz", "other"));
      expect(response.status).toBe(404);
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });
});
