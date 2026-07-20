**QA Scope**

- Source visual truth: `public/fate-astrolabe.png`.
- Implementation screenshots: `qa/final-pc-1840x860.png`, `qa/desktop-1366x768.png`, `qa/desktop-1024x768.png`, `qa/desktop-chart-open-1024x768.png`, `qa/mobile-390x844.png`, and `qa/mobile-plugin-map-390x844.png`.
- Combined focused comparison: `qa/astrolabe-comparison.png`.
- States: guest dashboard, plugin constellation revealed, memory chart closed/open.
- Viewports: 1366×768, 1024×768, and 375×811. The two mobile
  captures retain their original `390x844` filenames for traceability.

**Comparison History**

- [P1] At 1840x860 the PC document measured 937px tall, the browser scrolled, and the lower orbit nodes overlapped the ledger by 42.9px.
  Fix: constrained only the desktop observatory to one viewport, scaled the complete astrolabe and all five nodes together, and reserved a fixed lower region for the ledger.
  Post-fix evidence: the document is exactly 860px at 1840x860 with a 23.8px node-to-ledger gap; at 1366x768 and 1024x768 it is exactly 768px with a 38px gap. Opening a metric chart does not change those measurements.
- [P1] The desktop plugin constellation was hidden at widths from 721px to 1120px and at short desktop heights.
  Fix: kept both left-rail cards visible, moved the chronicle to a stable upper anchor, bottom-anchored the constellation, and added compact short-height rules.
  Post-fix evidence: both cards are fully visible in `qa/desktop-1366x768.png` and `qa/desktop-1024x768.png`.
- [P1] The five service nodes used viewport-dependent coordinates while the astrolabe image scaled independently.
  Fix: introduced a square `astrolabe-frame`; the image, portrait, and all node coordinates now share the same normalized coordinate system. Only the frame scales at breakpoints.
  Post-fix evidence: `qa/astrolabe-comparison.png` and `qa/mobile-390x844.png` show the five nodes staying on the same annulus.
- [P1] Opening a metric chart increased the ledger and page height.
  Fix: the forecast and chart now swap inside a fixed-height `ledger-visual` slot; charts were compacted and the pie radius reduced to fit.
  Post-fix evidence: at 1024×768 the ledger stayed `299.6px` and page height stayed `768px`; in the mobile capture the ledger stayed `441.6px` and page height stayed `1712px` before and after opening the chart.
- [P1] Updating `revealedCount` remounted the Recharts line and scatter set for every star, making the constellation flash repeatedly.
  Fix: all stars and one connection path now mount once with stable keys; animation timing is applied to those persistent elements, and the desktop card entrance animation is disabled.
  Post-fix evidence: at 220ms, 640ms, and 1340ms the DOM consistently contained three star symbols and one line; the card stayed at opacity 1 with `animation-name: none`.

**Required Fidelity Surfaces**

- Fonts and typography: existing Songti SC / Noto Serif SC and Cascadia Code hierarchy is preserved; compact desktop rules retain readable labels.
- Spacing and layout rhythm: the left rail fits both cards at tested desktop breakpoints; fixed chart slots prevent layout jumps; the astrolabe remains centered.
- Colors and visual tokens: existing navy, cyan, violet, glow, border, and transparency tokens are unchanged.
- Image quality and asset fidelity: the supplied `fate-astrolabe.png` remains the source asset and now scales uniformly without independent node drift.
- Copy and content: existing status copy is unchanged; plugin selection adds concise name and complete version.

**Interaction And Runtime Checks**

- Guest entry completed successfully.
- Plugin constellation reveals progressively without remounting or flashing the existing stars and connection.
- Plugin selection highlighted the chosen star and exposed its name/version.
- Metric chart open/close preserved both ledger and document height.
- Browser console errors: none.

**Residual Notes**

- The full-page mobile screenshot is not used as evidence because the fixed video layer repeats during browser full-page stitching; viewport captures and live scrolling were used instead.
- No actionable P0, P1, or P2 issue remains in the tested states.

final result: passed
