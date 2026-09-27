import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useToolpathEditorStore } from '../stores/toolpathEditor'
import type { ToolpathSegmentData } from '@/types'

function buildSampleData(count: number): ToolpathSegmentData[] {
  const data: ToolpathSegmentData[] = []
  for (let i = 0; i < count; i++) {
    data.push({
      type: (['rapid', 'linear', 'linear', 'arc'] as const)[i % 4],
      start_point: [i * 10, i * 5, 0] as [number, number, number],
      end_point: [(i + 1) * 10, (i + 1) * 5, i % 3 === 0 ? -2 : 0] as [number, number, number],
      block_number: i + 1,
      g_code: `G01 X${(i + 1) * 10} Y${(i + 1) * 5} F500`,
    })
  }
  return data
}

describe('Extreme Operation Tests', () => {
  let store: ReturnType<typeof useToolpathEditorStore>

  beforeEach(() => {
    setActivePinia(createPinia())
    store = useToolpathEditorStore()
  })

  describe('Multi-step Continuous Operations', () => {
    it('should handle 10 consecutive edit/delete/modify operations', () => {
      store.loadSegments(buildSampleData(10))

      expect(store.activeSegments).toHaveLength(10)

      store.modifyFeedRate(store.segments[0].id, 1200)
      expect(store.segments[0].feedRate).toBe(1200)

      store.modifyFeedRate(store.segments[1].id, 800)
      expect(store.segments[1].feedRate).toBe(800)

      store.deleteSegment(store.segments[2].id)
      expect(store.segments[2].isDeleted).toBe(true)
      expect(store.activeSegments).toHaveLength(9)

      store.modifyFeedRate(store.segments[3].id, 350)
      expect(store.segments[3].feedRate).toBe(350)

      store.deleteSegment(store.segments[4].id)
      expect(store.segments[4].isDeleted).toBe(true)

      store.modifyFeedRate(store.segments[5].id, 2000)
      expect(store.segments[5].feedRate).toBe(2000)

      store.deleteSegment(store.segments[6].id)
      store.modifyFeedRate(store.segments[7].id, 1500)

      store.deleteSegment(store.segments[8].id)
      store.modifyFeedRate(store.segments[9].id, 650)

      expect(store.activeSegments).toHaveLength(6)
      expect(store.undoCount).toBe(10)
      expect(store.canUndo).toBe(true)
      expect(store.isDirty).toBe(true)
    })

    it('should maintain segment identity after batch modifications', () => {
      store.loadSegments(buildSampleData(5))

      const originalIds = store.segments.map((s) => s.id)

      store.modifyFeedRate(originalIds[0], 111)
      store.modifyFeedRate(originalIds[1], 222)
      store.modifyFeedRate(originalIds[2], 333)
      store.modifyFeedRate(originalIds[3], 444)
      store.modifyFeedRate(originalIds[4], 555)

      const currentIds = store.segments.map((s) => s.id)
      expect(currentIds).toEqual(originalIds)
      expect(store.segments[0].feedRate).toBe(111)
      expect(store.segments[4].feedRate).toBe(555)
    })
  })

  describe('Operation History Stack Management', () => {
    it('should not produce duplicate entries on undo-redo-undo cycles', () => {
      store.loadSegments(buildSampleData(5))

      store.deleteSegment(store.segments[0].id)
      expect(store.undoCount).toBe(1)

      store.undo()
      expect(store.undoCount).toBe(0)
      expect(store.redoCount).toBe(1)

      store.redo()
      expect(store.undoCount).toBe(1)
      expect(store.redoCount).toBe(0)

      store.undo()
      expect(store.undoCount).toBe(0)
      expect(store.redoCount).toBe(1)

      store.undo()
      expect(store.undoCount).toBe(0)

      expect(store.activeSegments).toHaveLength(5)
    })

    it('should clear redo stack on new operation after undo', () => {
      store.loadSegments(buildSampleData(5))

      store.deleteSegment(store.segments[0].id)
      store.deleteSegment(store.segments[1].id)
      expect(store.undoCount).toBe(2)

      store.undo()
      expect(store.undoCount).toBe(1)
      expect(store.redoCount).toBe(1)

      store.modifyFeedRate(store.segments[0].id, 9999)
      expect(store.undoCount).toBe(2)
      expect(store.redoCount).toBe(0)
    })

    it('should cap history at configured maxDepth and discard oldest', () => {
      store.setMaxDepth(50)
      store.loadSegments(buildSampleData(60))

      for (let i = 0; i < 55; i++) {
        store.modifyFeedRate(store.segments[i % 60].id, 100 + i)
      }

      expect(store.undoCount).toBeLessThanOrEqual(50)
    })
  })

  describe('Multi-step Undo/Redo Stability', () => {
    it('should undo 10 consecutive operations and restore exact state', () => {
      store.loadSegments(buildSampleData(5))

      const snapshot = JSON.parse(JSON.stringify(store.segments))

      store.deleteSegment(store.segments[0].id)
      store.modifyFeedRate(store.segments[1].id, 1200)
      store.deleteSegment(store.segments[2].id)
      store.modifyFeedRate(store.segments[3].id, 350)
      store.modifyFeedRate(store.segments[4].id, 2000)

      for (let i = 0; i < 5; i++) {
        store.undo()
      }

      const restored = JSON.parse(JSON.stringify(store.segments))
      expect(restored).toEqual(snapshot)
      expect(store.isDirty).toBe(false)
      expect(store.undoCount).toBe(0)
      expect(store.redoCount).toBe(5)
    })

    it('should redo all operations after full undo and get same result', () => {
      store.loadSegments(buildSampleData(5))

      store.deleteSegment(store.segments[0].id)
      store.modifyFeedRate(store.segments[1].id, 1200)
      store.deleteSegment(store.segments[2].id)
      store.modifyFeedRate(store.segments[3].id, 350)

      const afterEdits = JSON.parse(JSON.stringify(store.segments))

      for (let i = 0; i < 4; i++) store.undo()
      for (let i = 0; i < 4; i++) store.redo()

      const afterRedoAll = JSON.parse(JSON.stringify(store.segments))
      expect(afterRedoAll).toEqual(afterEdits)
      expect(store.activeSegments).toHaveLength(3)
      expect(store.segments[1].feedRate).toBe(1200)
    })
  })

  describe('Data Consistency: View, Model, and G-code', () => {
    it('should maintain G-code/feed-rate consistency through edit-undo-export cycle', () => {
      store.loadSegments(buildSampleData(5))

      store.modifyFeedRate(store.segments[0].id, 1200)
      store.modifyFeedRate(store.segments[2].id, 350)

      const fBefore = store.segments.map((s) => s.feedRate)
      expect(fBefore[0]).toBe(1200)
      expect(fBefore[2]).toBe(350)

      store.undo()
      store.undo()

      const fAfter = store.segments.map((s) => s.feedRate)
      expect(fAfter[0]).toBe(500)
      expect(fAfter[2]).toBe(500)
      expect(store.isDirty).toBe(false)
      expect(store.activeSegments).toHaveLength(5)

      const gcode = store.exportGCode('fanuc', 1)
      expect(gcode.validation.valid).toBe(true)
    })

    it('should keep exported G-code in sync with deletion + undo', () => {
      store.loadSegments(buildSampleData(5))

      const seg2Id = store.segments[2].id
      const seg2Coords = [...store.segments[2].endPoint]
      store.deleteSegment(seg2Id)
      expect(store.segments[2].isDeleted).toBe(true)
      expect(store.activeSegments).toHaveLength(4)

      const gcodeAfterDelete = store.exportGCode('fanuc', 1)
      expect(gcodeAfterDelete.validation.valid).toBe(true)

      store.undo()
      expect(store.segments[2].isDeleted).toBe(false)
      expect(store.segments[2].endPoint).toEqual(seg2Coords)
      expect(store.activeSegments).toHaveLength(5)
      expect(store.isDirty).toBe(false)

      const gcodeAfterUndo = store.exportGCode('fanuc', 1)
      expect(gcodeAfterUndo.validation.valid).toBe(true)
    })
  })

  describe('Delete Key Behavior', () => {
    it('should soft-delete selected segment when Delete key logic is triggered', () => {
      store.loadSegments(buildSampleData(5))

      store.selectedSegmentId = store.segments[2].id
      store.deleteSegment(store.selectedSegmentId)

      expect(store.segments[2].isDeleted).toBe(true)
      expect(store.activeSegments).toHaveLength(4)

      store.undo()
      expect(store.segments[2].isDeleted).toBe(false)
      expect(store.activeSegments).toHaveLength(5)
    })
  })

  describe('Escape Key Behavior', () => {
    it('should clear selection state', () => {
      store.loadSegments(buildSampleData(5))

      store.selectedSegmentId = store.segments[0].id
      expect(store.selectedSegmentId).not.toBeNull()

      store.selectedSegmentId = null
      expect(store.selectedSegmentId).toBeNull()
    })
  })

  describe('Cross-format G-code Export After Edits', () => {
    it('should correctly export edited feed rates in all three controller formats', () => {
      store.loadSegments(buildSampleData(3))

      store.modifyFeedRate(store.segments[0].id, 1200)
      store.modifyFeedRate(store.segments[1].id, 800)

      for (const fmt of ['fanuc', 'siemens', 'heidenhain'] as const) {
        const result = store.exportGCode(fmt, 1)
        const gcode = result.gcode

        expect(gcode.length).toBeGreaterThan(50)

        const fValues: number[] = []
        for (const line of gcode.split('\n')) {
          const fm = line.match(/F(\d+)/)
          if (fm) fValues.push(parseInt(fm[1]))
        }
        expect(fValues).toContain(1200)
        expect(fValues).toContain(800)

        if (fmt === 'fanuc') expect(gcode).toContain('O0001')
        if (fmt === 'siemens') expect(gcode).toMatch(/siemens/i)
        if (fmt === 'heidenhain') expect(gcode).toContain('BEGIN PGM')
      }
    })
  })

  describe('Rapid Fire Operations', () => {
    it('should handle rapid sequence of undo/redo without errors', () => {
      store.loadSegments(buildSampleData(5))

      store.deleteSegment(store.segments[0].id)
      store.modifyFeedRate(store.segments[1].id, 1200)
      store.deleteSegment(store.segments[2].id)

      for (let i = 0; i < 10; i++) {
        store.undo()
      }

      expect(store.isDirty).toBe(false)
      expect(store.activeSegments).toHaveLength(5)

      for (let i = 0; i < 10; i++) {
        store.redo()
      }

      expect(store.activeSegments).toHaveLength(3)
      expect(store.segments[1].feedRate).toBe(1200)
    })

    it('should maintain consistent state after interleaved undo-redo', () => {
      store.loadSegments(buildSampleData(5))

      store.deleteSegment(store.segments[0].id)

      store.undo()
      store.redo()
      store.undo()
      store.redo()

      expect(store.segments[0].isDeleted).toBe(true)
      expect(store.activeSegments).toHaveLength(4)
      expect(store.undoCount).toBe(1)
      expect(store.redoCount).toBe(0)
    })
  })
})
