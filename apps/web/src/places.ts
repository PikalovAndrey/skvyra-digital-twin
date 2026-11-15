// Ready-made views of recognisable places (local frame: x east, y north, z up, metres; same values as
// blender/presets/viewpoints.json). Only places whose geometry is in the data; the camera framing is the only choice.
import type { Key } from './i18n'

export interface Place { id: string; label: Key; camera: [number, number, number]; target: [number, number, number] }

export const PLACES: Place[] = [
  { id: 'centre', label: 'place.centre', camera: [-130, -80, 300], target: [60, 170, 208] },
  { id: 'lyceum', label: 'place.lyceum', camera: [-760, 600, 250], target: [-840, 715, 218] },
  { id: 'synagogue', label: 'place.synagogue', camera: [412.89, 110.31, 262.21], target: [459, 237, 196.45] },
  { id: 'kyivska-1', label: 'place.kyivska1', camera: [292, 660, 212], target: [270, 694, 198] },
  { id: 'houses', label: 'place.houses', camera: [-800, -660, 260.11], target: [-760, -560, 215.11] },
]
