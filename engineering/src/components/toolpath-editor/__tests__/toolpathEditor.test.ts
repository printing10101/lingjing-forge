import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useToolpathEditorStore } from '../stores/toolpathEditor'
import type { ToolpathSegmentData } from '@/types'

describe('toolpathEditorStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  const sampleData: ToolpathSegmentData[] = [
    {
      type: 'rapid', start_point: [0, 0, 0], end_point: [0, 0, 50],
      block_number: 1, g_code: 'G00 Z50.000',
    },
    {
      type: 'linear', start_point: [0, 0, 50], end_point: [10, 10, 0],
      block_number: 2, g_code: 'G01 X10.000 Y10.000 Z0.000 F500',
    },
    {
      type: 'linear', start_point: [10, 10, 0], end_point: [50, 50, 0],
      block_number: 3, g_code: 'G01 X50.000 Y50.000 F500',
    },
  ]

  describe('loadSegments', () => {
    it('should convert ToolpathSegmentData to EditableToolpathSegment', () => {
      const store = useToolpathEditorStore()
      store.loadSegments(sampleData)

      expect(store.segments).toHaveLength(3)
      expect(store.activeSegments).toHaveLength(3)
      expect(store.segments[0].type).toBe('rapid')
      expect(store.segments[0].feedRate).toBe(500)
      expect(store.segments[0].spindleSpeed).toBe(8000)
      expect(store.isDirty).toBe(false)
    })

    it('should reset history on new load', () => {
      const store = useToolpathEditorStore()
      store.loadSegments(sampleData)
      store.deleteSegment(store.segments[0].id)

      store.loadSegments(sampleData)
      expect(store.canUndo).toBe(false)
      expect(store.isDirty).toBe(false)
    })
  })

  describe('loadGCode', () => {
    it('should parse valid G-code text', () => {
      const store = useToolpathEditorStore()
      const result = store.loadGCode('G01 X10.0 Y20.0 F500.0\nG01 X30.0 Y40.0 F500.0')

      expect(result.success).toBe(true)
      expect(store.segments).toHaveLength(2)
    })

    it('should reject empty G-code', () => {
      const store = useToolpathEditorStore()
      const result = store.loadGCode('')

      expect(result.success).toBe(false)
      expect(result.message).toContain('No valid G-code')
    })
  })

  describe('deleteSegment', () => {
    it('should soft-delete a segment via command', () => {
      const store = useToolpathEditorStore()
      store.loadSegments(sampleData)

      const targetId = store.segments[1].id
      store.deleteSegment(targetId)

      expect(store.segments[1].isDeleted).toBe(true)
      expect(store.activeSegments).toHaveLength(2)
      expect(store.isDirty).toBe(true)
      expect(store.canUndo).toBe(true)
    })
  })

  describe('modifyFeedRate', () => {
    it('should modify feed rate via command', () => {
      const store = useToolpathEditorStore()
      store.loadSegments(sampleData)

      const targetId = store.segments[0].id
      store.modifyFeedRate(targetId, 1200)

      expect(store.segments[0].feedRate).toBe(1200)
      expect(store.isDirty).toBe(true)
      expect(store.canUndo).toBe(true)
    })
  })

  describe('undo / redo', () => {
    it('should undo delete to restore segment', () => {
      const store = useToolpathEditorStore()
      store.loadSegments(sampleData)

      const targetId = store.segments[0].id
      store.deleteSegment(targetId)
      expect(store.activeSegments).toHaveLength(2)

      store.undo()
      expect(store.segments[0].isDeleted).toBe(false)
      expect(store.activeSegments).toHaveLength(3)
      expect(store.canRedo).toBe(true)
    })

    it('should redo to re-apply delete', () => {
      const store = useToolpathEditorStore()
      store.loadSegments(sampleData)

      const targetId = store.segments[0].id
      store.deleteSegment(targetId)
      store.undo()
      store.redo()

      expect(store.segments[0].isDeleted).toBe(true)
      expect(store.activeSegments).toHaveLength(2)
    })

    it('should undo feed rate modification', () => {
      const store = useToolpathEditorStore()
      store.loadSegments(sampleData)

      const targetId = store.segments[0].id
      const originalFeed = store.segments[0].feedRate
      store.modifyFeedRate(targetId, 9999)

      store.undo()
      expect(store.segments[0].feedRate).toBe(originalFeed)
    })
  })

  describe('exportGCode', () => {
    it('should export valid G-code with validation', () => {
      const store = useToolpathEditorStore()
      store.loadSegments(sampleData)

      const result = store.exportGCode('fanuc', 1)
      expect(result.validation.valid).toBe(true)
      expect(result.gcode).toContain('O0001')
      expect(result.gcode).toContain('M30')
    })
  })

  describe('isDirty', () => {
    it('should detect changes', () => {
      const store = useToolpathEditorStore()
      store.loadSegments(sampleData)

      expect(store.isDirty).toBe(false)

      store.deleteSegment(store.segments[0].id)
      expect(store.isDirty).toBe(true)
    })

    it('should reset to clean after undo all', () => {
      const store = useToolpathEditorStore()
      store.loadSegments(sampleData)

      store.deleteSegment(store.segments[0].id)
      store.undo()

      expect(store.isDirty).toBe(false)
    })
  })

  describe('reset', () => {
    it('should clear all state', () => {
      const store = useToolpathEditorStore()
      store.loadSegments(sampleData)
      store.deleteSegment(store.segments[0].id)

      store.reset()
      expect(store.segments).toHaveLength(0)
      expect(store.canUndo).toBe(false)
      expect(store.isDirty).toBe(false)
    })
  })
})
