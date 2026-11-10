// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { readDraft, replaceDraft, sameDraft } from "./editor";
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
