import { describe, expect, it } from "vitest";
import { codePointToUtf16 } from "./offsets";

describe("codePointToUtf16", () => {
  it("is the identity for text without astral characters", () => {
    const toUtf16 = codePointToUtf16("Café ünïcode");
    expect(toUtf16(0)).toBe(0);
    expect(toUtf16(4)).toBe(4);
    expect(toUtf16(12)).toBe(12);
  });

  it("counts an emoji as one code point and two UTF-16 units", () => {
    const text = "📎 note to a@b.test";
    const toUtf16 = codePointToUtf16(text);
    // Python's offsets for "a@b.test": 10..18 (the emoji is one code point).
    expect(text.slice(toUtf16(10), toUtf16(18))).toBe("a@b.test");
    expect(text.slice(10, 18)).not.toBe("a@b.test");
  });

  it("maps the end of the text", () => {
    const text = "x😀y";
    expect(codePointToUtf16(text)(3)).toBe(text.length);
  });

  it("refuses offsets outside the text", () => {
    expect(() => codePointToUtf16("ab")(3)).toThrow(RangeError);
    expect(() => codePointToUtf16("ab")(-1)).toThrow(RangeError);
  });
});
