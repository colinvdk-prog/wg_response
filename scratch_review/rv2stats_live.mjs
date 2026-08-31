import fs from "node:fs";

async function oneGet(url) {
  const requestedAt = new Date();
  const response = await fetch(url, {
    method: "GET",
    redirect: "manual",
    headers: { "accept": "application/json" },
  });
  const body = await response.text();
  const receivedAt = new Date();
  return {
    url,
    requested_at_utc: requestedAt.toISOString(),
    received_at_utc: receivedAt.toISOString(),
    received_epoch_s: receivedAt.getTime() / 1000,
    http_status: response.status,
    body,
  };
}

function namedFields(value, path = "$", out = []) {
  if (value === null || value === undefined) return out;
  if (Array.isArray(value)) {
    value.forEach((item, i) => namedFields(item, `${path}[${i}]`, out));
    return out;
  }
  if (typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      const next = `${path}.${key}`;
      if (/cycle|cadence|period|stale|started|restored|solver|degraded|uptime/i.test(key)) {
        out.push({ path: next, value: child });
      }
      namedFields(child, next, out);
    }
  }
  return out;
}

// The only three network operations in this script.
const watchHealth = await oneGet("https://y372zmpfuj.us-west-2.awsapprunner.com/healthz");
const simHealth = await oneGet("https://m897yqgsmt.us-west-2.awsapprunner.com/healthz");
const watchState = await oneGet("https://y372zmpfuj.us-west-2.awsapprunner.com/api/state");

const watchHealthJson = JSON.parse(watchHealth.body);
const simHealthJson = JSON.parse(simHealth.body);
const watchStateJson = JSON.parse(watchState.body);
const lastCycleAge = watchHealthJson.last_cycle_t_s == null
  ? null
  : watchHealth.received_epoch_s - Number(watchHealthJson.last_cycle_t_s);

const saved = {
  summary: {
    watch_health: {
      http_status: watchHealth.http_status,
      requested_at_utc: watchHealth.requested_at_utc,
      received_at_utc: watchHealth.received_at_utc,
      json: watchHealthJson,
      derived_last_cycle_age_s_at_receipt: lastCycleAge,
      last_cycle_utc: watchHealthJson.last_cycle_t_s == null
        ? null
        : new Date(Number(watchHealthJson.last_cycle_t_s) * 1000).toISOString(),
    },
    simulator_health: {
      http_status: simHealth.http_status,
      requested_at_utc: simHealth.requested_at_utc,
      received_at_utc: simHealth.received_at_utc,
      json: simHealthJson,
    },
    watcher_state_unauthenticated: {
      http_status: watchState.http_status,
      requested_at_utc: watchState.requested_at_utc,
      received_at_utc: watchState.received_at_utc,
      body_bytes_utf8: Buffer.byteLength(watchState.body, "utf8"),
      top_level_fields: Object.keys(watchStateJson),
      cadence_staleness_related_fields: namedFields(watchStateJson),
    },
  },
  raw_responses: {
    watcher_health: watchHealth,
    simulator_health: simHealth,
    watcher_state: watchState,
  },
};

const json = JSON.stringify(saved, null, 2) + "\n";
fs.writeFileSync("scratch_review/rv2stats_live_node_out.json", json, "utf8");
process.stdout.write(JSON.stringify(saved.summary, null, 2) + "\n");
