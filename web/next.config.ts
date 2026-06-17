import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Emit a self-contained server bundle (.next/standalone) for a slim production image.
  output: "standalone",
  sassOptions: {
    // Make shared mixins/functions available to every SCSS Module automatically.
    // (Only mixins/vars — no CSS output — so prepending to every file is idempotent.)
    additionalData: `@use "styles/mixins" as *;`,
    includePaths: ["src"],
  },
};

export default nextConfig;
