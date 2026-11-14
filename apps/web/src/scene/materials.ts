// Shared materials. One instance per kind keeps the material (and shader program) count tiny no matter
// how many tiles are loaded; only terrain needs a per-tile material because each tile has its own texture.
// All facade / roof / ground detail is procedural in the shader (decorative, see docs/GEOGRAPHIC_ACCURACY.md):
//   walls  _style = facade type (0 none, 1 house, 2 apartments, 3 commercial, 4 public, 5 industrial,
//                   6 church, 7 historic); uv = (metres along wall / 3 m bay, floors above ground)
//   roofs  _style = roof material (0 bitumen, 1 metal tile, 2 slate, 3 corrugated, 4 standing seam);
//                   uv = metres (u along the eave, v up the slope)
// Surface relief is a procedural height field turned into a normal perturbation (bump from screen-space
// derivatives), so roofs and walls catch light without extra geometry or texture downloads.
import * as THREE from 'three'

export const uniforms = {
  uWindows: { value: 1 }, // 0 in data-quality modes: no decorative patterns
  uNight: { value: 0 },
  uTime: { value: 0 },
}

const NOISE = /* glsl */ `
float skHash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
float skNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(skHash(i), skHash(i + vec2(1, 0)), f.x), mix(skHash(i + vec2(0, 1)), skHash(i + vec2(1, 1)), f.x), f.y);
}
float skFbm(vec2 p) { return skNoise(p) * 0.5 + skNoise(p * 2.03 + 7.1) * 0.3 + skNoise(p * 4.1 + 3.7) * 0.2; }
float skBox(vec2 f, vec2 lo, vec2 hi, vec2 aa) {
  vec2 a = smoothstep(lo - aa, lo + aa, f) * (1.0 - smoothstep(hi - aa, hi + aa, f));
  return a.x * a.y;
}`

// perturbNormalArb (Mikkelsen 2010): bump from a height value using screen-space derivatives
const BUMP = /* glsl */ `
#include <normal_fragment_maps>
{
  vec3 dpdx = dFdx(-vViewPosition), dpdy = dFdy(-vViewPosition);
  float dhdx = dFdx(skH), dhdy = dFdy(skH);
  vec3 r1 = cross(dpdy, normal), r2 = cross(normal, dpdx);
  float det = dot(dpdx, r1);
  vec3 grad = sign(det) * (dhdx * r1 + dhdy * r2);
  normal = normalize(abs(det) * normal - grad * skBumpScale);
}`

