// Fails when web/db/schema.ts and the committed migrations in web/drizzle/ disagree, i.e.
// when someone changed the schema without running `npm run db:generate` (or edited a
// generated file by hand). How: copy web/drizzle/ to a temporary folder, run
// `drizzle-kit generate` against the copy, and compare. An up-to-date folder produces no new
// file and no change. Never connects to a database.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cpSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const web = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const committed = join(web, "drizzle");

function listing(root) {
  const out = new Map();
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) walk(path);
      else out.set(relative(root, path), createHash("sha256").update(readFileSync(path)).digest("hex"));
    }
  };
  walk(root);
  return out;
}

function check(tmp) {
  const copy = join(tmp, "drizzle");
  cpSync(committed, copy, { recursive: true });
  const config = join(tmp, "drizzle.check.config.mjs");
  writeFileSync(
    config,
    `export default ${JSON.stringify({
      dialect: "postgresql",
      schema: "./db/schema.ts",
      out: `./${relative(web, copy)}`,
    })};\n`,
  );
  let stdout;
  try {
    stdout = execFileSync(
      join(web, "node_modules", ".bin", "drizzle-kit"),
      ["generate", "--config", config],
      { cwd: web, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
    );
  } catch (err) {
    console.error(`drizzle-kit generate failed:\n${err.stdout ?? ""}${err.stderr ?? ""}`);
    return 1;
  }
  const before = listing(committed);
  const after = listing(copy);
  const added = [...after.keys()].filter((f) => !before.has(f));
  const changed = [...after.keys()].filter((f) => before.has(f) && before.get(f) !== after.get(f));
  const removed = [...before.keys()].filter((f) => !after.has(f));
  if (added.length || changed.length || removed.length) {
    console.error("web/db/schema.ts and web/drizzle/ disagree:");
    for (const f of added) console.error(`  new migration needed: ${f}`);
    for (const f of changed) console.error(`  would change: ${f}`);
    for (const f of removed) console.error(`  missing: ${f}`);
    console.error("Run `npm run db:generate` in web/ and commit the new files.");
    return 1;
  }
  // drizzle-kit can print an error and still exit 0: only its own "nothing to migrate" line
  // proves that it compared the schema with the snapshots.
  if (!/No schema changes/.test(stdout)) {
    console.error(`drizzle-kit did not confirm the comparison:\n${stdout}`);
    return 1;
  }
  console.log(`migrations match db/schema.ts (${before.size} files checked)`);
  return 0;
}

// The copy lives inside web/ (gitignored) so every path can be relative to web/: drizzle-kit
// prefixes "./" to `out` and cannot follow a schema path that leaves the working directory.
const tmp = mkdtempSync(join(web, ".drizzle-check-"));
let code = 1;
try {
  code = check(tmp);
} finally {
  rmSync(tmp, { recursive: true, force: true });
}
process.exit(code);
