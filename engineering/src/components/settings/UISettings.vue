<template>
  <div class="ui-settings">
    <div class="content-card">
      <div class="content-card__header">
        <span class="content-card__title">
          <el-icon style="margin-right: 6px;"><Key /></el-icon>
          {{ $t('settings.agentTokenManage') }}
        </span>
      </div>
      <div class="content-card__body">
        <el-alert
          :title="$t('settings.agentTokenInfo')"
          type="info"
          :closable="false"
          show-icon
        >
          <div>
            <p>{{ $t('settings.agentTokenDisabled') }}</p>
            <p>{{ $t('settings.contactAdmin') }}</p>
          </div>
        </el-alert>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Key } from '@element-plus/icons-vue'
</script>

<style scoped>
.ui-settings {
  padding: 0;
}
</style>
