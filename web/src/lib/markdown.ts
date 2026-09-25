/**
 * The assistant writes markdown; this turns it into HTML we are willing to
 * insert.
 *
 * Two passes, and both are load-bearing. `marked` does the markdown, and
 * DOMPurify decides what is allowed to survive it. A model's output is not
 * trusted input — it can contain anything it read, and a product whose answers
 * quote a shop's own uploaded documents is a product where "the model would
 * never write a script tag" is not a security argument.
 *
 * Rendering happens mid-stream, on a half-written document, so the parser has
 * to cope with an unclosed bold or a table with one row so far. `marked`'s
 * behavior there is to render what it has, which is exactly what is wanted.
 */

import DOMPurify from 'dompurify'
import { Marked } from 'marked'

const marked = new Marked({
  gfm: true,
  breaks: true,
})

// Prose, lists, tables and inline code. No images (nothing should be loading a
// remote URL an answer mentioned), no raw HTML blocks, no ids or classes.
const ALLOWED_TAGS = [
  'p',
  'br',
  'strong',
  'em',
  'del',
  'code',
  'pre',
  'blockquote',
  'ul',
  'ol',
  'li',
  'h1',
  'h2',
  'h3',
  'h4',
  'hr',
  'table',
  'thead',
  'tbody',
  'tr',
  'th',
  'td',
  'a',
]

export function renderMarkdown(source: string): string {
  const html = marked.parse(source, { async: false })
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR: ['href', 'title'],
    // A link may only go somewhere a browser can safely follow.
    ALLOWED_URI_REGEXP: /^https?:\/\//i,
  })
}
