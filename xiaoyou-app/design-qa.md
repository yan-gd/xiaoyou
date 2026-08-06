# Product Design QA — 我们的轨道

- Design reference: `C:\Users\qq157\.codex\generated_images\019f7ea8-d255-7340-9adb-21da946c6cad\call_HxJmtTRALC7KIIe7tlbFBv7W.png`
- Runtime: Android emulator `emulator-5554`
- Tested build: Flutter release `0.6.0+14`
- Tested viewport: `1290 × 2796`
- Final screen: `tooling/xiaoyou-orbit-poster-final.png`
- Voice room screen: `tooling/xiaoyou-voice-room.png`
- Side-by-side review: `tooling/orbit-design-comparison-final.png`

## Visual fidelity

- Matches the poster's continuous pearl, blush, lavender, and warm-gold star-mist canvas.
- Keeps Xiaoyou at the visual center and uses layered luminous elliptical orbits to express relationship history.
- Gives all eight relationship nodes a readable name, a distinct semantic icon, glass depth, and a highlighted state.
- Uses moving comets, soft glow, star particles, floating glass bubbles, orbital motion, and selected-node emphasis without reducing text contrast.
- Recreates the poster's translucent memory ribbon and bottom `写一封信 / 进入语音房间` pill actions.

## Interaction and layout

- Orbit canvas supports pinch zoom, drag exploration, recentering, and focus mode.
- Initial camera is centered on Xiaoyou instead of opening at a clipped edge.
- Node selection updates the highlighted constellation point and the memory detail card.
- Daily journal, time capsule, voice room, and achievement atlas are reachable from the same relationship hub.
- Continuous voice room respects Android safe areas and keeps the stop/keepsake action reachable.
- No RenderFlex overflow, clipped primary action, or unsafe status/navigation-bar overlap was observed.

## Data integrity

- Daily journal drafts expose the source message and media identifiers used to create the draft.
- Journal confirmation is explicit; drafts are not silently promoted to shared history.
- Time-capsule contents remain sealed until the configured unlock time.
- Voice-room keepsakes store metadata and highlights rather than retaining the full recording.
- When the relationship API is unavailable, the app labels the state and uses local chat history for a non-destructive preview.

## Siri-style voice orb

- Latest visual reference: `C:\Users\qq157\AppData\Local\Temp\codex-clipboard-d5873701-3288-461f-8f44-5005d27f6019.png`
- Final emulator capture: `tooling/orb-final-texture.png`
- Motion verification frames: `tooling/orb-final-texture-b.png`, `tooling/orb-final-texture-c.png`
- 15-second loop capture: `tooling/orb-loop-v2.mp4`
- Side-by-side review: `tooling/siri-orb-design-comparison-v2.png`
- Replaced the flat painted blades with a production glass texture: a controlled white core, four translucent cyan/mint/rose/sapphire fluid petals, internal caustics, a dark refractive shell, and a clean circular rim.
- The 12-second rotation is driven by one monotonic controller. Every orbit and background particle uses integer-periodic sine/cosine motion, so the final frame and first frame share the same position without a phase jump.
- Voice-state intensity and microphone level are interpolated independently (560 ms and 140 ms), so listening/recording/thinking/speaking transitions do not restart or snap the rotation.
- Three emulator frames and the full loop recording confirm that the inner light pattern changes continuously while the sphere stays centered, unclipped, and tappable.

final result: passed

## Xiaoyou profile redesign — living album

- Selected design reference: `C:\Users\qq157\.codex\generated_images\019fadf4-ea59-7703-a4e9-a0b44d513e74\call_GTEaRY1RmwDErABpJK1U4Kyw.png`
- Reference size: `853 x 1844`
- Runtime: Android emulator `emulator-5554`
- Runtime viewport: `1280 x 2856` at density `480` (`426.7 x 952` logical pixels)
- Final mood view: `tooling/profile-redesign-final-profile.png`
- Final photo view: `tooling/profile-photo-contain-final.png`
- Full-screen gallery: `tooling/profile-redesign-gallery.png`
- Full-view review: `tooling/profile-redesign-comparison.png`
- Focused photo-crop review: `tooling/profile-photo-contain-comparison.png`

