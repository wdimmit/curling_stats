/* Build the two frontend bundles, and stamp what produced them.
 *
 * Always run this through `npm run build`. Invoking esbuild directly skips the
 * stamp, and tests/test_frontend_build.py will then fail for everyone else.
 *
 * Two bundles, not one, and the shapes are not interchangeable:
 *
 *   viewer -> iife.  `viewer/__init__.py` copies exactly three flat filenames
 *   into the directory `curling-score serve` hosts, so the viewer has to be a
 *   single file; and an iife stays a classic `<script src="app.js">`, which is
 *   the exact string the service replaces to inject window.CHART. A module
 *   script would make that replacement miss -- silently, because it is
 *   `.replace(..., 1)` -- and a view-only link would boot as editable.
 *
 *   site -> esm.  auth.js imports Firebase from gstatic with a static import,
 *   and an iife bundle cannot contain one. Marking https:// external keeps
 *   those as runtime imports so the CDN copy is still shared and cached.
 *
 * Nothing here touches HTML or CSS. index.html is hand-written because it is
 * the injection anchor, and style.css is 548 lines whose selectors are the
 * application's state machine; running either through a bundler would be
 * churn against the one part of this that is already proven.
 */
import * as esbuild from "esbuild";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { readFile, writeFile } from "node:fs/promises";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..");
const STAMP = resolve(HERE, ".buildstamp.json");

const watch = process.argv.includes("--watch");
const check = process.argv.includes("--check");
const dev = watch;

/* Minify is not a nicety here: neither server compresses. `curling-score
 * serve` is SimpleHTTPRequestHandler and the FastAPI app installs no
 * GZipMiddleware, so these bytes are the bytes on the wire. line-limit keeps
 * the committed artifact reviewable -- `git diff` is how it gets looked at. */
const common = {
  bundle: true,
  target: "es2022",
  jsx: "automatic",
  logLevel: "silent",
  metafile: true,
  define: { "process.env.NODE_ENV": dev ? '"development"' : '"production"' },
  minify: !dev,
  lineLimit: dev ? 0 : 500,
  sourcemap: dev ? "inline" : false,
};

export const TARGETS = [
  {
    name: "viewer",
    entry: "viewer/main.jsx",
    outfile: "src/curling_score/viewer/app.js",
    format: "iife",
    external: [],
  },
  {
    name: "site",
    entry: "site/main.jsx",
    outfile: "src/curling_score/service/static/site.js",
    format: "esm",
    external: ["https://*"],
  },
];

const optionsFor = t => ({
  ...common,
  entryPoints: [resolve(HERE, t.entry)],
  outfile: resolve(REPO, t.outfile),
  format: t.format,
  external: t.external,
});

const sha256 = buf => createHash("sha256").update(buf).digest("hex");
const hashFile = async p => sha256(await readFile(p));

/* The flags are stamped too, so a bundle built with different options is
 * caught the same way a stale one is. */
function flagsOf(t) {
  const o = optionsFor(t);
  return JSON.stringify({ ...o, entryPoints: [t.entry], outfile: t.outfile });
}

async function stampFor(results) {
  const sources = {};
  const outputs = {};
  for (const { target, result } of results) {
    // metafile.inputs is what esbuild actually read to produce this bundle --
    // a truer answer than globbing the tree, which would also hash files
    // nothing imports.
    for (const input of Object.keys(result.metafile.inputs)) {
      if (input.includes("node_modules")) continue;
      const abs = resolve(HERE, input);
      sources[relative(REPO, abs)] = await hashFile(abs);
    }
    outputs[target.outfile] = await hashFile(resolve(REPO, target.outfile));
  }
  return {
    note: "Written by `npm run build`. tests/test_frontend_build.py checks it.",
    esbuild: esbuild.version,
    flags: sha256(results.map(r => flagsOf(r.target)).join("\n")),
    sources: Object.fromEntries(Object.entries(sources).sort()),
    outputs: Object.fromEntries(Object.entries(outputs).sort()),
  };
}

/* A target whose entry does not exist yet is skipped rather than fatal, and
 * said so out loud. The two bundles land in separate commits, and a build that
 * refused to run until both existed would mean neither could be checked. */
const present = () => TARGETS.filter(t => {
  const here = existsSync(resolve(HERE, t.entry));
  if (!here) console.log(`  (skipping ${t.name}: no ${t.entry} yet)`);
  return here;
});

async function buildAll() {
  const results = [];
  for (const target of present())
    results.push({ target, result: await esbuild.build(optionsFor(target)) });
  return results;
}

function report(results) {
  for (const { target, result } of results) {
    const out = Object.entries(result.metafile.outputs)
      .find(([p]) => p.endsWith(target.outfile.split("/").pop()));
    const bytes = out ? out[1].bytes : 0;
    console.log(`  ${target.outfile.padEnd(46)} ${(bytes / 1024).toFixed(1).padStart(7)} KB`);
  }
}

try {
  if (watch) {
    for (const target of TARGETS) {
      const ctx = await esbuild.context(optionsFor(target));
      await ctx.watch();
      console.log(`watching ${target.entry} -> ${target.outfile}`);
    }
    console.log("\nesbuild is watching. Reload the page to pick changes up.");
    await new Promise(() => {});
  }

  const results = await buildAll();
  const stamp = await stampFor(results);

  if (check) {
    const on_disk = JSON.parse(await readFile(STAMP, "utf8"));
    const same = JSON.stringify(on_disk) === JSON.stringify(stamp);
    console.log(same ? "stamp matches" : "STAMP DIFFERS -- run `npm run build`");
    process.exit(same ? 0 : 1);
  }

  await writeFile(STAMP, JSON.stringify(stamp, null, 2) + "\n");
  console.log("built:");
  report(results);
  console.log(`\nstamp: ${Object.keys(stamp.sources).length} sources, esbuild ${stamp.esbuild}`);
} catch (err) {
  if (err.errors) for (const e of err.errors)
    console.error(`${e.location?.file ?? "?"}:${e.location?.line ?? "?"}  ${e.text}`);
  else console.error(err.message);
  process.exit(1);
}
