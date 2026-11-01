// A stand-in for OpenRouter's Decisions endpoint in the end-to-end tests, on port 8005, so the
// backend's judge never reaches Jev. Every claim gets the same answer: a B, the same grounds and
// not the same section.
import { createServer } from "node:http";

const answer = {
  model: "typesafe/jev-1.13",
  answers: {
    grade: {
      type: "score",
      score: 2.2,
      probabilities: { 0: 0.05, 1: 0.15, 2: 0.5, 3: 0.3 },
    },
    same_grounds: { type: "noul", noul: 0.9 },
    same_section: { type: "noul", noul: 0.3 },
  },
  usage: { input_tokens: 300, output_tokens: 4, cost: 0.00003 },
};

createServer((request, response) => {
  if (request.method === "GET" && request.url === "/health") {
    response.end("ok");
    return;
  }
  request.resume();
  request.on("end", () => {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify(answer));
  });
}).listen(8005, "127.0.0.1");
