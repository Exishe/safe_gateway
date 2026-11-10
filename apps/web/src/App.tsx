import { useCallback, useEffect, useState } from "react";
import { Mark } from "./components/icons";
import { ThemeSwitch } from "./components/ThemeSwitch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/ui/tabs";
import { type Health, gateway } from "./lib/api";
import { providerName } from "./lib/providers";
import { AuditScreen } from "./screens/AuditScreen";
import { MaskScreen } from "./screens/MaskScreen";
import { SettingsScreen } from "./screens/SettingsScreen";

export type GatewayState =
  | { kind: "loading" }
  | { kind: "ready"; health: Health; models: string[] }
  | { kind: "down"; message: string };

function StatusLine({ state, onRetry }: { state: GatewayState; onRetry: () => void }) {
  if (state.kind === "loading") {
    return <p className="text-sm text-ink-muted">Connecting to the gateway</p>;
  }
  if (state.kind === "down") {
    return (
      <p className="flex flex-wrap items-center gap-x-2 text-sm">
        <span className="inline-block size-2 rounded-full bg-seal" aria-hidden="true" />
        <span>Gateway not answering.</span>
        <button type="button" onClick={onRetry} className="font-medium text-binding underline underline-offset-2">
          Try again
        </button>
      </p>
    );
  }
  const { health } = state;
  return (
    <p className="flex items-center gap-2 text-sm text-ink-muted">
      <span className={`inline-block size-2 rounded-full ${health.local ? "bg-ok" : "bg-binding"}`} aria-hidden="true" />
      <span>
        <span className="text-ink">{providerName(health.provider)}</span>
        {health.local ? ", on this computer" : ", in the cloud"}
      </span>
    </p>
  );
}

async function readGateway(): Promise<GatewayState> {
  try {
    const [health, models] = await Promise.all([gateway.health(), gateway.models()]);
    return { kind: "ready", health, models };
  } catch (error) {
    return { kind: "down", message: error instanceof Error ? error.message : String(error) };
  }
}

export function App() {
  const [state, setState] = useState<GatewayState>({ kind: "loading" });
  const [tab, setTab] = useState("mask");

  const connect = useCallback(() => {
    void readGateway().then(setState);
  }, []);

  useEffect(connect, [connect]);

  return (
    <div className="mx-auto flex min-h-dvh max-w-[90rem] flex-col px-3 pb-10 sm:px-6 lg:px-10">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:bg-surface focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 pb-5 pt-6 sm:pt-8">
        <div className="flex items-center gap-3">
          <Mark className="size-9 text-binding" />
          <div>
            <p className="font-display text-[1.6rem] leading-none tracking-tight">SafeGuard</p>
            <p className="mt-1 text-[0.8125rem] text-ink-muted">Personal data is pseudonymised on this computer first</p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <StatusLine state={state} onRetry={connect} />
          <ThemeSwitch />
        </div>
      </header>

      <Tabs value={tab} onValueChange={setTab} className="flex flex-1 flex-col">
        <nav aria-label="Sections">
          <TabsList>
            <TabsTrigger value="mask">Mask &amp; send</TabsTrigger>
            <TabsTrigger value="audit">Audit log</TabsTrigger>
            <TabsTrigger value="settings">Settings</TabsTrigger>
          </TabsList>
        </nav>
        <main id="main" className="flex flex-1 flex-col">
          <TabsContent value="mask" className="flex-1 p-4 sm:p-7" forceMount hidden={tab !== "mask"}>
            <MaskScreen state={state} />
          </TabsContent>
          <TabsContent value="audit" className="flex-1 p-4 sm:p-7">
            <AuditScreen />
          </TabsContent>
          <TabsContent value="settings" className="flex-1 p-4 sm:p-7">
            <SettingsScreen state={state} onRefresh={connect} />
          </TabsContent>
        </main>
      </Tabs>
    </div>
  );
}
