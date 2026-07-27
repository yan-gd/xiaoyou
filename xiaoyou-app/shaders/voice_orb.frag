#version 320 es

precision highp float;

#include <flutter/runtime_effect.glsl>

layout(location = 0) uniform vec2 u_size;
layout(location = 1) uniform float u_time;
layout(location = 2) uniform float u_activity;
layout(location = 3) uniform float u_level;
layout(location = 4) uniform float u_breath;

layout(location = 0) out vec4 frag_color;

const float PI = 3.14159265358979323846;

mat2 rotation(float angle) {
  float c = cos(angle);
  float s = sin(angle);
  return mat2(c, -s, s, c);
}

float petal(
  vec2 point,
  float angle,
  float length,
  float width,
  float bend
) {
  vec2 q = rotation(-angle) * point;
  float x = q.x / length;
  float envelope = 0.15 + 0.85 * sin(PI * clamp(x, 0.0, 1.0));
  float center_line = bend * sin(PI * x) * (1.0 - x * 0.45);
  float transverse = abs(q.y - center_line) / max(width * envelope, 0.002);
  float body = smoothstep(1.0, 0.08, transverse);
  float root = smoothstep(-0.12, 0.04, x);
  float tip = 1.0 - smoothstep(0.72, 1.06, x);
  return body * root * tip;
}

float filament(
  vec2 point,
  float angle,
  float length,
  float width,
  float bend
) {
  vec2 q = rotation(-angle) * point;
  float x = q.x / length;
  float center_line = bend * sin(PI * x) * (1.0 - x * 0.45);
  float line = exp(-abs(q.y - center_line) / max(width * 0.12, 0.001));
  return line * smoothstep(-0.04, 0.08, x) *
      (1.0 - smoothstep(0.72, 1.0, x));
}

void main() {
  vec2 fragment = FlutterFragCoord().xy;
  float shortest = min(u_size.x, u_size.y);
  vec2 point = (fragment - u_size * 0.5) / shortest;
  float radius = 0.285 + u_breath * 0.008 + sin(u_time * 2.0) * 0.003 +
      u_level * 0.008;
  float distance_to_center = length(point);
  float sphere = smoothstep(radius + 0.002, radius - 0.004, distance_to_center);

  vec3 deep_navy = vec3(0.012, 0.055, 0.105);
  vec3 upper_glass = vec3(0.075, 0.245, 0.34);
  float glass_depth = smoothstep(radius, 0.03, distance_to_center);
  vec3 color = mix(deep_navy, upper_glass, glass_depth * 0.62);

  float clockwise_0 = u_time + sin(u_time * 2.0) * 0.18;
  float clockwise_1 = u_time + 2.094 + sin(u_time * 2.0 + 1.31) * 0.18;
  float clockwise_2 = u_time + 4.189 + sin(u_time * 2.0 + 2.62) * 0.18;
  float counter_0 = -u_time * 2.0 + cos(u_time * 3.0) * 0.16;
  float counter_1 = -u_time * 2.0 + 1.571 + cos(u_time * 3.0 + 0.93) * 0.16;
  float counter_2 = -u_time * 2.0 + 3.142 + cos(u_time * 3.0 + 1.86) * 0.16;
  float counter_3 = -u_time * 2.0 + 4.712 + cos(u_time * 3.0 + 2.79) * 0.16;

  float pulse = 0.92 + u_level * 0.1 + u_activity * 0.05;
  float b0 = petal(point, clockwise_0, 0.245 * pulse, 0.126, 0.025);
  float b1 = petal(point, clockwise_1, 0.238 * pulse, 0.12, -0.022);
  float b2 = petal(point, clockwise_2, 0.25 * pulse, 0.13, 0.02);
  float f0 = petal(point, counter_0, 0.205 * pulse, 0.078, -0.018);
  float f1 = petal(point, counter_1, 0.198 * pulse, 0.074, 0.02);
  float f2 = petal(point, counter_2, 0.21 * pulse, 0.08, -0.016);
  float f3 = petal(point, counter_3, 0.2 * pulse, 0.076, 0.018);

  vec3 sapphire = vec3(0.12, 0.42, 1.0);
  vec3 rose = vec3(1.0, 0.18, 0.58);
  vec3 cyan = vec3(0.02, 0.88, 1.0);
  vec3 mint = vec3(0.26, 1.0, 0.78);
  float broad_energy = 0.34 + u_activity * 0.14;
  float front_energy = 0.52 + u_activity * 0.2;

  color += sapphire * b0 * broad_energy;
  color += rose * b1 * broad_energy;
  color += cyan * b2 * broad_energy;
  color += rose * f0 * front_energy;
  color += cyan * f1 * front_energy;
  color += sapphire * f2 * front_energy;
  color += mint * f3 * front_energy;

  float threads =
      filament(point, clockwise_0, 0.245, 0.126, 0.025) +
      filament(point, clockwise_1, 0.238, 0.12, -0.022) +
      filament(point, counter_1, 0.198, 0.074, 0.02) +
      filament(point, counter_3, 0.2, 0.076, 0.018);
  color += vec3(0.55, 0.95, 1.0) * threads * 0.34;

  float core = exp(-distance_to_center * distance_to_center * 580.0);
  float soft_core = exp(-distance_to_center * distance_to_center * 125.0);
  color += vec3(1.0, 1.0, 1.0) * core * 1.55;
  color += vec3(0.68, 0.94, 1.0) * soft_core * (0.48 + u_activity * 0.18);

  float edge = exp(-abs(distance_to_center - radius) * 205.0);
  float rim_angle = atan(point.y, point.x);
  float cyan_rim = 0.5 + 0.5 * cos(rim_angle + u_time * 2.0);
  float pink_rim = 1.0 - cyan_rim;
  color += edge * (cyan * cyan_rim + rose * pink_rim) * 0.7;

  vec2 highlight_point = point - vec2(-0.105, -0.125);
  float highlight = exp(
    -(highlight_point.x * highlight_point.x * 360.0 +
      highlight_point.y * highlight_point.y * 760.0)
  );
  color += vec3(0.85, 0.98, 1.0) * highlight * 0.6;

  float halo = exp(-max(distance_to_center - radius, 0.0) * 42.0) *
      (1.0 - sphere);
  vec3 halo_color = mix(cyan, rose, 0.42 + 0.25 * sin(u_time * 2.0));
  vec3 final_color = color * sphere + halo_color * halo * 0.16;
  float alpha = max(sphere, halo * 0.28);
  frag_color = vec4(final_color * alpha, alpha);
}
