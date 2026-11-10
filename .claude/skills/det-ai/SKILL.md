---
name: "det-ai"
description: "Scans a website for the \"AI-generated look\" (oversized headlines, generic copy, stock or AI images, template animations), scores it, rewrites the text to read human-written, then builds an improved demo. Use for det-ai, AI-looking sites, or making a site feel human."
---

# det-ai: detect the AI look, then fix it

Input: a website URL (live or a Vercel preview), or a local project folder. Optionally, notes on the company the site is for.

Output:
1. **Report:** an AI-look score from 0 to 100 (higher means more generic), broken down by category with concrete evidence (the exact headline, the stock image URL, the buzzword list), plus prioritized fixes.
2. **Rewritten text:** every visible string rewritten to read as written by a person who knows this business, in every language the site has, shown as old → new.
3. **New demo:** the site with the design and text fixes, deployed as a preview, with a before/after score.

Why this matters: clients and visitors now spot "vibe-coded" sites instantly. A site that looks or reads as generated feels cheap and untrustworthy. The goal is a site made **for this specific business**, not one that merely passes an AI detector.

## Safety rules for the user's code

- Never commit to `main`/`master` or deploy to production. Put all fixes on a new branch named `det-ai-fixes` (or a copy of the folder) and deploy only a **preview**.
- Before building, say in one line what you will touch: "New branch `det-ai-fixes` in <repo> plus a Vercel preview. `main` and the live site stay as they are."
- At the end, confirm with `git ls-remote origin main` that `main` is unchanged, and list exactly what was added (branch, commits, files changed, preview) and how to undo it.
- If the repo has a design brief or handoff notes (`BRIEF.md`, `HANDOFF.md`, `design-handoff/`), read them first. When a fix goes against an approved decision, say so instead of silently overriding it.
- API keys the user gives you (Sapling, GPTZero, Originality.ai, Hive, Sightengine) are used only for the current run: pass them as environment variables to commands, and never write them into files, commits, memory or the chat summary.

## Step 1: Get access to the site (try in this order)

1. **A browser on the user's device** (built-in browser or Claude in Chrome; load the matching browser skill first). This gives the most accurate result: computed styles, screenshots, the image provenance check. Read the page at desktop width, then at 390px mobile width if the tool can resize. Take one screenshot of the hero and one of the full page.
2. **The source code.** For a local project, read `app/`, `components/`, styles and `tailwind.config`. If the site is on the user's Vercel account, find the project (Vercel `list_projects` with a name search) and its Git repo (`list_repos`), attach the repo, and clone it. Then use the static scan below.
3. **The rendered HTML.** Use Vercel `web_fetch_vercel_url` for the user's own deployments (save the large response to a file and parse it with a script), or WebFetch otherwise. This gives the real headlines, copy, image URLs and class names even when the host is blocked from the workspace shell.
4. **Existing screenshots** in the repo (`_handoff/screenshots`, `docs/`) for visual judgement; note how old they are.

Use every source you can reach. In the report, state plainly which checks ran and which didn't. Don't guess at those.

## Step 2a: Browser scanner

Run this with the browser's JavaScript tool on each page you check (home page first, then 1–2 key inner pages):

