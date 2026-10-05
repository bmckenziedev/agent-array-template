// TCP reachability probe (node, no dependencies). Used by verify.sh inside throwaway pods.
//
//   TARGETS='[["host-or-ip", 443, "open"|"blocked"], ...]' node - < probe_tcp.js
//
// Prints one PASS/FAIL line per target and exits 1 if any target behaved differently than
// expected. "blocked" = the connection was refused, timed out or the name did not resolve.
const net = require("net");
const dns = require("dns").promises;

const targets = JSON.parse(process.env.TARGETS || "[]");

function probe(host, port) {
  return new Promise(async (resolve) => {
    let ip = host;
    if (!/^[0-9.]+$/.test(host)) {
      try { ip = (await dns.lookup(host)).address; } catch (e) { return resolve(["blocked", `dns ${e.code}`]); }
    }
    const s = net.connect({ host: ip, port, timeout: 6000 });
    s.on("connect", () => { s.destroy(); resolve(["open", ip]); });
    s.on("timeout", () => { s.destroy(); resolve(["blocked", `timeout ${ip}`]); });
    s.on("error", (e) => resolve(["blocked", `${e.code} ${ip}`]));
  });
}

(async () => {
  let fails = 0;
  for (const [host, port, want] of targets) {
    const [got, detail] = await probe(host, port);
    const ok = got === want;
    if (!ok) fails++;
    console.log(`${ok ? "PASS" : "FAIL"} tcp ${host}:${port} expected ${want}, got ${got} (${detail})`);
  }
  process.exitCode = fails ? 1 : 0;
})();
