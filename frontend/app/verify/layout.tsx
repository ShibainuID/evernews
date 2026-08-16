export default function VerifyLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // Below `lg` this stays a capped single column (phone-first); at
    // `lg`+ the cap lifts entirely and the page's own grid (see
    // verify/page.tsx) takes over the full viewport width.
    <div className="mx-auto flex min-h-screen w-full max-w-[480px] flex-col bg-[linear-gradient(180deg,#ffffff_0%,#c6e1ff_100%)] pb-[env(safe-area-inset-bottom)] pt-[env(safe-area-inset-top)] sm:max-w-xl md:max-w-2xl lg:max-w-none">
      {children}
    </div>
  );
}
