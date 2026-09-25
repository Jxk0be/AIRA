<script setup lang="ts">
/**
 * An email the assistant wrote, for the owner to send.
 *
 * The draft is editable, because the first thing anyone does with a written
 * email is change a word of it, and a read-only panel with a Copy button makes
 * that someone else's problem. What is edited here is what gets copied and
 * what gets opened — there is one copy of the text on the screen.
 *
 * We never send it. Not because sending is hard, but because a button in a
 * chat window that puts a message in a vendor's inbox is a different product
 * with a different set of promises: `app/notify` sends what we send, under
 * consent and quiet hours, and this is not that. What this does is hand the
 * owner the words.
 *
 * `mailto:` has a length the operating system will take and no more — Windows
 * stops at about two kilobytes — and a mail app that silently drops the last
 * paragraph is the worst possible failure here. So the length is checked, and
 * past it the mail-app button says why it is off instead of truncating.
 */
import { computed, ref, watch } from 'vue'

import type { AssistantAction } from '../api/types'
import UiButton from '../ui/UiButton.vue'
import UiDialog from '../ui/UiDialog.vue'
import { toast } from '../ui/toast'

const props = defineProps<{ action: AssistantAction | null }>()
const emit = defineEmits<{ close: [] }>()

const to = ref('')
const subject = ref('')
const body = ref('')

// Conservative: Windows hands a mailto: link to the mail app through a command
// line that stops around 2048 characters, and the tail is what gets lost.
const MAILTO_LIMIT = 1900

watch(
  () => props.action,
  (action) => {
    if (!action?.email) return
    to.value = action.email.to ?? ''
    subject.value = action.email.subject
    body.value = action.email.body
  },
  { immediate: true },
)

const link = computed(
  () =>
    `mailto:${encodeURIComponent(to.value)}` +
    `?subject=${encodeURIComponent(subject.value)}` +
    `&body=${encodeURIComponent(body.value)}`,
)

const tooLong = computed(() => link.value.length > MAILTO_LIMIT)

/** What lands on the clipboard: the headers, then the message. */
const plain = computed(() => {
  const header = to.value ? `To: ${to.value}\n` : ''
  return `${header}Subject: ${subject.value}\n\n${body.value}`
})

async function copy() {
  try {
    await navigator.clipboard.writeText(plain.value)
    toast.success('Copied — paste it into your email')
  } catch {
    // No clipboard permission, or an insecure origin. The text is on the
    // screen and selectable, so say that rather than pretending it worked.
    toast.warning('Could not copy it for you', { detail: 'Select the text and copy it by hand.' })
  }
}

function open() {
  window.location.href = link.value
  emit('close')
}
</script>

<template>
  <UiDialog
    :open="action !== null"
    title="An email you can send"
    description="Read it over — nothing is sent until you send it from your own email app."
    @update:open="(value: boolean) => !value && emit('close')"
  >
    <div class="space-y-3">
      <label class="block">
        <span class="mb-1 block text-sm font-medium text-ink">To</span>
        <input
          v-model="to"
          type="email"
          placeholder="who it goes to"
          class="min-h-11 w-full rounded-md border border-border-strong bg-surface px-3 text-base text-ink"
        />
      </label>

      <label class="block">
        <span class="mb-1 block text-sm font-medium text-ink">Subject</span>
        <input
          v-model="subject"
          class="min-h-11 w-full rounded-md border border-border-strong bg-surface px-3 text-base text-ink"
        />
      </label>

      <label class="block">
        <span class="mb-1 block text-sm font-medium text-ink">Message</span>
        <textarea
          v-model="body"
          rows="10"
          class="max-h-[40vh] w-full resize-y rounded-md border border-border-strong bg-surface px-3 py-2 text-base leading-relaxed text-ink"
        />
      </label>

      <p v-if="tooLong" class="text-sm text-ink-muted">
        This is too long to hand to your email app as a link — some of it would be lost. Copy it
        instead and paste it into a new message.
      </p>
    </div>

    <template #footer="{ close }">
      <UiButton variant="ghost" @click="close()">Cancel</UiButton>
      <UiButton variant="secondary" @click="copy">Copy</UiButton>
      <UiButton :disabled="tooLong || !subject.trim()" @click="open">Open in email app</UiButton>
    </template>
  </UiDialog>
</template>
