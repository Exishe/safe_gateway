// Where each site keeps its textbox, its send button and its messages. These are the
// selectors the sites used in late 2026; they change without notice, which is the
// extension's main limit (ADR 0026). Each list goes from most to least specific, and
// the content script falls back to the whole page for rehydration.

export interface Site {
  name: string;
  /** The draft textbox: contenteditable or a textarea. */
  editor: string;
  /** The button that sends the draft. */
  send: string;
  /** A message, user or assistant, to rehydrate as one unit. */
  message: string;
  /** A message the user sent: a new one that SafeGuard did not mask raises the alarm. */
  user: string;
}

const CHATGPT: Site = {
  name: "ChatGPT",
  editor: '#prompt-textarea, div.ProseMirror[contenteditable="true"], form textarea',
  send: 'button[data-testid="send-button"], button#composer-submit-button, button[aria-label="Send prompt"]',
  message: "[data-message-author-role]",
  user: '[data-message-author-role="user"]',
};

const CLAUDE: Site = {
  name: "Claude",
  editor: 'div.ProseMirror[contenteditable="true"], [contenteditable="true"][role="textbox"], fieldset textarea',
  send: 'button[aria-label="Send message"], button[aria-label="Send Message"], button[type="submit"]',
  message: '[data-testid="user-message"], .font-claude-response, .font-claude-message, [data-is-streaming]',
  user: '[data-testid="user-message"]',
};

const GEMINI: Site = {
  name: "Gemini",
  editor: 'rich-textarea .ql-editor[contenteditable="true"], div.ql-editor[contenteditable="true"]',
  send: 'button.send-button, button[aria-label="Send message"]',
  message: "user-query, model-response, message-content",
  user: "user-query",
};

const SITES: Record<string, Site> = {
  "chatgpt.com": CHATGPT,
  "chat.openai.com": CHATGPT,
  "claude.ai": CLAUDE,
  "gemini.google.com": GEMINI,
};

export function siteFor(hostname: string): Site | null {
  return Object.hasOwn(SITES, hostname) ? (SITES[hostname] ?? null) : null;
}
