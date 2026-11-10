// GET /audit as the gateway serves it (gateway/proxy/app.py, `audit` route, and
// gateway/audit/sink.py): rows newest first with null columns left out, the id to
// ask for the next older page, and the VerifyResult of ADR 0021 point 10.
// A cell that does not have its expected type (a tampered row is served as repr
// text) is shown as unknown; the row is never dropped.

export interface AuditRow {
  id: number | null;
  kind: "request" | "checkpoint" | "other";
  time: string | null;
  upstream: string | null;
  model: string | null;
  entities: [string, number][] | null; // null: the stored counts could not be read
  status: number | null;
  local: boolean | null;
  bytesOut: number | null;
  purgedCount: number | null;
}

export interface VerifyView {
  state: "intact" | "broken";
  rowsChecked: number | null;
  firstBadId: number | null;
  reason: string | null;
  detail: string | null;
  headId: number | null;
}

export interface AuditView {
  rows: AuditRow[];
  nextBeforeId: number | null;
  verify: VerifyView;
}

type Json = Record<string, unknown>;

function isRecord(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function int(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function counts(value: unknown): [string, number][] | null {
  if (!isRecord(value)) return null;
  return Object.entries(value)
    .filter((entry): entry is [string, number] => typeof entry[1] === "number")
    .sort(([a], [b]) => a.localeCompare(b));
}

function parseRow(row: Json): AuditRow {
  const kind = row.kind === "request" || row.kind === "checkpoint" ? row.kind : "other";
  return {
    id: int(row.id),
    kind,
    time: str(row.ts_utc),
    upstream: str(row.upstream_host),
    model: str(row.model),
    entities: counts(row.entity_counts),
    status: int(row.status),
    local: typeof row.local === "boolean" ? row.local : null,
    bytesOut: int(row.bytes_out),
    purgedCount: int(row.purged_count),
  };
}

function parseVerification(value: Json): VerifyView {
  return {
    state: value.ok === true ? "intact" : "broken",
    rowsChecked: int(value.rows_checked),
    firstBadId: int(value.first_bad_id),
    reason: str(value.reason),
    detail: str(value.detail),
    headId: int(value.head_id),
  };
}

export function parseAudit(body: unknown): AuditView {
  if (!isRecord(body) || !Array.isArray(body.rows) || !isRecord(body.verification)) {
    throw new Error("The gateway's /audit answer is not an audit log.");
  }
  return {
    rows: (body.rows as unknown[]).filter(isRecord).map(parseRow),
    nextBeforeId: int(body.next_before_id),
    verify: parseVerification(body.verification),
  };
}
