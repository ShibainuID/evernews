// Ambient module for plain (non-CSS-module) stylesheet imports, e.g. `import "./globals.css"`.
// next-env.d.ts only declares `*.module.css`; this covers the side-effect-only case.
declare module "*.css";
