import type { Metadata, Viewport } from "next";
import { Inter, Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const jakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-jakarta",
});

export const metadata: Metadata = {
  title: "Evernews - trace where a clip really came from",
  description:
    "Drop in a short clip and see what we found: where it likely came from, what it currently claims, and whether the two still agree.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${inter.variable} ${jakarta.variable}`}>
      <body className="font-sans text-black">
        <div className="mx-auto flex min-h-screen w-full max-w-[480px] flex-col pb-[env(safe-area-inset-bottom)] pt-[env(safe-area-inset-top)] sm:my-10 sm:min-h-0 sm:overflow-hidden sm:rounded-[2rem] sm:border sm:border-black/10 sm:pb-0 sm:pt-0 sm:shadow-[0_30px_60px_-15px_rgba(1,51,161,0.25)]">
          {children}
        </div>
      </body>
    </html>
  );
}
