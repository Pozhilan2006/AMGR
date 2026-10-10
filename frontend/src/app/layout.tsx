import type { Metadata } from "next";
import type { ReactNode } from "react";

import { DashboardShell } from "@/components/layout/dashboard-shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "AMGR | Runtime Governance",
  description: "AMGR runtime information-flow control plane.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <DashboardShell>{children}</DashboardShell>
      </body>
    </html>
  );
}