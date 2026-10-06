/**
 * AC-5：Assistant 与 Steward 分进程的部署契约。
 *
 * ## 为什么这是「测试」而不是纯文档
 *
 * 分进程的价值在于**资源不共享**：event loop、HTTP client、heap、重试风暴。
 * 若有人把 compose 改回单个 `role=both`，这些共享会静默回来，而运行时没有任何
 * 报错——只是长流会互相拖慢。因此把部署形态钉成可断言的契约。
 *
 * ## 为什么用 YAML 解析而不是字符串匹配
 *
 * 初版用「取服务块再 `toContain`」的字符串匹配，结果**误报失败**：两个服务通过
 * YAML 锚点 `&agent_env` / `<<: *agent_env` 共享公共环境变量，因此
 * `STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE` 不在 `agent-steward` 的字面文本里，
 * 但**解析后确实生效**。字符串匹配把「YAML 合并」误读成「配置缺失」。
 *
 * 因此本文件解析 YAML 后断言**解析结果**，而不是源码文本。
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// js-yaml 无随包类型；这里用最小接口断言解析结果，而不是为测试引入新的
// @types 依赖（`npm run type-check` 会因缺少声明而失败）。
// eslint-disable-next-line @typescript-eslint/no-require-imports
const yamlLoad = require("js-yaml").load as (text: string) => unknown;

const here = dirname(fileURLToPath(import.meta.url));
const composePath = resolve(here, "../../docker-compose.yml");
const compose = yamlLoad(readFileSync(composePath, "utf8")) as {
  services: Record<string, {
    environment?: Record<string, string>;
    profiles?: string[];
    build?: unknown;
  }>;
};

const services = compose.services;

describe("AC-5 sidecar deployment split", () => {
  it("runs assistant and steward as two separate services with single roles", () => {
    const assistant = services["agent-assistant"];
    const steward = services["agent-steward"];
    expect(assistant, "缺少 agent-assistant").toBeDefined();
    expect(steward, "缺少 agent-steward").toBeDefined();
    if (!assistant || !steward) throw new Error("分进程服务缺失");
    expect(assistant.environment?.FG_AGENT_ROLE).toBe("assistant");
    expect(steward.environment?.FG_AGENT_ROLE).toBe("steward");
  });

  it("does not default any service to role=both", () => {
    // `both` 让两类共享 event loop / HTTP client / heap / 重试风暴——
    // 正是分进程要消除的形态，因此不得成为默认。
    for (const name of ["agent-assistant", "agent-steward"]) {
      const service = services[name];
      if (!service) throw new Error(`${name} 缺失`);
      expect(service.environment?.FG_AGENT_ROLE, name).not.toBe("both");
      expect(service.profiles, `${name} 不应有 profile 门禁`).toBeUndefined();
    }
  });

  it("keeps the combined form behind a profile so it is opt-in", () => {
    const combined = services["agent-combined"];
    expect(combined, "缺少 agent-combined").toBeDefined();
    // `toBeDefined()` 不缩窄类型，因此显式守卫后再断言。
    if (!combined) throw new Error("agent-combined 缺失");
    expect(combined.environment?.FG_AGENT_ROLE).toBe("both");
    expect(combined.profiles).toContain("combined");
  });

  it("gives each process its own container so no runtime resource is shared", () => {
    // 分进程的实质：不同 service = 不同容器 = 不同 event loop / heap / client。
    for (const name of ["agent-assistant", "agent-steward"]) {
      const service = services[name];
      if (!service) throw new Error(`${name} 缺失`);
      expect(service.build, name).toBeDefined();
    }
  });

  it("resolves the shared anchor so steward slots really reach both processes", () => {
    // 这一条守护的正是初版字符串匹配踩到的坑：环境变量经 YAML 锚点合并后
    // **解析结果**必须存在。若有人把锚点改成字面复制却漏掉某个变量，
    // 解析结果会缺键，而字符串匹配发现不了。
    for (const name of ["agent-assistant", "agent-steward"]) {
      const service = services[name];
      if (!service) throw new Error(`${name} 缺失`);
      const env = service.environment ?? {};
      expect(env.STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE, name).toBeDefined();
      expect(env.AGENT_MAX_CONCURRENT_RUNS, name).toBeDefined();
      expect(env.FG_INTERNAL_API_BASE_URL, name).toBeDefined();
    }
  });
});
