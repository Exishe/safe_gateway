import type { Detection } from "./api";
import { codePointToUtf16 } from "./offsets";

export type Segment =
  | { kind: "plain"; text: string }
  | { kind: "detection"; text: string; detection: Detection; masked: boolean; locked: boolean };

export interface Group {
  placeholder: string;
  type: string;
  value: string;
  count: number;
  score: number | null;
  sources: string[];
  masked: boolean;
  // Placeholder-shaped input stays masked: sent as written, the browser could not
  // tell it from a placeholder of this session when it restores the answer.
  locked: boolean;
}

export interface Review {
  segments: Segment[];
  groups: Group[];
  request: string;
}

export class MisalignedDetections extends Error {
  constructor() {
    super("The gateway's detections do not line up with the text. Nothing was sent; check the text again.");
    this.name = "MisalignedDetections";
  }
}

function isLocked(detection: Detection): boolean {
  return detection.source === "literal" || detection.type === "LITERAL";
}

/**
 * The text cut into plain and detected stretches, the detections grouped by value,
 * and the request text: each detection's placeholder, or its original value where
 * the user switched that value off. Throws rather than skip a detection, because a
 * skipped one would go out unmasked.
 */
export function buildReview(
  text: string,
  detections: readonly Detection[],
  unmasked: ReadonlySet<string>,
  mapping?: Readonly<Record<string, string>>,
): Review {
  const toUtf16 = codePointToUtf16(text);
  const segments: Segment[] = [];
  const groups = new Map<string, Group>();
  const request: string[] = [];
  let cursor = 0;
  for (const detection of [...detections].sort((a, b) => a.start - b.start)) {
    let start: number;
    let end: number;
    try {
      start = toUtf16(detection.start);
      end = toUtf16(detection.end);
    } catch {
      throw new MisalignedDetections();
    }
    const value = text.slice(start, end);
    const mapped = mapping?.[detection.placeholder];
    if (start < cursor || end <= start || (mapped !== undefined && mapped !== value)) {
      throw new MisalignedDetections();
    }
    const locked = isLocked(detection);
    const masked = locked || !unmasked.has(detection.placeholder);
    if (start > cursor) {
      const plain = text.slice(cursor, start);
      segments.push({ kind: "plain", text: plain });
      request.push(plain);
    }
    segments.push({ kind: "detection", text: value, detection, masked, locked });
    request.push(masked ? detection.placeholder : value);
    cursor = end;

    const group = groups.get(detection.placeholder);
    if (group === undefined) {
      groups.set(detection.placeholder, {
        placeholder: detection.placeholder,
        type: detection.type,
        value,
        count: 1,
        score: detection.score,
        sources: [detection.source],
        masked,
        locked,
      });
    } else {
      group.count += 1;
      if (detection.score !== null && (group.score === null || detection.score > group.score)) {
        group.score = detection.score;
      }
      if (!group.sources.includes(detection.source)) group.sources.push(detection.source);
    }
  }
  if (cursor < text.length) {
    const rest = text.slice(cursor);
    segments.push({ kind: "plain", text: rest });
    request.push(rest);
  }
  return { segments, groups: [...groups.values()], request: request.join("") };
}
