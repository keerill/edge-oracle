import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Badge from "@/components/Badge";
import GlassCard from "@/components/GlassCard";
import EdgeMeter from "@/components/EdgeMeter";
import ThemeToggle from "@/components/ThemeToggle";
import { THEME_STORAGE_KEY } from "@/lib/theme";

describe("Badge", () => {
  it("renders its label", () => {
    render(<Badge variant="pass">Gate ✓</Badge>);
    expect(screen.getByText("Gate ✓")).toBeInTheDocument();
  });

  it("renders a dot only when requested", () => {
    const { container } = render(<Badge dot>Live</Badge>);
    // The dot is the single aria-hidden span inside the badge.
    expect(container.querySelectorAll("span[aria-hidden]").length).toBe(1);
  });
});

describe("GlassCard", () => {
  it("renders children", () => {
    render(<GlassCard>panel body</GlassCard>);
    expect(screen.getByText("panel body")).toBeInTheDocument();
  });

  it("honours the `as` prop to change the element", () => {
    render(
      <ul>
        <GlassCard as="li">row</GlassCard>
      </ul>,
    );
    expect(screen.getByRole("listitem")).toHaveTextContent("row");
  });
});

describe("EdgeMeter", () => {
  it("exposes a meter role with accessible value text", () => {
    render(<EdgeMeter edgeBps={184} thresholdBps={70} label="Net edge" />);
    const meter = screen.getByRole("meter", { name: "Net edge" });
    expect(meter).toHaveAttribute("aria-valuenow", "184");
    expect(meter.getAttribute("aria-valuetext")).toContain("gate 70 bps");
  });

  it("shows a signed value with the bps unit", () => {
    render(<EdgeMeter edgeBps={96} thresholdBps={60} />);
    expect(screen.getByText("+96")).toBeInTheDocument();
    expect(screen.getByText("bps")).toBeInTheDocument();
  });
});

describe("ThemeToggle", () => {
  it("toggles data-theme and persists the choice", async () => {
    const user = userEvent.setup();
    document.documentElement.setAttribute("data-theme", "dark");
    render(<ThemeToggle />);

    const sw = screen.getByRole("switch");
    expect(sw).toHaveAttribute("aria-checked", "true");

    await user.click(sw);
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(sw).toHaveAttribute("aria-checked", "false");
  });
});
