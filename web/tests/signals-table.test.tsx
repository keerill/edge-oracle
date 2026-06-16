import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SignalsTable from "@/app/signals/SignalsTable";
import type { AdvisedSignal } from "@/lib/schemas/signal";

function sig(over: Partial<AdvisedSignal>): AdvisedSignal {
  return {
    id: "x",
    time: "2026-06-16T12:00:00+00:00",
    market_id: "m",
    condition_id: "c",
    market_question: "Q",
    strategy: "extreme_correction",
    kind: "buy_yes",
    market_price: 0.4,
    p: 0.55,
    edge: 0.13,
    net_edge: 0.06,
    recommended_size_usd: 50,
    recommended_size_pct: 0.05,
    confidence: 0.107143,
    gate_passed: true,
    gate: null,
    ...over,
  };
}

const SIGNALS: AdvisedSignal[] = [
  sig({ id: "a", market_question: "Alpha", net_edge: 0.06, recommended_size_usd: 50 }),
  sig({
    id: "b",
    market_question: "Bravo",
    strategy: "set_arb",
    kind: "long_set",
    p: null,
    net_edge: 0.03,
    recommended_size_usd: 0,
    market_price: 0.95,
  }),
  sig({
    id: "c",
    market_question: "Charlie",
    strategy: "favourite_longshot",
    kind: "buy_no",
    p: null,
    net_edge: 0,
    recommended_size_usd: 0,
    gate_passed: false,
    market_price: 0.1,
    confidence: 0.5,
  }),
];

const NAMES = ["Alpha", "Bravo", "Charlie"] as const;

function rowOrder(): string[] {
  // Skip the header row; the first cell of each body row leads with the market question.
  return screen
    .getAllByRole("row")
    .slice(1)
    .map((r) => {
      const text = within(r).getAllByRole("cell")[0]!.textContent ?? "";
      return NAMES.find((n) => text.startsWith(n)) ?? text;
    });
}

describe("SignalsTable", () => {
  it("renders a row per signal with formatted money, p, and side", () => {
    render(<SignalsTable signals={SIGNALS} />);
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("BUY YES")).toBeInTheDocument();
    expect(screen.getByText("Extreme correction")).toBeInTheDocument();
    expect(screen.getByText("$50.00")).toBeInTheDocument(); // recommended size (USD)
    expect(screen.getByText("0.55")).toBeInTheDocument(); // your p
    // Links point at the dedicated detail route.
    expect(screen.getByRole("link", { name: /Alpha/ })).toHaveAttribute("href", "/signals/a");
  });

  it("sorts by net edge descending by default and toggles on click", async () => {
    const user = userEvent.setup();
    render(<SignalsTable signals={SIGNALS} />);
    expect(rowOrder()).toEqual(["Alpha", "Bravo", "Charlie"]);

    await user.click(screen.getByRole("button", { name: /Net edge/ }));
    expect(rowOrder()).toEqual(["Charlie", "Bravo", "Alpha"]);
  });

  it("shows an empty state when there are no signals", () => {
    render(<SignalsTable signals={[]} />);
    expect(screen.getByText(/No open signals/i)).toBeInTheDocument();
  });
});
