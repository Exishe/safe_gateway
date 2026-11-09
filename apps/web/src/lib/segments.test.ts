import { describe, expect, it } from "vitest";
import type { Detection } from "./api";
import { buildReview } from "./segments";

const TEXT = "Write to Ann Roe at ann@roe.test, then ann@roe.test again.";

function at(value: string, from = 0): [number, number] {
  const start = TEXT.indexOf(value, from);
  return [start, start + value.length];
}

const [nameStart, nameEnd] = at("Ann Roe");
const [mail1Start, mail1End] = at("ann@roe.test");
const [mail2Start, mail2End] = at("ann@roe.test", mail1End);

const DETECTIONS: Detection[] = [
  { start: nameStart, end: nameEnd, type: "PERSON", placeholder: "[PERSON_1]", score: 0.85, source: "detected" },
  { start: mail1Start, end: mail1End, type: "EMAIL", placeholder: "[EMAIL_1]", score: 1, source: "detected" },
  { start: mail2Start, end: mail2End, type: "EMAIL", placeholder: "[EMAIL_1]", score: null, source: "repeat" },
];

describe("buildReview", () => {
  it("masks every detection by default and matches the gateway's masked text", () => {
    const review = buildReview(TEXT, DETECTIONS, new Set());
    expect(review.request).toBe("Write to [PERSON_1] at [EMAIL_1], then [EMAIL_1] again.");
    expect(review.segments.map((s) => s.text).join("")).toBe(TEXT);
  });

  it("puts a switched-off value back everywhere it occurs", () => {
    const review = buildReview(TEXT, DETECTIONS, new Set(["[EMAIL_1]"]));
    expect(review.request).toBe("Write to [PERSON_1] at ann@roe.test, then ann@roe.test again.");
    const emails = review.segments.filter((s) => s.kind === "detection" && s.detection.type === "EMAIL");
    expect(emails.every((s) => s.kind === "detection" && !s.masked)).toBe(true);
  });

  it("groups detections by placeholder with the best score and occurrence count", () => {
    const { groups } = buildReview(TEXT, DETECTIONS, new Set());
    expect(groups).toEqual([
      { placeholder: "[PERSON_1]", type: "PERSON", value: "Ann Roe", count: 1, score: 0.85, sources: ["detected"], masked: true, locked: false },
      { placeholder: "[EMAIL_1]", type: "EMAIL", value: "ann@roe.test", count: 2, score: 1, sources: ["detected", "repeat"], masked: true, locked: false },
    ]);
  });

  it("keeps placeholder-shaped text masked: switching it off would confuse restoring", () => {
    const text = "See [PERSON_4] here.";
    const literal: Detection = { start: 4, end: 14, type: "LITERAL", placeholder: "[LITERAL_1]", score: null, source: "literal" };
    const review = buildReview(text, [literal], new Set(["[LITERAL_1]"]));
    expect(review.request).toBe("See [LITERAL_1] here.");
    expect(review.groups[0]?.locked).toBe(true);
    expect(review.groups[0]?.masked).toBe(true);
  });

  it("converts the gateway's code point offsets to UTF-16", () => {
    const text = "📎 ann@roe.test";
    const detection: Detection = { start: 2, end: 14, type: "EMAIL", placeholder: "[EMAIL_1]", score: 1, source: "detected" };
    const review = buildReview(text, [detection], new Set());
    expect(review.request).toBe("📎 [EMAIL_1]");
    expect(review.groups[0]?.value).toBe("ann@roe.test");
  });

  it("refuses detections that overlap or fall outside the text: a skipped one would leak", () => {
    const first: Detection = { start: 0, end: 5, type: "PERSON", placeholder: "[PERSON_1]", score: 1, source: "detected" };
    const overlapping: Detection = { ...first, start: 3, end: 8, placeholder: "[PERSON_2]" };
    const outside: Detection = { ...first, start: 50, end: 900 };
    expect(() => buildReview(TEXT, [first, overlapping], new Set())).toThrow(/do not line up/);
    expect(() => buildReview(TEXT, [outside], new Set())).toThrow(/do not line up/);
  });

  it("refuses detections whose text is not what the gateway mapped", () => {
    const wrong: Detection = { start: 0, end: 5, type: "PERSON", placeholder: "[PERSON_1]", score: 1, source: "detected" };
    expect(() => buildReview(TEXT, [wrong], new Set(), { "[PERSON_1]": "Somebody" })).toThrow(/do not line up/);
  });
});
