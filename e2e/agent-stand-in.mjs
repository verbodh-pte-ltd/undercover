// A stand-in for the agent's AgentCore app in the end-to-end tests, on port 8003. It takes each
// payload the backend posts to /invocations, records it, and answers that it took the work, so the
// tests never reach the real agent. GET /calls returns the payloads, and GET /ping answers as an
// AgentCore app does.
import { createServer } from "node:http";

const calls = [];

const answer = (response, body) => {
  response.setHeader("Content-Type", "application/json");
  response.end(JSON.stringify(body));
};

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
  });
}).listen(8003, "127.0.0.1");
