"use client";

import { useState } from "react";
import { Header } from "@/components/Header";
import { HeroCard } from "@/components/HeroCard";
import {
  FindingsDetail,
  type YourClipPreview,
} from "@/components/FindingsDetail";
import { RecentUploads } from "@/components/RecentUploads";
import { HowItWorks } from "@/components/HowItWorks";
import type { VerificationResult } from "@/lib/api";

export default function Home() {
  const [result, setResult] = useState<VerificationResult | null>(null);
  const [yourClip, setYourClip] = useState<YourClipPreview | null>(null);
  const [heroKey, setHeroKey] = useState(0);

  function handleResult(
    next: VerificationResult | null,
    preview?: YourClipPreview,
  ) {
    setResult(next);
    setYourClip(next ? (preview ?? null) : null);
  }

  function handleTraceAnother() {
    handleResult(null);
    setHeroKey((k) => k + 1);
  }

  return (
    <>
      <Header />

      <main className="flex-1 px-4 pb-8 pt-5 lg:px-10 lg:pt-8 xl:px-16">
        <div className="mx-auto max-w-[1600px] lg:grid lg:grid-cols-[minmax(0,1fr)_380px] lg:gap-8">
          <div className="space-y-4 lg:flex lg:flex-col lg:space-y-6">
            <HeroCard
              key={heroKey}
              onResult={handleResult}
              fillHeight={!result}
            />
            {result && (
              <FindingsDetail
                key={result.verification_id}
                result={result}
                yourClip={yourClip}
                onReset={handleTraceAnother}
              />
            )}
          </div>
          <aside className="mt-4 space-y-4 lg:sticky lg:top-8 lg:mt-0 lg:space-y-6">
            <RecentUploads />
            <HowItWorks />
          </aside>
        </div>
      </main>
    </>
  );
}
