<script setup lang="ts">
import { computed } from 'vue'
import type { ServerJob } from '../api'
import type { MessageKey } from '../i18n'

const props = defineProps<{
  tt: (key: MessageKey) => string
  jobs: ServerJob[]
  filter: 'all' | 'active' | 'done' | 'error'
  autoRefresh: boolean
  formatTime: (ts: number) => string
}>()

const emit = defineEmits<{
  'update:filter': [value: 'all' | 'active' | 'done' | 'error']
  'update:autoRefresh': [value: boolean]
  refresh: []
  download: [row: ServerJob]
  reprocess: [row: ServerJob]
  resume: [row: ServerJob]
}>()

const filtered = computed(() => {
  const list = props.jobs
  if (props.filter === 'all') return list
  if (props.filter === 'active') return list.filter((j) => j.status === 'queued' || j.status === 'running')
  if (props.filter === 'done') return list.filter((j) => j.status === 'done')
  return list.filter((j) => j.status === 'error')
})

const counts = computed(() => ({
  queued: props.jobs.filter((j) => j.status === 'queued').length,
  running: props.jobs.filter((j) => j.status === 'running').length,
  done: props.jobs.filter((j) => j.status === 'done').length,
  error: props.jobs.filter((j) => j.status === 'error').length,
}))

function stageLabel(row: ServerJob): string {
  if (row.stage === 'ocr') return props.tt('stageOcr')
  if (row.stage === 'convert') return props.tt('stageConvert')
  if (row.stage === 'done' || row.status === 'done') return props.tt('stageDone')
  if (row.stage === 'error' || row.status === 'error') return props.tt('stageError')
  if (row.status === 'queued') return props.tt('stageQueued')
  return row.stage || row.status
}

function pct(row: ServerJob): number {
  return Math.round((row.progress || 0) * 100)
}
</script>

<template>
  <section class="md-panel" data-testid="task-board">
    <div class="md-p-head">
      <div class="md-p-title"><span class="k" />{{ tt('serverJobs') }}</div>
    </div>
    <div class="md-stat-row" style="margin-bottom:14px" data-testid="task-board-counts">
      <div class="md-stat"><b>{{ counts.queued }}</b><span>{{ tt('stageQueued') }}</span></div>
      <div class="md-stat"><b class="blue">{{ counts.running }}</b><span>{{ tt('stageConvert') }}</span></div>
      <div class="md-stat"><b class="green">{{ counts.done }}</b><span>{{ tt('stageDone') }}</span></div>
      <div class="md-stat"><b class="gray">{{ counts.error }}</b><span>{{ tt('stageError') }}</span></div>
    </div>
    <div class="md-tasks-toolbar">
      <label class="md-check">
        <input
          :checked="autoRefresh"
          type="checkbox"
          @change="emit('update:autoRefresh', ($event.target as HTMLInputElement).checked)"
        />
        {{ tt('autoRefresh') }}
      </label>
      <div class="md-seg">
        <button type="button" :class="{ active: filter === 'all' }" @click="emit('update:filter', 'all')">{{ tt('filterAll') }}</button>
        <button type="button" :class="{ active: filter === 'active' }" @click="emit('update:filter', 'active')">{{ tt('filterActive') }}</button>
        <button type="button" :class="{ active: filter === 'done' }" @click="emit('update:filter', 'done')">{{ tt('filterDone') }}</button>
        <button type="button" :class="{ active: filter === 'error' }" @click="emit('update:filter', 'error')">{{ tt('filterError') }}</button>
      </div>
      <button type="button" class="md-btn md-btn--g" @click="emit('refresh')">{{ tt('refreshJobs') }}</button>
    </div>
    <div v-if="!filtered.length" class="md-empty-pad">{{ tt('serverJobsEmpty') }}</div>
    <div v-else class="md-table-wrap">
      <table class="md-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>File</th>
            <th>Route</th>
            <th>Status</th>
            <th>{{ tt('progress') }}</th>
            <th>{{ tt('createdAt') }}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in filtered" :key="row.id">
            <td class="mono">{{ row.id.slice(0, 8) }}</td>
            <td class="mono">{{ row.filename }}</td>
            <td>{{ row.source_format }} → {{ row.target_format }}</td>
            <td>
              <span
                class="md-st"
                :class="{
                  wait: row.status === 'queued',
                  run: row.status === 'running',
                  ready: row.status === 'done',
                  bad: row.status === 'error',
                }"
              >{{ row.status }}</span>
              <div class="mono" style="font-size:11px;opacity:.75" data-testid="job-stage">{{ stageLabel(row) }}</div>
            </td>
            <td>
              <div class="md-bar" style="flex:none;width:90px" :data-ocr="row.stage === 'ocr' ? '1' : '0'">
                <i :style="{ width: `${pct(row)}%` }" />
              </div>
              <span class="mono" style="font-size:11px">{{ pct(row) }}%</span>
            </td>
            <td>{{ formatTime(row.created_at) }}</td>
            <td>
              <button v-if="row.status === 'done'" type="button" class="md-link" @click="emit('download', row)">
                {{ tt('download') }}
              </button>
              <button
                v-if="row.reprocessable && (row.status === 'done' || row.status === 'error')"
                type="button"
                class="md-link"
                data-testid="btn-reprocess-job"
                @click="emit('reprocess', row)"
              >
                {{ tt('reprocessJob') }}
              </button>
              <button
                v-else-if="row.status === 'error' && row.resumable"
                type="button"
                class="md-link"
                data-testid="btn-resume-job"
                @click="emit('resume', row)"
              >
                {{ tt('resumeJob') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