function patch(m: THREE.MeshStandardMaterial, key: string, opts: { vert?: string; varyings: string; frag: string; bump?: boolean }) {
  m.onBeforeCompile = (s) => {
    s.uniforms.uWindows = uniforms.uWindows
    s.uniforms.uNight = uniforms.uNight
    s.uniforms.uTime = uniforms.uTime
    s.vertexShader = s.vertexShader
      .replace('#include <common>', `#include <common>\n${opts.varyings}\n${opts.vert ? 'attribute float _style;\nattribute float _feature_id;' : ''}`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>\n#ifdef USE_INSTANCING\nvSkWorld = (modelMatrix * instanceMatrix * vec4(transformed, 1.0)).xyz;\n#else\nvSkWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;\n#endif\n${opts.vert ?? ''}`)
    s.fragmentShader = s.fragmentShader
      .replace('#include <common>', `#include <common>\n${opts.varyings}\nuniform float uWindows;\nuniform float uNight;\nuniform float uTime;\nfloat skWin = 0.0;\nfloat skRough = -1.0;\nfloat skMetal = -1.0;\nfloat skH = 0.0;\nfloat skBumpScale = 1.0;\n${NOISE}`)
      .replace('#include <color_fragment>', `#include <color_fragment>\n${opts.frag}`)
      .replace('#include <roughnessmap_fragment>', '#include <roughnessmap_fragment>\nif (skRough >= 0.0) roughnessFactor = skRough;')
      .replace('#include <metalnessmap_fragment>', '#include <metalnessmap_fragment>\nif (skMetal >= 0.0) metalnessFactor = skMetal;')
      .replace(
        '#include <emissivemap_fragment>',
        '#include <emissivemap_fragment>\ntotalEmissiveRadiance += vec3(1.0, 0.62, 0.32) * skWin * uNight * 1.6;',
      )
    if (opts.bump) s.fragmentShader = s.fragmentShader.replace('#include <normal_fragment_maps>', BUMP)
  }
  m.customProgramCacheKey = () => key
  return m
}

const WALL_FRAG = /* glsl */ `
{
  vec3 base = diffuseColor.rgb;
  int st = int(vStyle + 0.5);
  vec2 uv = vFacadeUv;
  float dist = length(vSkWorld - cameraPosition);
  float near = (1.0 - smoothstep(25.0, 140.0, dist)) * uWindows;
  // wall finish: reddish, saturated walls read as brick, the rest as plaster (decorative inference)
  float sat = max(base.r, max(base.g, base.b)) - min(base.r, min(base.g, base.b));
  float brick = step(0.12, sat) * step(base.g, base.r * 0.55) * step(0.5, float(st));   // clearly red walls only
  vec2 wm = vec2(uv.x * 3.0, uv.y * 3.0);                       // ~metres on the wall
  float row = floor(wm.y / 0.075);
  vec2 bc = vec2(wm.x / 0.25 + 0.5 * mod(row, 2.0), wm.y / 0.075);
  vec2 bf = fract(bc);
  float mortar = 1.0 - skBox(bf, vec2(0.04, 0.08), vec2(0.96, 0.92), fwidth(bc));
  float plaster = skFbm(vSkWorld.xz * 2.5 + vSkWorld.y * 3.1);
  base *= mix(0.94 + 0.1 * plaster, 0.9 + 0.18 * skHash(floor(bc)) - 0.25 * mortar, brick * near);
  base *= 0.93 + 0.12 * skNoise(vSkWorld.xz * 0.35 + vSkWorld.y * 0.2);   // weathering
  skH = mix(plaster * 0.004, (1.0 - mortar) * 0.006, brick) * near;
  vec2 cell = floor(uv);
  vec2 f = fract(uv);
  vec2 aa = fwidth(uv) * 0.75;
  float fade = (1.0 - smoothstep(0.12, 0.4, max(aa.x, aa.y))) * uWindows;
  float valid = step(-0.5, uv.x) * step(0.0, uv.y);
  float seed = skHash(vec2(vFid, 7.13));
  float ww = 0.17 + 0.05 * seed, y0 = 0.33, wh = 0.38;            // house: ~1.0-1.3 m wide windows
  if (st == 2) { ww = 0.2; y0 = 0.3; wh = 0.42; }
  if (st == 4) { ww = 0.16; y0 = 0.22; wh = 0.55; }
  if (st == 7) { ww = 0.14 + 0.03 * seed; y0 = 0.2; wh = 0.6; }
  if (st == 6) { ww = 0.12; y0 = 0.16; wh = 0.68; }
  float win = skBox(f, vec2(0.5 - ww, y0), vec2(0.5 + ww, y0 + wh), aa);
  float fr = skBox(f, vec2(0.5 - ww - 0.03, y0 - 0.04), vec2(0.5 + ww + 0.03, y0 + wh + 0.03), aa) - win;
  float sill = skBox(f, vec2(0.5 - ww - 0.06, y0 - 0.075), vec2(0.5 + ww + 0.06, y0 - 0.035), aa);
  float mullion = skBox(f, vec2(0.497, y0), vec2(0.503, y0 + wh), aa) + skBox(f, vec2(0.5 - ww, y0 + wh * 0.68), vec2(0.5 + ww, y0 + wh * 0.68 + 0.01), aa);
  if (st == 6 || st == 7) {
    vec2 c = vec2(0.5, y0 + wh - ww);
    win *= mix(1.0, 1.0 - smoothstep(ww - aa.x, ww + aa.x, length(f - c)), step(c.y, f.y));
  }
  if ((st == 1 || st == 3) && skHash(cell + vec2(vFid * 0.37, 1.7)) < 0.2) { win = 0.0; fr = 0.0; sill = 0.0; mullion = 0.0; }
  if (st == 3 && cell.y < 0.5) {
    win = skBox(f, vec2(0.08, 0.12), vec2(0.92, 0.86), aa);
    fr = skBox(f, vec2(0.05, 0.08), vec2(0.95, 0.9), aa) - win;
    sill = 0.0; mullion = skBox(f, vec2(0.497, 0.12), vec2(0.503, 0.86), aa);
  }
  float balcony = 0.0;
  if (st == 2 && cell.y > 0.5 && mod(cell.x, 2.0) < 0.5) balcony = skBox(f, vec2(0.18, 0.0), vec2(0.82, 0.24), aa);
  float plinth = (st >= 1 && st != 5) ? (1.0 - smoothstep(0.1, 0.1 + aa.y * 2.0, uv.y)) * step(0.0, uv.y + 0.001) : 0.0;
  float on = valid * fade * step(0.5, float(st)) * (st == 5 ? 0.0 : 1.0);
  skWin = clamp(win - mullion, 0.0, 1.0) * on;
  float curtain = skHash(cell + vec2(vFid, 3.1));
  vec3 glass = mix(vec3(0.02, 0.03, 0.04), vec3(0.16, 0.13, 0.1), step(0.62, curtain) * 0.55);
  base = mix(base, base * 0.62 + 0.02, plinth * uWindows);
  vec3 frameCol = mix(vec3(0.88, 0.88, 0.86), vec3(0.32, 0.22, 0.15), step(0.75, seed));   // white PVC or wood
  base = mix(base, frameCol, clamp(fr + mullion, 0.0, 1.0) * on * (st == 2 ? 0.5 : 1.0));
  base = mix(base, vec3(0.78, 0.78, 0.76), sill * on);
  base = mix(base, base * 1.12 + 0.04, balcony * valid * fade);
  float ao = 0.62 + 0.38 * smoothstep(-0.05, 0.45, uv.y);         // contact darkening at the foot
  base *= mix(1.0, ao, step(-0.5, uv.y));
  diffuseColor.rgb = mix(base, glass, skWin);
  skRough = mix(0.9, 0.04, skWin);
  skBumpScale = 1.0 - skWin;
  skH += sill * on * 0.02 + fr * on * 0.012 - skWin * 0.02;
}`

const ROOF_FRAG = /* glsl */ `
{
  vec3 base = diffuseColor.rgb;
  base *= 0.88 + 0.2 * skFbm(vSkWorld.xz * 0.25);              // weathering / stains
  int rm = int(vStyle + 0.5);
  vec2 uv = vRoofUv;
  vec2 aa = fwidth(uv);
  float dist = length(vSkWorld - cameraPosition);
  float near = 1.0 - smoothstep(60.0, 320.0, dist);
  float h = 0.0, rough = 0.75, metal = 0.0, tint = 0.0;
  if (rm == 1) {         // metal tile: stepped rows, wave crests
    float row = fract(uv.y / 0.35);
    float wave = 0.5 + 0.5 * sin(uv.x * 6.2831 / 0.185);
    h = (smoothstep(0.0, 0.85, row) - smoothstep(0.85, 1.0, row)) * 0.008 + wave * 0.008;
    tint = -smoothstep(0.88, 1.0, row) * 0.22 * (1.0 - smoothstep(0.04, 0.12, aa.y)); // shadow line under each row
    rough = 0.5; metal = 0.15;
  } else if (rm == 2) {  // asbestos-cement slate: fine waves, sheet overlaps, lichen spots
    float wave = 0.5 + 0.5 * sin(uv.x * 6.2831 / 0.115);
    float sheet = smoothstep(0.94, 1.0, fract(uv.y / 1.6));
    h = wave * 0.012 + sheet * 0.008;
    tint = -sheet * 0.2 - smoothstep(0.62, 0.8, skNoise(vSkWorld.xz * 1.7)) * 0.18;
    rough = 0.92;
  } else if (rm == 3) {  // corrugated sheet
    h = (0.5 + 0.5 * sin(uv.x * 6.2831 / 0.076)) * 0.01;
    rough = 0.42; metal = 0.4;
  } else if (rm == 4) {  // standing seam
    float s = fract(uv.x / 0.55);
    h = smoothstep(0.9, 0.96, s) * (1.0 - smoothstep(0.96, 1.0, s)) * 0.03;
    tint = h * 6.0;
    rough = 0.35; metal = 0.5;
  } else {               // bitumen / membrane with patches
    tint = (skFbm(vSkWorld.xz * 0.6) - 0.5) * 0.25;
    h = skNoise(vSkWorld.xz * 6.0) * 0.003;
    rough = 0.95;
  }
  diffuseColor.rgb = base * (1.0 + tint * uWindows);
  skH = h * near * uWindows;
  skRough = rough;
  skMetal = metal * uWindows;
}`

function walls(withWindows: boolean) {
  const m = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.88, metalness: 0, flatShading: true })
  if (!withWindows) return m
  return patch(m, 'skvyra-walls-2', {
    varyings: 'varying vec2 vFacadeUv;\nvarying float vStyle;\nvarying float vFid;\nvarying vec3 vSkWorld;',
    vert: 'vFacadeUv = uv;\nvStyle = _style;\nvFid = _feature_id;',
    frag: WALL_FRAG,
    bump: true,
  })
}

