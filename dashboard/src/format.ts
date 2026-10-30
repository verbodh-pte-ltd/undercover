import type { Status } from "./api";

const dollars = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

export const money = (amount: number) => dollars.format(amount);

const moments = new Intl.DateTimeFormat("en-US", {
  dateStyle: "medium",
  timeStyle: "short",
});

export const when = (iso: string) => moments.format(new Date(iso));

export const statusLabels: Record<Status, string> = {
  waiting: "Waiting",
  in_progress: "In progress",
  waiting_for_sign_off: "Waiting for sign-off",
  decided: "Decided",
  needs_handler: "Left for a handler",
};

const costs = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
});

// Model costs are fractions of a cent, so they show four decimal places.
export const cost = (amount: number) => costs.format(amount);

export const seconds = (value: number | null) =>
  value === null ? "Not finished" : `${value.toFixed(1)} s`;

export const percent = (share: number) => `${String(Math.round(share * 100))}%`;

const documentLabels: Record<string, string> = {
  policy_wording: "Policy wording",
  claims_manual: "Claims manual",
};

// "claims_manual 9" becomes "Claims manual, section 9".
export const sectionLabel = (section: string) => {
  const [document, number] = section.split(" ");
  return `${documentLabels[document] ?? document}, section ${number}`;
};

export const label = (value: string) => {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};
