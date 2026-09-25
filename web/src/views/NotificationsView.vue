<script setup lang="ts">
/**
 * Who hears from us, what Monday's email will say, and what actually went out.
 *
 * This lived at the bottom of the month-end screen, which is where nobody looks
 * for an email address. It is a setting, so it is under Settings.
 *
 * The send log is shown in full, held-back messages included, with the reason.
 * A quiet spell there is a setting doing its job rather than a fault, and the
 * only way to tell those apart is to show both.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import type { DigestPreview, NotificationRecipient, OutboundMessage } from '../api/types'
import SectionCard from '../components/SectionCard.vue'
import { shopDate, shopDateTime } from '../lib/format'
import { useTenantStore } from '../stores/tenant'
import UiBadge from '../ui/UiBadge.vue'
import UiButton from '../ui/UiButton.vue'
import { toast, withToast } from '../ui/toast'

const route = useRoute()
const shop = useTenantStore()

const slug = computed(() => String(route.params.tenant ?? ''))
const digest = ref<DigestPreview | null>(null)
const people = ref<NotificationRecipient[]>([])
const sent = ref<OutboundMessage[]>([])
const draft = ref({ email: '', name: '' })
const loading = ref(true)
const adding = ref(false)
const error = ref<string | null>(null)

/** The API's words for a message's fate, in the owner's. */
const STATUS: Record<string, { word: string; tone: 'success' | 'neutral' | 'warning' | 'danger' }> = {
  sent: { word: 'Sent', tone: 'success' },
  queued: { word: 'Waiting to send', tone: 'neutral' },
  suppressed: { word: 'Held back', tone: 'warning' },
  failed: { word: 'Failed', tone: 'danger' },
}

const statusOf = (value: string) => STATUS[value] ?? { word: value, tone: 'neutral' as const }

