#!/usr/bin/env node
// MAG — ByteRover (brv) memory OAuth helper, driven by the control plane.
//
// brv's OAuth is a TUI flow with no clean CLI path, BUT its daemon exposes a
// programmatic transport API (provider:startOAuth / provider:awaitOAuthCallback).
// This helper talks to the per-tenant brv daemon over that transport:
//   1. provider:startOAuth  → prints the auth URL (the operator opens it + logs in).
//   2. provider:awaitOAuthCallback → keeps the daemon's loopback callback server
//      (localhost:1455) alive and BLOCKS until the code arrives.
//
// The redirect after login goes to localhost:1455 on the OPERATOR's machine (not the
// container), so the control plane captures the pasted redirect URL and curls it into
// THIS container's localhost:1455 — the daemon validates the PKCE `state`, exchanges
// the code, and brv is connected (stored in /opt/data). This process stays alive
// (held by dockerService) between start and complete.
//
// Protocol (one line each, to stdout):
//   __BRV_URL__<json: {authUrl, state, callbackMode}>
//   __BRV_DONE__<json: the awaitOAuthCallback result {success, ...}>
//   __BRV_ERR__<json: {error}>
//
// The brv-transport-client lib lives in the runtime-installed brv-cli tree.

const PROVIDER = (process.argv[2] || "openai").trim();
const PROJECT = process.env.BRV_PROJECT_DIR || "/opt/data/byterover";
const LIB =
  process.env.BRV_TRANSPORT_CLIENT ||
  "/opt/data/.local/share/brv-cli/lib/node_modules/@campfirein/brv-transport-client/dist/index.js";

const emit = (tag, obj) => process.stdout.write(`${tag}${JSON.stringify(obj)}\n`);

function stateFromUrl(url) {
  try {
    return new URL(url).searchParams.get("state");
  } catch {
    return null;
  }
}

async function main() {
  const { connectToDaemon } = await import(LIB);
  const res = await connectToDaemon({ clientType: "cli", projectPath: PROJECT });
  const client = res.client;
  if (!client) throw new Error("daemon transport client unavailable");

  const start = await client.requestWithAck("provider:startOAuth", { providerId: PROVIDER });
  if (!start || !start.authUrl) {
    throw new Error(start && start.error ? start.error : "startOAuth returned no authUrl");
  }
  emit("__BRV_URL__", {
    authUrl: start.authUrl,
    state: stateFromUrl(start.authUrl),
    callbackMode: start.callbackMode || null,
  });

  // Block until the loopback callback delivers the code (the control plane curls
  // localhost:1455 with the pasted redirect's code+state). 10-min ceiling.
  const done = await client.requestWithAck(
    "provider:awaitOAuthCallback",
    { providerId: PROVIDER },
    { timeout: 600000 },
  );
  emit("__BRV_DONE__", done ?? { success: false, error: "no result" });
  process.exit(done && done.success ? 0 : 1);
}

main().catch((e) => {
  emit("__BRV_ERR__", { error: String((e && e.message) || e) });
  process.exit(1);
});
