export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-2 p-8 text-center">
      <h1 className="text-2xl font-bold">Page not found</h1>
      <p className="text-sm text-black/60">That page doesn't exist. Head back to the homepage.</p>
      <a href="/" className="mt-2 rounded-full bg-brand px-4 py-2 text-sm font-bold text-white">
        Back home
      </a>
    </div>
  );
}
