import { describe, expect, it } from "vitest";
import { LinkContext } from "./links";
import { canonicalOf, TOKEN } from "./placeholders";
import vectors from "./rehydrate.parity.json";
import { rehydrateExplained } from "./rehydrate";

// rehydrate.parity.json is the engine's own output (tests/unit/test_extension_parity.py
// writes it from gateway/engine/rehydrate.py), so every case here is a check that the
// port says what Python says, link rule included.
const mapping: Record<string, string> = vectors.mapping;

describe("parity with gateway/engine/rehydrate.py", () => {
  it.each(vectors.cases.map((c) => [JSON.stringify(c.text), c] as const))("%s", (_, c) => {
    const { text, unresolved } = rehydrateExplained(c.text, mapping);
    expect(text).toBe(c.restored);
    expect([...unresolved.entries()]).toEqual(c.unresolved);
  });

  it("covers both outcomes of the link rule", () => {
    const reasons = vectors.cases.flatMap((c) => c.unresolved.map(([, reason]) => reason));
    expect(reasons).toContain("inside_link");
    expect(reasons).toContain("not_in_mapping");
  });
});

describe("the token grammar", () => {
  it("maps each shape to its canonical key", () => {
    const keys = [..."[person-1] PERSON 2 [Email_3]".matchAll(TOKEN)].map(canonicalOf);
    expect(keys).toEqual(["[PERSON_1]", "[PERSON_2]", "[EMAIL_3]"]);
  });

  it("never restores lower-case unbracketed forms or unknown numbers", () => {
    const { text, unresolved } = rehydrateExplained("person_1, [PERSON_7]", mapping);
    expect(text).toBe("person_1, [PERSON_7]");
    expect([...unresolved.keys()]).toEqual(["[PERSON_7]"]);
  });

  it("does not rescan a restored value that is itself placeholder-shaped", () => {
    // [LITERAL_1] holds "[PERSON_1]", text the user typed; it must come back as typed.
    expect(rehydrateExplained("[LITERAL_1]", mapping).text).toBe("[PERSON_1]");
  });
});

describe("LinkContext", () => {
  it("gives the same answer fed whole or one character at a time", () => {
    for (const c of vectors.cases) {
      const whole = new LinkContext();
      whole.feed(c.text);
      const pieces = new LinkContext();
      for (const char of c.text) pieces.feed(char);
      expect(pieces.inside).toBe(whole.inside);
    }
  });
});
