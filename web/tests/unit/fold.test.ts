import assert from "node:assert/strict";
import { test } from "node:test";
import { FOLD_AT, foldWords, splitFold } from "../../lib/fold";

const range = (n: number) => Array.from({ length: n }, (_, i) => i + 1);

test("a list of 10 rows or fewer does not fold", () => {
  assert.equal(FOLD_AT, 10);
  for (const n of [0, 1, 10]) {
    const { head, rest } = splitFold(range(n));
    assert.deepEqual(head, range(n));
    assert.deepEqual(rest, []);
  }
});

test("a longer list shows its first 10 rows and folds the rest, in order", () => {
  const { head, rest } = splitFold(range(25));
  assert.deepEqual(head, range(10));
  assert.deepEqual(rest, range(25).slice(10));
  const eleven = splitFold(range(11));
  assert.equal(eleven.head.length, 10);
  assert.deepEqual(eleven.rest, [11]);
});

test("the input is not changed, and another cut-off works", () => {
  const rows = range(5);
  const { head, rest } = splitFold(rows, 3);
  assert.deepEqual(head, [1, 2, 3]);
  assert.deepEqual(rest, [4, 5]);
  head.push(99);
  assert.deepEqual(rows, [1, 2, 3, 4, 5]);
});

test("the control says how many rows there are, and names its list for screen readers", () => {
  assert.deepEqual(foldWords(21), { more: "Show all 21", less: "Show the first 10 only", context: "" });
  assert.equal(foldWords(12, 10, "K list, 2026 week 3 (live)").context, " (K list, 2026 week 3 (live))");
});
