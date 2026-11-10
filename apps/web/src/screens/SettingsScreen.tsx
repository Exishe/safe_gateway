import type { GatewayState } from "@/App";
import { CopyCommand } from "@/components/CopyCommand";
import { Info, Refresh, Warning } from "@/components/icons";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ENTITY_TYPES } from "@/lib/entities";
import { keyringCommand, needsKey, providerName } from "@/lib/providers";

const EXAMPLE = `# ~/.safeguard/config.toml (or $SAFEGUARD_HOME/config.toml)
# API keys never go in this file; they live in the system keychain.

provider = "openai"            # openai, azure_openai, anthropic or ollama
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"           # the gateway uses this model for every request
gazetteer = "~/clients.csv"     # names and codenames to always mask
retention_days = 30             # audit entries older than this can be purged

[thresholds]                    # 0 to 1 per type; higher means fewer, surer detections
PERSON = 0.6
HEALTH_CONDITION = 0.5

[proxy]
max_body_bytes = 4194304
allow_dev_ui = false            # true lets the Vite dev server (port 5173) call the gateway
`;

const GAZETTEER = `value,entity_type
Project Bluebird,CODENAME
Northwind Holdings,ORG`;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="grid gap-3 border-t border-rule pt-6 md:grid-cols-[14rem_minmax(0,1fr)] md:gap-10">
      <h3 className="font-display text-xl">{title}</h3>
      <div className="flex min-w-0 max-w-[68ch] flex-col gap-3 text-[0.9375rem]">{children}</div>
    </section>
  );
}

function Fact({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-ink-muted">{term}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}

export function SettingsScreen({ state, onRefresh }: { state: GatewayState; onRefresh: () => void }) {
  const provider = state.kind === "ready" ? state.health.provider : null;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="font-display text-2xl sm:text-[1.75rem]">Settings</h2>
        <p className="mt-1 max-w-[64ch] text-[0.9375rem] text-ink-muted">
          This page shows what the gateway is using. Settings are changed in its configuration file, not here, so a web page
          can never change where your data goes.
        </p>
      </div>

      <Section title="Gateway now">
        {state.kind === "ready" ? (
          <dl className="grid grid-cols-[8.5rem_minmax(0,1fr)] gap-x-4 gap-y-2">
            <Fact term="Provider">{providerName(state.health.provider)}</Fact>
            <Fact term="Requests go to">
              {state.health.local ? "A model on this computer" : "The provider's cloud service"}
            </Fact>
            <Fact term="Model">
              {state.models[0] !== undefined ? (
                <span className="token">{state.models[0]}</span>
              ) : (
                "Not set; named on each request"
              )}
            </Fact>
            <Fact term="Address">
              <span className="token">127.0.0.1:8787</span>, reachable from this computer only
            </Fact>
            <Fact term="Audit log">
              {state.health.audit === false ? (
                <span className="text-seal">Off: started with --no-audit, so requests leave no record</span>
              ) : (
                "One tamper-evident entry for each request sent to a model"
              )}
            </Fact>
            <Fact term="Version">
              <span className="token">{state.health.version}</span>
            </Fact>
          </dl>
        ) : (
          <Alert tone={state.kind === "down" ? "seal" : "neutral"} role="status">
            {state.kind === "down" ? <Warning /> : <Info />}
            <p>{state.kind === "down" ? state.message : "Connecting to the gateway"}</p>
          </Alert>
        )}
        <div>
          <Button variant="outline" size="sm" onClick={onRefresh}>
            <Refresh />
            Read again
          </Button>
        </div>
      </Section>

      <Section title="API key">
        {provider !== null && !needsKey(provider) ? (
          <p>Ollama runs on this computer and needs no key.</p>
        ) : (
          <>
            <p>
              The key stays in your system keychain and is never typed into this page. Store or replace it with this
              command, which asks for the key without showing it:
            </p>
            <CopyCommand label="the keyring command" text={keyringCommand(provider ?? "<provider>")} />
            <p className="text-ink-muted">Then restart the gateway.</p>
          </>
        )}
      </Section>

      <Section title="Change settings">
        <p>
          Edit <span className="token">~/.safeguard/config.toml</span>, then restart{" "}
          <span className="token">uv run safeguard serve</span>. Fields you leave out keep their defaults; with no file at
          all, the gateway uses Ollama on this computer.
        </p>
        <CopyCommand label="an example config.toml" text={EXAMPLE} block />
      </Section>

      <Section title="Detection thresholds">
        <p>
          Each detector gives a confidence from 0 to 1. A value is masked when its confidence reaches the threshold for its
          type. Lower a threshold to catch more, at the cost of masking words that are not personal data; raise it for
          the opposite. Use these type names under <span className="token">[thresholds]</span>:
        </p>
        <ul className="flex flex-wrap gap-1.5" aria-label="Entity type names">
          {ENTITY_TYPES.filter((t) => t !== "LITERAL").map((type) => (
            <li key={type} className="token rounded bg-sunken px-1.5 py-0.5 text-[0.8125rem]">
              {type}
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Names to always mask">
        <p>
          A gazetteer is a CSV file of client names, matter codenames and other values that must always be masked. Point{" "}
          <span className="token">gazetteer</span> at it in config.toml.
        </p>
        <CopyCommand label="an example gazetteer" text={GAZETTEER} block />
      </Section>
    </div>
  );
}
