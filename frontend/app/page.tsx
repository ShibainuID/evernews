import { LandingHeader } from "@/components/landing/LandingHeader";
import { Hero } from "@/components/landing/Hero";
import { PipelineExperience } from "@/components/landing/PipelineExperience";
import { ProofGallery } from "@/components/landing/ProofGallery";
import { MILNote } from "@/components/landing/MILNote";
import { CTASection } from "@/components/landing/CTASection";
import { LandingFooter } from "@/components/landing/LandingFooter";
import { Atmosphere } from "@/components/landing/Atmosphere";

export default function LandingPage() {
  return (
    <>
      {/* Single fixed backdrop behind the hero + pipeline sections, see
          Atmosphere.tsx for why this is rendered once at page level. */}
      <Atmosphere />
      <LandingHeader />
      {/* `relative` (not just a style choice): a `position:fixed` element
          always paints above plain static content regardless of DOM order,
          so main+footer need to be positioned too to correctly stack above
          the fixed Atmosphere layer wherever their own sections are opaque. */}
      <main className="relative">
        <Hero />
        <PipelineExperience />
        <ProofGallery />
        <MILNote />
        <CTASection />
      </main>
      <LandingFooter />
    </>
  );
}
