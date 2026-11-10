import { useId } from "react";
import { familyOf, labelOf } from "@/lib/entities";
import type { Segment } from "@/lib/segments";

/** The user's text with each detection shown as a value carrying its placeholder tab. */
export function Document({ segments, onToggle }: { segments: Segment[]; onToggle: (placeholder: string) => void }) {
  const helpId = useId();
  return (
    <>
      <p id={helpId} className="sr-only">
        Each marked value is a toggle button. Pressed means it is masked.
      </p>
      <div
        role="region"
        aria-label="Your text with detections"
        className="whitespace-pre-wrap break-words rounded-lg border border-rule bg-paper px-4 py-4 text-[1.0625rem] leading-[1.9] sm:px-6"
      >
        {segments.map((segment, index) => {
          if (segment.kind === "plain") return <span key={index}>{segment.text}</span>;
          const { detection, masked, locked } = segment;
          // The name starts with exactly what is visible ("value [PLACEHOLDER]"), so
          // speech input users can say what they see (WCAG 2.5.3).
          const visible = `${segment.text} ${detection.placeholder}`;
          const state = locked
            ? "placeholder-shaped text, always wrapped"
            : masked
              ? "masked"
              : "switched off, goes into your request as written";
          return (
            <button
              key={index}
              type="button"
              className={`exhibit fam-${familyOf(detection.type)}`}
              aria-pressed={masked}
              aria-label={`${visible}: ${labelOf(detection.type)}, ${state}`}
              aria-describedby={helpId}
              title={`${labelOf(detection.type)}: ${state}`}
              disabled={locked}
              onClick={() => {
                onToggle(detection.placeholder);
              }}
            >
              {segment.text} <span className="exhibit-tab">{detection.placeholder}</span>
            </button>
          );
        })}
      </div>
    </>
  );
}
