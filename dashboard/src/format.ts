import type { Status, TodoState } from "./api";

const dollars = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

export const money = (amount: number) => dollars.format(amount);

export const statusLabels: Record<Status, string> = {
  waiting: "Waiting",
  in_progress: "In progress",
  waiting_for_sign_off: "Waiting for sign-off",
  decided: "Decided",
  needs_handler: "Left for a handler",
};

export const todoLabels: Record<TodoState, string> = {
  to_do: "To do",
  in_progress: "In progress",
  done: "Done",
  left_for_handler: "Left for a handler",
};

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
