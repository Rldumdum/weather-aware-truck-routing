import type { CustomLayerInterface, CustomRenderMethodInput, Map as MapLibreMap } from "maplibre-gl";

import { RISK_LEVELS } from "../risk";
import type { HeatMetric, HeatmapField, Route } from "../types";

/**
 * Corridor weather heatmap as a WebGL custom layer.
 *
 * All 49 forecast hours live on the GPU as one texture array, and a uniform picks
 * the time. Moving the slider therefore costs one uniform write and no network,
 * no re-upload and no React render, so it stays smooth regardless of trip length.
 * The shader blends adjacent hours, which makes time continuous rather than
 * stepping hour by hour.
 */

const CORRIDOR_HALF_WIDTH_MI = 30;
const MASK_LONG_SIDE_PX = 1536;
const MILES_PER_MERCATOR_UNIT_AT_EQUATOR = 24_901;

const LAST_HOUR = 48;

const METRIC_INDEX: Record<HeatMetric, number> = { overall: 0, wind: 1, rain: 2, snow: 3 };

const VERTEX_SHADER = `#version 300 es
uniform mat4 uMatrix;
uniform vec4 uBounds;
in vec2 aCorner;
out vec2 vUv;
void main() {
  vUv = aCorner;
  gl_Position = uMatrix * vec4(mix(uBounds.xy, uBounds.zw, aCorner), 0.0, 1.0);
}`;

const FRAGMENT_SHADER = `#version 300 es
precision highp float;
precision highp sampler2DArray;

uniform sampler2DArray uField;
uniform sampler2D uMask;
uniform vec2 uFieldSize;
uniform float uHour;
uniform int uMetric;
uniform vec3 uRamp[5];

in vec2 vUv;
out vec4 fragColor;

const float ALPHA[5] = float[5](0.22, 0.62, 0.70, 0.78, 0.86);

// Catmull-Rom interpolation from nine bilinear taps. It passes exactly through every
// forecast point, and clamping to the four surrounding cells stops cubic overshoot
// from ever showing a higher risk class than the data contains.
vec3 sampleField(vec2 uv, float layer) {
  vec2 position = uv * uFieldSize - 0.5;
  vec2 cell = floor(position);
  vec2 f = position - cell;

  vec2 w0 = f * (-0.5 + f * (1.0 - 0.5 * f));
  vec2 w1 = 1.0 + f * f * (-2.5 + 1.5 * f);
  vec2 w2 = f * (0.5 + f * (2.0 - 1.5 * f));
  vec2 w3 = f * f * (-0.5 + 0.5 * f);
  vec2 w12 = w1 + w2;

  vec2 positions[3] = vec2[3](
    (cell - 0.5) / uFieldSize,
    (cell + w2 / w12 + 0.5) / uFieldSize,
    (cell + 2.5) / uFieldSize
  );
  vec2 weights[3] = vec2[3](w0, w12, w3);
  vec3 value = vec3(0.0);
  for (int row = 0; row < 3; row++) {
    for (int col = 0; col < 3; col++) {
      vec3 tap = texture(uField, vec3(positions[col].x, positions[row].y, layer)).rgb;
      value += tap * weights[col].x * weights[row].y;
    }
  }

  ivec2 last = ivec2(uFieldSize) - 1;
  vec3 lowest = vec3(1.0);
  vec3 highest = vec3(0.0);
  for (int y = 0; y < 2; y++) {
    for (int x = 0; x < 2; x++) {
      ivec2 corner = clamp(ivec2(cell) + ivec2(x, y), ivec2(0), last);
      vec3 texel = texelFetch(uField, ivec3(corner, int(layer)), 0).rgb;
      lowest = min(lowest, texel);
      highest = max(highest, texel);
    }
  }
  return clamp(value, lowest, highest);
}

float selectScore(vec3 scores) {
  if (uMetric == 1) return scores.r;
  if (uMetric == 2) return scores.g;
  if (uMetric == 3) return scores.b;
  return max(scores.r, max(scores.g, scores.b));
}

void main() {
  float mask = texture(uMask, vUv).r;
  if (mask < 0.004) discard;

  float layer = floor(uHour);
  vec3 earlier = sampleField(vUv, layer);
  vec3 later = sampleField(vUv, min(layer + 1.0, ${LAST_HOUR}.0));
  vec3 scores = mix(earlier, later, uHour - layer) * 4.0;

  float score = clamp(selectScore(scores), 0.0, 4.0);
  int band = int(min(floor(score), 3.0));
  float inBand = score - float(band);
  // Crisp but antialiased steps at each class boundary, like contour bands.
  float step = smoothstep(0.90, 1.0, inBand);
  vec3 color = mix(uRamp[band], uRamp[band + 1], step);
  float alpha = mix(ALPHA[band], ALPHA[band + 1], step);
  if (band == 0) alpha *= 0.45 + 0.55 * inBand;  // calm air stays quiet

  alpha *= mask;
  fragColor = vec4(color * alpha, alpha);
}`;

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type)!;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error(`Heatmap shader failed: ${gl.getShaderInfoLog(shader)}`);
  }
  return shader;
}

