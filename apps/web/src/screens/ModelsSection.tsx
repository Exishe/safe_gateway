import { useEffect, useState } from "react";
import { Download, Info, ShieldCheck, Warning } from "@/components/icons";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { gateway, type ModelRowStatus, type ModelStatus } from "@/lib/api";

const POLL_MS = 1500;

const ROW_TEXT: Record<ModelRowStatus, string> = {
  installed: "Installed",
  missing: "Not downloaded",
  downloading: "Downloading",
  failed: "Failed",
};

type Load = { kind: "loading" } | { kind: "failed"; message: string } | { kind: "ok"; status: ModelStatus };

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function readStatus(): Promise<Load> {
  try {
    return { kind: "ok", status: await gateway.modelStatus() };
  } catch (error) {
    return { kind: "failed", message: messageOf(error) };
  }
}

async function pullStatus(): Promise<Load> {
  try {
    return { kind: "ok", status: await gateway.pullModels() };
  } catch (error) {
    return { kind: "failed", message: messageOf(error) };
  }
}

/** The language models, and the one button that downloads them (ADR 0029). */
export function ModelsSection() {
  const [load, setLoad] = useState<Load>({ kind: "loading" });

  useEffect(() => {
    void readStatus().then(setLoad);
  }, []);

  const running = load.kind === "ok" && load.status.state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      void readStatus().then(setLoad);
    }, POLL_MS);
    return () => {
      clearInterval(timer);
    };
  }, [running]);

  const start = () => {
    void pullStatus().then(setLoad);
  };

  if (load.kind === "loading") {
    return (
      <Alert role="status">
        <Info />
        <p>Reading which models are installed</p>
      </Alert>
    );
  }
  if (load.kind === "failed") {
    return (
      <Alert tone="seal" role="alert">
        <Warning />
        <p>{load.message}</p>
      </Alert>
    );
  }

  const { status } = load;
  const allInstalled = status.models.every((m) => m.status === "installed");
  return (
    <>
      <p>
        Detecting names and places needs language models. They are not shipped inside the app; they are downloaded once,
        about 900 MB in all.
      </p>
      <ul className="flex flex-col gap-1.5" aria-label="Models">
        {status.models.map((m) => (
          <li key={m.key} className="flex flex-wrap items-baseline gap-x-3">
            <span className="token">
              {m.name} {m.version}
            </span>
            <span className={m.status === "failed" ? "text-seal" : "text-ink-muted"}>{ROW_TEXT[m.status]}</span>
          </li>
        ))}
      </ul>
      {status.state === "failed" && status.error !== null ? (
        <Alert tone="seal" role="alert">
          <Warning />
          <p>The download stopped: {status.error} Nothing half-downloaded is kept. You can try again.</p>
        </Alert>
      ) : null}
      {running ? (
        <Alert role="status">
          <Info />
          <p>Downloading. This can take several minutes; you can keep using the other tabs.</p>
        </Alert>
      ) : null}
      {allInstalled ? (
        <Alert tone="ok" role="status">
          <ShieldCheck />
          <p>All models are installed.</p>
        </Alert>
      ) : (
        <div>
          <Button onClick={start} disabled={running}>
            <Download />
            {running ? "Downloading" : "Download models"}
          </Button>
        </div>
      )}
      <p className="text-ink-muted">
        {status.source_note ||
          "This is the one network call the gateway makes by itself, to GitHub and Hugging Face."}
      </p>
    </>
  );
}
