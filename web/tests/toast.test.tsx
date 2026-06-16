import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Toast from "@/components/Toast";

describe("Toast", () => {
  it("renders a warning toast with its badge, title and a polite live region", () => {
    render(<Toast severity="warning" title="Calibration drift" detail="gap 0.08 >= 0.05" />);
    expect(screen.getByText("Calibration drift")).toBeInTheDocument();
    expect(screen.getByText("gap 0.08 >= 0.05")).toBeInTheDocument();
    expect(screen.getByText(/warning/i)).toBeInTheDocument(); // severity badge label
    const region = screen.getByRole("status");
    expect(region).toHaveAttribute("aria-live", "polite");
  });

  it("uses an assertive alert role for errors", () => {
    render(<Toast severity="error" title="Drawdown breached" />);
    const region = screen.getByRole("alert");
    expect(region).toHaveAttribute("aria-live", "assertive");
  });

  it("calls onDismiss when the dismiss button is clicked", async () => {
    const onDismiss = vi.fn();
    const user = userEvent.setup();
    render(<Toast severity="info" title="Heads up" onDismiss={onDismiss} />);
    await user.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});