```js
(() => {
const all=[...document.querySelectorAll('body *')].slice(0,4000), cs=e=>getComputedStyle(e), text=document.body.innerText, low=text.toLowerCase();
const heads=[...document.querySelectorAll('h1,h2,h3')].filter(e=>e.offsetParent);
const bigText=heads.map(e=>({tag:e.tagName,px:parseFloat(cs(e).fontSize),ls:cs(e).letterSpacing,text:e.innerText.trim().slice(0,90)})).filter(h=>h.px>=56).sort((a,b)=>b.px-a.px).slice(0,6);
const fonts=[...new Set(all.map(e=>cs(e).fontFamily.split(',')[0].replace(/["']/g,'').trim()))].slice(0,8);
const buzz=['seamless','elevate','unlock','empower','revolutioni','cutting-edge','next-level','game-chang','harness','transform your','supercharge','effortless','unleash','leverage','in today','world-class','tailored','robust','streamline','delve','journey','passionate about','look no further','trusted partner','innovative solution','to the next level','your vision','reimagin','crafted','bespoke','we believe'];
const buzzHits=buzz.map(w=>[w,(low.match(new RegExp(w,'g'))||[]).length]).filter(x=>x[1]);
const hue=s=>(s.match(/rgba?\([^)]+\)/g)||[]).some(c=>{const [r,g,b]=c.match(/[\d.]+/g).map(Number),mx=Math.max(r,g,b),mn=Math.min(r,g,b);if(mx-mn<60)return false;let h=mx===r?60*(((g-b)/(mx-mn))%6):mx===g?60*((b-r)/(mx-mn)+2):60*((r-g)/(mx-mn)+4);if(h<0)h+=360;return h>=235&&h<=295});
const imgs=[...document.images].map(i=>({src:i.currentSrc||i.src,alt:i.alt,w:i.naturalWidth,h:i.naturalHeight}));
const stockRe=/unsplash|pexels|pixabay|freepik|shutterstock|istock|placehold|dalle|oaidalle|openai|midjourney|replicate|leonardo|ideogram|stable-?diffusion|generated/i;
const radii={};all.forEach(e=>{const r=cs(e).borderRadius;if(r!=='0px')radii[r]=(radii[r]||0)+1});
const html=document.documentElement.outerHTML.slice(0,300000).toLowerCase();
return {
 typography:{fonts,bigText,h1Count:document.querySelectorAll('h1').length},
 copy:{words:text.split(/\s+/).length,buzzHits,emDashes:(text.match(/—/g)||[]).length,emojiHeadings:heads.filter(h=>/\p{Extended_Pictographic}/u.test(h.innerText)).map(h=>h.innerText.slice(0,40)),digits:(text.match(/\d/g)||[]).length,phones:(text.match(/\+?\d[\d\s()-]{7,}/g)||[]).length},
 images:{count:imgs.length,stockOrAiHost:imgs.filter(i=>stockRe.test(i.src)).map(i=>i.src).slice(0,10),genericAlt:imgs.filter(i=>!i.alt||/^(image|photo|picture|hero|banner|img)\b/i.test(i.alt)).length,svgBlobs:document.querySelectorAll('svg [filter],svg feTurbulence').length},
 color:{gradients:all.filter(e=>/gradient/.test(cs(e).backgroundImage)).length,purpleBlue:all.filter(e=>hue(cs(e).backgroundImage+' '+cs(e).backgroundColor)).length,glass:all.filter(e=>cs(e).backdropFilter&&cs(e).backdropFilter!=='none').length},
 motion:{cssAnimated:all.filter(e=>cs(e).animationName!=='none').length,revealHooks:document.querySelectorAll('[data-aos],[data-framer-appear-id],[class*="fade"],[class*="animate-"],[class*="reveal"],[data-scroll]').length,libs:{gsap:!!window.gsap,aos:!!window.AOS,lottie:!!document.querySelector('lottie-player,dotlottie-player,[class*="lottie"]'),framer:!!document.querySelector('[data-framer-name]'),motion:!!document.querySelector('[style*="opacity:0"],[style*="opacity: 0"]')}},
 layout:{topRadii:Object.entries(radii).sort((a,b)=>b[1]-a[1]).slice(0,3),sections:document.querySelectorAll('section').length},
 builder:{generator:[...document.querySelectorAll('meta[name=generator]')].map(m=>m.content),hints:['framer','webflow','wix','lovable','v0.dev','bolt','tilda','shadcn'].filter(k=>html.includes(k))}
};})()
```

Then check the images for AI provenance markers (C2PA content credentials, or the IPTC tag `trainedAlgorithmicMedia` that OpenAI, Adobe Firefly and others embed):

```js
await Promise.all([...document.images].slice(0,15).map(async i=>{const src=i.currentSrc||i.src;try{const b=new Uint8Array(await (await fetch(src)).arrayBuffer()).slice(0,300000);const s=new TextDecoder('latin1').decode(b);return {src,c2pa:/c2pa|jumb/i.test(s),aiTag:/trainedAlgorithmicMedia|DALL.?E|Midjourney|Firefly|Stable Diffusion|OpenAI/i.test(s)}}catch(e){return {src,error:'blocked (CORS)'}}}))
```

A blocked fetch means "unknown", not "clean". With the repo, run the same marker check on files in `public/` with `grep -c`.

## Step 2b: Static scan (source code and HTML, when there is no browser)

From the repo root:

