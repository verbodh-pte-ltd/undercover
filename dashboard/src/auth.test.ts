import { afterEach, expect, it, vi } from "vitest";
import { chooseAuth, cognitoAuth, supabaseAuth, WRONG_PASSWORD } from "./auth";
import { session, signIn } from "./fixtures";
import { supabase } from "./supabase";

const POOL = "eu-west-2_Test";
const CLIENT = "test-client";
const EMAIL = "dana.whitfield@undercover.example";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// Stands in for Cognito: each call to InitiateAuth gets the next reply.
function cognitoReplies(...replies: object[]) {
  const fetch = vi.fn<typeof globalThis.fetch>();
  for (const reply of replies)
    fetch.mockResolvedValueOnce(new Response(JSON.stringify(reply)));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const signedIn = (idToken: string, expiresIn = 3600) => ({
  AuthenticationResult: {
    IdToken: idToken,
    AccessToken: "access",
    RefreshToken: "refresh",
    ExpiresIn: expiresIn,
  },
});

it("uses Cognito only when the build has its user pool and app client", () => {
  expect(chooseAuth(undefined, undefined)).toBe(supabaseAuth);
  expect(chooseAuth(POOL, undefined)).toBe(supabaseAuth);
  expect(chooseAuth(POOL, CLIENT)).not.toBe(supabaseAuth);
});

it("signs in to Cognito with InitiateAuth and sends the ID token", async () => {
  const fetch = cognitoReplies(signedIn("id-token"));
  const auth = cognitoAuth(POOL, CLIENT);
  const users: (string | null)[] = [];
  auth.onChange((user) => users.push(user));

  expect(await auth.token()).toBe("");
  expect(await auth.signIn(EMAIL, "secret")).toBeNull();
  expect(await auth.token()).toBe("id-token");
  expect(users).toEqual([null, EMAIL]);

  const [url, init] = fetch.mock.calls[0];
  expect(url).toBe("https://cognito-idp.eu-west-2.amazonaws.com/");
  expect(init?.headers).toMatchObject({
    "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
  });
  expect(JSON.parse(init?.body as string)).toEqual({
    AuthFlow: "USER_PASSWORD_AUTH",
    ClientId: CLIENT,
    AuthParameters: { USERNAME: EMAIL, PASSWORD: "secret" },
  });
});

it("keeps a Cognito sign-in across a reload, and signs out", async () => {
  cognitoReplies(signedIn("id-token"));
  await cognitoAuth(POOL, CLIENT).signIn(EMAIL, "secret");

  const reloaded = cognitoAuth(POOL, CLIENT);
  const users: (string | null)[] = [];
  const stop = reloaded.onChange((user) => users.push(user));
  await reloaded.signOut();
  stop();
  expect(users).toEqual([EMAIL, null]);
  expect(await reloaded.token()).toBe("");
});

it("says what to do when Cognito refuses the password", async () => {
  cognitoReplies(
    {
      __type: "NotAuthorizedException",
      message: "Incorrect username or password.",
    },
    { __type: "NotAuthorizedException", message: "User is disabled." },
    { ChallengeName: "NEW_PASSWORD_REQUIRED", Session: "s" },
  );
  const auth = cognitoAuth(POOL, CLIENT);
  expect(await auth.signIn(EMAIL, "wrong")).toBe(WRONG_PASSWORD);
  expect(await auth.signIn(EMAIL, "x")).toBe(
    "User is disabled. Try again in a moment.",
  );
  expect(await auth.signIn(EMAIL, "x")).toBe(
    "This account needs its password set before it can sign in.",
  );
  expect(await auth.token()).toBe("");
});

it("renews the Cognito ID token when it is about to expire", async () => {
  const fetch = cognitoReplies(signedIn("old", 30), signedIn("new"));
  const auth = cognitoAuth(POOL, CLIENT);
  await auth.signIn(EMAIL, "secret");
  expect(await auth.token()).toBe("new");
  expect(JSON.parse(fetch.mock.calls[1][1]?.body as string)).toMatchObject({
    AuthFlow: "REFRESH_TOKEN_AUTH",
    AuthParameters: { REFRESH_TOKEN: "refresh" },
  });
  expect(await auth.token()).toBe("new");
});

it("signs out of Cognito when the token can't be renewed", async () => {
  cognitoReplies(signedIn("old", 30), {
    __type: "NotAuthorizedException",
    message: "Refresh Token has expired",
  });
  const auth = cognitoAuth(POOL, CLIENT);
  await auth.signIn(EMAIL, "secret");
  const users: (string | null)[] = [];
  auth.onChange((user) => users.push(user));
  expect(await auth.token()).toBe("");
  expect(users).toEqual([EMAIL, null]);
});

it("signs in through Supabase and sends its access token", async () => {
  signIn();
  const signInWithPassword = vi
    .spyOn(supabase().auth, "signInWithPassword")
    .mockResolvedValue({
      data: { user: null, session: null },
      error: { code: "over_request_rate_limit", message: "Too many requests" },
    } as never);
  expect(await supabaseAuth.signIn(EMAIL, "secret")).toBe(
    "Too many requests. Try again in a moment.",
  );
  expect(signInWithPassword).toHaveBeenCalledWith({
    email: EMAIL,
    password: "secret",
  });
  expect(await supabaseAuth.token()).toBe(session.access_token);
});
