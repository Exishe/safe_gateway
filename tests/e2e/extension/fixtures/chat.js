// A stand-in for a chat site's front end, shared by the fixture pages (ADR 0026).
// It does what the real sites do as far as the extension can tell: a textbox it reads
// on Enter or on its send button, the user's message shown in the thread, and a reply
// streamed in small chunks, so placeholders arrive split across text nodes. It records
// what it "sent" in window.__sent; nothing leaves the page.
(() => {
  const config = document.body.dataset;
  const editor = document.querySelector(config.editor);
  const button = document.querySelector(config.send);
  const thread = document.querySelector("#thread");
  window.__sent = [];
  window.__replies = 0;

  const isField = editor instanceof HTMLTextAreaElement;
  const read = () => (isField ? editor.value : editor.innerText).trim();
  const clear = () => {
    if (isField) editor.value = "";
    else editor.replaceChildren(document.createElement("p"));
  };

  function el(spec) {
    const [tag, ...classes] = spec.split(".");
    const node = document.createElement(tag);
    if (classes.length) node.className = classes.join(" ");
    return node;
  }

  function message(role, text) {
    const outer = el(role === "user" ? config.userTag : config.botTag);
    for (const [k, v] of Object.entries(JSON.parse(config[role === "user" ? "userAttrs" : "botAttrs"]))) {
      outer.setAttribute(k, v);
    }
    const inner = el(role === "user" ? "div.user-text" : config.botInner);
    if (text !== undefined) inner.textContent = text;
    outer.append(inner);
    thread.append(outer);
    return inner;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Appends text in 4-character pieces, each its own text node, as a streaming
  // renderer does; a placeholder lands across several nodes.
  async function stream(parent, text) {
    for (let i = 0; i < text.length; i += 4) {
      parent.append(document.createTextNode(text.slice(i, i + 4)));
      await sleep(8);
    }
  }

  function paragraphs(tokens) {
    const [first = "[PERSON_99]", second = first] = tokens;
    return [
      { text: `Here is a draft for ${first}. I will copy ${second} on it.` },
      { text: "Reference: ", link: { href: `https://example.invalid/r?d=${second}`, text: second } },
      { text: `Raw link: https://example.invalid/t?d=${first} end.` },
      { text: "I also mentioned [PERSON_99], whom I do not know." },
    ];
  }

  async function reply(sent) {
    const tokens = [...new Set(sent.match(/\[[A-Z_]+_\d+\]/g) ?? [])];
    const body = message("bot");
    const parts = paragraphs(tokens);
    for (const part of parts) {
      const p = document.createElement("p");
      body.append(p);
      await stream(p, part.text);
      if (part.link) {
        const a = document.createElement("a");
        a.href = part.link.href;
        p.append(a);
        await stream(a, part.link.text);
      }
    }
    if (config.rerender === "true") {
      // A framework's final render: the whole message rebuilt from its own state.
      await sleep(50);
      const fresh = parts.map((part) => {
        const p = document.createElement("p");
        p.textContent = part.text;
        if (part.link) {
          const a = document.createElement("a");
          a.href = part.link.href;
          a.textContent = part.link.text;
          p.append(a);
        }
        return p;
      });
      body.replaceChildren(...fresh);
    }
    window.__replies += 1;
  }

  // Real sites clear the box and render the bubble a moment after they send (state
  // updates, network); `data-defer` is that delay in ms. The text is "sent" at once.
  const defer = Number(config.defer ?? 0);

  function send() {
    const text = read();
    if (!text) return;
    window.__sent.push(text);
    const finish = () => {
      clear();
      message("user", text);
      void reply(text);
    };
    if (defer > 0) setTimeout(finish, defer);
    else finish();
  }

  // The mechanisms the fixtures vary (ADR 0026 amendment 2026-11-10): which event a
  // site sends on. Default: Enter on keydown at the textbox, and a click.
  const plainEnter = (event) => event.key === "Enter" && !event.shiftKey;
  const enterOn = config.enterOn ?? "keydown";
  if (enterOn === "keydown") {
    editor.addEventListener("keydown", (event) => {
      if (plainEnter(event)) {
        event.preventDefault();
        send();
      }
    });
  } else if (enterOn === "keyup") {
    // Sends when the key is released; the keydown only types a newline.
    editor.addEventListener("keyup", (event) => {
      if (plainEnter(event)) send();
    });
  } else if (enterOn === "window-capture") {
    // A site's own capture-phase listener on window, sending synchronously.
    window.addEventListener(
      "keydown",
      (event) => {
        if (plainEnter(event) && event.target instanceof Element && event.target.closest(config.editor)) {
          event.preventDefault();
          send();
        }
      },
      true,
    );
  }

  if (config.sendOn === "pointerdown") {
    // Sends on the press, not the release; the click that follows is ignored.
    let pressed = false;
    button.addEventListener("pointerdown", () => {
      pressed = true;
      send();
    });
    button.addEventListener("click", () => {
      if (pressed) pressed = false;
      else send();
    });
  } else {
    button.addEventListener("click", send);
  }

  // A send path no listener can see: the page puts a user message in the thread
  // itself (dictation, an "edit message" box, a script). Only for the fail-safe tests.
  window.__bypass = (text) => {
    message("user", text);
  };
})();
