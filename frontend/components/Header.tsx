"use client";

import { Megaphone, Settings, LogOut } from "lucide-react";

export function Header() {
  return (
    <header className="flex items-center justify-between bg-white px-4 py-3">
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand text-sm font-semibold text-white">
        GK
      </div>
      <div className="flex items-center gap-1.5 text-brand">
        <Megaphone size={20} className="rotate-[-10deg]" />
        <span className="text-lg font-extrabold tracking-tight">
          EVER<span className="font-medium">NEWS</span>
        </span>
      </div>
      <div className="flex items-center text-brand">
        <button aria-label="Settings" className="flex h-11 w-11 items-center justify-center rounded-full hover:bg-black/5">
          <Settings size={20} />
        </button>
        <button aria-label="Log out" className="flex h-11 w-11 items-center justify-center rounded-full hover:bg-black/5">
          <LogOut size={18} />
        </button>
      </div>
    </header>
  );
}
