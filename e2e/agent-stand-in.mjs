// A stand-in for the agent's AgentCore app in the end-to-end tests, on port 8003. It takes each
// payload the backend posts to /invocations, records it, and answers that it took the work, so the
// tests never reach the real agent. GET /calls returns the payloads, and GET /ping answers as an
// AgentCore app does.
//
// The app keeps one run or round going at a time, so the stand-in finishes each run it's given
// through the app at UNDERCOVER_API with the agent's key: it refers every claim of an evaluation,
// then finishes the run; a dispatch it finishes without working a claim. The specs play a round
// out through the app themselves, and stopping the round frees it.
import { createServer } from "node:http";

const app = process.env.UNDERCOVER_API ?? "http://127.0.0.1:8001";
const key = { "X-API-Key": process.env.UNDERCOVER_API_KEY ?? "" };
const calls = [];

const answer = (response, body) => {
  response.setHeader("Content-Type", "application/json");
  response.end(JSON.stringify(body));
};

const call = async (method, path, body) => {
  const response = await fetch(`${app}${path}`, {
    method,
    headers: { ...key, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return response.json();
};

const refer = {
  decision: "refer",
  payout: 0,
  reason: "Needs a handler.",
  policy_section: "Manual 2",
  decided_by: "UnderCover Agent, version 1",
};

async function workRun(runId) {
  const run = await call("GET", `/api/runs/${runId}`);
  if (run.kind === "evaluation") {
    for (const claimId of await call("GET", `/api/runs/${runId}/claims`)) {
      await call("POST", `/api/claims/${claimId}/start`, { run_id: runId });
      await call("POST", `/api/claims/${claimId}/decision`, refer);
    }
  }
  await call("POST", `/api/runs/${runId}/finish`);
}

createServer((request, response) => {
  if (request.method === "GET" && request.url === "/calls") {
    answer(response, calls);
    return;
  }
  if (request.method === "GET" && request.url === "/ping") {
    answer(response, { status: "Healthy" });
    return;
  }
  let body = "";
  request.on("data", (chunk) => {
    body += chunk;
  });
  request.on("end", () => {
    if (request.method !== "POST" || request.url !== "/invocations") {
      response.statusCode = 404;
      answer(response, { error: "Not found" });
      return;
    }
    calls.push(body);
    answer(response, { accepted: true });
    const payload = JSON.parse(body);
    if (payload.operation === "work_run") {
      workRun(payload.run_id).catch((error) => {
        console.error(
          `The stand-in couldn't work run ${payload.run_id}:`,
          error,
        );
      });
    }
  });
}).listen(8003, "127.0.0.1");
