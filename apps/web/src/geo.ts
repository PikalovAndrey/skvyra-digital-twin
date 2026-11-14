// Local frame <-> WGS84, mirroring pipeline/skvyra/geo.py.
// local: x = east, y = north (metres from the UTM origin in index.json); three.js: (x, up, -north).
// UTM via the Krüger series (Karney 2011, 3rd order): sub-millimetre inside a zone.

const A_ = 6378137
const F_ = 1 / 298.257223563
const K0 = 0.9996
const n = F_ / (2 - F_)
const A = (A_ / (1 + n)) * (1 + (n * n) / 4 + (n ** 4) / 64)
const alpha = [n / 2 - (2 * n * n) / 3 + (5 * n ** 3) / 16, (13 * n * n) / 48 - (3 * n ** 3) / 5, (61 * n ** 3) / 240]
const beta = [n / 2 - (2 * n * n) / 3 + (37 * n ** 3) / 96, (n * n) / 48 + n ** 3 / 15, (17 * n ** 3) / 480]
const delta = [2 * n - (2 * n * n) / 3 - 2 * n ** 3, (7 * n * n) / 3 - (8 * n ** 3) / 5, (56 * n ** 3) / 15]
const RAD = Math.PI / 180

export interface Frame {
  crs: string
  origin_utm: [number, number]
  origin_wgs84: [number, number]
}

function zoneOf(crs: string): { lon0: number; south: boolean } {
  const m = /^EPSG:327?(\d{3})$/.exec(crs) ?? /^EPSG:(\d{5})$/.exec(crs)
  const code = Number(crs.split(':')[1])
  if (!m || !((code > 32600 && code <= 32660) || (code > 32700 && code <= 32760))) {
    throw new Error(`unsupported CRS ${crs}: only WGS84 / UTM (EPSG:326xx, 327xx) is implemented`)
  }
  const zone = code % 100
  return { lon0: (zone - 1) * 6 - 180 + 3, south: code > 32700 }
}

export function utmToGeo(e: number, nn: number, crs: string): [number, number] {
  const { lon0, south } = zoneOf(crs)
  const xi = (nn - (south ? 10_000_000 : 0)) / (K0 * A)
  const eta = (e - 500_000) / (K0 * A)
  let xi1 = xi
  let eta1 = eta
  for (let j = 1; j <= 3; j++) {
    xi1 -= beta[j - 1] * Math.sin(2 * j * xi) * Math.cosh(2 * j * eta)
    eta1 -= beta[j - 1] * Math.cos(2 * j * xi) * Math.sinh(2 * j * eta)
  }
  const chi = Math.asin(Math.sin(xi1) / Math.cosh(eta1))
  let phi = chi
  for (let j = 1; j <= 3; j++) phi += delta[j - 1] * Math.sin(2 * j * chi)
  const lam = lon0 * RAD + Math.atan2(Math.sinh(eta1), Math.cos(xi1))
  return [lam / RAD, phi / RAD]
}

export function geoToUtm(lon: number, lat: number, crs: string): [number, number] {
  const { lon0, south } = zoneOf(crs)
  const phi = lat * RAD
  const dl = (lon - lon0) * RAD
  const c = (2 * Math.sqrt(n)) / (1 + n)
  const t = Math.sinh(Math.atanh(Math.sin(phi)) - c * Math.atanh(c * Math.sin(phi)))
  const xi1 = Math.atan2(t, Math.cos(dl))
  const eta1 = Math.atanh(Math.sin(dl) / Math.sqrt(1 + t * t))
  let xi = xi1
  let eta = eta1
  for (let j = 1; j <= 3; j++) {
    xi += alpha[j - 1] * Math.sin(2 * j * xi1) * Math.cosh(2 * j * eta1)
    eta += alpha[j - 1] * Math.cos(2 * j * xi1) * Math.sinh(2 * j * eta1)
  }
  return [500_000 + K0 * A * eta, (south ? 10_000_000 : 0) + K0 * A * xi]
}

export const localToGeo = (f: Frame, x: number, y: number) => utmToGeo(x + f.origin_utm[0], y + f.origin_utm[1], f.crs)

export function geoToLocal(f: Frame, lon: number, lat: number): [number, number] {
  const [e, nn] = geoToUtm(lon, lat, f.crs)
  return [e - f.origin_utm[0], nn - f.origin_utm[1]]
}

/** three.js position -> local (east, north, up) */
export const threeToLocal = (x: number, y: number, z: number): [number, number, number] => [x, -z, y]
export const localToThree = (x: number, y: number, h: number): [number, number, number] => [x, h, -y]

export function formatLatLon(lon: number, lat: number): string {
  return `${lat.toFixed(6)}° N, ${lon.toFixed(6)}° E`
}
