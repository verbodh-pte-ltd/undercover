import { createClient } from "@supabase/supabase-js";

// The handlers' sign-in, through Supabase Auth. The URL and the publishable key are public.
export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL as string,
  import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string,
);
