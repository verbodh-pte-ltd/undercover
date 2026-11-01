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

const days = new Intl.DateTimeFormat("en-US", {
  dateStyle: "medium",
  timeZone: "UTC",
});

// A date with no time, such as "2026-06-18", shows as "Jun 18, 2026" wherever the reader is.
export const day = (iso: string) => days.format(new Date(iso));

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

// The judge's grade runs from 0 (F) to 3 (A).
export const grade = (value: number | null) =>
  value === null ? "Not graded" : `${value.toFixed(2)} of 3`;

// The judge's checks are probabilities, shown as yes or no.
export const yesNo = (probability: number) =>
  probability >= 0.5 ? "Yes" : "No";

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
