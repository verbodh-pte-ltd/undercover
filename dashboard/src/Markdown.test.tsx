import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { Markdown } from "./Markdown";

afterEach(cleanup);

it("formats an answer with claim ids linked", () => {
  const text = [
    "## Needs you",
    "Two payouts wait for **your sign-off**, see *CLM-1012*.",
    "",
    "- CLM-1012 for `$1,400.00`",
    "- [the queue](#/) and [elsewhere](javascript:alert(1))",
    "",
    "1. First",
    "2) Second",
    "",
    "| Criterion | Met |",
    "|---|:-:|",
    "| Accuracy | Yes |",
  ].join("\n");
  render(<Markdown text={text} />);
  expect(screen.getByRole("heading", { name: "Needs you" })).toBeDefined();
  expect(screen.getByText("your sign-off").tagName).toBe("STRONG");
  const links = screen.getAllByRole("link", { name: "CLM-1012" });
  expect(links).toHaveLength(2);
  expect(links[0].getAttribute("href")).toBe("#/claims/CLM-1012");
  expect(links[0].parentElement?.tagName).toBe("EM");
  expect(screen.getByText("$1,400.00").tagName).toBe("CODE");
  expect(screen.getByRole("link", { name: "the queue" })).toBeDefined();
  expect(screen.queryByRole("link", { name: "elsewhere" })).toBeNull();
  expect(screen.getByText(/and elsewhere/)).toBeDefined();
  expect(screen.getByText("Second").closest("ol")).not.toBeNull();
  expect(screen.getByRole("columnheader", { name: "Met" })).toBeDefined();
  expect(screen.getByRole("cell", { name: "Accuracy" })).toBeDefined();
});

it("shows the model's text as text, never as HTML", () => {
  render(
    <Markdown
      text={
        "```\n<script>alert(1)</script>\n```\n\n<b>bold</b> * star and ` tick"
      }
    />,
  );
  expect(screen.getByText("<script>alert(1)</script>").tagName).toBe("PRE");
  expect(screen.getByText("<b>bold</b> * star and ` tick").tagName).toBe("P");
  expect(document.querySelector("script, b")).toBeNull();
});
