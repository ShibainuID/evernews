"use client";

import { useState } from "react";
import { Header } from "@/components/Header";
import { HeroCard } from "@/components/HeroCard";
import { FindingsDetail } from "@/components/FindingsDetail";
import { RecentUploads } from "@/components/RecentUploads";
import { HowItWorks } from "@/components/HowItWorks";
import type { VerificationResult } from "@/lib/api";

export default function Home() {
  const [result, setResult] = useState<VerificationResult | null>(null);

  return (
    <>
      <Header />
      <HeroCard onResult={setResult} />
      {result && <FindingsDetail result={result} onReset={() => setResult(null)} />}
      <RecentUploads />
      <HowItWorks />
      <div className="h-8" />
    </>
  );
}
