import { test, expect } from '@playwright/test'

/**
 * Phase C · Playwright browser E2E (auth UI + convert surface + mocked convert/download).
 * CI: npm run build && npx playwright install --with-deps chromium && npm run test:e2e
 */

async function mockJobConvertApi(page) {
  const jobId = 'e2e-job-1'
  const token = 'e2e-token'
  const bodyMd = '# E2E Converted\n\nSENT_E2E_DOWNLOAD\n'

  await page.route('**/api/jobs', async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: jobId,
          status: 'queued',
          progress: 0.1,
          access_token: token,
        }),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ jobs: [] }),
    })
  })

  await page.route(`**/api/jobs/${jobId}**`, async (route) => {
    const url = route.request().url()
    if (url.includes('/result')) {
      await route.fulfill({
        status: 200,
        contentType: 'text/markdown',
        headers: {
          'content-disposition': 'attachment; filename="e2e-out.md"',
        },
        body: bodyMd,
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: jobId,
        status: 'done',
        progress: 1,
        result_filename: 'e2e-out.md',
      }),
    })
  })

  // formats / health noise
  await page.route('**/api/formats**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        sources: ['md', 'txt', 'docx'],
        targets: ['md', 'html', 'docx', 'ods', 'odp'],
        fidelity_tiers: {
          full: ['md', 'html', 'docx'],
          text_level: [],
          export_shallow: ['ods', 'odp'],
        },
      }),
    })
  })
}

test.describe('md-bridge web smoke (C)', () => {
  test('brand + convert tab render', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.md-brand-name')).toContainText('md')
    await expect(page.getByTestId('tab-convert')).toBeVisible()
    await expect(page.getByTestId('tab-ops')).toBeVisible()
    await expect(page.getByTestId('tab-tasks')).toBeVisible()
  })

  test('ops tab exposes API Key field (FE-01)', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('tab-ops').click()
    const key = page.getByTestId('api-key-input')
    await expect(key).toBeVisible()
    await key.fill('test-key-e2e')
    await page.getByTestId('api-key-save').click()
    await expect(key).toHaveValue('test-key-e2e')
  })

  test('convert workspace visible', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('tab-convert').click()
    await expect(page.locator('.md-workspace')).toBeVisible()
    await expect(page.locator('.md-fmt-grid')).toBeVisible()
  })

  test('format selects + convert CTA (C deepen)', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('tab-convert').click()
    await expect(page.getByTestId('source-format')).toBeVisible()
    await expect(page.getByTestId('target-format')).toBeVisible()
    const convertBtn = page.getByTestId('btn-convert')
    await expect(convertBtn).toBeVisible()
    await expect(convertBtn).toBeEnabled()

    await page.getByTestId('target-format-btn').click()
    const menu = page.locator('.md-select__menu')
    await expect(menu).toBeVisible()
    await expect(menu.getByText(/ods/i).first()).toBeVisible()
    // shallow export honesty tag (zh or en)
    await expect(menu.getByText(/浅导出|shallow/i).first()).toBeVisible()
    await menu.getByText(/ods/i).first().click()
    await expect(page.getByTestId('target-format-btn')).toContainText(/ods/i)
  })

  test('true convert + download (mocked job API)', async ({ page }) => {
    await mockJobConvertApi(page)
    await page.goto('/')
    await page.getByTestId('tab-convert').click()

    // Prefer md target for preview text path
    await page.getByTestId('target-format-btn').click()
    const menu = page.locator('.md-select__menu')
    await expect(menu).toBeVisible()
    await menu.getByText(/^md\b|markdown/i).first().click().catch(async () => {
      await menu.getByText(/md/i).first().click()
    })

    await page.getByTestId('file-input').setInputFiles({
      name: 'e2e-in.md',
      mimeType: 'text/markdown',
      buffer: Buffer.from('# Hello\n\nbody\n', 'utf-8'),
    })
    await expect(page.locator('.md-frow').first()).toBeVisible()

    await page.getByTestId('btn-convert').click()
    await expect(page.getByTestId('btn-download-job')).toBeVisible({ timeout: 15_000 })
    await expect(page.locator('.md-preview')).toContainText(/SENT_E2E_DOWNLOAD|E2E Converted/)

    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByTestId('btn-download-job').click(),
    ])
    expect(download.suggestedFilename()).toMatch(/e2e-out\.md|converted/i)
  })
})
