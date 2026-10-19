export type RagElementView = {
  id: string
  type: string
  text: string
}

export function parseRagElements(raw: string): RagElementView[] {
  const text = (raw || '').trim()
  if (!text.startsWith('{') || !text.includes('"elements"')) return []
  try {
    const data = JSON.parse(text) as { elements?: unknown }
    if (!Array.isArray(data.elements)) return []
    return data.elements.slice(0, 200).map((item, index) => {
      const el = item && typeof item === 'object' ? item as Record<string, unknown> : {}
      return {
        id: String(el.element_id || el.id || index + 1),
        type: String(el.type || el.category || ''),
        text: String(el.text || '').slice(0, 400),
      }
    })
  } catch {
    return []
  }
}
