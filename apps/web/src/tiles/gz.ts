/** Tiles of the packed demo build are gzip files (scripts/pack_demo.py): any static host serves them byte for byte,
 * no server-side compression needed. Plain GLB (magic "glTF") passes through, so dev and packed builds both work. */
export async function maybeGunzip(buf: ArrayBuffer): Promise<ArrayBuffer> {
  const b = new Uint8Array(buf, 0, Math.min(2, buf.byteLength))
  if (b[0] !== 0x1f || b[1] !== 0x8b) return buf
  return new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer()
}
