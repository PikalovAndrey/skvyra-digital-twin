import { describe, expect, it } from 'vitest'
import fixture from '../../../tests/fixtures/utm_points.json'
import { geoToLocal, geoToUtm, localToGeo, utmToGeo, type Frame } from './geo'

describe('UTM (Krüger series) vs pyproj', () => {
  for (const p of fixture.points) {
    it(`forward ${p.lon},${p.lat}`, () => {
      const [e, n] = geoToUtm(p.lon, p.lat, fixture.crs)
      expect(Math.abs(e - p.e)).toBeLessThan(0.001)
      expect(Math.abs(n - p.n)).toBeLessThan(0.001)
    })
    it(`inverse ${p.e},${p.n}`, () => {
      const [lon, lat] = utmToGeo(p.e, p.n, fixture.crs)
      expect(Math.abs(lon - p.lon)).toBeLessThan(1e-8)
      expect(Math.abs(lat - p.lat)).toBeLessThan(1e-8)
    })
  }
  it('local round trip keeps mm precision', () => {
    const f: Frame = { crs: 'EPSG:32635', origin_utm: [691900, 5512000], origin_wgs84: [29.6629, 49.7299] }
    for (const [x, y] of [[0, 0], [-3000, 2500], [3123.456, -2987.654]]) {
      const [lon, lat] = localToGeo(f, x, y)
      const [x2, y2] = geoToLocal(f, lon, lat)
      expect(Math.hypot(x2 - x, y2 - y)).toBeLessThan(0.001)
    }
  })
  it('rejects unsupported CRS', () => {
    expect(() => utmToGeo(0, 0, 'EPSG:3857')).toThrow(/unsupported CRS/)
  })
})
