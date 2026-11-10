import { describe, expect, it } from "vitest";
import { parseAudit } from "./audit";

// A row as `GET /audit` serves it (gateway/audit/sink.py, _shown): null columns are
// left out, `local` is a boolean and `entity_counts` an object.
const ROW = {
  id: 7,
  kind: "request",
  ts_utc: "2026-11-09T17:00:00.000000Z",
  upstream_host: "api.openai.test",
  model: "gpt-test",
  masked_payload_sha256: "a".repeat(64),
  bytes_out: 812,
  status: 200,
  row_hash: "b".repeat(64),
  local: false,
  entity_counts: { PERSON: 1, EMAIL: 2 },
};

const VERIFICATION = {
  ok: true,
  rows_checked: 7,
  first_bad_id: null,
  reason: null,
  detail: "7 rows verified",
  head_id: 7,
  head_hash: "b".repeat(64),
  purged_through_id: 0,
};

describe("parseAudit", () => {
  it("reads the rows, the next page and an intact verification", () => {
    const view = parseAudit({ rows: [ROW], next_before_id: null, verification: VERIFICATION });
    expect(view.rows).toEqual([
      {
        id: 7,
        kind: "request",
        time: "2026-11-09T17:00:00.000000Z",
        upstream: "api.openai.test",
        model: "gpt-test",
        entities: [["EMAIL", 2], ["PERSON", 1]],
        status: 200,
        local: false,
        bytesOut: 812,
        purgedCount: null,
      },
    ]);
    expect(view.nextBeforeId).toBeNull();
    expect(view.verify).toEqual({
      state: "intact",
      rowsChecked: 7,
      firstBadId: null,
      reason: null,
      detail: "7 rows verified",
      headId: 7,
    });
  });

  it("keeps the gateway's order, newest first, and the id of the next page", () => {
    const view = parseAudit({
      rows: [{ ...ROW, id: 9 }, { ...ROW, id: 8 }],
      next_before_id: 8,
      verification: VERIFICATION,
    });
    expect(view.rows.map((r) => r.id)).toEqual([9, 8]);
    expect(view.nextBeforeId).toBe(8);
  });

  it("reports a broken chain with the first bad entry and the reason", () => {
    const verification = { ...VERIFICATION, ok: false, rows_checked: 4, first_bad_id: 5, reason: "edited", detail: "row 5 changed" };
    expect(parseAudit({ rows: [], next_before_id: null, verification }).verify).toEqual({
      state: "broken",
      rowsChecked: 4,
      firstBadId: 5,
      reason: "edited",
      detail: "row 5 changed",
      headId: 7,
    });
  });

  it("marks counts an edited entry made unreadable instead of showing none", () => {
    const view = parseAudit({ rows: [{ ...ROW, entity_counts: null }], next_before_id: null, verification: VERIFICATION });
    expect(view.rows[0]?.entities).toBeNull();
  });

  it("shows checkpoint rows as retention, with what they purged", () => {
    const checkpoint = { id: 3, kind: "checkpoint", ts_utc: ROW.ts_utc, purged_count: 120, purged_through_id: 2 };
    const view = parseAudit({ rows: [checkpoint], next_before_id: null, verification: VERIFICATION });
    expect(view.rows[0]?.kind).toBe("checkpoint");
    expect(view.rows[0]?.purgedCount).toBe(120);
  });

  it("keeps a row with odd cells instead of dropping it", () => {
    // A tampered cell is served as its repr string; verification reports the row.
    const view = parseAudit({ rows: [{ ...ROW, status: "b'200'" }], next_before_id: null, verification: VERIFICATION });
    expect(view.rows).toHaveLength(1);
    expect(view.rows[0]?.status).toBeNull();
  });

  it("refuses anything without rows and a verification result", () => {
    expect(() => parseAudit("nope")).toThrow(/audit/);
    expect(() => parseAudit({ rows: [] })).toThrow(/audit/);
    expect(() => parseAudit({ rows: [], verify: VERIFICATION })).toThrow(/audit/);
  });
});
