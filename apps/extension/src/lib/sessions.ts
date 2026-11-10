// Each tab's gateway session and its placeholder map, in chrome.storage.session
// (ADR 0002 point 2): memory only, cleared when the browser closes, and readable by
// the extension's own pages and worker only (content scripts get a copy by message).

export interface StorageArea {
  get(key: string): Promise<Record<string, unknown>>;
  set(items: Record<string, unknown>): Promise<void>;
  remove(key: string): Promise<void>;
}

export interface TabSession {
  sessionId: string;
  mapping: Record<string, string>;
  entities: Record<string, number>;
}

function isTabSession(value: unknown): value is TabSession {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.sessionId === "string" && typeof v.mapping === "object" && typeof v.entities === "object";
}

export function countOf(entities: Readonly<Record<string, number>>): number {
  return Object.values(entities).reduce((sum, n) => sum + n, 0);
}

export class TabSessions {
  private readonly area: StorageArea;

  constructor(area: StorageArea) {
    this.area = area;
  }

  private static key(tabId: number): string {
    return `tab:${tabId}`;
  }

  async get(tabId: number): Promise<TabSession | null> {
    const key = TabSessions.key(tabId);
    const value = (await this.area.get(key))[key];
    return isTabSession(value) ? value : null;
  }

  async put(tabId: number, session: TabSession): Promise<void> {
    await this.area.set({ [TabSessions.key(tabId)]: session });
  }

  async drop(tabId: number): Promise<void> {
    await this.area.remove(TabSessions.key(tabId));
  }
}
