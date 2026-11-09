import { useCallback, useEffect, useState } from "react";
import { CopyCommand } from "@/components/CopyCommand";
import { Info, Refresh, ShieldBroken, ShieldCheck, Warning } from "@/components/icons";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { type AuditResult, gateway } from "@/lib/api";
import type { AuditRow, VerifyView } from "@/lib/audit";
import { familyOf, labelOf } from "@/lib/entities";

type Load = { kind: "loading" } | { kind: "failed"; message: string } | AuditResult;

const TIME = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" });

function localTime(utc: string | null): string {
  if (utc === null) return "Unknown time";
  const date = new Date(utc);
  return Number.isNaN(date.getTime()) ? utc : TIME.format(date);
}

const REASONS: Record<string, string> = {
  edited: "an entry was changed after it was written",
  deleted: "entries are missing",
  reordered: "entries were moved",
  inserted: "an entry was added that the chain does not account for",
  "bad HMAC": "an entry was not written with this computer's key",
  bad_hmac: "an entry was not written with this computer's key",
};

function VerifyBanner({ verify }: { verify: VerifyView | null }) {
  if (verify === null) {
    return (
      <Alert tone="neutral">
        <Info />
        <p>The gateway did not report whether the chain verifies. Run the check on the command line to be sure.</p>
      </Alert>
    );
  }
  if (verify.state === "intact") {
    return (
      <Alert tone="ok" role="status">
        <ShieldCheck />
        <div>
          <p className="font-semibold">Chain intact</p>
          <p className="text-sm">
            {verify.rowsChecked ?? "All"} {verify.rowsChecked === 1 ? "entry" : "entries"} checked
            {verify.headId !== null ? `, newest entry ${verify.headId}` : ""}. No change, deletion or reordering was found.
            This is tamper-evident, not tamper-proof: someone holding this computer's key could rewrite it.
          </p>
        </div>
      </Alert>
    );
  }
  const why = verify.reason === null ? null : (REASONS[verify.reason] ?? verify.reason);
  return (
    <Alert tone="seal" role="alert" className="border-l-[6px]">
      <ShieldBroken />
      <div>
        <p className="text-lg font-semibold">
          Chain broken{verify.firstBadId !== null ? ` at entry ${verify.firstBadId}` : ""}
        </p>
        <p className="mt-1">
          {why !== null ? `The check found that ${why}. ` : ""}
          Entries from {verify.firstBadId ?? "that point"} on cannot be relied on as evidence.
          {verify.detail !== null ? ` Detail: ${verify.detail}.` : ""}
        </p>
        <p className="mt-2 text-sm">
          Keep the log file as it is. Export it for your records, then move it aside so the gateway can start a new log.
        </p>
      </div>
    </Alert>
  );
}

function Entities({ entities }: { entities: [string, number][] }) {
  if (entities.length === 0) return <span className="text-ink-muted">None</span>;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {entities.map(([type, count]) => (
        <li key={type} className={`fam-${familyOf(type)} rounded bg-(--fam-wash) px-1.5 py-0.5 text-[0.8125rem]`}>
          {labelOf(type)} <span className="font-semibold">{count}</span>
        </li>
      ))}
    </ul>
  );
}

function Where({ local }: { local: boolean | null }) {
  if (local === null) return <span className="text-ink-muted">Unknown</span>;
  return local ? <span>This computer</span> : <span className="font-medium">Cloud</span>;
}

function Status({ status }: { status: number | null }) {
  if (status === null) return <span className="text-ink-muted">Unknown</span>;
  if (status === 0) return <span className="text-seal">No answer</span>;
  return <span className={status >= 400 ? "text-seal" : ""}>{status}</span>;
}

