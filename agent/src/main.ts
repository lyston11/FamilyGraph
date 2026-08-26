/**
 * FamilyGraph agent sidecar entrypoint.
 *
 * Starts the worker poll loop and the health server. The sidecar holds no DB
 * connection, mounts nothing, and is reachable only on the internal network;
 * browsers talk exclusively to FastAPI.
 */

import { InternalClient } from "./client.js";
import { loadConfig } from "./config.js";
import { createHealthServer } from "./health.js";
import { createLogger } from "./logger.js";
import { SidecarWorker } from "./worker.js";

const logger = createLogger({ component: "fg-agent-sidecar" });

try {
  const config = loadConfig();
  const client = new InternalClient(config);
  const worker = new SidecarWorker({ client, config, logger });

  const health = createHealthServer(config, client);
  health.listen(config.healthPort, () => {
    logger.info("health server listening", { port: config.healthPort });
  });

  worker.start();
  logger.info("sidecar started", {
    api_base_url: config.apiBaseUrl,
    sidecar_id: config.sidecarId,
  });

  let shuttingDown = false;
  const shutdown = (signal: string): void => {
    if (shuttingDown) return;
    shuttingDown = true;
    logger.info("shutting down", { signal });
    // No local state to persist: in-flight runs are recovered by FastAPI's
    // lease reaper; the next sidecar instance re-leases the job.
    worker.stop();
    health.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 5000).unref();
  };
  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("SIGINT", () => shutdown("SIGINT"));
} catch (error) {
  // Fail fast on configuration errors — never run with a missing secret.
  logger.error("fatal startup error", {
    error: error instanceof Error ? error.message : String(error),
  });
  process.exit(1);
}
