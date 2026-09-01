<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: string
    options: { value: string; label: string; hint?: string }[]
    label?: string
  }>(),
  { label: '' },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const open = ref(false)
const root = ref<HTMLElement | null>(null)
const btn = ref<HTMLElement | null>(null)
const menu = ref<HTMLElement | null>(null)
const menuStyle = ref<Record<string, string>>({})

function extClass(v: string) {
  const key = v.toLowerCase()
  if (['pdf', 'docx', 'doc', 'md', 'html', 'xlsx', 'csv', 'auto'].includes(key)) {
    return `is-${key}`
  }
  return ''
}

function extText(v: string) {
  if (v === 'auto') return 'AU'
  return v.slice(0, 4).toUpperCase()
}

function placeMenu() {
  const el = btn.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const maxH = Math.min(280, window.innerHeight - r.bottom - 12)
  menuStyle.value = {
    position: 'fixed',
    top: `${Math.round(r.bottom + 7)}px`,
    left: `${Math.round(r.left)}px`,
    width: `${Math.round(r.width)}px`,
    maxHeight: `${Math.max(120, maxH)}px`,
    zIndex: '200',
  }
}

async function toggle(e: MouseEvent) {
  e.stopPropagation()
  open.value = !open.value
  if (open.value) {
    await nextTick()
    placeMenu()
  }
}

function select(v: string) {
  emit('update:modelValue', v)
  open.value = false
}

function onDoc(e: MouseEvent) {
  const t = e.target as Node
  if (root.value?.contains(t) || menu.value?.contains(t)) return
  open.value = false
}

function onReposition() {
  if (open.value) placeMenu()
}

onMounted(() => {
  document.addEventListener('click', onDoc)
  window.addEventListener('resize', onReposition)
  window.addEventListener('scroll', onReposition, true)
})
onUnmounted(() => {
  document.removeEventListener('click', onDoc)
  window.removeEventListener('resize', onReposition)
  window.removeEventListener('scroll', onReposition, true)
})

const currentLabel = () => props.options.find((o) => o.value === props.modelValue)?.label || props.modelValue
</script>

<template>
  <div ref="root" class="md-select">
    <label v-if="label" class="md-label">{{ label }}</label>
    <button ref="btn" type="button" class="md-select__btn" @click="toggle">
      <span class="md-ext" :class="extClass(modelValue)">{{ extText(modelValue) }}</span>
      <span class="md-select__value">{{ currentLabel() }}</span>
      <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M6 9l6 6 6-6" stroke="currentColor" fill="none" stroke-width="1.5" stroke-linecap="round" />
      </svg>
    </button>
    <Teleport to="body">
      <div
        v-if="open"
        ref="menu"
        class="md-select__menu md-fade-in"
        :style="menuStyle"
        @click.stop
      >
        <button
          v-for="opt in options"
          :key="opt.value"
          type="button"
          class="md-select__option"
          :class="{ 'is-active': opt.value === modelValue, 'is-hint': !!opt.hint }"
          @click="select(opt.value)"
        >
          <span class="md-ext" :class="extClass(opt.value)">{{ extText(opt.value) }}</span>
          <span class="md-select__opt-text">
            <span>{{ opt.label }}</span>
            <small v-if="opt.hint" class="md-select__hint">{{ opt.hint }}</small>
          </span>
        </button>
      </div>
    </Teleport>
  </div>
</template>
