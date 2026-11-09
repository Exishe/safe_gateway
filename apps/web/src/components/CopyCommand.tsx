import { useState } from "react";
import { Button } from "./ui/button";
import { Copy } from "./icons";

/** A command or file snippet the user runs or pastes themselves, with a copy button. */
export function CopyCommand({ text, label, block = false }: { text: string; label: string; block?: boolean }) {
  const [note, setNote] = useState("");

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setNote("Copied.");
    } catch {
      setNote("Copying is blocked here; select the text and copy it.");
    }
  }

  return (
    <div className="rounded-lg border border-rule bg-sunken">
      <div className="flex items-start gap-2 p-1 pl-3">
        <pre
          tabIndex={0}
          className={`token min-w-0 flex-1 overflow-x-auto py-2 text-[0.8125rem] leading-relaxed ${block ? "" : "whitespace-pre-wrap break-all"}`}
          aria-label={label}
        >
          {text}
        </pre>
        <Button variant="ghost" size="sm" onClick={() => void copy()} aria-label={`Copy ${label}`}>
          <Copy />
          <span className="max-sm:sr-only">Copy</span>
        </Button>
      </div>
      <p className="px-3 text-xs text-ink-muted empty:hidden [&:not(:empty)]:pb-2" aria-live="polite">
        {note}
      </p>
    </div>
  );
}
