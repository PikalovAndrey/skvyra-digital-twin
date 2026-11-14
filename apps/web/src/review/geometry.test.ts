import { describe, expect, it } from 'vitest'
import { area, selfIntersects } from './ReviewApp'

describe('review outline checks', () => {
  it('detects a bow-tie and accepts a rectangle', () => {
    expect(selfIntersects([[0, 0], [10, 10], [10, 0], [0, 10]])).toBe(true)
    expect(selfIntersects([[0, 0], [10, 0], [10, 8], [0, 8]])).toBe(false)
  })
  it('computes the area independent of orientation', () => {
    expect(area([[0, 0], [10, 0], [10, 8], [0, 8]])).toBe(80)
    expect(area([[0, 0], [0, 8], [10, 8], [10, 0]])).toBe(80)
  })
})