function roofs() {
  // double-sided: the underside of the eaves overhang is visible from the street (no separate soffit mesh)
  const m = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.62, metalness: 0.05, flatShading: true, side: THREE.DoubleSide })
  return patch(m, 'skvyra-roofs-2', {
    varyings: 'varying vec2 vRoofUv;\nvarying float vStyle;\nvarying float vFid;\nvarying vec3 vSkWorld;',
    vert: 'vRoofUv = uv;\nvStyle = _style;\nvFid = _feature_id;',
    frag: ROOF_FRAG,
    bump: true,
  })
}

/** Ground: satellite colour from afar, procedural grass / soil detail blended in as the camera approaches. */
const GROUND_FRAG = /* glsl */ `
{
  vec3 c = diffuseColor.rgb;
  vec2 p = vSkWorld.xz;
  float d = length(vSkWorld - cameraPosition);
  float near = 1.0 - smoothstep(25.0, 220.0, d);      // full detail close up
  float mid = 1.0 - smoothstep(150.0, 1400.0, d);     // macro variation at district scale
  float green = clamp((c.g - max(c.r, c.b)) * 6.0 + 0.35, 0.0, 1.0);
  float blades = skNoise(p * 9.0) * 0.55 + skNoise(p * 23.0) * 0.45;
  float clumps = skFbm(p * 0.9);
  vec3 grass = c * (0.72 + 0.42 * blades) * mix(vec3(0.92, 1.0, 0.85), vec3(1.08, 1.03, 0.9), clumps);
  float pebbles = smoothstep(0.72, 0.9, skNoise(p * 14.0));
  vec3 soil = c * (0.82 + 0.3 * skFbm(p * 3.3)) + pebbles * 0.05;
  vec3 detail = mix(soil, grass, green);
  c = mix(c, detail, near * 0.9);
  c *= 1.0 + (skFbm(p * 0.045) - 0.5) * 0.16 * mid;
  diffuseColor.rgb = c;
  skH = (green * (blades * 0.02 + clumps * 0.04) + (1.0 - green) * pebbles * 0.01) * near;
}`

