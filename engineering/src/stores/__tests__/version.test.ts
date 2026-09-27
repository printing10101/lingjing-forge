import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useVersionStore } from '@/stores/version'

describe('useVersionStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubEnv('VITE_APP_VERSION', '1.0.0')
    vi.stubEnv('VITE_APP_COMMIT', 'abc123')
  })

  describe('initial state', () => {
    it('reads frontend version from env', () => {
      const store = useVersionStore()
      expect(store.frontendVersion).toBe('1.0.0')
    })

    it('reads frontend commit from env', () => {
      const store = useVersionStore()
      expect(store.frontendCommit).toBe('abc123')
    })

    it('has empty rust version initially', () => {
      const store = useVersionStore()
      expect(store.rustVersion).toBe('')
    })

    it('has null python version initially', () => {
      const store = useVersionStore()
      expect(store.pythonVersion).toBeNull()
    })

    it('is not loading initially', () => {
      const store = useVersionStore()
      expect(store.isLoading).toBe(false)
    })

    it('is consistent by default', () => {
      const store = useVersionStore()
      expect(store.isConsistent).toBe(true)
    })
  })

  describe('allVersions computed', () => {
    it('aggregates all version sources', () => {
      const store = useVersionStore()
      const versions = store.allVersions
      expect(versions.frontend).toBe('1.0.0')
      expect(versions.rust).toBe('')
      expect(versions.python).toBeNull()
    })
  })

  describe('inconsistencyDetails computed', () => {
    it('returns null when consistent', () => {
      const store = useVersionStore()
      expect(store.inconsistencyDetails).toBeNull()
    })

    it('returns details when inconsistent', () => {
      const store = useVersionStore()
      store.$patch({
        rustVersion: '2.0.0',
        isConsistent: false,
      })
      const details = store.inconsistencyDetails
      expect(details).not.toBeNull()
      expect(details!.length).toBeGreaterThan(0)
    })
  })

  describe('checkConsistency', () => {
    it('marks inconsistent when no rust version', () => {
      const store = useVersionStore()
      store.checkConsistency()
      expect(store.isConsistent).toBe(false)
    })

    it('marks consistent when versions match', () => {
      const store = useVersionStore()
      store.$patch({ rustVersion: '1.0.0' })
      store.checkConsistency()
      expect(store.isConsistent).toBe(true)
    })

    it('marks inconsistent when rust differs', () => {
      const store = useVersionStore()
      store.$patch({ rustVersion: '2.0.0' })
      store.checkConsistency()
      expect(store.isConsistent).toBe(false)
    })

    it('marks consistent when all three match', () => {
      const store = useVersionStore()
      store.$patch({
        rustVersion: '1.0.0',
        pythonVersion: '1.0.0',
      })
      store.checkConsistency()
      expect(store.isConsistent).toBe(true)
    })
  })
})
