<script setup lang="ts">
import type { RagElementView } from '../ragBrowse'
import type { MessageKey } from '../i18n'

defineProps<{
  tt: (key: MessageKey) => string
  elements: RagElementView[]
}>()
</script>

<template>
  <section class="md-pv-doc" data-testid="rag-browser">
    <p class="md-p-sub" style="margin:0 0 10px">{{ tt('ragBrowse') }} · {{ elements.length }}</p>
    <ul class="md-pv-diff__list">
      <li v-for="el in elements" :key="el.id">
        <span class="md-pv-diff__miss">{{ el.type || 'element' }}</span>
        {{ el.text || tt('ragBrowseEmpty') }}
      </li>
    </ul>
  </section>
</template>