### Visual fidelity

- Replaced the synthetic dashboard and overlapping-photo collage with the selected editorial album layout.
- Matches the reference hierarchy: compact identity header, two quiet text tabs, one large current-mood portrait, a restrained 3 x 2 mood library, and a clean photo journal.
- Uses Xiaoyou's existing six chibi emotion assets without changing the character into a realistic portrait.
- Keeps the existing ivory, rose, ink, and warm-gray product palette while removing glow-heavy frames, star labels, connector lines, and artificial card decoration.
- The selected mood uses a thin rose outline and check badge; unselected moods remain visually quiet.

### Interaction and layout

- Mood and photo tabs scroll to their respective sections and expose a clear selected state.
- Selecting any mood updates the large portrait and mood copy immediately.
- The latest six photos use one lead image and five supporting journal tiles.
- Tapping a photo opens a swipeable, pinch-zoom full-screen gallery.
- The final pass changed portrait thumbnail alignment from center crop to top-center crop so faces remain visible.
- No overflow, unsafe-area collision, clipped primary content, or broken image state was observed at the tested viewport.

### Iteration history

- First pass: `P2` — the lead journal image used centered cover cropping, hiding the subject's face on portrait photos.
- Fix: added top-center alignment to journal image tiles.
- Post-fix evidence: `tooling/profile-redesign-final-profile.png`.
- User follow-up: `P1` — fixed-height cover thumbnails still cut off most of each portrait image.
- Fix: replaced cover-only rendering with a complete `BoxFit.contain` foreground and a softly blurred copy of the same photo as edge fill.
- Post-fix evidence: `tooling/profile-photo-contain-final.png`; all six source photos are visible from edge to edge without subject cropping.
- Focused before/after evidence: `tooling/profile-photo-contain-comparison.png`.

final result: passed

## Continuous O2.0 voice room

- Interaction reference: `E:\qq157\xwechat_files\wxid_7n4xe84ciy8k22_d2de\msg\video\2026-07\f6005aaa22c49a07a0e81a6bfcd6a221.mp4`
- Reference frame: `build/gpt-voice-reference/frame-3.png`
- Final emulator captures: `tooling/voice-room-live-1.png`, `tooling/voice-room-live-2.png`, `tooling/voice-room-live-3.png`
- Side-by-side review: `tooling/voice-room-gpt-comparison.png`
- Tested build: Flutter release `0.6.1+15`
- Runtime: Android emulator `emulator-5554`

### Interaction fidelity

- Opening the room starts a continuous microphone stream; the orb is no longer a per-turn button.
- Listening, user speech, thinking, Xiaoyou speech, interruption, and ending are distinct visual states.
- The first ASR result immediately flushes current playback and reports its actual played duration to the server, enabling natural barge-in.
- Mute keeps the realtime session alive with silence frames; ending remains an explicit destructive action.

### Visual fidelity

- The composition follows the reference hierarchy: quiet full-screen canvas, one dominant central orb, compact status copy, and two bottom controls.
- The orb uses Xiaoyou's current model-maintained emotion image as its core rather than a generic generated texture.
- Refraction, moving cyan/rose/violet ribbons, a traveling rim highlight, breathing scale, and state-driven glow make the orb feel fluid while preserving the portrait.
- Three non-identical emulator frames confirm continuous motion without clipping, overflow, or a tap affordance that conflicts with hands-free conversation.

### Delivery and memory integrity

- User speech is committed independently even when Xiaoyou is interrupted.
- Interrupted assistant speech stores only fully delivered sentence boundaries and carries a partial-delivery terminal state.
- Voice-room turns remain absent from the main chat bubble list, while asynchronous memory projection keeps subsequent text and voice conversations continuous.

final result: passed