function hexToRgb(hex: string): [number, number, number] {
  const value = parseInt(hex.slice(1), 16);
  return [(value >> 16) / 255, ((value >> 8) & 255) / 255, (value & 255) / 255];
}

function mercatorX(lon: number): number {
  return (lon + 180) / 360;
}

function mercatorY(lat: number): number {
  const sin = Math.sin((lat * Math.PI) / 180);
  return 0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI);
}

/** Paint the union of route corridors into a canvas with a feathered edge. */
function drawCorridorMask(field: HeatmapField, routes: Route[]): HTMLCanvasElement {
  const [west, north, east, south] = field.bounds;
  const aspect = (east - west) / (south - north);
  const width = aspect >= 1 ? MASK_LONG_SIDE_PX : Math.round(MASK_LONG_SIDE_PX * aspect);
  const height = aspect >= 1 ? Math.round(MASK_LONG_SIDE_PX / aspect) : MASK_LONG_SIDE_PX;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d")!;

  const midLat = routes[0].corridor[Math.floor(routes[0].corridor.length / 2)][1];
  const milesPerUnit = MILES_PER_MERCATOR_UNIT_AT_EQUATOR * Math.cos((midLat * Math.PI) / 180);
  const fullWidthPx = ((2 * CORRIDOR_HALF_WIDTH_MI) / milesPerUnit / (east - west)) * width;

  // Stacking progressively narrower strokes makes a soft edge. Each pass is drawn
  // opaque on its own canvas first, so overlapping routes don't add up.
  const featherPasses = 10;
  ctx.globalAlpha = 1 / featherPasses;
  for (let pass = 0; pass < featherPasses; pass++) {
    const layer = document.createElement("canvas");
    layer.width = width;
    layer.height = height;
    const layerCtx = layer.getContext("2d")!;
    layerCtx.lineCap = "round";
    layerCtx.lineJoin = "round";
    layerCtx.strokeStyle = "#fff";
    layerCtx.lineWidth = fullWidthPx * (1 - (pass / featherPasses) * 0.7);
    for (const route of routes) {
      layerCtx.beginPath();
      route.corridor.forEach(([lon, lat], index) => {
        const x = ((mercatorX(lon) - west) / (east - west)) * width;
        const y = ((mercatorY(lat) - north) / (south - north)) * height;
        if (index === 0) layerCtx.moveTo(x, y);
        else layerCtx.lineTo(x, y);
      });
      layerCtx.stroke();
    }
    ctx.drawImage(layer, 0, 0);
  }
  return canvas;
}

export class HeatLayer implements CustomLayerInterface {
  readonly id = "weather-heat";
  readonly type = "custom" as const;
  readonly renderingMode = "2d" as const;

