// Provider ids as gateway/cloud/providers.py names them.
const NAMES: Record<string, string> = {
  openai: "OpenAI",
  azure_openai: "Azure OpenAI",
  anthropic: "Anthropic",
  ollama: "Ollama",
};

export function providerName(id: string): string {
  return NAMES[id] ?? id;
}

export function needsKey(id: string): boolean {
  return id !== "ollama";
}

export function keyringCommand(id: string): string {
  return `uv run keyring set safeguard-gateway api-key:${id}`;
}