function Rows({ rows }: { rows: AuditRow[] }) {
  if (rows.length === 0) {
    return <p className="py-6 text-ink-muted">No requests have been recorded yet. Send one from Mask &amp; send.</p>;
  }
  return (
    <>
      <div className="hidden md:block">
        <Table>
          <caption className="sr-only">Requests the gateway sent, newest first</caption>
          <TableHeader>
            <TableRow>
              <TableHead>Time</TableHead>
              <TableHead>Upstream</TableHead>
              <TableHead>Model</TableHead>
              <TableHead>Masked values</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Where</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row, i) =>
              row.kind === "checkpoint" ? (
                <TableRow key={row.id ?? `c${i}`} className="bg-sunken">
                  <TableCell>{localTime(row.time)}</TableCell>
                  <TableCell colSpan={5} className="text-ink-muted">
                    Retention: {row.purgedCount ?? "older"} older {row.purgedCount === 1 ? "entry was" : "entries were"} removed
                    and the chain was carried forward.
                  </TableCell>
                </TableRow>
              ) : (
                <TableRow key={row.id ?? `r${i}`}>
                  <TableCell className="whitespace-nowrap" title={row.time ?? undefined}>
                    {localTime(row.time)}
                  </TableCell>
                  <TableCell className="token break-all">{row.upstream ?? "Unknown"}</TableCell>
                  <TableCell className="token break-all">{row.model ?? "Unknown"}</TableCell>
                  <TableCell>
                    <Entities entities={row.entities} />
                  </TableCell>
                  <TableCell>
                    <Status status={row.status} />
                  </TableCell>
                  <TableCell>
                    <Where local={row.local} />
                  </TableCell>
                </TableRow>
              ),
            )}
          </TableBody>
        </Table>
      </div>
      <ol className="md:hidden" aria-label="Requests the gateway sent, newest first">
        {rows.map((row, i) => (
          <li key={row.id ?? i} className="border-b border-rule py-3 last:border-b-0">
            <p className="font-medium">{localTime(row.time)}</p>
            {row.kind === "checkpoint" ? (
              <p className="text-sm text-ink-muted">Retention: {row.purgedCount ?? "older"} older entries removed.</p>
            ) : (
              <dl className="mt-1 grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
                <dt className="text-ink-muted">Upstream</dt>
                <dd className="token break-all">{row.upstream ?? "Unknown"}</dd>
                <dt className="text-ink-muted">Model</dt>
                <dd className="token break-all">{row.model ?? "Unknown"}</dd>
                <dt className="text-ink-muted">Masked</dt>
                <dd>
                  <Entities entities={row.entities} />
                </dd>
                <dt className="text-ink-muted">Status</dt>
                <dd>
                  <Status status={row.status} />
                </dd>
                <dt className="text-ink-muted">Where</dt>
                <dd>
                  <Where local={row.local} />
                </dd>
              </dl>
            )}
          </li>
        ))}
      </ol>
    </>
  );
}

async function readAudit(): Promise<Load> {
  try {
    return await gateway.audit();
  } catch (error) {
    return { kind: "failed", message: error instanceof Error ? error.message : String(error) };
  }
}

export function AuditScreen() {
  const [load, setLoad] = useState<Load>({ kind: "loading" });

  // The audit tab mounts when it is opened, so this reads the log each time it is shown.
  useEffect(() => {
    void readAudit().then(setLoad);
  }, []);

  const refresh = useCallback(() => {
    setLoad({ kind: "loading" });
    void readAudit().then(setLoad);
  }, []);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-display text-2xl sm:text-[1.75rem]">Audit log</h2>
          <p className="mt-1 max-w-[64ch] text-[0.9375rem] text-ink-muted">
            One entry for each request the gateway sent to a model: when, where to, and how many values of each kind were
            masked. It never holds the text itself.
          </p>
        </div>
        <Button variant="outline" onClick={refresh} disabled={load.kind === "loading"}>
          <Refresh />
          Check again
        </Button>
      </div>

      {load.kind === "loading" && <p className="text-ink-muted" role="status">Reading the audit log</p>}

      {load.kind === "failed" && (
        <Alert tone="seal" role="alert">
          <Warning />
          <p>{load.message}</p>
        </Alert>
      )}

      {load.kind === "unavailable" && (
        <div className="flex flex-col gap-4">
          <Alert tone="neutral" role="status">
            <Info />
            <div>
              <p className="font-semibold">This gateway does not serve the audit log yet</p>
              <p className="text-sm">The gateway said: {load.message}</p>
            </div>
          </Alert>
          <p className="text-[0.9375rem]">Check the chain and export it on the command line:</p>
          <CopyCommand label="the verify command" text="uv run safeguard audit verify" />
          <CopyCommand label="the export command" text="uv run safeguard audit export -o audit.json --pdf" />
        </div>
      )}

      {load.kind === "ok" && (
        <>
          <VerifyBanner verify={load.view.verify} />
          <Rows rows={load.view.rows} />
        </>
      )}
    </div>
  );
}
