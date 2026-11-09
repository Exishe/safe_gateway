import { describe, expect, it } from "vitest";
import { parseAudit } from "./audit";

const ROW = {
  id: 7,
  kind: "request",
  ts_utc: "2026-11-09T17:00:00.000000Z",
  session_id: "",
  upstream_host: "api.openai.test",
  model: "gpt-test",
  entity_counts_json: '{"EMAIL":2,"PERSON":1}',
  masked_payload_sha256: "a".repeat(64),
  bytes_out: 812,
  status: 200,
  local: 0,
  prev_hash: "0".repeat(64),
  row_hash: "b".repeat(64),
  hmac: "c".repeat(64),
};

const VERIFY_OK = { ok: true, rows_checked: 7, first_bad_id: null, reason: null, detail: "intact", head_id: 7 };

describe("parseAudit", () => {
  it("reads rows as stored (ADR 0021) and a passing verify result", () => {
    const view = parseAudit({ rows: [ROW], verify: VERIFY_OK });
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
    expect(view.verify).toEqual({ state: "intact", rowsChecked: 7, firstBadId: null, reason: null, detail: "intact", headId: 7 });
  });

  it("accepts the export document's field names and counts as an object", () => {
    const rest = Object.fromEntries(Object.entries(ROW).filter(([key]) => key !== "entity_counts_json"));
    const row = { ...rest, local: true, entity_counts: { PHONE: 1 } };
    const view = parseAudit({ rows: [row], verification: { ...VERIFY_OK, ok: true } });
    expect(view.rows[0]?.local).toBe(true);
    expect(view.rows[0]?.entities).toEqual([["PHONE", 1]]);
    expect(view.verify?.state).toBe("intact");
  });

  it("reports a broken chain with the first bad row and the reason", () => {
    const verify = { ok: false, rows_checked: 4, first_bad_id: 5, reason: "edited", detail: "row 5 changed", head_id: 9 };
    expect(parseAudit({ rows: [], verify }).verify).toEqual({
      state: "broken",
      rowsChecked: 4,
      firstBadId: 5,
      reason: "edited",
      detail: "row 5 changed",
      headId: 9,
    });
  });

  it("shows checkpoint rows as retention, with what they purged", () => {
    const checkpoint = { id: 3, kind: "checkpoint", ts_utc: ROW.ts_utc, purged_count: 120 };
    const view = parseAudit({ rows: [checkpoint] });
    expect(view.rows[0]?.kind).toBe("checkpoint");
    expect(view.rows[0]?.purgedCount).toBe(120);
    expect(view.verify).toBeNull();
  });

  it("keeps a row with odd fields instead of dropping it, and survives bad counts JSON", () => {
    const view = parseAudit({ rows: [{ id: "x", entity_counts_json: "{nope", status: "200" }] });
    expect(view.rows).toHaveLength(1);
    expect(view.rows[0]?.entities).toEqual([]);
    expect(view.rows[0]?.status).toBeNull();
  });

  it("accepts a bare list of rows and newest-first ordering", () => {
    const view = parseAudit([{ ...ROW, id: 1 }, { ...ROW, id: 2 }]);
    expect(view.rows.map((r) => r.id)).toEqual([2, 1]);
  });

  it("refuses something that is not an audit response", () => {
    expect(() => parseAudit("nope")).toThrow(/audit/);
  });
});
