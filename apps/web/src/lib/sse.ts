export interface SSEEvent {
  event: string;
  data: string;
}

export type ChatShape = "openai" | "anthropic";

/** Server-sent events from text chunks that may split anywhere, CRLF included. */
export class SSEParser {
  private buffer = "";
  private event = "";
  private data: string[] = [];

  feed(chunk: string): SSEEvent[] {
    this.buffer += chunk;
    const out: SSEEvent[] = [];
    for (;;) {
      const match = /\r\n|\r|\n/.exec(this.buffer);
      // A lone \r at the end may be the first half of \r\n: wait for the next chunk.
      if (match === null || (match[0] === "\r" && match.index === this.buffer.length - 1)) break;
      const line = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const dispatched = this.line(line);
      if (dispatched !== null) out.push(dispatched);
    }
    return out;
  }

  close(): SSEEvent[] {
    const out: SSEEvent[] = [];
    if (this.buffer !== "") {
      const dispatched = this.line(this.buffer);
      this.buffer = "";
      if (dispatched !== null) out.push(dispatched);
    }
    const last = this.dispatch();
    if (last !== null) out.push(last);
    return out;
  }

  private line(line: string): SSEEvent | null {
    if (line === "") return this.dispatch();
    if (line.startsWith(":")) return null;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") this.data.push(value);
    else if (field === "event") this.event = value;
    return null;
  }

  private dispatch(): SSEEvent | null {
    if (this.data.length === 0) {
      this.event = "";
      return null;
    }
    const event = { event: this.event, data: this.data.join("\n") };
    this.event = "";
    this.data = [];
    return event;
  }
}

export class StreamError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StreamError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorMessage(payload: Record<string, unknown>): string | null {
  const error = payload.error;
  if (!isRecord(error)) return null;
  return typeof error.message === "string" ? error.message : "the model's answer stopped with an error";
}

/** The answer text one event adds; throws on an error event. */
export function textOf(shape: ChatShape, event: SSEEvent): string {
  if (event.data === "[DONE]") return "";
  let payload: unknown;
  try {
    payload = JSON.parse(event.data);
  } catch {
    return "";
  }
  if (!isRecord(payload)) return "";
  const failure = errorMessage(payload);
  if (failure !== null) throw new StreamError(failure);
  if (shape === "anthropic") {
    const delta = payload.delta;
    return payload.type === "content_block_delta" && isRecord(delta) && typeof delta.text === "string"
      ? delta.text
      : "";
  }
  const choices = payload.choices;
  if (!Array.isArray(choices)) return "";
  let text = "";
  for (const choice of choices as unknown[]) {
    if (isRecord(choice) && (choice.index ?? 0) === 0 && isRecord(choice.delta)) {
      const content = choice.delta.content;
      if (typeof content === "string") text += content;
    }
  }
  return text;
}
