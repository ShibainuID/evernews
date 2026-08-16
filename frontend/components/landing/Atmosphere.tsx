const STAR_LAYER =
  "radial-gradient(1.5px 1.5px at 20px 30px, rgba(255,255,255,0.7), transparent)," +
  "radial-gradient(1px 1px at 78px 140px, rgba(255,255,255,0.5), transparent)," +
  "radial-gradient(1px 1px at 130px 60px, rgba(255,255,255,0.4), transparent)," +
  "radial-gradient(2px 2px at 165px 190px, rgba(255,255,255,0.6), transparent)," +
  "radial-gradient(1px 1px at 45px 170px, rgba(255,255,255,0.35), transparent)," +
  "radial-gradient(1.5px 1.5px at 190px 40px, rgba(255,255,255,0.5), transparent)";

/**
 * Ambient depth for the dark trail sections (hero + pipeline): a solid navy
 * base, a soft two-tone glow (brand blue + consistent-teal, echoing the
 * trail's own palette), and a faint starfield.
 *
 * Rendered ONCE, fixed to the viewport, behind everything. The dark
 * sections stay transparent over it so the pattern reads as one continuous
 * backdrop rather than each section tiling its own copy from y=0, which is
 * what produced a visible seam at the section boundary.
 *
 * Deliberately no negative z-index: a `position:fixed` element can never
 * paint above the page canvas's own background that way (the body/html
 * background always paints at the true bottom, below even negative
 * z-index content, by spec). Instead this relies on DOM order — rendered
 * first in page.tsx, so plain z-index:auto siblings after it paint on top.
 */
export function Atmosphere() {
  return (
    <div className="pointer-events-none fixed inset-0 overflow-hidden bg-[#050b24]">
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 55% 50% at 75% 30%, rgba(127,184,255,0.16), transparent 70%)," +
            "radial-gradient(ellipse 50% 45% at 15% 80%, rgba(95,224,184,0.09), transparent 70%)",
        }}
      />
      <div
        className="absolute inset-0 opacity-70"
        style={{ backgroundImage: STAR_LAYER, backgroundSize: "210px 210px" }}
      />
    </div>
  );
}
