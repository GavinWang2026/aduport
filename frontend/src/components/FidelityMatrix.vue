<script setup lang="ts">
import { ref } from 'vue'
import { probeFidelity, type FidelityProbeRow } from '../api'
import type { MessageKey } from '../i18n'

const props = defineProps<{
  tt: (key: MessageKey) => string
  text: string
}>()

const rows = ref<FidelityProbeRow[]>([])
const busy = ref(false)
const error = ref('')

async function run() {
  if (!props.text.trim() || busy.value) return
  busy.value = true
  error.value = ''
  try {
    rows.value = await probeFidelity(props.text)
  } catch (err) {
    rows.value = []
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

function label(row: FidelityProbeRow) {
  if (!row.ok) return props.tt('fidelityMatrixFail')
  return row.equal ? props.tt('fidelityMatrixEqual') : props.tt('fidelityMatrixDrift')
}
</script>

<template>
  <section class="matrix" data-testid="fidelity-matrix">
    <div class="matrix-head">
      <b>{{ tt('fidelityMatrix') }}</b>
      <button type="button" data-testid="fidelity-matrix-run" :disabled="busy || !text.trim()" @click="run">
        {{ busy ? '…' : tt('fidelityMatrixRun') }}
      </button>
    </div>
    <p class="hint">{{ text.trim() ? tt('fidelityMatrixHint') : tt('fidelityMatrixEmpty') }}</p>
    <p v-if="error" class="err">{{ error }}</p>
    <table v-if="rows.length">
      <tbody>
        <tr v-for="row in rows" :key="row.target" :data-testid="`fidelity-matrix-${row.target}`">
          <td>{{ row.target }}</td>
          <td>{{ label(row) }}</td>
          <td>{{ row.error || (row.changes ? String(row.changes) : '') }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.matrix { margin-top: 12px; padding: 10px 12px; border: 1px solid #e3e0d8; border-radius: 10px; background: #fff; }
.matrix-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.matrix button { border: 1px solid #0f6e56; background: #e1f5ee; color: #04342c; border-radius: 8px; padding: 4px 10px; cursor: pointer; }
.matrix button:disabled { opacity: 0.5; cursor: default; }
.hint, .err { margin: 6px 0 0; font-size: 12px; color: #5f5e5a; }
.err { color: #a32d2d; }
table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }
td { border-top: 1px solid #eee; padding: 4px 6px; }
</style>
