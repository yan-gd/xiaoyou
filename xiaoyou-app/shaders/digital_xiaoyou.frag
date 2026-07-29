#version 320 es

precision highp float;

#include <flutter/runtime_effect.glsl>

layout(location = 0) uniform vec2 u_size;
layout(location = 2) uniform float u_blink;
layout(location = 3) uniform float u_mouth;
layout(location = 4) uniform float u_activity;
layout(location = 5) uniform float u_time;
uniform sampler2D u_portrait;
uniform sampler2D u_speech_mouth;

layout(location = 0) out vec4 frag_color;

float gaussian(vec2 point, vec2 center, vec2 radius) {
  vec2 delta = (point - center) / radius;
  return exp(-dot(delta, delta) * 2.35);
}

void main() {
  vec2 point = FlutterFragCoord().xy / u_size;
  vec4 portrait = texture(u_portrait, point);

  // Close each eye with colors sampled from Xiaoyou's own cheek area. This
  // avoids the bulging that a whole-band geometric squash produces and keeps
  // the blink natural on every one of the six aligned mood portraits.
  float blink_amount = smoothstep(0.08, 0.88, u_blink);
  vec2 left_delta =
      (point - vec2(0.405, 0.555)) / vec2(0.105, 0.055);
  vec2 right_delta =
      (point - vec2(0.645, 0.555)) / vec2(0.105, 0.055);
  float left_lid = 1.0 - smoothstep(0.72, 1.0, length(left_delta));
  float right_lid = 1.0 - smoothstep(0.72, 1.0, length(right_delta));
  float lid_mask = max(left_lid, right_lid) * blink_amount;
  vec3 cheek_color = texture(
    u_portrait,
    vec2(point.x, 0.645)
  ).rgb;
  portrait.rgb = mix(portrait.rgb, cheek_color, lid_mask);

  float left_lash = exp(-abs(left_delta.y) * 24.0) *
      (1.0 - smoothstep(0.72, 1.0, abs(left_delta.x)));
  float right_lash = exp(-abs(right_delta.y) * 24.0) *
      (1.0 - smoothstep(0.72, 1.0, abs(right_delta.x)));
  float lash = max(left_lash, right_lash) * blink_amount;
  vec3 lash_color = mix(
    portrait.rgb,
    vec3(0.17, 0.09, 0.14),
    0.70
  );
  portrait.rgb = mix(portrait.rgb, lash_color, lash);

  // Use an actual aligned Xiaoyou mouth from the existing expression art.
  // The old shader painted a synthetic dark oval here, which detached from
  // her face.  A small feathered patch preserves the original linework,
  // highlights and skin shading while PCM energy only controls its opacity.
  vec2 speech_point = point;
  float mouth_center = 0.675;
  float speech_scale = mix(0.91, 1.04, u_mouth);
  speech_point.y =
      mouth_center + (speech_point.y - mouth_center) / speech_scale;
  vec4 speech_mouth = texture(
    u_speech_mouth,
    clamp(speech_point, vec2(0.002), vec2(0.998))
  );
  vec2 mouth_delta =
      (point - vec2(0.52, mouth_center)) / vec2(0.072, 0.038);
  float mouth_patch =
      1.0 - smoothstep(0.48, 1.0, length(mouth_delta));
  float mouth_mix =
      mouth_patch * smoothstep(0.10, 0.86, u_mouth) * 0.92;
  portrait = mix(portrait, speech_mouth, mouth_mix);

  float t = u_time * 6.28318530718;
  vec2 highlight_center = vec2(
    0.31 + sin(t * 0.72) * 0.016,
    0.22 + cos(t * 0.61) * 0.012
  );
  float highlight = gaussian(point, highlight_center, vec2(0.32, 0.18));
  float edge_distance = length(point - vec2(0.5));
  float edge = smoothstep(0.49, 0.40, edge_distance);
  vec3 tint = mix(
    vec3(1.0, 0.84, 0.94),
    vec3(0.74, 0.92, 1.0),
    0.5 + 0.5 * sin(t)
  );
  portrait.rgb += tint * highlight * (0.025 + u_activity * 0.028);
  portrait.rgb *= 0.97 + edge * 0.03;
  frag_color = portrait;
}
