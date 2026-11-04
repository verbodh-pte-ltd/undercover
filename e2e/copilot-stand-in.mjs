// A stand-in for the Copilot's AgentCore app in the end-to-end tests, on port 8007. It takes each
// payload the backend posts to /invocations, answers at once that it took the turn, then plays a
// scripted turn through the app's Copilot routes at UNDERCOVER_API with the handler's sign-in from
// the payload, as the Copilot does. It never calls a model. GET /ping answers as an AgentCore app
// does, and {"operation": "health"} as the Copilot does.
//
// The script goes by words in the message, and a claim id in it, such as CLM-1010:
// - "look up": reads the claim and answers with its status
// - "evaluation": starts a tiny evaluation, an action
// - "pay": a refused step, since the Copilot never pays a claim itself
// - "sign off": reads the claim and pauses for the handler's confirmation; a handler who can't
//   sign off over the limit is refused by the app, and the turn finishes with the reason
// - "table": a formatted answer with a table
// The answer to a confirmation signs off the turn's claim, or finishes without signing off.
import { createServer } from "node:http";

const app = process.env.UNDERCOVER_API ?? "http://127.0.0.1:8001";

const answer = (response, body) => {
  response.setHeader("Content-Type", "application/json");
  response.end(JSON.stringify(body));
};

// A call to the app as the handler; a refusal is returned with its reason, not thrown.
async function call(token, method, path, body) {
  const response = await fetch(`${app}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const json = await response.json();
  return { ok: response.ok, status: response.status, json };
}

// One turn's calls to the app: its steps, then its end. A step is a tool call, {key, kind,
// tool, input}, in a state, with what came back once it has.
function turnOf(token, turnId) {
  const base = `/api/copilot/turns/${turnId}`;
  const usage = { model_calls: 2, cost: 0.0012, seconds: 1.5 };
  return {
    call: (method, path, body) => call(token, method, path, body),
    step: (tool, state, reply = null) =>
      call(token, "POST", `${base}/steps`, {
        ...tool,
        state,
        seconds: state === "working" ? null : 0.4,
        detail:
          reply === null
            ? null
            : {
                asked: `${tool.tool} ${tool.input}`,
                answer: reply,
                file: null,
                owner: null,
              },
      }),
    pause: (claimId) =>
      call(token, "POST", `${base}/pause`, { claim_id: claimId }),
    finish: (text) =>
      call(token, "POST", `${base}/finish`, { answer: text, ...usage }),
    fail: (error) => call(token, "POST", `${base}/fail`, { error, ...usage }),
  };
}

const claimIn = (message) => /CLM-\d+/.exec(message)?.[0] ?? "CLM-1001";

const signOffCall = (claimId) => ({
  key: "sign-off",
  kind: "action",
  tool: "sign_off",
  input: claimId,
});

async function lookUp(turn, claimId) {
  const tool = {
    key: "lookup",
    kind: "claims",
    tool: "get_claim",
    input: claimId,
  };
  await turn.step(tool, "working");
  const claim = (await turn.call("GET", `/api/claims/${claimId}`)).json;
  await turn.step(
    tool,
    "done",
    `${claimId}: ${claim.status}, for ${claim.customer_name}`,
  );
  return claim;
}

async function startTurn(turn, message) {
  const text = message.toLowerCase();
  const claimId = claimIn(message);
  if (text.includes("sign off")) {
    await lookUp(turn, claimId);
    const paused = await turn.pause(claimId);
    if (paused.ok) return;
    const why = paused.json.detail;
    await turn.step(signOffCall(claimId), "refused", why);
    await turn.finish(`I can't sign off ${claimId}: ${why}.`);
    return;
  }
  if (text.includes("look up")) {
    const claim = await lookUp(turn, claimId);
    await turn.finish(
      `${claimId} is **${claim.status}**, for ${claim.customer_name}.`,
    );
    return;
  }
  if (text.includes("evaluation")) {
    const tool = {
      key: "evaluate",
      kind: "action",
      tool: "start_evaluation",
      input: "5 tiny claims, evaluation set",
    };
    await turn.step(tool, "working");
    const started = await turn.call("POST", "/api/evaluations", {
      n: 5,
      eval_set: "evaluation",
      tiny: true,
    });
    const run = String(started.json.run_id);
    await turn.step(tool, "done", `Evaluation ${run} started`);
    await turn.finish(`I started evaluation ${run} on five tiny claims.`);
    return;
  }
  if (text.includes("pay")) {
    const why =
      "The Copilot never pays a claim. A payout over the limit is paid by your sign-off.";
    const tool = {
      key: "pay",
      kind: "action",
      tool: "pay_claim",
      input: claimId,
    };
    await turn.step(tool, "refused", why);
    await turn.finish(`I can't pay ${claimId} myself. ${why}`);
    return;
  }
  if (text.includes("table")) {
    await turn.finish(
      [
        "## Waiting for you",
        "",
        "| Claim | Customer | Pays |",
        "|---|---|---:|",
        `| ${claimId} | Someone | $1,200.00 |`,
        "",
        "- **One** claim needs your sign-off.",
      ].join("\n"),
    );
    return;
  }
  await turn.finish("I can help with claims, customers and the pilot.");
}

async function answerConfirmation(turn, conversationId, turnId, choice) {
  const conversation = (
    await turn.call("GET", `/api/conversations/${conversationId}`)
  ).json;
  const claimId = conversation.turns.find((t) => t.id === turnId)?.card
    ?.claim_id;
  if (choice !== "sign_off") {
    await turn.finish(`I haven't signed off ${claimId}. Nothing was paid.`);
    return;
  }
  const tool = signOffCall(claimId);
  await turn.step(tool, "working");
  const signed = await turn.call("POST", `/api/claims/${claimId}/sign-off`);
  if (!signed.ok) {
    await turn.step(tool, "failed", signed.json.detail);
    await turn.finish(`I couldn't sign off ${claimId}: ${signed.json.detail}.`);
    return;
  }
  await turn.step(tool, "done", `Paid $${signed.json.paid_amount.toFixed(2)}`);
  await turn.finish(`Signed off and paid ${claimId}.`);
}

function play(payload) {
  const turn = turnOf(payload.token, payload.turn_id);
  const work =
    payload.operation === "start_turn"
      ? startTurn(turn, payload.message)
      : answerConfirmation(
          turn,
          payload.conversation_id,
          payload.turn_id,
          payload.answer,
        );
  work.catch(async (error) => {
    console.error(
      `The Copilot stand-in failed turn ${payload.turn_id}:`,
      error,
    );
    await turn.fail("The Copilot stand-in failed.").catch(() => undefined);
  });
}

createServer((request, response) => {
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
    const payload = JSON.parse(body);
    if (payload.operation === "health") {
      answer(response, { healthy: true });
      return;
    }
    answer(response, { accepted: true });
    if (
      payload.operation === "start_turn" ||
      payload.operation === "answer_confirmation"
    )
      play(payload);
  });
}).listen(8007, "127.0.0.1");
