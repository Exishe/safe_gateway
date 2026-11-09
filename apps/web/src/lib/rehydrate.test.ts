import { describe, expect, it } from "vitest";
import { restore } from "./rehydrate";

const MAP = { "[PERSON_1]": "Ann Roe", "[EMAIL_1]": "ann@roe.test" };

describe("restore", () => {
  it("puts each value of the session back", () => {
    const result = restore("Dear [PERSON_1], we wrote to [EMAIL_1].", MAP);
    expect(result.text).toBe("Dear Ann Roe, we wrote to ann@roe.test.");
    expect(result.unresolved).toEqual([]);
    expect(result.parts).toEqual([
      { kind: "text", text: "Dear " },
      { kind: "restored", text: "Ann Roe", placeholder: "[PERSON_1]" },
      { kind: "text", text: ", we wrote to " },
      { kind: "restored", text: "ann@roe.test", placeholder: "[EMAIL_1]" },
      { kind: "text", text: "." },
    ]);
  });

  it("flags a placeholder the session does not hold and leaves it as written", () => {
    const result = restore("Also [PERSON_99] and [PERSON_99].", MAP);
    expect(result.text).toBe("Also [PERSON_99] and [PERSON_99].");
    expect(result.unresolved).toEqual(["[PERSON_99]"]);
    expect(result.parts.filter((p) => p.kind === "unresolved")).toHaveLength(2);
  });

  it("flags placeholder-shaped variants the model rewrote", () => {
    for (const variant of ["[person 1]", "[EMAIL-1]", "PERSON_1", "EMAIL 1", "[LITERAL_3]"]) {
      expect(restore(`x ${variant} y`, MAP).unresolved).toEqual([variant]);
    }
  });

  it("does not flag words that merely contain a type name", () => {
    for (const text of ["PERSON_1x", "aPERSON_1", "PERSONAL 1", "email 1", "[PERSON_1234567]"]) {
      expect(restore(text, MAP).unresolved).toEqual([]);
    }
  });

  it("restores a placeholder split across stream deltas once the text is joined", () => {
    const joined = ["Dear [PERS", "ON_1]"].join("");
    expect(restore(joined, MAP).text).toBe("Dear Ann Roe");
  });

  it("does not restore a value inside another placeholder's value", () => {
    const result = restore("[PERSON_1]", { "[PERSON_1]": "[EMAIL_1]", "[EMAIL_1]": "x" });
    expect(result.text).toBe("[EMAIL_1]");
  });
});
