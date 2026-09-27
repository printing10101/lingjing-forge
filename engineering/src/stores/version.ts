import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
// 安全修复：不再静态导入 @tauri-apps/api/core 的 invoke，
// 改为在调用处动态导入，避免在 Web/测试环境因模块缺失抛错。

export interface VersionStatus {
  rust_version: string
  rust_commit: string
  python_version: string | null
  python_commit: string | null
  is_consistent: boolean
}

export const useVersionStore = defineStore('version', () => {
  const frontendVersion = ref(import.meta.env.VITE_APP_VERSION as string || '0.0.0')
  const frontendCommit = ref(import.meta.env.VITE_APP_COMMIT as string || 'dev')
  const rustVersion = ref('')
  const rustCommit = ref('')
  const pythonVersion = ref<string | null>(null)
  const pythonCommit = ref<string | null>(null)
  const isConsistent = ref(true)
  const isLoading = ref(false)

  const allVersions = computed(() => ({
    frontend: frontendVersion.value,
    rust: rustVersion.value,
    python: pythonVersion.value,
  }))

  const inconsistencyDetails = computed(() => {
    if (isConsistent.value) return null

    const details: string[] = []
    const frontend = frontendVersion.value
    const rust = rustVersion.value
    const python = pythonVersion.value

    if (rust && frontend !== rust) {
      details.push(`前端(${frontend}) 与 Rust 后端(${rust}) 版本不一致`)
    }
    if (python && rust && python !== rust) {
      details.push(`Python Sidecar(${python}) 与 Rust 后端(${rust}) 版本不一致`)
    }
    if (python && frontend && python !== frontend) {
      details.push(`Python Sidecar(${python}) 与前端(${frontend}) 版本不一致`)
    }

    return details.length > 0 ? details : ['版本状态未知']
  })

  async function fetchVersionInfo() {
    isLoading.value = true
    try {
      if (typeof window !== 'undefined' && '__TAURI__' in window) {
        // 安全修复：动态导入 invoke，避免在非 Tauri 环境静态导入抛错
        const { invoke } = await import('@tauri-apps/api/core')
        const result = await invoke<VersionStatus>('get_version_info')
        rustVersion.value = result.rust_version
        rustCommit.value = result.rust_commit
        pythonVersion.value = result.python_version
        pythonCommit.value = result.python_commit
        isConsistent.value = result.is_consistent
      }
      // 非 Tauri 环境（浏览器开发模式）：保持默认值，不请求后端
    } catch {
      isConsistent.value = false
    } finally {
      isLoading.value = false
    }
  }

  function checkConsistency() {
    const frontend = frontendVersion.value
    const rust = rustVersion.value
    const python = pythonVersion.value

    if (!rust) {
      isConsistent.value = false
      return
    }

    const consistent =
      (!python || python === rust) &&
      frontend === rust

    isConsistent.value = consistent
  }

  return {
    frontendVersion,
    frontendCommit,
    rustVersion,
    rustCommit,
    pythonVersion,
    pythonCommit,
    isConsistent,
    isLoading,
    allVersions,
    inconsistencyDetails,
    fetchVersionInfo,
    checkConsistency,
  }
})
