import { Warning } from "@/components/icons";
import { Alert } from "@/components/ui/alert";
import { familyOf } from "@/lib/entities";
import { type Part, flag, restore } from "@/lib/rehydrate";

export interface ExchangeState {
  sent: string;
  answer: string;
  status: "streaming" | "done" | "stopped" | "failed";
  error: string | null;
}

function typeOf(placeholder: string): string {
  return /^\[([A-Z_]+)_\d+\]$/.exec(placeholder)?.[1] ?? "LITERAL";
}

function Token({ text }: { text: string }) {
  return (
    <span className={`fam-${familyOf(typeOf(text))} token rounded-sm bg-(--fam-wash) px-1 py-0.5 text-ink ring-1 ring-inset ring-(--fam)`}>
      {text}
    </span>
  );
}

function Unresolved({ text }: { text: string }) {
  return (
    <span className="token rounded-sm px-1 py-0.5 text-seal ring-1 ring-inset ring-seal">
      {text}
      <span className="sr-only"> (not restored)</span>
    </span>
  );
}

/** The request as sent: placeholders marked, nothing else. */
function RequestText({ text, mapping }: { text: string; mapping: Record<string, string> }) {
  const { parts } = restore(text, mapping);
  return (
    <>
      {parts.map((part, i) =>
        part.kind === "text" ? <span key={i}>{part.text}</span> : <Token key={i} text={part.kind === "restored" ? part.placeholder : part.text} />,
      )}
    </>
  );
}

// The gateway restored the answer in the session (ADR 0025); only what it left as
// written is marked, so a token it kept out of a link is never restored here.
function AnswerPart({ part }: { part: Part }) {
  if (part.kind === "unresolved") return <Unresolved text={part.text} />;
  return <span>{part.text}</span>;
}

function Pane({ title, note, children, labelId }: { title: string; note: string; children: React.ReactNode; labelId: string }) {
  return (
    <section className="flex min-w-0 flex-col">
      <h3 id={labelId} className="font-display text-xl">
        {title}
      </h3>
      <p className="mb-2 mt-0.5 text-sm text-ink-muted">{note}</p>
      {children}
    </section>
  );
}

export function Exchange({
  request,
  exchange,
  mapping,
  destination,
}: {
  request: string;
  exchange: ExchangeState | null;
  mapping: Record<string, string>;
  destination: string;
}) {
  const restored = exchange === null ? null : flag(exchange.answer);
  const finished = exchange !== null && exchange.status !== "streaming";
  const unresolved = restored?.unresolved ?? [];
  // Once sent, the pane shows what went out, until a switch changes the next request.
  const sentAsShown = exchange !== null && exchange.sent === request;
  const changed = exchange !== null && !sentAsShown;

  return (
    <div className="grid gap-6 lg:grid-cols-2 lg:gap-8">
      <Pane
        labelId="request-title"
        title={sentAsShown ? "Request sent" : "Request to send"}
        note={
          changed
            ? "Changed since the last send; the answer is for the previous request."
            : `Pseudonymised. This is the text ${destination} receives, after the gateway checks it once more.`
        }
      >
        <div
          role="region"
          aria-labelledby="request-title"
          className="token min-h-40 whitespace-pre-wrap break-words rounded-lg border border-rule bg-sunken px-4 py-3 text-[0.875rem] leading-relaxed"
        >
          <RequestText text={request} mapping={mapping} />
        </div>
      </Pane>

      <Pane labelId="answer-title" title="Answer" note="The gateway on this computer put the real values back into the answer.">
        {finished && unresolved.length > 0 && (
          <Alert tone="seal" className="mb-3" role="alert">
            <Warning />
            <div>
              <p className="font-semibold">
                {unresolved.length === 1
                  ? "1 placeholder could not be restored"
                  : `${unresolved.length} placeholders could not be restored`}
              </p>
              <p className="mt-0.5 text-sm">
                {unresolved.map((token, i) => (
                  <span key={token}>
                    {i > 0 && ", "}
                    <span className="token">{token}</span>
                  </span>
                ))}
                . Shown exactly as the model wrote them. The model may have invented them or changed their spelling; check
                those passages before you use the answer.
              </p>
            </div>
          </Alert>
        )}
        <div
          role="region"
          aria-labelledby="answer-title"
          className="min-h-40 whitespace-pre-wrap break-words rounded-lg border border-rule bg-paper px-4 py-3 leading-relaxed"
        >
          {restored === null ? (
            <span className="text-ink-muted">The answer appears here once you send the request.</span>
          ) : (
            restored.parts.map((part, i) => <AnswerPart key={i} part={part} />)
          )}
          {exchange?.status === "streaming" && (
            <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-binding align-[-0.15em]" aria-hidden="true" />
          )}
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-ink-muted" role="status">
            {exchange === null
              ? ""
              : exchange.status === "streaming"
                ? "Receiving the answer"
                : exchange.status === "stopped"
                  ? "Stopped. The answer above is incomplete."
                  : exchange.status === "failed"
                    ? ""
                    : unresolved.length > 0
                      ? "Answer complete, with placeholders that could not be restored."
                      : "Answer complete. Every placeholder was restored."}
          </p>
        </div>
        {exchange?.status === "failed" && exchange.error !== null && (
          <Alert tone="seal" className="mt-3" role="alert">
            <Warning />
            <p>{exchange.error}</p>
          </Alert>
        )}
      </Pane>
    </div>
  );
}