```bash
grep -rhoE "text-\[[0-9.]+(px|rem|vw)\]|text-\[clamp\([^]]*\)\]|text-(6|7|8|9)xl" src app components 2>/dev/null | sort | uniq -c | sort -rn | head -20
grep -rnE "<h1[^>]*className" src app components 2>/dev/null | head
grep -rcE "backdrop-blur|backdrop-filter|\.glass" src app components 2>/dev/null | grep -v ":0"
grep -rcE "gradient" src app components 2>/dev/null | grep -v ":0"
grep -rhoE "rounded-[a-z0-9\[\]]+" src app components 2>/dev/null | sort | uniq -c | sort -rn | head -6
grep -rhoE "@keyframes [a-zA-Z-]+|data-aos|framer-motion|IntersectionObserver|marquee|three" src app components package.json 2>/dev/null | sort | uniq -c
cat public/**/CREDITS* 2>/dev/null | head -20; grep -rhoiE "unsplash|pexels|mixkit|freepik|dalle|midjourney" src public/*.md README* 2>/dev/null | sort | uniq -c
```

Collect all visible text from the translation files (`messages/*.json`, `locales/`), seed or CMS data, and components with hard-coded strings. You'll need this inventory for Step 5.

## Step 3: Judge each category

The scanners collect evidence; you make the call.

| Category | Weight | AI-look signals |
|---|---|---|
| **Typography** | 20 | Hero headline ≥72px taking most of the viewport, thin weight with tight negative tracking, sans headline with one italic serif "accent" word in gold or a gradient, glass pill label above a centered headline, Inter/Geist/system font only, one weight everywhere |
| **Copy** | 25 | See the text checklist in Step 5. Also: no names, places, prices or phone numbers; made-up testimonials ("Sarah J., CEO"); sample prices shown as real |
| **Imagery** | 25 | Stock or AI hosts (Unsplash, Pexels, Mixkit video), C2PA or AI tags, AI artifacts (warped hands or text, plastic skin, too-perfect symmetry, glossy 3D blobs), postcard shots with nothing of the business itself (team, office, customers), abstract gradient orbs instead of real photos, generic or missing alt text |
| **Motion** | 15 | Every block fades or slides up on scroll, marquee strips, floating blobs, typewriter heroes, decorative 3D globes, purposeless parallax, Framer/AOS defaults |
| **Layout & color** | 15 | Purple-to-blue gradients, glassmorphism pills and cards, `rounded-full` on everything, one big radius on every panel, card carousel over a full-bleed hero, default section order (features grid, logos, FAQ, CTA), default shadcn look |

Score each from 0 to 100 and take the weighted total. Calibration: a clean template with a few tells scores about 30–45; an obviously generated site 70+. Mark the score as an estimate if key checks couldn't run.

**Optional image detectors** (Hive, Sightengine) can be used only if the user gives an API key. Treat them as one more signal: they're unreliable on edited or compressed images.

## Step 4: Report

Answer in the user's language (natural Russian for Russian, Eastern Armenian in reformed spelling for Armenian). Keep it scannable:

```
AI-look score: 48/100 (medium) — estimate: no browser, mobile and image provenance not checked

| Category | Score | Key evidence |
| Typography | 60 | H1 at clamp(44px–76px), weight 300, -0.03em, gold italic serif accent |
| Copy | 25 | Specific facts; weak hero line "Enjoy your precious moments" |

Top fixes (most impact first):
1. ...
```

Quote exact evidence. Don't call content "AI-generated" as a fact unless there's provenance (a C2PA or AI tag); say it "reads as generic / AI-like". Also say what's already good, so the user doesn't change what works.

## Step 5: Rewrite the text

This is the most important fix: a calmer layout doesn't help if the words still sound generated. The rewriting is done by you, following the rules below. Detection services are only used to check the result. Avoid "AI humanizer" services (Undetectable.ai, StealthGPT and similar): they swap in odd synonyms and break grammar to fool detectors, which makes the copy worse for real readers.

**5.1 Load writing help.** If they're available, load `anthropic-skills:writing-guidelines` (general prose) and `design:ux-copy` (buttons, labels, short interface text) before writing.

**5.2 Build a fact sheet first.** Generic text comes from having nothing specific to say. Collect real facts: the business name, address, phones, hours, founding year, team names, services, real prices, destinations or products, real reviews and their authors, and what makes them different (from the existing site, their Instagram or Google profile, a web search, or the user). Mark anything unconfirmed as `[confirm]` and never invent numbers, prices, reviews or ratings.

