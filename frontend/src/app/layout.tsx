import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/app-shell";

export const metadata: Metadata = {
  title: "MARKETSHELPER",
  description: "Панель управления продажами на Wildberries",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ru" className="dark h-full antialiased">
      <body className="min-h-full flex bg-background text-foreground">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
