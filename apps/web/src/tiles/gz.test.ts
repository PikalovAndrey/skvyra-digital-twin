import { createServer, type Server } from 'node:http'
import { gzipSync } from 'node:zlib'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { maybeGunzip } from './gz'

const glb = new TextEncoder().encode('glTF\x02\x00\x00\x00 some binary payload '.repeat(20))
const gz = new Uint8Array(gzipSync(glb)) // a copy: Buffer.slice() would share (and let a test corrupt) the bytes

describe('maybeGunzip', () => {
  it('passes plain GLB through and inflates gzip', async () => {
    const plain = glb.slice().buffer as ArrayBuffer
    expect(await maybeGunzip(plain)).toBe(plain)
    expect(new Uint8Array(await maybeGunzip(gz.slice().buffer as ArrayBuffer))).toEqual(glb)
  })

  it('rejects a corrupt or truncated gzip (the tile counts as failed and is retried, no broken mesh)', async () => {
    const bad = gz.slice()
    bad[gz.length - 6] ^= 0xff // CRC mismatch
    await expect(maybeGunzip(bad.buffer as ArrayBuffer)).rejects.toThrow()
    await expect(maybeGunzip(gz.slice(0, gz.length >> 1).buffer as ArrayBuffer)).rejects.toThrow()
  })
})

// What a static host / CDN can do to a pre-gzipped tile, fetched through a real HTTP stack (fetch decodes any
// Content-Encoding transparently, as browsers do): the GLB must come out intact exactly once in every case.
describe('tile delivery variants', () => {
  let srv: Server
  let base = ''
  beforeAll(async () => {
    srv = createServer((req, res) => {
      const variant = req.url!.slice(1).split('?')[0]
      if (variant === 'plain') res.writeHead(200, { 'Content-Type': 'model/gltf-binary' }).end(Buffer.from(glb))
      // Cloudflare Pages today: model/gltf-binary is not on its compression list, bytes served as uploaded
      else if (variant === 'packed') res.writeHead(200, { 'Content-Type': 'model/gltf-binary' }).end(gz)
      // a CDN that compresses everything: transport gzip on top of the file's own gzip
      else if (variant === 'packed+cdn') res.writeHead(200, { 'Content-Type': 'model/gltf-binary', 'Content-Encoding': 'gzip' }).end(gzipSync(gz))
      // a host that recognises the gzip file and serves it with Content-Encoding: the client receives the plain GLB
      else if (variant === 'packed-as-encoding') res.writeHead(200, { 'Content-Type': 'model/gltf-binary', 'Content-Encoding': 'gzip' }).end(gz)
      else res.writeHead(404).end('not found')
    })
    await new Promise<void>((r) => srv.listen(0, '127.0.0.1', r))
    base = `http://127.0.0.1:${(srv.address() as { port: number }).port}/`
  })
  afterAll(() => new Promise<void>((r) => srv.close(() => r())))

  for (const v of ['plain', 'packed', 'packed+cdn', 'packed-as-encoding']) {
    it(v, async () => {
      const res = await fetch(`${base}${v}?v=hash`)
      expect(res.ok).toBe(true)
      expect(new Uint8Array(await maybeGunzip(await res.arrayBuffer()))).toEqual(glb)
    })
  }

  it('a missing tile is an HTTP error, not data', async () => {
    expect((await fetch(`${base}missing`)).status).toBe(404) // TileManager.load throws `HTTP 404` before decoding
  })
})
