// The handlers' sign-in: Amazon Cognito when the build has a user pool and an app client,
// otherwise Supabase Auth. Supabase's client is made only when it is the one in use.
import { supabase } from "./supabase";

export interface Auth {
  // Resolves to a message for the handler when the sign-in is refused, otherwise null.
  signIn(email: string, password: string): Promise<string | null>;
  signOut(): Promise<void>;
  // The token that goes with every read and action, or "" when signed out. With renewWithinMs,
  // the sign-in is renewed first when less than that is left on it.
  token(renewWithinMs?: number): Promise<string>;
  // Calls back now and on every change with the signed-in user, or null; returns the stop.
  onChange(listener: (user: string | null) => void): () => void;
}

export const WRONG_PASSWORD =
  "That email and password don't match. Check them and try again.";

export const supabaseAuth: Auth = {
  async signIn(email, password) {
    const { error } = await supabase().auth.signInWithPassword({
      email,
      password,
    });
    if (!error) return null;
    return error.code === "invalid_credentials"
      ? WRONG_PASSWORD
      : `${error.message}. Try again in a moment.`;
  },
  async signOut() {
    await supabase().auth.signOut();
  },
  async token(renewWithinMs = 0) {
    const { session } = (await supabase().auth.getSession()).data;
    // expires_at is in seconds.
    const left = (session?.expires_at ?? Infinity) * 1000 - Date.now();
    if (!session || left >= renewWithinMs) return session?.access_token ?? "";
    const { data } = await supabase().auth.refreshSession();
    return data.session?.access_token ?? "";
  },
  onChange(listener) {
    const { data } = supabase().auth.onAuthStateChange((_event, session) => {
      listener(session?.user.id ?? null);
    });
    return () => {
      data.subscription.unsubscribe();
    };
  },
};

interface Tokens {
  email: string;
  idToken: string;
  refreshToken: string;
  expiresAt: number;
}

interface InitiateAuthReply {
  AuthenticationResult?: {
    IdToken: string;
    RefreshToken?: string;
    ExpiresIn: number;
  };
  ChallengeName?: string;
  message?: string;
}

const SAVED = "undercover-cognito";
// Renew the ID token this long before it expires.
const MARGIN_MS = 60_000;

// Cognito's InitiateAuth by plain fetch: USER_PASSWORD_AUTH to sign in, REFRESH_TOKEN_AUTH to
// renew. The backend checks the ID token, whose audience is the app client.
export function cognitoAuth(userPoolId: string, clientId: string): Auth {
  const region = userPoolId.split("_")[0];
  const listeners = new Set<(user: string | null) => void>();

  const saved = () => {
    const text = localStorage.getItem(SAVED);
    return text ? (JSON.parse(text) as Tokens) : null;
  };

  const save = (tokens: Tokens | null) => {
    if (tokens) localStorage.setItem(SAVED, JSON.stringify(tokens));
    else localStorage.removeItem(SAVED);
    for (const listener of listeners) listener(tokens?.email ?? null);
  };

  async function initiateAuth(
    flow: string,
    parameters: Record<string, string>,
  ) {
    const response = await fetch(
      `https://cognito-idp.${region}.amazonaws.com/`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/x-amz-json-1.1",
          "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        },
        body: JSON.stringify({
          AuthFlow: flow,
          ClientId: clientId,
          AuthParameters: parameters,
        }),
      },
    );
    return (await response.json()) as InitiateAuthReply;
  }

  return {
    async signIn(email, password) {
      const reply = await initiateAuth("USER_PASSWORD_AUTH", {
        USERNAME: email,
        PASSWORD: password,
      });
      const result = reply.AuthenticationResult;
      if (result) {
        save({
          email,
          idToken: result.IdToken,
          refreshToken: result.RefreshToken ?? "",
          expiresAt: Date.now() + result.ExpiresIn * 1000,
        });
        return null;
      }
      if (reply.message === "Incorrect username or password.")
        return WRONG_PASSWORD;
      return reply.ChallengeName
        ? "This account needs its password set before it can sign in."
        : `${reply.message ?? "The sign-in failed"} Try again in a moment.`;
    },
    signOut() {
      save(null);
      return Promise.resolve();
    },
    async token(renewWithinMs = MARGIN_MS) {
      const tokens = saved();
      if (!tokens) return "";
      const margin = Math.max(renewWithinMs, MARGIN_MS);
      if (tokens.expiresAt - margin > Date.now()) return tokens.idToken;
      const result = (
        await initiateAuth("REFRESH_TOKEN_AUTH", {
          REFRESH_TOKEN: tokens.refreshToken,
        })
      ).AuthenticationResult;
      if (!result) {
        save(null);
        return "";
      }
      const renewed = {
        ...tokens,
        idToken: result.IdToken,
        expiresAt: Date.now() + result.ExpiresIn * 1000,
      };
      save(renewed);
      return renewed.idToken;
    },
    onChange(listener) {
      listeners.add(listener);
      listener(saved()?.email ?? null);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

export function chooseAuth(
  userPoolId: string | undefined,
  clientId: string | undefined,
): Auth {
  return userPoolId && clientId
    ? cognitoAuth(userPoolId, clientId)
    : supabaseAuth;
}

export const auth = chooseAuth(
  import.meta.env.VITE_COGNITO_USER_POOL_ID as string | undefined,
  import.meta.env.VITE_COGNITO_CLIENT_ID as string | undefined,
);
