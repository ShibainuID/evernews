import Link from "next/link";

export function LandingHeader() {
  return (
    <header className="absolute inset-x-0 top-0 z-20">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-5 sm:px-8">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/logo.svg" alt="Evernews" className="h-9 w-auto brightness-0 invert" />
        <Link
          href="/verify"
          className="rounded-full border border-white/25 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-white/10"
        >
          Trace a clip
        </Link>
      </div>
    </header>
  );
}
