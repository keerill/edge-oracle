import type { Metadata } from "next";
import { Syne, Sora, JetBrains_Mono } from "next/font/google";
import { NO_FLASH_SCRIPT } from "@/lib/theme";
import AppShell from "@/components/AppShell";
import NotificationsProvider from "@/components/NotificationsProvider";
import "@/styles/globals.scss";

const syne = Syne({
  subsets: ["latin"],
  weight: ["700", "800"],
  variable: "--font-syne",
  display: "swap",
});

const sora = Sora({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-sora",
  display: "swap",
});

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-jetbrains",
  display: "swap",
});

export const metadata: Metadata = {
  title: "EdgeOracle — quant advisor",
  description:
    "Quantitative edge signals on Polymarket. Advisor, not executor.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // data-theme is set by NO_FLASH_SCRIPT before paint; default below is a
    // fallback for no-JS. suppressHydrationWarning: the script mutates the attr.
    <html
      lang="en"
      data-theme="dark"
      suppressHydrationWarning
      className={`${syne.variable} ${sora.variable} ${jetbrains.variable}`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH_SCRIPT }} />
      </head>
      <body>
        <AppShell>
          <NotificationsProvider>{children}</NotificationsProvider>
        </AppShell>
      </body>
    </html>
  );
}
