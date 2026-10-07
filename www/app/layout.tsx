import type { Metadata } from "next";
import { Space_Mono } from "next/font/google";
import "./globals.css";

const spaceMono = Space_Mono({
  subsets: ["latin"],
  weight: ["400", "700"],
  variable: "--font-space",
});

export const metadata: Metadata = {
  icons: {
    icon: "/images/favicon.gif",
    apple: "/images/webclip.gif",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="fr" className={spaceMono.variable}>
      <body>{children}</body>
    </html>
  );
}
