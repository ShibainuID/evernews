export function LandingFooter() {
  return (
    <footer className="relative bg-[#050b24] py-10">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-5 text-sm text-white/50 sm:flex-row sm:px-8">
        <span>Evernews, built for the AI &amp; Media Literacy track.</span>
        <a href="mailto:hello@evernews.app" className="font-semibold text-white/70 hover:text-white">
          hello@evernews.app
        </a>
      </div>
    </footer>
  );
}