  private map?: MapLibreMap;
  private gl?: WebGL2RenderingContext;
  private program?: WebGLProgram;
  private vao?: WebGLVertexArrayObject;
  private fieldTexture?: WebGLTexture;
  private maskTexture?: WebGLTexture;
  private uniforms: Record<string, WebGLUniformLocation | null> = {};

  private field?: HeatmapField;
  private hour = 0;
  private metric: HeatMetric = "overall";

  onAdd(map: MapLibreMap, context: WebGLRenderingContext | WebGL2RenderingContext): void {
    const gl = context as WebGL2RenderingContext;
    this.map = map;
    this.gl = gl;

    const program = gl.createProgram()!;
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX_SHADER));
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(`Heatmap program failed: ${gl.getProgramInfoLog(program)}`);
    }
    this.program = program;
    for (const name of ["uMatrix", "uBounds", "uField", "uMask", "uFieldSize", "uHour", "uMetric", "uRamp"]) {
      this.uniforms[name] = gl.getUniformLocation(program, name);
    }

    const vao = gl.createVertexArray()!;
    gl.bindVertexArray(vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), gl.STATIC_DRAW);
    const corner = gl.getAttribLocation(program, "aCorner");
    gl.enableVertexAttribArray(corner);
    gl.vertexAttribPointer(corner, 2, gl.FLOAT, false, 0, 0);
    gl.bindVertexArray(null);
    this.vao = vao;

    this.fieldTexture = gl.createTexture()!;
    this.maskTexture = gl.createTexture()!;
  }

  onRemove(): void {
    const gl = this.gl;
    if (!gl) return;
    if (this.fieldTexture) gl.deleteTexture(this.fieldTexture);
    if (this.maskTexture) gl.deleteTexture(this.maskTexture);
    if (this.vao) gl.deleteVertexArray(this.vao);
    if (this.program) gl.deleteProgram(this.program);
  }

  /** Upload a new forecast field. This is the only expensive call, once per trip. */
  setField(field: HeatmapField | null, routes: Route[]): void {
    const gl = this.gl;
    if (!gl) return;
    this.field = field ?? undefined;
    if (field) {
      const bytes = Uint8Array.from(atob(field.data), (char) => char.charCodeAt(0));
      gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.fieldTexture!);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      gl.texImage3D(
        gl.TEXTURE_2D_ARRAY, 0, gl.RGB8,
        field.width, field.height, field.frames, 0,
        gl.RGB, gl.UNSIGNED_BYTE, bytes,
      );
      gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);

      gl.bindTexture(gl.TEXTURE_2D, this.maskTexture!);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, drawCorridorMask(field, routes));
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    }
    this.map?.triggerRepaint();
  }

  setHour(hour: number): void {
    this.hour = hour;
    this.map?.triggerRepaint();
  }

  setMetric(metric: HeatMetric): void {
    this.metric = metric;
    this.map?.triggerRepaint();
  }

  render(context: WebGLRenderingContext | WebGL2RenderingContext, options: CustomRenderMethodInput): void {
    const { field, program } = this;
    if (!field || !program) return;
    const gl = context as WebGL2RenderingContext;

    gl.useProgram(program);
    gl.bindVertexArray(this.vao!);
    gl.uniformMatrix4fv(this.uniforms.uMatrix, false, options.defaultProjectionData.mainMatrix);
    gl.uniform4fv(this.uniforms.uBounds, field.bounds);
    gl.uniform2f(this.uniforms.uFieldSize, field.width, field.height);
    gl.uniform1f(this.uniforms.uHour, this.hour);
    gl.uniform1i(this.uniforms.uMetric, METRIC_INDEX[this.metric]);
    gl.uniform3fv(this.uniforms.uRamp, RISK_LEVELS.flatMap((level) => hexToRgb(level.color)));

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.fieldTexture!);
    gl.uniform1i(this.uniforms.uField, 0);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.maskTexture!);
    gl.uniform1i(this.uniforms.uMask, 1);

    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.disable(gl.DEPTH_TEST);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    gl.bindVertexArray(null);
  }
}
