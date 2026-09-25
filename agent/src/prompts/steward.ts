/**
 * Sidecar-local Steward system prompt (09-25 S1 skeleton; content finalised in S2).
 *
 * Deliberately separate from ASSISTANT_SYSTEM_PROMPT. The two runtimes have
 * different contracts: the assistant answers a person in prose and may call
 * read-only domain tools, while Steward is a personalized engine whose output is
 * a closed structured product (candidates, a strict ranking, declared
 * explanation slots, terminology choices) that the server validates and applies.
 * Sharing one prompt would blur that and invite the model to answer
 * conversationally where the server expects a schema.
 *
 * The prompt never travels through the FastAPI context projection; the server
 * sends the structured input and this text supplies the behaviour. Its version
 * is asserted against the server's STEWARD_PROMPT_VERSION (see
 * PROMPT_VERSION below and src/client.ts verifyStewardPromptVersion).
 */

/**
 * Must equal the server's `steward_assist.STEWARD_PROMPT_VERSION`. The server no
 * longer holds the prompt text (it lives here), so a hash of local text can no
 * longer anchor evaluation reports — the constant is the anchor, and the
 * mismatch check is what keeps a stale image from silently running old prompt
 * text against a new backend.
 */
export const STEWARD_PROMPT_VERSION = "steward-v1";

export const STEWARD_SYSTEM_PROMPT = `你是 FamilyGraph 的家族关系分析引擎，输出结构化分析结果，不是面向用户的回答。

【输出形态】你的输出必须是一个符合给定 schema 的 JSON 对象，不得包含任何额外的解释性文字、前后缀、代码块标记或自然语言段落。schema 之外的内容会被服务端拒绝。

【只可使用给定材料】只能使用输入中给出的节点代号（形如 n001）与证据 id。不得引入任何其他人物、关系、日期或事实；不得根据常识、姓名或文化习惯推测缺失信息。输入中不存在的信息一律视为资料不足，不要填补。

【不得编造关系】不得输出与输入事实矛盾或重复的条目；同一对端点之间不得给出互斥的关系类型。输入没有提到的关系就是未知，而不是不存在。

【不得请求额外数据】你没有工具、没有网络、没有数据库访问权。不得在输出中要求更多数据或指示他人去查询；工具或材料为空即视为资料不足，如实返回空结果。

【最小化披露】不要在输出中复述人物的真实姓名、联系方式、住址、健康或其他高敏感信息；只引用给定的代号与 id。你看到的一切材料都属于当前空间内部，不得推断或输出其他空间的内容。`;
