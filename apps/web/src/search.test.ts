import { describe, expect, it } from 'vitest'
import { buildIndex, foldKey, nearestStreet, search, translit } from './search'

const items = buildIndex([
  { name: 'Сквира', kind: 'place=town', x: 0, y: 0 },
  { name: 'вулиця Богачевського', kind: 'street', x: 10, y: 10 },
  { name: 'вулиця Шевченка', kind: 'street', x: 20, y: 20, bb: [0, 0, 100, 0], parts: [[0, 0, 100, 0]] },
  { name: 'Сквирський ліцей №2', name_en: 'Skvyra Lyceum 2', kind: 'amenity=school', x: 5, y: 5 },
  { name: 'Київська вулиця', kind: 'street', x: 30, y: 30 },
])

describe('search', () => {
  it('transliterates Ukrainian per KMU 2010', () => {
    expect(translit('Сквира')).toBe('skvyra')
    expect(translit('Київ')).toBe('kyiv')
  })
  it('folds transliteration variants together', () => {
    expect(foldKey('Skvira')).toBe(foldKey('Skvyra'))
    expect(foldKey('Сквира')).toBe(foldKey('Skvira'))
    expect(foldKey('Shevchenka')).toBe(foldKey('Шевченка'))
    expect(foldKey('Bohachevskoho')).toBe(foldKey('Богачевського'))
  })
  it.each([['Сквира'], ['Skvira'], ['skvyra'], ['СКВИРА']])('finds the town for %s', (q) => {
    expect(search(items, q).map((r) => r.name)).toContain('Сквира')
  })
  it('matches Latin queries to Cyrillic streets and ignores "вулиця"/"street"', () => {
    expect(search(items, 'Shevchenka street')[0].name).toBe('вулиця Шевченка')
    expect(search(items, 'вул. Шевченка')[0].name).toBe('вулиця Шевченка')
    expect(search(items, 'Kyivska')[0].name).toBe('Київська вулиця')
    expect(search(items, 'Kievskaya')).toHaveLength(0) // Russian spelling differs too much: honest miss
  })
  it('ranks streets before other kinds for equal matches', () => {
    expect(search(items, 'skv')[0].kind).not.toBe('amenity=school')
  })
  it('empty query returns nothing', () => {
    expect(search(items, '  ')).toEqual([])
  })
  it('nearestStreet uses polyline distance', () => {
    expect(nearestStreet(items, 50, 10)?.name).toBe('вулиця Шевченка')
    expect(nearestStreet(items, 50, 40)).toBeNull()
  })
})
