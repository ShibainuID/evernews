"use client";

const RECENT = [
  { title: "Sari Roti Zionist Allegations", image: "/recent/sari-roti.jpg" },
  { title: "Aqua's Water Origin Questioned", image: "/recent/aqua-water.jpg" },
  { title: "Taxi and KRL Crash", image: "/recent/krl-crash.jpg" },
];

export function RecentUploads() {
  return (
    <section className="rounded-card bg-brand-panel p-5">
      <h2 className="text-xl font-bold text-white">Your recent uploads and traces</h2>
      <div className="mt-4 flex gap-3 overflow-x-auto pb-1">
        {RECENT.map((item) => (
          <div key={item.title} className="relative h-[205px] w-[115px] shrink-0 overflow-hidden rounded-card">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={item.image} alt="" className="h-full w-full object-cover" />
            <div className="absolute inset-x-0 bottom-0 h-2/3 bg-gradient-to-t from-black/80 to-transparent" />
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
