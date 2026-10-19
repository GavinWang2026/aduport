<script setup lang="ts">
import type { LicensePayload, TenantPublic, UsagePayload } from '../api'
import type { MessageKey } from '../i18n'

defineProps<{
  tt: (key: MessageKey) => string
  opsLoading: boolean
  apiKeyDraft: string
  usageInfo: UsagePayload | null
  licenseInfo: LicensePayload | null
  metricsPreview: string
  tenants: TenantPublic[]
}>()

const emit = defineEmits<{
  'update:apiKeyDraft': [value: string]
  save: []
  refresh: []
}>()
</script>

<template>
  <section class="md-panel" data-testid="ops-panel">
    <div class="md-p-head">
      <div class="md-p-title"><span class="k" />{{ tt('opsTitle') }}</div>
      <button type="button" class="md-btn md-btn--g" :disabled="opsLoading" @click="emit('refresh')">
        {{ opsLoading ? tt('opsLoading') : tt('refreshJobs') }}
      </button>
    </div>
    <p class="md-p-sub" style="margin:0 0 14px">{{ tt('opsHint') }}</p>
    <div class="md-adv" style="margin-bottom:14px">
      <div class="md-field" style="flex:1;min-width:240px">
        <span>{{ tt('apiKeyLabel') }}</span>
        <input
          :value="apiKeyDraft"
          type="password"
          class="md-input"
          data-testid="api-key-input"
          autocomplete="off"
          :placeholder="tt('apiKeyPh')"
          @input="emit('update:apiKeyDraft', ($event.target as HTMLInputElement).value)"
          @keyup.enter="emit('save')"
        >
      </div>
      <button type="button" class="md-btn md-btn--p" data-testid="api-key-save" style="align-self:flex-end" @click="emit('save')">
        {{ tt('apiKeySave') }}
      </button>
    </div>
    <p class="md-p-sub" style="margin:0 0 14px;font-size:12px;opacity:.8">{{ tt('apiKeyHint') }}</p>
    <div class="md-stat-row" style="margin-bottom:16px">
      <div class="md-stat">
        <b class="blue">{{ usageInfo?.conversions_today ?? '—' }}</b>
        <span>{{ tt('opsToday') }}</span>
      </div>
      <div class="md-stat">
        <b class="green">{{ usageInfo?.conversions_total ?? '—' }}</b>
        <span>{{ tt('opsTotal') }}</span>
      </div>
      <div class="md-stat">
        <b class="gray">{{ usageInfo?.conversions_failed ?? '—' }}</b>
        <span>{{ tt('opsFailed') }}</span>
      </div>
    </div>
    <div class="md-adv" style="margin-bottom:14px">
      <div class="md-field">
        <span>{{ tt('opsTenant') }}</span>
        <code class="mono">{{ usageInfo?.tenant_id || '—' }}</code>
      </div>
      <div class="md-field">
        <span>{{ tt('opsLicense') }}</span>
        <code class="mono">
          {{
            licenseInfo
              ? (licenseInfo.required ? tt('opsLicenseRequired') : tt('opsLicenseOpen'))
                + (licenseInfo.license ? ` · ${JSON.stringify(licenseInfo.license).slice(0, 120)}` : '')
              : '—'
          }}
        </code>
      </div>
    </div>
    <div class="md-p-title" style="margin:4px 0 8px">{{ tt('opsTenants') }}</div>
    <p v-if="!tenants.length" class="md-p-sub" data-testid="tenant-admin">{{ tt('opsTenantsEmpty') }}</p>
    <ul v-else class="md-pv-diff__list" data-testid="tenant-admin">
      <li v-for="tenant in tenants" :key="tenant.tenant_id">
        <span class="md-pv-diff__miss">{{ tenant.tenant_id }}</span>
        {{ tt('opsTenantRate') }} {{ tenant.rate_limit ?? '—' }}
        · {{ tt('opsTenantQuota') }} {{ tenant.daily_quota ?? '—' }}
      </li>
    </ul>
    <div class="md-p-title" style="margin-bottom:8px">{{ tt('opsMetrics') }}</div>
    <pre class="md-metrics-pre">{{ metricsPreview || tt('opsMetricsEmpty') }}</pre>
  </section>
</template>
