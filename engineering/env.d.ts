/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, never>, Record<string, never>, any>
  export default component
}

import 'vue'

declare module '@vue/runtime-core' {
  export interface ComponentCustomProperties {
    $t(key: string, ...args: any[]): string
  }
}
