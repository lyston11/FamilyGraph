import { describe, expect, it } from "vitest";
import { streamSimple } from "@earendil-works/pi-ai/api/openai-responses";
import type { Context, Model } from "@earendil-works/pi-ai";

describe("liu-dada OpenAI Responses wire contract", () => {
  it("sends the Pi profile shape to /responses without a provider key", async () => {
    let capturedUrl = "";
    let capturedHeaders: Headers | undefined;
    let capturedBody: Record<string, unknown> | undefined;
    const fetchImpl: typeof fetch = async (input, init) => {
      capturedUrl = String(input);
      capturedHeaders = new Headers(init?.headers);
      capturedBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
      const sse = [
        'data: {"type":"response.created","response":{"id":"resp_test"}}',
        'data: {"type":"response.completed","response":{"id":"resp_test","status":"completed","output":[]}}',
        "data: [DONE]",
        "",
      ].join("\n\n");
      return new Response(sse, { status: 200, headers: { "content-type": "text/event-stream" } });
    };
    const model = {
      id: "gpt-5.6-sol",
      name: "gpt-5.6-sol",
      api: "openai-responses",
      provider: "liu-dada",
      baseUrl: "https://api.liu-dada.com/v1",
      reasoning: true,
      input: ["text", "image"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 272000,
      maxTokens: 60000,
      thinkingLevelMap: { low: "low", medium: "medium", high: "high", xhigh: "xhigh", max: "max" },
    } satisfies Model<"openai-responses">;
    const context: Context = {
      systemPrompt: "You are a family assistant.",
      messages: [{ role: "user", content: "hello", timestamp: Date.now() }],
      tools: [],
    };
    const stream = streamSimple(model, context, {
      apiKey: "run-token-only",
      fetch: fetchImpl,
      reasoning: "high",
      maxTokens: 60000,
      sessionId: "run-session-1",
      maxRetries: 0,
    });
    const events: unknown[] = [];
    for await (const event of stream) events.push(event);

    expect(capturedUrl).toBe("https://api.liu-dada.com/v1/responses");
    expect(capturedHeaders?.get("authorization")).toBe("Bearer run-token-only");
    expect(capturedBody?.model).toBe("gpt-5.6-sol");
    expect(capturedBody?.stream).toBe(true);
    expect(capturedBody?.store).toBe(false);
    expect(capturedBody?.max_output_tokens).toBe(60000);
    expect(capturedBody?.input).toBeInstanceOf(Array);
    expect(capturedBody).not.toHaveProperty("api_key");
    expect(events.some((event) => (event as { type?: string }).type === "done")).toBe(true);
  });
});