**5.3 Rewrite every string**, using the fact sheet. Rules:
- Each heading says something specific and true: "From Malé by seaplane, straight to your villa", not "Enjoy your precious moments".
- Concrete nouns and numbers over adjectives. Cut "easy", "perfect", "unforgettable", "seamless", "premium", "world-class" and every word on the buzzword list.
- Vary sentence length. Mix short sentences with longer ones; avoid three parallel items in a row ("fast, simple and reliable").
- No em dashes, no emoji in headings, no rhetorical questions as headings, no "Whether you're X or Y".
- Write the way the owner would explain it to a customer at the counter. Use "we" and "you".
- Buttons say exactly what happens: "Get a price in WhatsApp", not "Get started".
- **Each language is written natively, not translated word for word.** Armenian: natural Eastern Armenian in reformed spelling with Armenian punctuation (։ ՝ ՞ « »). Russian: conversational, never bureaucratic. Keep every language's meaning in sync.
- Keep string lengths close to the originals so the layout doesn't break, or check the layout after.

**5.4 Self-check.** Re-run the buzzword scan and the checklist above on the new text, and fix anything that's left.

**5.5 Optional detector check.** Only if the user gives an API key for this run:
- **Sapling** (cheapest, simple REST API): `POST https://api.sapling.ai/api/v1/aidetect` with JSON `{"key": "$SAPLING_API_KEY", "text": "..."}`. It returns an overall `score` (0 = human, 1 = AI) and `sentence_scores`.
- **GPTZero** or **Originality.ai** (more accurate; Originality.ai also handles Russian): fetch the vendor's current API docs first, since endpoints change.

Check only English and Russian blocks of about 300+ characters: detectors are unreliable on short marketing lines, give false positives on non-native English, and have no real support for Armenian. Rewrite only the sentences that are flagged, at most 2 rounds. If good, specific copy still gets flagged, keep it: readers matter, not the detector score. Report the scores as one signal, not proof.

**5.6 Show it.** Present a table of old → new for the key strings (hero headlines, section headings, buttons, intro paragraphs), with any `[confirm]` facts listed for the client.

## Step 6: Build the new demo

State the scope in one line (see Safety rules). Then apply the text from Step 5 and the design fixes in the order of the report:
- **Typography:** a distinctive pairing that suits the brand, a sensible scale (hero roughly 40–64px on desktop), normal weight, light tracking, real hierarchy, readable line length. Drop gimmicks like the gold italic accent word. For Armenian, use fonts that support Armenian and `lang="hy"`.
- **Imagery:** prefer the company's real photos (from their site or socials). Otherwise use honest illustrations or an abstract treatment tied to the brand, and list which shots the client should provide. Remove AI images with visible artifacts.
- **Motion:** keep 1–2 purposeful interactions (hover states, one hero moment). Respect `prefers-reduced-motion`. Remove blanket fade-ups.
- **Layout & color:** a brand-derived palette, solid surfaces instead of glass, varied section rhythm, mixed radii, layout that follows the content instead of the template order.

Keep the project's stack and code style; change only what the fixes need. With no source code, build a new demo (Next.js + Tailwind + shadcn by default) and load a frontend-design skill first.

**Check before deploying:** run the project's typecheck and build. If the build can't run in the workspace (for example, blocked downloads like Prisma engines), typecheck the changed files and let the preview build be the real check.

**Deploy a preview:**
1. Push the `det-ai-fixes` branch. A Vercel project linked to the repo starts a preview build.
2. Check it with Vercel `list_deployments` (filter by branch). If it's `BLOCKED` (on the Hobby plan Vercel blocks git deployments whose commit author isn't the account owner), start the same commit with Vercel `create_deployment` using `gitSource` (`type: github`, org, repo, `ref: det-ai-fixes`, sha) and no production target.
3. Wait until it's `READY`, then verify it: open it in the browser on desktop and mobile, check for console errors and re-run the scanner, or fetch it with `web_fetch_vercel_url` and confirm the changes are in the HTML.

## Step 7: Deliver

Keep it short:
- the before/after score (marked as an estimate if not re-scanned in a browser), plus detector scores if a key was used
- the preview link
- the 3–5 biggest changes, and the old → new text table
- what was touched and what wasn't (branch, files, `main` and live site untouched, how to undo)
- what only the client can supply (photos, prices, reviews, `[confirm]` facts)
- what couldn't be checked, and how the user can check it