async function load() {
  loading.value = true
  try {
    const [preview, recipients, messages] = await Promise.all([
      api.digestPreview(slug.value).catch(() => null),
      api.recipients(slug.value).catch(() => []),
      api.outboundMessages(slug.value).catch(() => []),
    ])
    digest.value = preview
    people.value = recipients
    sent.value = messages
    error.value = null
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

async function addRecipient() {
  const email = draft.value.email.trim()
  if (!email) return
  adding.value = true
  const done = await withToast(
    () => api.saveRecipient(slug.value, { email, name: draft.value.name.trim() || null }),
    { success: `${email} will get Monday's email`, failure: 'Could not add that address' },
  )
  if (done !== undefined) {
    draft.value = { email: '', name: '' }
    await load()
  }
  adding.value = false
}

async function sendTest() {
  try {
    const result = await api.sendTestDigest(slug.value)
    if (result.status === 'sent') toast.success(`Test digest sent to ${result.to}`)
    else toast.warning(`Not sent to ${result.to}`, { detail: result.detail })
    await load()
  } catch {
    toast.danger('Could not send the test')
  }
}

onMounted(load)
watch(slug, load)
</script>

<template>
  <div class="mx-auto max-w-3xl px-4 py-5 sm:px-6">
    <div v-if="error" class="rounded-lg border border-danger bg-danger-subtle p-4" role="alert">
      <p class="font-medium text-ink">We could not load the notification settings.</p>
      <p class="mt-1 text-sm text-ink-muted">{{ error }}</p>
      <UiButton class="mt-3" size="sm" variant="secondary" @click="load">Try again</UiButton>
    </div>

    <div v-else class="space-y-4">
      <SectionCard title="Who hears from us" subtitle="The digest, and anything urgent" :loading="loading">
        <ul v-if="people.length" class="divide-y divide-border">
          <li v-for="person in people" :key="person.id" class="py-2.5">
            <p class="text-base text-ink">
              {{ person.name || person.email }}
              <span v-if="person.name" class="text-ink-muted">· {{ person.email }}</span>
            </p>
            <p class="text-sm text-ink-muted">
              {{ person.channels.join(', ') }}
              <template v-if="!person.wants_digest"> · no digest</template>
              <template v-if="person.max_per_day">
                · at most {{ person.max_per_day }} alerts a day
              </template>
              <template v-if="person.quiet_hours_start && person.quiet_hours_end">
                · quiet {{ person.quiet_hours_start }}–{{ person.quiet_hours_end }}
              </template>
            </p>
          </li>
        </ul>
        <p v-else class="text-base text-ink-muted">
          Nobody yet, so the Monday digest is built and then dropped. Add an address below.
        </p>

        <form class="mt-4 flex flex-wrap items-end gap-3 border-t border-border pt-4" @submit.prevent="addRecipient">
          <label class="min-w-48 flex-1 text-sm text-ink-muted">
            Email
            <input
              v-model="draft.email"
              type="email"
              required
              placeholder="owner@theshop.com"
              class="mt-1 block min-h-11 w-full rounded-md border border-border-strong bg-surface px-3 text-base text-ink"
            />
          </label>
          <label class="min-w-32 flex-1 text-sm text-ink-muted">
            Name (optional)
            <input
              v-model="draft.name"
              type="text"
              class="mt-1 block min-h-11 w-full rounded-md border border-border-strong bg-surface px-3 text-base text-ink"
            />
          </label>
          <UiButton type="submit" :loading="adding">Add</UiButton>
        </form>
        <p class="mt-2 text-sm text-ink-muted">
          Adding the same address again updates it rather than making a second one. Every message we
          send carries an unsubscribe link, and an unsubscribe here is final until they ask again.
        </p>
      </SectionCard>

      <SectionCard
        title="Monday's email"
        :subtitle="
          digest
            ? `${shopDate(digest.week_start, shop.timezone)} – ${shopDate(digest.week_end, shop.timezone)}`
            : undefined
        "
        :loading="loading"
        :empty="!loading && !digest"
        empty-text="Not enough recent data to build a digest from."
      >
        <template #actions>
          <UiButton size="sm" variant="secondary" @click="sendTest">Send me a test</UiButton>
        </template>

        <p class="mb-2 text-base font-semibold text-ink">{{ digest?.subject }}</p>
        <!-- Focusable: a scrollable box that cannot be reached by keyboard
             cannot be read by keyboard (axe scrollable-region-focusable). -->
        <pre
          class="max-h-96 overflow-auto rounded-md border border-border bg-raised p-3 text-sm leading-relaxed whitespace-pre-wrap text-ink-muted"
          tabindex="0"
          role="region"
          aria-label="Preview of Monday's email"
          >{{ digest?.text }}</pre
        >
        <p class="mt-2 text-sm text-ink-muted">
          <template v-if="digest?.copy_source === 'model'">
            The wording was written by the model and every figure in it was checked back against the
            data before you saw it.
          </template>
          <template v-else>
            Written from the plain template — either the model was unavailable or its wording
            contained a figure that was not in the data, so it was thrown away.
          </template>
        </p>
      </SectionCard>

      <SectionCard
        title="What we actually sent"
        subtitle="Last 30 days"
        :loading="loading"
        :empty="!loading && !sent.length"
        empty-text="Nothing has gone out yet."
      >
        <ul class="divide-y divide-border">
          <li v-for="message in sent" :key="message.id" class="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2">
            <span class="tabular shrink-0 text-sm text-ink-muted">
              {{ shopDateTime(message.created_at, shop.timezone) }}
            </span>
            <span class="shrink-0 text-base text-ink">{{ message.to_address }}</span>
            <span class="min-w-0 flex-1 truncate text-sm text-ink-muted">
              {{ message.detail || message.subject || '' }}
            </span>
            <UiBadge :tone="statusOf(message.status).tone">{{ statusOf(message.status).word }}</UiBadge>
          </li>
        </ul>
        <p class="mt-2 text-sm text-ink-muted">
          Held-back messages are listed too, with the reason. A quiet spell here is a setting doing
          its job, not a fault.
        </p>
      </SectionCard>
    </div>
  </div>
</template>
