import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  getClaim,
  getClaimHistory,
  getCurrentRun,
  getEvaluation,
  getPolicy,
  getSettings,
  listClaims,
  listEvaluations,
  listRecipes,
  listRounds,
  listSteps,
  listVersions,
} from "./api";
import { fakeBackend, signIn } from "./fixtures";

beforeEach(() => {
  signIn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// The backend refuses every read without a handler's sign-in or the agent's key.
it("sends the handler's token with every read", async () => {
  const reads = [
    listClaims,
    () => getClaim("CLM-1001"),
    () => getPolicy("POL-2001"),
    () => getClaimHistory("CUS-3001"),
    () => listSteps("CLM-1001"),
    getSettings,
    getCurrentRun,
    listVersions,
    listEvaluations,
    () => getEvaluation("1"),
    listRounds,
    listRecipes,
  ];
  const fetchMock = fakeBackend(
    Object.fromEntries(
      [
        "/api/claims",
        "/api/claims/CLM-1001",
        "/api/policies/POL-2001",
        "/api/customers/CUS-3001/claims",
        "/api/claims/CLM-1001/steps",
        "/api/settings",
        "/api/runs/current",
        "/api/configuration/versions",
        "/api/evaluations",
        "/api/evaluations/1",
        "/api/rounds",
        "/api/recipes",
      ].map((path) => [path, []]),
    ),
  );
  for (const read of reads) await read();
  expect(fetchMock).toHaveBeenCalledTimes(reads.length);
  for (const [, init] of fetchMock.mock.calls as unknown as [
    string,
    RequestInit,
  ][])
    expect(init.headers).toEqual({ Authorization: "Bearer token" });
});