const ASPHALT_FRAG = /* glsl */ `
{
  vec2 p = vSkWorld.xz;
  float d = length(vSkWorld - cameraPosition);
  float near = 1.0 - smoothstep(40.0, 400.0, d);
  vec3 c = diffuseColor.rgb;
  float agg = skNoise(p * 16.0) * 0.6 + skNoise(p * 41.0) * 0.4;                // aggregate
  float patches = smoothstep(0.58, 0.62, skFbm(p * 0.18));                        // repairs
  float cracks = (1.0 - smoothstep(0.0, 0.006, abs(skNoise(p * 1.7) - 0.5))) * smoothstep(0.55, 0.7, skNoise(p * 0.3)); // sparse hairline cracks
  float wear = skFbm(p * 0.06);
  c *= 0.86 + 0.24 * agg * near;
  c = mix(c, c * 0.72, patches * 0.8);
  c = mix(c, c * 0.62, cracks * near * 0.6);
  c *= 0.92 + 0.16 * wear;
  diffuseColor.rgb = c;
  skH = (agg * 0.004 - cracks * 0.006) * near;
}`

const GRAVEL_FRAG = /* glsl */ `
{
  vec2 p = vSkWorld.xz;
  float d = length(vSkWorld - cameraPosition);
  float near = 1.0 - smoothstep(30.0, 300.0, d);
  float st = skNoise(p * 26.0);
  float big = smoothstep(0.7, 0.85, skNoise(p * 7.0));
  diffuseColor.rgb *= (0.8 + 0.35 * st * near) * (1.0 + big * 0.12) * (0.9 + 0.2 * skFbm(p * 0.4));
  skH = (st * 0.008 + big * 0.01) * near;
}`

