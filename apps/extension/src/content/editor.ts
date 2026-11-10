// Reading and replacing the site's draft. The replacement must go through the page's
// own input handling (ProseMirror, Quill, React), or the site would send its own copy
// of the draft; so it is checked by reading the textbox back (fail closed, ADR 0026).

const BLOCKS = new Set(["P", "DIV", "LI", "PRE", "BLOCKQUOTE", "H1", "H2", "H3", "H4", "H5", "H6", "UL", "OL"]);

export type Editor = HTMLElement;

function isField(editor: Editor): editor is HTMLTextAreaElement | HTMLInputElement {
  return editor instanceof HTMLTextAreaElement || editor instanceof HTMLInputElement;
}

/** Whether the element can be seen: no `hidden`, `display: none` or `visibility: hidden` up the tree. */
export function isVisible(element: Element): boolean {
  for (let node: Element | null = element; node !== null; node = node.parentElement) {
    if (node instanceof HTMLElement && node.hidden) return false;
    const style = node.ownerDocument.defaultView?.getComputedStyle(node);
    if (style?.display === "none") return false;
    if (node === element && style?.visibility === "hidden") return false;
  }
  return true;
}

/**
 * The textbox the user is typing in, found afresh each time (the site may replace it
 * at any moment). A selector list matches in document order, so the first match can be
 * a hidden fallback textarea; the visible ones are taken, the focused one first, then
 * one that holds a draft.
 */
export function resolveEditor(selector: string, scope: ParentNode = document): Editor | null {
  const visible = [...scope.querySelectorAll(selector)].filter(
    (node): node is HTMLElement => node instanceof HTMLElement && isVisible(node),
  );
  const focused = visible.find((node) => node.contains(node.ownerDocument.activeElement));
  return focused ?? visible.find((node) => readDraft(node).trim() !== "") ?? visible[0] ?? null;
}

/** The draft as text: one line per paragraph, `<br>` as a line break. */
export function readDraft(editor: Editor): string {
  if (isField(editor)) return editor.value;
  const out: string[] = [];
  const newline = (): void => {
    if (out.length > 0 && !out[out.length - 1]?.endsWith("\n")) out.push("\n");
  };
  const walk = (node: Node): void => {
    for (const child of Array.from(node.childNodes)) {
      if (child instanceof Text) out.push(child.data);
      else if (child instanceof HTMLBRElement) {
        if (!child.classList.contains("ProseMirror-trailingBreak")) out.push("\n");
      } else if (child instanceof Element) {
        const block = BLOCKS.has(child.tagName.toUpperCase());
        if (block) newline();
        walk(child);
        if (block) newline();
      }
    }
  };
  walk(editor);
  return out.join("").replace(/\n+$/, "");
}

/** Equal up to how whitespace and paragraph breaks were rendered. */
export function sameDraft(a: string, b: string): boolean {
  const norm = (s: string): string => s.replace(/\s+/gu, " ").trim();
  return norm(a) === norm(b);
}

function nextFrame(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 30));
}

function selectAll(editor: Editor): void {
  editor.focus();
  const selection = editor.ownerDocument.getSelection();
  if (selection === null) return;
  const range = editor.ownerDocument.createRange();
  range.selectNodeContents(editor);
  selection.removeAllRanges();
  selection.addRange(range);
}

function setField(field: HTMLTextAreaElement | HTMLInputElement, text: string): void {
  // React keeps its own copy of a field's value; the prototype's setter updates the
  // DOM so that React's input listener sees a change.
  const proto = field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Reflect.set(proto, "value", text, field);
  field.dispatchEvent(new Event("input", { bubbles: true }));
}

function pasteInto(editor: Editor, text: string): void {
  const data = new DataTransfer();
  data.setData("text/plain", text);
  editor.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
}

/**
 * Replace the whole draft with `text` the way typing would, and say whether the
 * textbox now reads as `text`. First `insertText` (beforeinput and input, as a key
 * press makes), then a paste event, which ProseMirror and Quill both handle.
 */
export async function replaceDraft(editor: Editor, text: string): Promise<boolean> {
  if (isField(editor)) {
    setField(editor, text);
    await nextFrame();
    return sameDraft(readDraft(editor), text);
  }
  selectAll(editor);
  // Deprecated, but still the only way to edit as the user would (trusted beforeinput).
  // eslint-disable-next-line @typescript-eslint/no-deprecated
  editor.ownerDocument.execCommand("insertText", false, text);
  await nextFrame();
  if (sameDraft(readDraft(editor), text)) return true;
  selectAll(editor);
  pasteInto(editor, text);
  await nextFrame();
  return sameDraft(readDraft(editor), text);
}
