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

describe("kinship term behavior clauses (V2.3)", () => {
  it("routes who-is-this questions through the resolver and path tools", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("resolve_free_text_relation");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("get_relationship_path");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("get_term_alternatives");
  });

  it("relays the clarifying question verbatim under ambiguity, at most one follow-up", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("clarifying_question");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("原样转问用户");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("最多追问一次");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("不得自行编造结论");
  });

  it("redirects corrections to personal preference or consented usage recording, never structure", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("record_term_usage");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("明确同意");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("个人显示偏好");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("不得声称能够修改人物之间的结构关系");
  });

  it("carves record_term_usage out as the only write exception in the read-only boundary", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("唯一例外是称谓用词积累");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("写入、修改或删除任何结构数据");
  });
});
