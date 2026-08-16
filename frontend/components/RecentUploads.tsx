"use client";

const RECENT = [
  { title: "Sari Roti Zionist Allegations", from: "#3a3a3a", to: "#6b6b6b" },
  { title: "Aqua's Water Origin Questioned", from: "#0b3d91", to: "#1f9c8c" },
  { title: "Student with Pink iPad Controversy", from: "#5e2ca5", to: "#c24d9c" },
];

export function RecentUploads() {
  return (
    <section className="mx-4 mt-4 rounded-card bg-brand-panel p-5">
      <h2 className="text-xl font-bold text-white">Your recent uploads and traces</h2>
      <div className="mt-4 flex gap-3 overflow-x-auto pb-1">
        {RECENT.map((item) => (
          <div
            key={item.title}
            className="relative h-[205px] w-[115px] shrink-0 overflow-hidden rounded-card"
            style={{ background: `linear-gradient(160deg, ${item.from}, ${item.to})` }}
          >
            <span className="absolute bottom-2 left-2 right-2 text-xs font-semibold leading-tight text-white">
              {item.title}
            </span>
          </div>
        ))}
      </div>
      <button className="mt-2 text-[10px] font-semibold text-[#aadaff]">View entire list</button>
    </section>
  );
}
