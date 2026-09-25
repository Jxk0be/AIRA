import { expect, test, type Page } from '@playwright/test'

import { open, TENANT } from './support/app'

/**
 * The buttons an answer ends with.
 *
 * The rest of the suite reads pages as they load. This one has to drive a
 * conversation, so the chat endpoint is replayed as a real server-sent stream
 * rather than as a fixture file: the events, their order and the blank line
 * between them are the contract `api/stream.ts` parses, and a test that handed
 * the parser a JSON blob would be testing something nobody ships.
 *
 * What is worth proving here is the two halves of the promise. A button that
 * says it opens the reorder list opens the reorder list — with the tab, in the
 * same shop. And an email is a draft: it opens where the owner can read and
 * change it, and nothing about pressing the button sends anything.
 */

const ASK = `/${TENANT}/ask`

interface StreamEvent {
  event: string
  data: unknown
}

const DONE = {
  event: 'done',
  data: {
    conversation_id: '00000000-0000-4000-8000-000000000001',
    title: 'Anything running out',
    model: 'claude-sonnet-5',
    latency_ms: 1200,
    input_tokens: 900,
    output_tokens: 120,
    cache_read_tokens: 0,
    cost_usd: '0.003000',
    tools: ['low_stock'],
  },
}

/** Answer the next chat POST with these events, as the wire would carry them. */
async function replyWith(page: Page, events: StreamEvent[]): Promise<void> {
  const body = events.map((one) => `event: ${one.event}\ndata: ${JSON.stringify(one.data)}\n\n`).join('')
  // Registered after `open`, and Playwright prefers the newest matching route,
  // so this wins over the catch-all 200 the fixture mock gives every POST.
  await page.route(`**/api/tenants/${TENANT}/chat`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'text/event-stream', body })
  })
}

async function ask(page: Page, question: string): Promise<void> {
  await page.getByLabel('Your question').fill(question)
  await page.getByRole('button', { name: 'Ask', exact: true }).click()
}

test.describe('what an answer offers to do', () => {
  test('a button that names a screen goes to that screen', async ({ page }, testInfo) => {
    await open(page, ASK, testInfo)
    await replyWith(page, [
      { event: 'token', data: { text: 'Four are under a week of cover.' } },
      {
        event: 'action',
        data: {
          key: 'open_reorder',
          kind: 'open',
          label: 'Order the ones that are low',
          detail: 'four are under a week of cover',
          route: 'stock?tab=reorder',
          task: null,
          email: null,
        },
      },
      DONE,
    ])

    await ask(page, 'Anything running out?')

    const button = page.getByRole('button', { name: 'Order the ones that are low' })
    await expect(button).toBeVisible()
    // The reason lives under the button, not inside it.
    await expect(page.getByText('four are under a week of cover', { exact: true })).toBeVisible()

    await button.click()
    await expect(page).toHaveURL(new RegExp(`/${TENANT}/stock\\?tab=reorder$`))
  })

  test('an email opens as a draft the owner can edit, and is not sent', async ({
    page,
  }, testInfo) => {
    await open(page, ASK, testInfo)
    await replyWith(page, [
      { event: 'token', data: { text: 'Here is an email you can send.' } },
      {
        event: 'action',
        data: {
          key: 'draft_email',
          kind: 'email',
          label: 'Open this email',
          detail: null,
          route: null,
          task: null,
          email: {
            to: 'orders@example.test',
            subject: 'Restock order',
            body: 'Hello — could we get these on the next delivery?',
          },
        },
      },
      DONE,
    ])

    await ask(page, 'Email my distributor about a restock')
    await page.getByRole('button', { name: 'Open this email' }).click()

    await expect(page.getByRole('textbox', { name: 'To' })).toHaveValue('orders@example.test')
    await expect(page.getByRole('textbox', { name: 'Subject' })).toHaveValue('Restock order')
    const body = page.getByRole('textbox', { name: 'Message' })
    await expect(body).toHaveValue(/next delivery/)

    // Editable, because the first thing anyone does to a written email is
    // change a word of it.
    await body.fill('Rewritten by hand.')
    await expect(body).toHaveValue('Rewritten by hand.')

    // And the whole point: the app never claims to have sent it.
    await expect(page.getByText(/nothing is sent until you send it/i)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Open in email app' })).toBeEnabled()
  })

  test('an email too long for a mail app link says so instead of losing the end', async ({
    page,
  }, testInfo) => {
    await open(page, ASK, testInfo)
    await replyWith(page, [
      { event: 'token', data: { text: 'That is a long one.' } },
      {
        event: 'action',
        data: {
          key: 'draft_email',
          kind: 'email',
          label: 'Open this email',
          detail: null,
          route: null,
          task: null,
          email: { to: null, subject: 'Every line', body: 'x'.repeat(2400) },
        },
      },
      DONE,
    ])

    await ask(page, 'Write the long version')
    await page.getByRole('button', { name: 'Open this email' }).click()

    await expect(page.getByRole('button', { name: 'Open in email app' })).toBeDisabled()
    await expect(page.getByText(/too long to hand to your email app/i)).toBeVisible()
    // Copy is still there, which is the way out.
    await expect(page.getByRole('button', { name: 'Copy' })).toBeEnabled()
  })
})
