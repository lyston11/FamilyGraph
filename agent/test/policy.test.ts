/**
 * Unit test for the policy guard's fail-closed allowlist channel: a V2.2 tool
 * name passes when the server-issued allowlist contains it and is blocked
 * (with a recorded violation) otherwise. No full Pi session is needed — the
 * inline extension factory is invoked against a stub `pi` bus.
 */

import { describe, expect, it } from "vitest";
import { createPolicyGuard } from "../src/policy.js";

type Handler = (event: unknown) => unknown;

function installGuard(allowlist: readonly string[]): {
  guard: ReturnType<typeof createPolicyGuard>;
  handlers: Map<string, Handler>;
} {
  const handlers = new Map<string, Handler>();
  const guard = createPolicyGuard({
    allowlist: new Set(allowlist),
    secrets: ["unit-test-secret"],
  });
  const pi = {
    on: (name: string, handler: never) => {
      handlers.set(name, handler as Handler);
    },
  };
  // InlineExtension = named-object | bare-factory union; ours is the former.
  const extension = guard.extension as unknown as { factory: (pi: unknown) => void };
  extension.factory(pi);
  return { guard, handlers };
}

describe("policy guard tool_call allowlist enforcement", () => {
  it("allows a V2.2 tool when allowlisted and blocks it otherwise", () => {
    const allowed = installGuard(["familygraph.list_visible_people"]);
    const onToolCallAllowed = allowed.handlers.get("tool_call")!;
    // undefined decision → Pi proceeds with execution.
    expect(onToolCallAllowed({ toolName: "familygraph.list_visible_people" })).toBeUndefined();
    expect(allowed.guard.violationCount).toBe(0);

    const blocked = installGuard(["familygraph.echo"]);
    const onToolCallBlocked = blocked.handlers.get("tool_call")!;
    const decision = onToolCallBlocked({
      toolName: "familygraph.list_visible_people",
    }) as { block?: boolean; reason?: string; terminate?: boolean };
    expect(decision.block).toBe(true);
    expect(decision.terminate).toBe(true);
    expect(decision.reason).toContain("familygraph.list_visible_people");
    expect(blocked.guard.violationCount).toBe(1);
    expect(blocked.guard.violations[0]).toMatchObject({
      kind: "tool_not_allowed",
      detail: expect.stringContaining("familygraph.list_visible_people"),
    });
  });
});
