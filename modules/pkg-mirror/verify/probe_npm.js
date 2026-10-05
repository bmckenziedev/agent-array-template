// npm + Verdaccio probe, run inside a throwaway pod in session-jobs by verify.sh:
//
//   node - < probe_npm.js
//
// Self-asserting: prints PASS/FAIL per check, exits 1 if any failed. No dependencies. The
// install is run as modules/session-jobs/orchestrator.py builds it (--registry=<mirror>,
// --ignore-scripts, --no-audit) and the way tester.py exports it (npm_config_registry only).
const { execSync } = require("child_process");
const fs = require("fs");
const http = require("http");

const HOST = `npm.${process.env.MIRROR_NAMESPACE}.svc.cluster.local`;
const R = `http://${HOST}:4873/`;
const FLAGS = "--no-audit --no-fund --ignore-scripts";
let fails = 0;
const check = (label, ok, detail = "") => {
  if (!ok) fails++;
  console.log(`${ok ? "PASS" : "FAIL"} ${label}${ok ? "" : "  -> " + detail}`);
};
const sh = (cmd, cwd, env = {}) => {
  try {
    return [0, execSync(cmd, { cwd, env: { ...process.env, HOME: "/tmp", ...env }, stdio: ["ignore", "pipe", "pipe"], timeout: 240000 }).toString()];
  } catch (e) {
    return [e.status || 1, `${e.stdout || ""}${e.stderr || ""}`];
  }
};
const req = (method, path, body) => new Promise((resolve) => {
  const q = http.request({ host: HOST, port: 4873, method, path,
                           headers: { "content-type": "application/json" } },
                         (s) => { s.resume(); s.on("end", () => resolve(s.statusCode)); });
  q.on("error", (e) => resolve("err " + e.message));
  q.end(body || "");
});

(async () => {
  for (const d of ["p", "q", "r"]) { fs.mkdirSync(`/tmp/${d}`, { recursive: true }); sh("npm init -y", `/tmp/${d}`); }
  let [rc, out] = sh(`npm install ${FLAGS} --registry=${R} left-pad@1.3.0 @types/node@22`, "/tmp/p");
  check("npm install --registry=<mirror> left-pad + @types/node (orchestrator flags)", rc === 0, out.slice(-300));
  const lock = fs.existsSync("/tmp/p/package-lock.json") ? fs.readFileSync("/tmp/p/package-lock.json", "utf8") : "";
  const resolved = [...lock.matchAll(/"resolved": "([^"]+)"/g)].map((m) => m[1]);
  check("every resolved URL in the lockfile points at the mirror",
        resolved.length >= 3 && resolved.every((u) => u.startsWith(R)), JSON.stringify(resolved));
  sh("rm -rf node_modules", "/tmp/p");
  [rc, out] = sh(`npm ci ${FLAGS} --registry=${R}`, "/tmp/p");
  check("npm ci replays the lockfile through the mirror", rc === 0, out.slice(-300));
  [rc, out] = sh(`npm install ${FLAGS} is-number@7.0.0`, "/tmp/q", { npm_config_registry: R });
  check("npm install with only npm_config_registry (how tester.py exports it)", rc === 0, out.slice(-300));
  [rc, out] = sh(`npm install ${FLAGS} --registry=https://registry.npmjs.org/ --fetch-retries=0 --fetch-timeout=8000 is-odd@3.0.1`, "/tmp/r");
  check("npm install straight from registry.npmjs.org FAILS (no internet egress)", rc !== 0, out.slice(-200));
  [rc, out] = sh(`npm publish --registry=${R}`, "/tmp/p");
  check("npm publish to the mirror FAILS", rc !== 0 && /ENEEDAUTH|401/.test(out), out.slice(-200));
  const want = {
    "PUT /left-pad (publish)": 401, "PUT /-/user/org.couchdb.user:probe (adduser)": 409,
    "DELETE /left-pad/-rev/1 (unpublish)": 401, "PUT /-/package/left-pad/dist-tags/latest": 401,
    "POST /-/npm/v1/security/advisories/bulk (audit, off)": 404,
  };
  const got = {
    "PUT /left-pad (publish)": await req("PUT", "/left-pad", JSON.stringify({ name: "left-pad", versions: {} })),
    "PUT /-/user/org.couchdb.user:probe (adduser)": await req("PUT", "/-/user/org.couchdb.user:probe", JSON.stringify({ name: "probe", password: "probeprobe1" })),
    "DELETE /left-pad/-rev/1 (unpublish)": await req("DELETE", "/left-pad/-rev/1"),
    "PUT /-/package/left-pad/dist-tags/latest": await req("PUT", "/-/package/left-pad/dist-tags/latest", JSON.stringify("1.0.0")),
    "POST /-/npm/v1/security/advisories/bulk (audit, off)": await req("POST", "/-/npm/v1/security/advisories/bulk", "{}"),
  };
  for (const k of Object.keys(want)) check(`Verdaccio refuses: ${k} -> ${want[k]}`, got[k] === want[k], `got ${got[k]}`);
  process.exitCode = fails ? 1 : 0;
})();
