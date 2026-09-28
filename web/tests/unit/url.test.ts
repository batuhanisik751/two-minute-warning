import assert from "node:assert/strict";
import { test } from "node:test";
import { LOCAL_DEFAULT_URL, databaseUrl, describe, isLocalUrl, withDatabase } from "../../db/url";

test("DATABASE_URL wins, then TWM_LOCAL_DATABASE_URL, then the local default", () => {
  assert.equal(databaseUrl({ DATABASE_URL: "postgresql://a@db.example/x", TWM_LOCAL_DATABASE_URL: "postgres://b@127.0.0.1/y" }), "postgresql://a@db.example/x");
  assert.equal(databaseUrl({ TWM_LOCAL_DATABASE_URL: "postgres://b@127.0.0.1/y" }), "postgres://b@127.0.0.1/y");
  assert.equal(databaseUrl({ DATABASE_URL: "  " }), LOCAL_DEFAULT_URL);
  assert.equal(databaseUrl({}), LOCAL_DEFAULT_URL);
});

test("only this computer counts as local", () => {
  assert.ok(isLocalUrl("postgres://u:p@127.0.0.1:5434/twm"));
  assert.ok(isLocalUrl("postgres://u:p@localhost/twm"));
  assert.ok(isLocalUrl("postgres://u:p@[::1]:5432/twm"));
  assert.ok(!isLocalUrl("postgresql://u:p@ep-x-pooler.us-east-1.aws.neon.tech/db?sslmode=verify-full"));
  assert.ok(!isLocalUrl("not a url"));
});

test("describe() never includes the user name or password", () => {
  const d = describe("postgresql://twm_web:s3cret@ep-x.us-east-1.aws.neon.tech/neondb?sslmode=verify-full");
  assert.equal(d, "database neondb on ep-x.us-east-1.aws.neon.tech");
  assert.ok(!d.includes("s3cret") && !d.includes("twm_web"));
  assert.equal(describe("::"), "an unparseable connection string");
});

test("withDatabase swaps the database only", () => {
  assert.equal(withDatabase("postgres://u:p@127.0.0.1:5434/twm", "twm_web_test"), "postgres://u:p@127.0.0.1:5434/twm_web_test");
});
