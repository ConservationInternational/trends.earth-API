import https from "node:https";
import http from "node:http";
import net from "node:net";
import { execFileSync } from "node:child_process";
import { readFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const port = async () => {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const value = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return value;
};
const gatewayPort = await port();
const fixturePort = Number(process.env.FIXTURE_PORT || 18952);
const env = { ...process.env, GATEWAY_PORT: String(gatewayPort), FIXTURE_PORT: String(fixturePort) };
const args = ["compose", "-p", `public-viewer-tests-${process.pid}`, "-f", "tests/compose.yml"];
const directory = mkdtempSync(join(tmpdir(), "te-viewer-tls-"));
const key = join(directory, "key.pem");
const cert = join(directory, "cert.pem");
execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
  "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost", "-keyout", key, "-out", cert],
{ stdio: "ignore" });
let server;
let closing = false;
function cleanup(code = 0) {
  if (closing) return;
  closing = true;
  server?.close();
  execFileSync("docker", [...args, "down", "--timeout", "5"], { env, stdio: "inherit" });
  rmSync(directory, { recursive: true });
  process.exit(code);
}
process.on("SIGINT", () => cleanup());
process.on("SIGTERM", () => cleanup());
try {
  execFileSync("docker", [...args, "up", "-d"], { env, stdio: "inherit" });
  for (let i = 0; i < 60; i++) {
    try {
      const response = await fetch(`http://127.0.0.1:${gatewayPort}/_dash-layout`);
      if (response.ok) break;
    } catch {}
    if (i === 59) throw new Error("Viewer stack failed readiness");
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  server = https.createServer({ key: readFileSync(key), cert: readFileSync(cert) }, (req, res) => {
    const upstream = http.request({
      hostname: "127.0.0.1", port: gatewayPort, path: req.url, method: req.method, headers: req.headers,
    }, (response) => {
      res.writeHead(response.statusCode, response.headers);
      response.pipe(res);
    });
    upstream.on("error", () => { res.writeHead(502); res.end(); });
    req.pipe(upstream);
  });
  server.listen(Number(process.env.VIEWER_TEST_PORT || 18951), "127.0.0.1");
} catch (error) {
  console.error(error.message);
  execFileSync("docker", [...args, "logs", "--tail", "30"], { env, stdio: "inherit" });
  cleanup(1);
}
