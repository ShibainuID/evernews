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
        <div className="mx-auto flex min-h-screen w-full max-w-[480px] flex-col pb-[env(safe-area-inset-bottom)] pt-[env(safe-area-inset-top)] sm:max-w-xl lg:max-w-2xl xl:max-w-3xl">
          {children}
        </div>
      </body>
    </html>
  );
}
