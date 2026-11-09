// GET /audit, read tolerantly. Written against ADR 0021 (row columns, VerifyResult)
// and ADR 0022 (the export document) while the route itself is being wired, so it
// accepts either's field names and never drops a row it cannot fully read.

export interface AuditRow {
  id: number | null;
  kind: "request" | "checkpoint" | "other";
  time: string | null;
  upstream: string | null;
  model: string | null;
  entities: [string, number][];
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
  verify: VerifyView | null;
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

function bool(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (value === 0 || value === 1) return value === 1;
  return null;
}

function counts(row: Json): [string, number][] {
  let raw: unknown = row.entity_counts;
  if (raw === undefined && typeof row.entity_counts_json === "string") {
    try {
      raw = JSON.parse(row.entity_counts_json);
    } catch {
      raw = undefined;
    }
  }
  if (!isRecord(raw)) return [];
  return Object.entries(raw)
    .filter((entry): entry is [string, number] => typeof entry[1] === "number")
    .sort(([a], [b]) => a.localeCompare(b));
}

function parseRow(row: Json): AuditRow {
  const kind = row.kind === "checkpoint" || row.kind === "request" ? row.kind : row.kind === undefined ? "request" : "other";
  return {
    id: int(row.id),
    kind,
    time: str(row.ts_utc) ?? str(row.time),
    upstream: str(row.upstream_host),
    model: str(row.model),
    entities: counts(row),
    status: int(row.status),
    local: bool(row.local),
    bytesOut: int(row.bytes_out),
    purgedCount: int(row.purged_count),
  };
}

function parseVerify(value: unknown): VerifyView | null {
  if (!isRecord(value) || typeof value.ok !== "boolean") return null;
  return {
    state: value.ok ? "intact" : "broken",
    rowsChecked: int(value.rows_checked),
    firstBadId: int(value.first_bad_id),
    reason: str(value.reason),
    detail: str(value.detail),
    headId: int(value.head_id),
  };
}

function rowsOf(body: unknown): unknown[] | null {
  if (Array.isArray(body)) return body as unknown[];
  if (!isRecord(body)) return null;
  for (const key of ["rows", "items", "entries"]) {
    const value = body[key];
    if (Array.isArray(value)) return value as unknown[];
  }
  return null;
}

export function parseAudit(body: unknown): AuditView {
  const rows = rowsOf(body);
  if (rows === null) throw new Error("The gateway's /audit answer is not an audit log.");
  const parsed = rows.filter(isRecord).map(parseRow);
  // Newest first: the question is usually "what left this machine just now".
  parsed.sort((a, b) => (b.id ?? 0) - (a.id ?? 0));
  const verify = isRecord(body) ? parseVerify(body.verify ?? body.verification ?? body.verify_result) : null;
  return { rows: parsed, verify };
}
