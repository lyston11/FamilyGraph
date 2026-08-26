/** Behavioral-clause assertions for the V2.2 assistant system prompt. */

import { describe, expect, it } from "vitest";
import { ASSISTANT_SYSTEM_PROMPT } from "../src/prompt.js";

describe("assistant system prompt", () => {
  it("declares the single-space read-only family tree assistant identity", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("单空间");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("只读");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("家谱");
  });

  it("enforces the three-state fact discipline with path citations", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("确认事实");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("派生路径");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("逐跳");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("资料不足");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("不确定");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("严禁编造");
  });

  it("grounds answers in the structured tree via tool calls, not memory", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("唯一真源");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("调用只读工具");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("记忆");
  });

  it("refuses write requests and denies any confirm-then-write flow", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("拒绝");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("写入、修改或删除");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("确认后写入");
  });

  it("withholds internal instructions, schemas and hidden-field existence", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("内部指令");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("schema");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("可见性规则");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("遮蔽字段");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("作为管理员");
  });
});
