import { z } from "zod";

// Personal sizing / risk config. Money/fractions cross the wire as Decimal strings; we coerce
// to numbers for the sliders. `risk_threshold` is the max acceptable probability of loss [0,1].
const money = z.coerce.number();

export const UserConfigSchema = z.object({
  bankroll: money,
  kelly_frac: money,
  kelly_cap: money,
  corr_cap_frac: money,
  risk_threshold: money,
});
export type UserConfig = z.infer<typeof UserConfigSchema>;
