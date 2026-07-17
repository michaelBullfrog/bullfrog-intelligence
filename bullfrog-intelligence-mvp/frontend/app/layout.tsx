import type { ReactNode } from "react";
import "./styles.css";

export const metadata = {
  title: "Bullfrog Intelligence",
  description: "Private AI operations portal for Bullfrog Group",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
