# Assets not pulled from Figma

The Figma file is view-only for the account this session used, so the
design-to-code tool couldn't download real icon/image assets (only text,
color, and layout data came through the API). Everywhere an asset was
needed, a placeholder was used instead. This is the list of what's faked and
exactly what to swap it for once you can export the real files (open the
file yourself, select the node, and use Figma's "Export" panel, or the
`download_assets` MCP tool if you're running with edit access).

Each entry gives: the Figma node to export, where to save the exported file,
and the exact line to change in the component.

## 1. Logo (header)

- **Figma node:** `1:95` ("Group 1171277463", inside every "Landing Page"
  frame's header) — the "EVERNEWS" wordmark + megaphone mark.
- **Save to:** `frontend/public/logo.svg`
- **Replace in:** `frontend/components/Header.tsx`, the block:
  ```tsx
  <div className="flex items-center gap-1.5 text-brand">
    <Megaphone size={20} className="rotate-[-10deg]" />
    <span className="text-lg font-extrabold tracking-tight">
      EVER<span className="font-medium">NEWS</span>
    </span>
  </div>
  ```
  with:
  ```tsx
  <img src="/logo.svg" alt="Evernews" className="h-6 w-auto" />
  ```
  (drop the now-unused `Megaphone` import from `lucide-react`).

## 2. Header icons

- **Settings icon** — Figma node `1:88` ("Setting"), exported image is
  `1:89` ("image 4"). Save to `frontend/public/icons/settings.svg`.
- **Logout/share icon** — Figma node `1:91` ("Logout"), exported image is
  `1:92` ("image 5"). Save to `frontend/public/icons/logout.svg`.
- **Replace in:** `frontend/components/Header.tsx`:
  ```tsx
  <Settings size={20} />   ->   <img src="/icons/settings.svg" className="h-5 w-5" alt="" />
  <LogOut size={18} />     ->   <img src="/icons/logout.svg" className="h-[18px] w-[18px]" alt="" />
  ```
  (drop the `Settings`/`LogOut` imports if nothing else uses them).

## 3. "How Evernews Works" step icons

Four masked icon images, one per step, all inside frame `1:29`'s
"Frame 1000003807" group:

| Step | Figma node | Save to |
|---|---|---|
| "You insert your clip for us to analyze" | `1:54` (image 2735) | `frontend/public/icons/step-upload.svg` |
| "We pull keyframes, on-screen text, & speech" | `1:59` (image 2735, 2nd instance) | `frontend/public/icons/step-extract.svg` |
| "We match against known sources using visual embeddings" | `1:64` (image 2736) | `frontend/public/icons/step-match.svg` |
| "We check for recontextualization..." | `1:69` (image 2738) | `frontend/public/icons/step-flag.svg` |

- **Replace in:** `frontend/components/HowItWorks.tsx`, the `STEPS` array
  currently maps to lucide icons (`Upload`, `ScanText`, `Video`,
  `FlagTriangleRight`). Swap the `<Icon size={18} />` render for
  `<img src={step.iconSrc} className="h-[18px] w-[18px]" alt="" />` and give
  each step entry an `iconSrc` pointing at the files above instead of a
  lucide component.

## 4. "Your recent uploads and traces" thumbnails

Three demo-history cards, each a masked photo inside instance `1:43`
("Frame 1000003806"):

| Card | Figma node (photo) | Save to |
|---|---|---|
| "Sari Roti Zionist Allegations" | `1343:3316` (image 2733) | `frontend/public/recent/sari-roti.jpg` |
| "Aqua's Water Origin Questioned" | `1343:3330` (image 2733, 2nd instance) | `frontend/public/recent/aqua-water.jpg` |
| "Student with Pink iPad Controversy" | `1344:3347` (image 2733, 3rd instance) | `frontend/public/recent/pink-ipad.jpg` |

- **Replace in:** `frontend/components/RecentUploads.tsx` — the `RECENT`
  array currently has `from`/`to` gradient stops per card instead of a real
  photo. Add an `image` field per entry (e.g. `"/recent/sari-roti.jpg"`) and
  swap the gradient `style` div for an `<img src={item.image} className="h-full w-full object-cover" alt="" />`
  behind the title overlay.

## Not a gap (already correct, no action needed)

- Fonts: Inter + Plus Jakarta Sans, loaded via `next/font/google` in
  `frontend/app/layout.tsx` — matches the Figma type styles exactly.
- The "+" glyph in the empty upload drop zone was a plain text character in
  Figma, not an image, so the current icon (lucide `Plus`) is a fine stand-in
  and doesn't need a real asset.
- The "GK" avatar circle is plain text-on-color in Figma too, no image.

## Sample video clips (separate from Figma)

Not a Figma asset, but also currently missing: the landing page brief calls
for a couple of pre-loaded example clips people can try without uploading
their own. There's no upload feature wired for this yet, drop 1-2 short
(under 15s) `.mp4` files into `frontend/public/samples/` and say so if you
want that wired back in.
