import { describe, expect, it } from "vitest";
import { SSEParser, textOf } from "./sse";

function feedAll(parser: SSEParser, chunks: string[]) {
  return [...chunks.flatMap((c) => parser.feed(c)), ...parser.close()];
}

const openai = (content: string) =>
  `data: ${JSON.stringify({ choices: [{ index: 0, delta: { content }, finish_reason: null }] })}\n\n`;

describe("SSEParser", () => {
  it("parses events split at any byte", () => {
    const stream = openai("Hello ") + openai("[EMAIL_1]") + "data: [DONE]\n\n";
    for (let size = 1; size <= stream.length; size += 7) {
      const chunks: string[] = [];
      for (let i = 0; i < stream.length; i += size) chunks.push(stream.slice(i, i + size));
      const events = feedAll(new SSEParser(), chunks);
      expect(events.map((e) => e.data)).toHaveLength(3);
      expect(events.map((e) => textOf("openai", e)).join("")).toBe("Hello [EMAIL_1]");
    }
  });

  it("handles CRLF, comments, event names and multi-line data", () => {
    const events = feedAll(new SSEParser(), [": ping\r\nevent: message_start\r\ndata: {\"a\":\r\ndata: 1}\r\n\r\n"]);
    expect(events).toEqual([{ event: "message_start", data: '{"a":\n1}' }]);
  });

  it("flushes a last event without a blank line at close", () => {
    expect(feedAll(new SSEParser(), ["data: tail"])).toEqual([{ event: "", data: "tail" }]);
  });
});

describe("textOf", () => {
  it("reads Anthropic text deltas and ignores other events", () => {
    const delta = { event: "content_block_delta", data: JSON.stringify({ type: "content_block_delta", delta: { type: "text_delta", text: "Hi" } }) };
    const ping = { event: "ping", data: JSON.stringify({ type: "ping" }) };
    expect(textOf("anthropic", delta)).toBe("Hi");
    expect(textOf("anthropic", ping)).toBe("");
  });

  it("raises the error event the gateway adds when the upstream fails mid-stream", () => {
    const openaiError = { event: "", data: JSON.stringify({ error: { type: "upstream_error", message: "timed out" } }) };
    const anthropicError = { event: "error", data: JSON.stringify({ type: "error", error: { type: "upstream_error", message: "overloaded" } }) };
    expect(() => textOf("openai", openaiError)).toThrow("timed out");
    expect(() => textOf("anthropic", anthropicError)).toThrow("overloaded");
  });

  it("ignores [DONE] and data that is not JSON", () => {
    expect(textOf("openai", { event: "", data: "[DONE]" })).toBe("");
    expect(textOf("openai", { event: "", data: "not json" })).toBe("");
  });
});
