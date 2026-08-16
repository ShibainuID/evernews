"use client";

import { Settings, LogOut } from "lucide-react";

export function Header() {
  return (
    <header className="bg-white">
      <div className="mx-auto flex max-w-[1600px] items-center justify-between px-4 py-3 lg:px-10 xl:px-16">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand text-sm font-semibold text-white">
          GK
        </div>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/logo.svg" alt="Evernews" className="h-6 w-auto" />
        <div className="flex items-center text-brand">
          <button aria-label="Settings" className="flex h-11 w-11 items-center justify-center rounded-full hover:bg-black/5">
            <Settings size={20} />
          </button>
          <button aria-label="Log out" className="flex h-11 w-11 items-center justify-center rounded-full hover:bg-black/5">
            <LogOut size={18} />
          </button>
        </div>
      </div>
    </header>
  );
}