const WATER_FRAG = /* glsl */ `
{
  vec2 p = vSkWorld.xz;
  float t = uTime * 0.35;
  float w = skNoise(p * 0.55 + vec2(t, t * 0.6)) * 0.6 + skNoise(p * 1.9 - vec2(t * 1.3, -t)) * 0.4;
  float d = length(vSkWorld - cameraPosition);
  skH = w * 0.05 * (1.0 - smoothstep(80.0, 900.0, d));
  skRough = 0.05;
}`

const TREE_FRAG = /* glsl */ `
{
  // leaf clusters: break the faceted crowns with world-space speckle (decorative)
  float l = skNoise(vSkWorld.xz * 3.1 + vSkWorld.y * 2.7) * 0.6 + skNoise(vSkWorld.xz * 9.0 + vSkWorld.y * 7.0) * 0.4;
  diffuseColor.rgb *= 0.75 + 0.45 * l;
  skH = l * 0.06;
}`

function grain(m: THREE.MeshStandardMaterial, key: string, frag: string) {
  return patch(m, key, { varyings: 'varying vec3 vSkWorld;', frag, bump: true })
}

const overlay = (color: string, rough = 1, factor = -2) =>
  new THREE.MeshStandardMaterial({
    color, vertexColors: false, roughness: rough, metalness: 0, flatShading: true,
    polygonOffset: true, polygonOffsetFactor: factor, polygonOffsetUnits: factor * 2,
  })

export const MAT = {
  walls: walls(true),
  wallsPlain: walls(false),
  roofs: roofs(),
  details: new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.7, flatShading: true }),
  road_paved: grain(overlay('#5b5c5f', 0.9, -3), 'skvyra-asphalt-2', ASPHALT_FRAG),
  road_unpaved: grain(overlay('#8f7b5e', 1, -2), 'skvyra-dirt-2', GRAVEL_FRAG),
  road_foot: grain(overlay('#aca393', 1, -2), 'skvyra-path-2', GRAVEL_FRAG),
  road_shoulder: grain(overlay('#857a64', 1, -2), 'skvyra-shoulder', GRAVEL_FRAG),
  bridge: new THREE.MeshStandardMaterial({ color: '#8f8b86', roughness: 0.85, flatShading: true }),
  rail: grain(overlay('#6f655d', 1, -2), 'skvyra-ballast-2', GRAVEL_FRAG),
  water: patch(new THREE.MeshStandardMaterial({
    vertexColors: true, roughness: 0.05, metalness: 0.0, flatShading: true, envMapIntensity: 1.2,
    polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -2,
  }), 'skvyra-water-2', { varyings: 'varying vec3 vSkWorld;', frag: WATER_FRAG, bump: true }),
  decor_fence: new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.55, metalness: 0.2, flatShading: true, side: THREE.DoubleSide }),
  issue: new THREE.MeshStandardMaterial({ color: '#d63b3b', emissive: '#7a1010', roughness: 0.5 }),
  tree: patch(new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.82 }), 'skvyra-tree',
    { varyings: 'varying vec3 vSkWorld;', frag: TREE_FRAG, bump: true }),
}

export function terrainMaterial(map: THREE.Texture | null) {
  if (map) {
    map.colorSpace = THREE.SRGBColorSpace
    map.anisotropy = 8
  }
  const m = new THREE.MeshStandardMaterial({ map, roughness: 1, metalness: 0, color: map ? '#ffffff' : '#8f9a6c', flatShading: true })
  return grain(m, 'skvyra-terrain-2', GROUND_FRAG)
}

// data-quality palette (sRGB); identical in the legend
export const QUALITY_COLORS = {
  verified: '#2e9e5b',
  source: '#3a7bd5',
  inferred: '#e8913a',
  missing: '#d63b3b',
  hOverride: '#2e9e5b',
  hSource: '#3a7bd5',
  hGhsl: '#9b7fd1',
  hDefault: '#b9b4ab',
  treeModel: '#1fa59a',
} as const

// data-quality layer: probably missing building outlines along residential streets (lines, never buildings)
export const GAP_COLORS = { suspect_gap_medium: '#c026d3', suspect_gap_low: '#e9a3f0', insufficient: '#9aa3ab', no_signs: '#5b9bd5' } as const

const tmp = new THREE.Color()
export function linear(hex: string): [number, number, number] {
  tmp.setStyle(hex) // ColorManagement converts sRGB -> linear working space
  return [tmp.r, tmp.g, tmp.b]
}
