// A stand-in for the agent's service in the end-to-end tests, on port 8003. It records each call
// the backend makes, and GET /calls returns them, so the tests never reach the real agent.
import { createServer } from "node:http";

const calls = [];

createServer((request, response) => {
  if (request.method === "GET" && request.url === "/calls") {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify(calls));
    return;
  }
  if (request.method === "GET" && request.url === "/health") {
    response.end("ok");
    return;
  }
  calls.push(`${request.method} ${request.url}`);
  response.statusCode = 202;
  response.end();
}).listen(8003, "127.0.0.1");
