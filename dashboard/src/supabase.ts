import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let client: SupabaseClient | undefined;

// The handlers' sign-in, through Supabase Auth. The URL and the publishable key are public. The
// client is made on first use, so a build that signs in through Cognito never needs them.
export function supabase() {
  client ??= createClient(
    import.meta.env.VITE_SUPABASE_URL as string,
    import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string,
  );
  return client;
}
