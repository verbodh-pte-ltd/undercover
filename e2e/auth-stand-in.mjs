// A stand-in for Supabase Auth in the end-to-end tests, on port 8004. It signs in any email with
// the tests' password, signs the token with a key of its own, serves the public key as Supabase
// does, and answers the browser's cross-origin checks, so the tests never reach Supabase.
import { generateKeyPairSync, sign } from "node:crypto";
import { createServer } from "node:http";

// The same password as PASSWORD in e2e/sign-in.ts.
const PASSWORD = "e2e-password";
const KID = "e2e";

const { privateKey, publicKey } = generateKeyPairSync("ec", {
  namedCurve: "P-256",
});
const jwk = { ...publicKey.export({ format: "jwk" }), kid: KID, alg: "ES256" };

const encode = (value) =>
  Buffer.from(JSON.stringify(value)).toString("base64url");

function accessToken(user) {
  const now = Math.floor(Date.now() / 1000);
  const claims = { sub: user.id, email: user.email, aud: user.aud, iat: now };
  const body = `${encode({ alg: "ES256", kid: KID, typ: "JWT" })}.${encode({ ...claims, exp: now + 3600 })}`;
  const signature = sign("sha256", Buffer.from(body), {
    key: privateKey,
    dsaEncoding: "ieee-p1363",
  });
  return `${body}.${signature.toString("base64url")}`;
}

function send(response, status, body) {
  response.statusCode = status;
  response.setHeader("Content-Type", "application/json");
  response.end(body === undefined ? undefined : JSON.stringify(body));
}

createServer((request, response) => {
  response.setHeader("Access-Control-Allow-Origin", "*");
  if (request.method === "OPTIONS") {
    response.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    response.setHeader(
      "Access-Control-Allow-Headers",
      request.headers["access-control-request-headers"] ?? "*",
    );
    response.statusCode = 204;
    response.end();
    return;
  }
  const path = request.url.split("?")[0];
  if (path === "/health") {
    response.end("ok");
    return;
  }
  if (path === "/auth/v1/.well-known/jwks.json") {
    send(response, 200, { keys: [jwk] });
    return;
  }
  if (path === "/auth/v1/logout") {
    send(response, 204);
    return;
  }
  let body = "";
  request.on("data", (chunk) => {
    body += chunk;
  });
  request.on("end", () => {
    const { email, password } = JSON.parse(body || "{}");
    if (path !== "/auth/v1/token" || password !== PASSWORD) {
      send(response, 400, {
        error_code: "invalid_credentials",
        msg: "Invalid login credentials",
      });
      return;
    }
    const user = {
      id: email,
      aud: "authenticated",
      role: "authenticated",
      email,
      app_metadata: { provider: "email" },
      user_metadata: {},
      created_at: "2026-10-01T00:00:00Z",
    };
    send(response, 200, {
      access_token: accessToken(user),
      token_type: "bearer",
      expires_in: 3600,
      refresh_token: "e2e-refresh",
      user,
    });
  });
}).listen(8004, "127.0.0.1");
