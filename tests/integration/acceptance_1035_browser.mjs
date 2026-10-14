/** Real-provider browser acceptance. Start tests/integration/acceptance_1035.py first. */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

const dir = resolve(".amp/in/artifacts/1035-real-browser");
mkdirSync(dir, { recursive: true });
const state = JSON.parse(readFileSync("/tmp/phlo-1035-state.json", "utf8"));
const session = "real1035";
const base = "http://127.0.0.1:3138";
function browser(...args) {
  const result = JSON.parse(
    execFileSync("agent-browser", ["--session", session, ...args, "--json"], {
      encoding: "utf8",
      timeout: 45000,
    }),
  );
  assert(result.success, result.error);
  return result.data;
}
const pause = () => browser("wait", "1000");
function capture(name) {
  assert.equal(browser("eval", "devicePixelRatio").result, 2);
  browser("screenshot", resolve(dir, `${name}.png`));
  writeFileSync(
    resolve(dir, `${name}-dom.txt`),
    JSON.stringify(browser("snapshot"), null, 2),
  );
}
async function api(path, name) {
  const response = await fetch(`${state.api}/api/v1/${path}`, {
    headers: { Authorization: "Bearer acceptance-alice" },
  });
  const body = await response.json();
  writeFileSync(
    resolve(dir, `${name}.json`),
    JSON.stringify({ status: response.status, body }, null, 2),
  );
  return body;
}
function container(image, action) {
  execFileSync("docker", [action, state.containers[image]], { timeout: 30000 });
}
async function trinoReady() {
  for (let i = 0; i < 90; i++) {
    try {
      const response = await fetch("http://127.0.0.1:3142/v1/info");
      if (response.ok && !(await response.json()).starting) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("Trino did not recover");
}
try {
  browser("close");
  browser(
    "--headers",
    JSON.stringify({ Authorization: "Bearer acceptance-alice" }),
    "open",
    `${base}/query?env=prod`,
  );
  browser("set", "viewport", "1280", "800", "2");
  for (const [env, expected, prefix] of [
    ["prod", 3, "01"],
    ["staging", 7, "02"],
  ]) {
    browser("open", `${base}/query?env=${env}`);
    browser("wait", ".cm-content");
    browser("click", ".cm-content");
    browser("press", "Control+A");
    browser("type", ".cm-content", "SELECT id FROM warehouse.orders");
    browser("press", "Escape");
    browser("find", "role", "button", "click", "--name", "Run", "--exact");
    browser("wait", 'table[aria-label="Query results"]');
    assert.match(
      browser("get", "text", 'table[aria-label="Query results"]').text,
      new RegExp(`\\b${expected}\\b`),
    );
    browser("find", "role", "button", "click", "--name", "Refresh history");
    pause();
    capture(`${prefix}-${env}-query-history`);
    const history = await api(`queries/history?env=${env}`, `${env}-history`);
    assert.equal(history.items.length, 1);
    assert.equal(
      history.items[0].nessie_ref,
      env === "prod" ? "main" : "candidate",
    );
    const query = await api(
      `queries/${history.items[0].id}?env=${env}`,
      `${env}-query`,
    );
    assert.deepEqual(query.result.rows, [{ id: expected }]);
  }
  for (const [env, expected, prefix] of [
    ["prod", 3, "03"],
    ["staging", 7, "05"],
  ]) {
    browser("open", `${base}/assets/warehouse%2Forders?env=${env}&tab=data`);
    browser("reload");
    browser("wait", 'table[aria-label="Sample rows from warehouse/orders"]');
    pause();
    assert.match(
      browser(
        "get",
        "text",
        'table[aria-label="Sample rows from warehouse/orders"]',
      ).text,
      new RegExp(`\\b${expected}\\b`),
    );
    capture(`${prefix}-${env}-preview`);
    browser("find", "role", "tab", "click", "--name", "Usage evidence");
    browser("wait", "--text", "Verified query usage");
    pause();
    const main = browser("get", "text", "main").text;
    assert.match(main, /FINISHED/);
    assert.match(main, /returned rows/);
    capture(`${env === "prod" ? "04" : "06"}-${env}-usage`);
    const usage = await api(
      `assets/warehouse/orders/query-usage?env=${env}`,
      `${env}-query-usage`,
    );
    assert(usage.items.length > 0);
    await api(
      `assets/warehouse/orders/usage?env=${env}`,
      `${env}-preview-access`,
    );
  }
  container("trinodb/trino:483", "stop");
  browser("open", `${base}/assets/warehouse%2Forders?env=prod&tab=data`);
  browser("reload");
  browser("wait", "--text", "Trino preview service is unavailable");
  assert.equal(
    browser("eval", 'document.querySelectorAll("table").length').result,
    0,
  );
  capture("07-trino-preview-outage");
  await api("assets/warehouse/orders/preview?env=prod", "preview-outage");
  browser("open", `${base}/query?env=prod`);
  browser("reload");
  browser("wait", "--text", "Query catalog is unavailable");
  pause();
  browser("find", "role", "button", "click", "--name", "Refresh history");
  pause();
  capture("08-trino-query-outage");
  assert.equal(
    (await api("queries/history?env=prod", "history-during-trino-outage")).items
      .length,
    1,
  );
  container("trinodb/trino:483", "start");
  await trinoReady();
  browser("find", "role", "button", "click", "--name", "Try again");
  browser("wait", ".cm-content");
  browser("find", "role", "button", "click", "--name", "Run", "--exact");
  browser("wait", 'table[aria-label="Query results"]');
  capture("09-query-recovery");
  browser("open", `${base}/assets/warehouse%2Forders?env=prod&tab=data`);
  browser("reload");
  browser("wait", 'table[aria-label="Sample rows from warehouse/orders"]');
  capture("10-preview-recovery");
  await api("assets/warehouse/orders/preview?env=prod", "preview-recovery");
  container("postgres:18-alpine", "stop");
  browser("open", `${base}/assets/warehouse%2Forders?env=prod&tab=usage`);
  browser("reload");
  browser("wait", "--text", "storage is unavailable");
  capture("11-database-outage");
  await api("assets/warehouse/orders/query-usage?env=prod", "usage-db-outage");
  await api("queries/history?env=prod", "history-db-outage");
  container("postgres:18-alpine", "start");
  await new Promise((r) => setTimeout(r, 3000));
  browser("reload");
  browser("wait", "--text", "Verified query usage");
  pause();
  capture("12-database-recovery");
  await api("queries/history?env=prod", "history-db-recovery");
  await api(
    "assets/warehouse/orders/query-usage?env=prod",
    "usage-db-recovery",
  );
  writeFileSync(
    resolve(dir, "status.json"),
    JSON.stringify({
      status: "passed",
      commit: execFileSync("git", ["rev-parse", "HEAD"], {
        encoding: "utf8",
      }).trim(),
    }),
  );
} finally {
  for (const image of ["trinodb/trino:483", "postgres:18-alpine"]) {
    try {
      container(image, "start");
    } catch (error) {
      process.stderr.write(`Cleanup: ${error.message}\n`);
    }
  }
  browser("close");
}
