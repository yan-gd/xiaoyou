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
