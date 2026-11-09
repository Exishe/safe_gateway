import { useEffect, useMemo, useRef, useState } from "react";
import type { GatewayState } from "@/App";
import { FileText, Info, Send, Stop, Warning } from "@/components/icons";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input, Label, Textarea } from "@/components/ui/fields";
import { type MaskResult, gateway, shapeFor } from "@/lib/api";
import { providerName } from "@/lib/providers";
import { type Review, buildReview } from "@/lib/segments";
import { Document } from "./mask/Document";
import { Exchange, type ExchangeState } from "./mask/Exchange";
import { Ledger } from "./mask/Ledger";

// The gateway refuses bodies over 4 MiB by default; leave room for JSON escaping.
const MAX_FILE_BYTES = 1024 * 1024;

interface Checked {
  text: string;
  result: MaskResult;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function destinationOf(state: GatewayState): string {
  if (state.kind !== "ready") return "the model";
  const name = providerName(state.health.provider);
  return state.health.local ? `${name} on this computer` : `${name} in the cloud`;
}

function useSessionCleanup(sessionId: string | null) {
  // A session holds the real values in the gateway's memory: drop it when the page goes.
  useEffect(() => {
    if (sessionId === null) return;
    const drop = () => {
      void gateway.deleteSession(sessionId, true).catch(() => undefined);
    };
    window.addEventListener("pagehide", drop);
    return () => {
      window.removeEventListener("pagehide", drop);
    };
  }, [sessionId]);
}

function Compose({
  text,
  setText,
  onCheck,
  checking,
}: {
  text: string;
  setText: (text: string) => void;
  onCheck: () => void;
  checking: boolean;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [fileNote, setFileNote] = useState<{ tone: "info" | "seal"; text: string } | null>(null);

  async function open(file: File | undefined) {
    if (file === undefined) return;
    const isText = file.name.toLowerCase().endsWith(".txt") || file.type === "text/plain";
    if (!isText) {
      setFileNote({
        tone: "seal",
        text: `${file.name} is not a .txt file. For PDF, Word, Excel and email files, use \`uv run safeguard mask\` on the command line.`,
      });
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      setFileNote({ tone: "seal", text: `${file.name} is larger than 1 MB. Split it, or use \`uv run safeguard mask\`.` });
      return;
    }
    setText(await file.text());
    setFileNote({ tone: "info", text: `Opened ${file.name}. Nothing has been checked or sent yet.` });
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem] lg:gap-10">
      <div>
        <Label htmlFor="source-text" className="font-display text-xl font-normal">
          Text to check
        </Label>
        <p id="source-help" className="mt-0.5 text-sm text-ink-muted">
          Paste text or drop a .txt file here. Press Ctrl+Enter to check it.
        </p>
        <div className="mt-3">
          <Textarea
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => {
              setDragging(false);
            }}
            onDrop={(e) => {
              setDragging(false);
              if (e.dataTransfer.files.length === 0) return; // dropped text: the textarea inserts it
              e.preventDefault();
              void open(e.dataTransfer.files[0]);
            }}
            id="source-text"
            aria-describedby="source-help"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && text.trim() !== "") {
                e.preventDefault();
                onCheck();
              }
            }}
            aria-keyshortcuts="Control+Enter"
            rows={12}
            spellCheck={false}
            placeholder="For example: Please draft a reply to the client about the invoice they disputed…"
            className={`min-h-64 resize-y bg-paper ${dragging ? "outline-2 outline-offset-2 outline-dashed outline-binding" : ""}`}
          />
        </div>
        {fileNote !== null && (
          <Alert tone={fileNote.tone} className="mt-3" role={fileNote.tone === "seal" ? "alert" : "status"}>
            {fileNote.tone === "seal" ? <Warning /> : <Info />}
            <p>{fileNote.text}</p>
          </Alert>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button size="lg" onClick={onCheck} disabled={checking || text.trim() === ""}>
            {checking ? "Checking" : "Find personal data"}
          </Button>
          <Button variant="outline" size="lg" onClick={() => fileInput.current?.click()}>
            <FileText />
            Open a .txt file
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept=".txt,text/plain"
            className="sr-only"
            tabIndex={-1}
            aria-hidden="true"
            onChange={(e) => {
              void open(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </div>
      </div>
      <aside className="border-t border-rule pt-5 text-[0.9375rem] text-ink-muted lg:border-l lg:border-t-0 lg:pl-8 lg:pt-1">
        <h3 className="font-display text-lg text-ink">What happens</h3>
        <ol className="mt-2 list-decimal space-y-2 pl-5">
          <li>The gateway on this computer finds names, numbers and other personal data.</li>
          <li>You review what it found and can switch any of it off.</li>
          <li>Only the pseudonymised text goes to the model.</li>
          <li>The real values are put back into the answer here.</li>
        </ol>
        <p className="mt-3">This reduces exposure; detection can miss things, so read the request before you send it.</p>
      </aside>
    </div>
  );
}

export function MaskScreen({ state }: { state: GatewayState }) {
  const [text, setText] = useState("");
  const [checked, setChecked] = useState<Checked | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unmasked, setUnmasked] = useState<ReadonlySet<string>>(new Set());
  const [modelInput, setModelInput] = useState("");
  const [exchange, setExchange] = useState<ExchangeState | null>(null);
  const abort = useRef<AbortController | null>(null);
  const reviewHeading = useRef<HTMLHeadingElement>(null);

  const sessionId = checked?.result.session_id ?? null;
  useSessionCleanup(sessionId);

  const review = useMemo<Review | { error: string } | null>(() => {
    if (checked === null) return null;
    try {
      const detections = checked.result.detections[0] ?? [];
      return buildReview(checked.text, detections, unmasked, checked.result.mapping);
    } catch (e) {
      return { error: message(e) };
    }
  }, [checked, unmasked]);

  const configuredModel = state.kind === "ready" ? state.models[0] : undefined;
  const model = configuredModel ?? modelInput.trim();
  const streaming = exchange?.status === "streaming";

  useEffect(() => {
    if (checked !== null) reviewHeading.current?.focus();
  }, [checked]);

  async function dropSession() {
    if (sessionId !== null) await gateway.deleteSession(sessionId).catch(() => undefined);
  }

  async function check() {
    setChecking(true);
    setError(null);
    try {
      await dropSession();
      const result = await gateway.mask(text);
      setChecked({ text, result });
      setUnmasked(new Set());
      setExchange(null);
    } catch (e) {
      setError(message(e));
    } finally {
      setChecking(false);
    }
  }

  function toggle(placeholder: string) {
    setUnmasked((current) => {
      const next = new Set(current);
      if (next.has(placeholder)) next.delete(placeholder);
      else next.add(placeholder);
      return next;
    });
  }

  async function send() {
    if (review === null || "error" in review || state.kind !== "ready" || model === "") return;
    const controller = new AbortController();
    abort.current = controller;
    const sent = review.request;
    setExchange({ sent, answer: "", status: "streaming", error: null });
    try {
      await gateway.chat(
        shapeFor(state.health.provider),
        model,
        sent,
        (piece) => {
          setExchange((current) => (current === null ? current : { ...current, answer: current.answer + piece }));
        },
        controller.signal,
      );
      setExchange((current) => (current === null ? current : { ...current, status: "done" }));
    } catch (e) {
      const stopped = e instanceof DOMException && e.name === "AbortError";
      setExchange((current) =>
        current === null ? current : { ...current, status: stopped ? "stopped" : "failed", error: stopped ? null : message(e) },
      );
    } finally {
      abort.current = null;
    }
  }

  async function clear() {
    abort.current?.abort();
    await dropSession();
    setChecked(null);
    setExchange(null);
    setUnmasked(new Set());
    setText("");
    setError(null);
  }

  function edit() {
    abort.current?.abort();
    void dropSession();
    setChecked(null);
    setExchange(null);
  }

  const errorAlert = error !== null && (
    <Alert tone="seal" className="mb-5" role="alert">
      <Warning />
      <p>{error}</p>
    </Alert>
  );

  if (checked === null || review === null) {
    return (
      <>
        <h2 className="sr-only">Mask and send</h2>
        {errorAlert}
        <Compose text={text} setText={setText} onCheck={() => void check()} checking={checking} />
      </>
    );
  }

  if ("error" in review) {
    return (
      <Alert tone="seal" role="alert">
        <Warning />
        <div>
          <p>{review.error}</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={edit}>
            Back to the text
          </Button>
        </div>
      </Alert>
    );
  }

  const destination = destinationOf(state);
  const switchedOff = review.groups.filter((g) => !g.masked).length;

  return (
    <div className="flex flex-col gap-8">
      {errorAlert}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 ref={reviewHeading} tabIndex={-1} className="font-display text-2xl sm:text-[1.75rem]">
            Review before sending
          </h2>
          <p className="mt-1 max-w-[62ch] text-[0.9375rem] text-ink-muted">
            Each marked value carries the placeholder the model will see. Select a value, or use its switch, to leave it
            unmasked.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={edit}>
            Edit text
          </Button>
          <Button variant="ghost" onClick={() => void clear()}>
            Clear
          </Button>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem] lg:gap-10">
        <div>
          <Document segments={review.segments} onToggle={toggle} />
        </div>
        <div className="flex flex-col gap-4">
          <Ledger groups={review.groups} onToggle={toggle} />
          {switchedOff > 0 && (
            <Alert tone="info">
              <Info />
              <p className="text-sm">
                Switched-off values go into your request as written. The gateway checks every request again before it
                leaves this computer and masks whatever its detectors find, so some may still be masked.
              </p>
            </Alert>
          )}
        </div>
      </div>

      <section aria-label="Send" className="flex flex-col gap-3 border-y border-rule py-5 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex flex-col gap-1.5">
          {configuredModel !== undefined ? (
            <p className="text-[0.9375rem]">
              Model <span className="token">{configuredModel}</span>, set in the gateway's configuration.
            </p>
          ) : (
            <>
              <Label htmlFor="model">Model</Label>
              <Input
                id="model"
                value={modelInput}
                onChange={(e) => {
                  setModelInput(e.target.value);
                }}
                placeholder="for example llama3.2"
                className="w-full sm:w-72"
                aria-describedby="model-help"
              />
              <p id="model-help" className="text-sm text-ink-muted">
                No model is set in config.toml, so name one here.
              </p>
            </>
          )}
          <p className="text-sm text-ink-muted">Goes to {destination} through the gateway.</p>
        </div>
        {streaming ? (
          <Button size="lg" variant="danger" onClick={() => abort.current?.abort()}>
            <Stop />
            Stop
          </Button>
        ) : (
          <Button size="lg" onClick={() => void send()} disabled={state.kind !== "ready" || model === ""}>
            <Send />
            {exchange === null ? "Send pseudonymised request" : "Send again"}
          </Button>
        )}
      </section>

      <Exchange request={review.request} exchange={exchange} mapping={checked.result.mapping} destination={destination} />
    </div>
  );
}
