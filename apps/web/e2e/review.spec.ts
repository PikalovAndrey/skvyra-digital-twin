// Mapping review workbench (review.html): priority list, outline checks, change-set export.
import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'

test('review workbench: priorities, self-intersection check, reject + export change set', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto('/review.html')
  await expect(page.getByTestId('item-zemstvo-building')).toBeVisible()
  await expect(page.getByTestId('item-kostel')).toBeVisible()
  await page.getByTestId('item-zemstvo-building').click()
  await expect(page.getByTestId('item-detail')).toContainText('Каштановий')
  // draw a bow-tie: must be flagged and cannot be added
  await page.getByText('Креслити').click()
  const map = page.getByTestId('review-map')
  const b = (await map.boundingBox())!
  const cx = b.x + b.width / 2, cy = b.y + b.height / 2
  for (const [dx, dy] of [[-60, -60], [60, 60], [60, -60], [-60, 60]]) await page.mouse.click(cx + dx, cy + dy)
  await page.getByPlaceholder('метод', { exact: false }).fill('e2e test')
  await expect(page.getByTestId('draft-problems')).toContainText('самоперетин')
  await expect(page.getByTestId('add-outline')).toHaveCount(0)
  // reject the unconfirmed candidate and export
  await page.getByText('Відхилити зіставлення').click()
  await expect(page.getByTestId('changes')).toContainText('Q122300599')
  const [dl] = await Promise.all([page.waitForEvent('download'), page.getByText('Завантажити набір змін').click()])
  const doc = JSON.parse(readFileSync((await dl.path())!, 'utf8'))
  expect(doc.changes).toEqual([expect.objectContaining({ op: 'reject_match', wikidata: 'Q122300599', source_id: 'ms:aa972e1721d8' })])
  expect(errors).toEqual([])
})
