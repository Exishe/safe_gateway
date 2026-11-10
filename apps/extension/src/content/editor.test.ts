// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { isVisible, readDraft, replaceDraft, resolveEditor, sameDraft } from "./editor";
import { siteFor } from "./sites";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("readDraft", () => {
  it("reads a ProseMirror-style editor, one line per paragraph", () => {
    document.body.innerHTML =
      '<div contenteditable="true"><p>Dear Anna,</p><p>call me at <strong>555</strong>.</p><p><br></p></div>';
    const editor = document.querySelector("div") as HTMLElement;
    expect(readDraft(editor)).toBe("Dear Anna,\ncall me at 555.");
  });

  it("reads line breaks inside one paragraph", () => {
    document.body.innerHTML = '<div contenteditable="true">one<br>two</div>';
    expect(readDraft(document.querySelector("div") as HTMLElement)).toBe("one\ntwo");
  });

  it("reads a textarea's value", () => {
    document.body.innerHTML = "<textarea></textarea>";
    const area = document.querySelector("textarea") as HTMLTextAreaElement;
    area.value = "a\nb";
    expect(readDraft(area)).toBe("a\nb");
  });
});

describe("replaceDraft", () => {
  it("sets a textarea through the native setter and tells the page", async () => {
    document.body.innerHTML = "<textarea></textarea>";
    const area = document.querySelector("textarea") as HTMLTextAreaElement;
    area.value = "Anna";
    let inputs = 0;
    area.addEventListener("input", () => (inputs += 1));
    expect(await replaceDraft(area, "[PERSON_1]")).toBe(true);
    expect(area.value).toBe("[PERSON_1]");
    expect(inputs).toBe(1);
  });
});

describe("sameDraft", () => {
  it("ignores how whitespace and paragraphs were rendered, nothing else", () => {
    expect(sameDraft("a\n\nb  c", "a\nb c")).toBe(true);
    expect(sameDraft("[PERSON_1] x", "Anna x")).toBe(false);
  });
});

describe("siteFor", () => {
  it("knows the three sites and nothing else", () => {
    expect(siteFor("chatgpt.com")?.name).toBe("ChatGPT");
    expect(siteFor("chat.openai.com")?.name).toBe("ChatGPT");
    expect(siteFor("claude.ai")?.name).toBe("Claude");
    expect(siteFor("gemini.google.com")?.name).toBe("Gemini");
    expect(siteFor("evil-chatgpt.com")).toBeNull();
  });
});

describe("resolveEditor", () => {
  const SELECTOR = '#prompt-textarea, div.ProseMirror[contenteditable="true"], form textarea';

  it("skips a hidden fallback textarea that comes first in the form", () => {
    document.body.innerHTML =
      '<form><textarea style="display:none"></textarea><div id="prompt-textarea" class="ProseMirror" contenteditable="true"><p>hi</p></div></form>';
    const editor = resolveEditor(SELECTOR);
    expect(editor?.id).toBe("prompt-textarea");
  });

  it("finds the box again after the page replaced it", () => {
    document.body.innerHTML = '<form><div id="prompt-textarea" contenteditable="true"><p>one</p></div></form>';
    expect(readDraft(resolveEditor(SELECTOR) as HTMLElement)).toBe("one");
    document.body.innerHTML = '<form><div id="prompt-textarea" contenteditable="true"><p>two</p></div></form>';
    expect(readDraft(resolveEditor(SELECTOR) as HTMLElement)).toBe("two");
  });

  it("prefers the visible box that holds a draft, and gives null when none is visible", () => {
    document.body.innerHTML =
      '<div id="a" contenteditable="true"></div><div id="b" contenteditable="true"><p>draft</p></div>';
    expect(resolveEditor('[contenteditable="true"]')?.id).toBe("b");
    document.body.innerHTML = '<div hidden><div id="prompt-textarea" contenteditable="true"></div></div>';
    expect(resolveEditor(SELECTOR)).toBeNull();
  });

  it("looks inside a given scope only", () => {
    document.body.innerHTML =
      '<form id="f"><div id="in" contenteditable="true"></div></form><div id="out" contenteditable="true"><p>x</p></div>';
    expect(resolveEditor('[contenteditable="true"]', document.getElementById("f") as HTMLElement)?.id).toBe("in");
  });
});

describe("isVisible", () => {
  it("is false under display:none or hidden, true otherwise", () => {
    document.body.innerHTML = '<div style="display:none"><span id="a"></span></div><span id="b"></span>';
    expect(isVisible(document.getElementById("a") as HTMLElement)).toBe(false);
    expect(isVisible(document.getElementById("b") as HTMLElement)).toBe(true);
  });
});
