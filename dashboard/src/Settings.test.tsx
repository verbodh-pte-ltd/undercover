import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Settings as Loaded } from "./api";
import { dana, fakeBackend, marcus, signIn } from "./fixtures";
import { Settings } from "./Settings";
import { applyTheme } from "./theme";

const loaded: Loaded = {
  settings: {
    approval_limit: 1000,
    objectives: { accuracy: 0.6, grade: 1 },
    noise: {
      accuracy: 0.1,
      grade: 0.15,
      cost_per_claim: 0.15,
      seconds_per_claim: 0.15,
    },
    tolerance: {
      accuracy: 0.1,
      grade: 0.15,
      cost_per_claim: 1,
      seconds_per_claim: 0.25,
    },
    round_spending_limit: 0.5,
    agents_at_once: 25,
  },
  models_allowed: [
    {
      model: "openai/gpt-6-luna",
      reasoning_efforts: ["none", "low", "medium"],
      input_price: 0.1,
      output_price: 0.5,
    },
  ],
  changes: {
    agents_at_once: {
      handler: "Dana Whitfield",
      changed_at: "2026-11-01T09:00:00+00:00",
    },
  },
};

beforeEach(() => {
  signIn();
});

afterEach(() => {
  cleanup();
  applyTheme("system");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("shows every setting, the models allowed and who last changed each", async () => {
  fakeBackend({ "/api/settings": loaded });
  render(<Settings handler={dana} />);
  const limit = await screen.findByLabelText("Approval limit");
  expect(limit).toHaveProperty("value", "1000");
  // The input edits a plain number; the dollars read as money over it.
  expect(limit.nextElementSibling?.textContent).toBe("$1,000.00");
  expect(screen.getByLabelText("Held-out accuracy at least")).toHaveProperty(
    "value",
    "60",
  );
  expect(screen.getByLabelText("Noise on cost per claim")).toHaveProperty(
    "value",
    "15",
  );
  expect(screen.getByLabelText("Tolerance on cost per claim")).toHaveProperty(
    "value",
    "100",
  );
  expect(screen.getByLabelText("Spending limit for each round")).toHaveProperty(
    "value",
    "0.5",
  );
  expect(screen.getByText("openai/gpt-6-luna")).toBeDefined();
  expect(screen.getByText("none, low, medium")).toBeDefined();
  // A model's output price, and the spending limit as money over its input.
  expect(screen.getAllByText("$0.50")).toHaveLength(2);
  expect(screen.getByText(/Last changed by Dana Whitfield/)).toBeDefined();
  expect(screen.getByRole("button", { name: "Save changes" })).toHaveProperty(
    "disabled",
    true,
  );
});

it("asks in its own dialog before saving, then shows the new value", async () => {
  const saved = {
    ...loaded,
    settings: { ...loaded.settings, approval_limit: 2000 },
  };
  const fetchMock = fakeBackend({ "/api/settings": loaded });
  render(<Settings handler={dana} />);
  fireEvent.change(await screen.findByLabelText("Approval limit"), {
    target: { value: "2000" },
  });
  fireEvent.change(screen.getByLabelText("Noise on held-out accuracy"), {
    target: { value: "5" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  const dialog = screen.getByRole("dialog", { name: "Save these changes?" });
  expect(dialog.textContent).toContain(
    "Approval limit: $1,000.00 to $2,000.00",
  );
  expect(dialog.textContent).toContain(
    "Noise on held-out accuracy: 10 % points to 5 % points",
  );
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  // Only the settings were read: nothing was saved.
  expect(fetchMock).toHaveBeenCalledTimes(1);

  fakeBackend({ "/api/settings": saved });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  const confirm = screen.getAllByRole("button", { name: "Save changes" })[1];
  fireEvent.click(confirm);
  expect((await screen.findByRole("status")).textContent).toContain("Saved");
  expect(screen.getByLabelText("Approval limit")).toHaveProperty(
    "value",
    "2000",
  );
});

it("sends every setting with the sign-in", async () => {
  const fetchMock = fakeBackend({ "/api/settings": loaded });
  render(<Settings handler={dana} />);
  fireEvent.change(await screen.findByLabelText("Claim agents at once"), {
    target: { value: "10" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  fireEvent.click(screen.getAllByRole("button", { name: "Save changes" })[1]);
  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith("/api/settings", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer token",
      },
      body: JSON.stringify({ ...loaded.settings, agents_at_once: 10 }),
    });
  });
});

it("says why a change was refused", async () => {
  fakeBackend({ "/api/settings": loaded });
  render(<Settings handler={dana} />);
  fireEvent.change(await screen.findByLabelText("Approval limit"), {
    target: { value: "2000" },
  });
  fakeBackend({
    "/api/settings": [
      403,
      { detail: "Dana Whitfield can't change the settings" },
    ],
  });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  fireEvent.click(screen.getAllByRole("button", { name: "Save changes" })[1]);
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Dana Whitfield can't change the settings",
  );
});

it("closes the dialog on Escape", async () => {
  fakeBackend({ "/api/settings": loaded });
  render(<Settings handler={dana} />);
  fireEvent.change(await screen.findByLabelText("Approval limit"), {
    target: { value: "2000" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("shows Marcus the settings read only, and says why", async () => {
  fakeBackend({ "/api/settings": loaded });
  render(<Settings handler={marcus} />);
  expect(await screen.findByLabelText("Approval limit")).toHaveProperty(
    "disabled",
    true,
  );
  expect(
    screen.getByText(/Only a handler who can sign off payouts over/),
  ).toBeDefined();
  expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
});

it("says what to do when the settings can't be loaded", async () => {
  fakeBackend({});
  render(<Settings handler={dana} />);
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Check that the backend is running",
  );
});

it("switches the appearance at once and remembers it", async () => {
  fakeBackend({ "/api/settings": loaded });
  render(<Settings handler={dana} />);
  expect(await screen.findByLabelText("Same as the computer")).toHaveProperty(
    "checked",
    true,
  );
  fireEvent.click(screen.getByLabelText("Dark"));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(localStorage.getItem("undercover-theme")).toBe("dark");
  cleanup();
  render(<Settings handler={dana} />);
  expect(await screen.findByLabelText("Dark")).toHaveProperty("checked", true);
  fireEvent.click(screen.getByLabelText("Light"));
  expect(document.documentElement.dataset.theme).toBe("light");
  fireEvent.click(screen.getByLabelText("Same as the computer"));
  expect(document.documentElement.dataset.theme).toBeUndefined();
  expect(localStorage.getItem("undercover-theme")).toBeNull();
});
