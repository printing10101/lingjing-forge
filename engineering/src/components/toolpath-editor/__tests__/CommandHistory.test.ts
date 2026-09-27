import { describe, it, expect, beforeEach } from 'vitest'
import { CommandHistory } from '../commands/CommandHistory'
import { BaseCommand } from '../commands/BaseCommand'
import type { EditableToolpathSegment } from '../types/editor'

function makeSegment(id: string): EditableToolpathSegment {
  return {
    id,
    type: 'linear',
    startPoint: [0, 0, 0],
    endPoint: [10, 10, 0],
    feedRate: 500,
    spindleSpeed: 8000,
    toolId: 1,
    blockNumber: 1,
    gCode: 'G01 X10 Y10',
    isDeleted: false,
  }
}

class TestCommand extends BaseCommand {
  private action: string
  constructor(segments: EditableToolpathSegment[], onUpdate: () => void, action: string) {
    super(segments, onUpdate)
    this.action = action
  }
  override execute(): void {
    this.segments.push(makeSegment(`test-${this.action}`))
    this.onUpdate()
  }
  override undo(): void {
    this.segments.pop()
    this.onUpdate()
  }
  override getDescription(): string {
    return `Test: ${this.action}`
  }
}

describe('CommandHistory', () => {
  let history: CommandHistory
  let segments: EditableToolpathSegment[]
  let updateCount: number

  beforeEach(() => {
    history = new CommandHistory(50)
    segments = []
    updateCount = 0
  })

  function onUpdate() {
    updateCount++
  }

  describe('execute', () => {
    it('should execute command and push to undo stack', () => {
      const cmd = new TestCommand(segments, onUpdate, 'add1')
      history.execute(cmd)

      expect(segments.length).toBe(1)
      expect(segments[0].id).toBe('test-add1')
      expect(history.canUndo()).toBe(true)
      expect(history.canRedo()).toBe(false)
      expect(updateCount).toBe(1)
    })

    it('should clear redo stack on new execute', () => {
      const cmd1 = new TestCommand(segments, onUpdate, 'add1')
      history.execute(cmd1)
      history.undo()

      const cmd2 = new TestCommand(segments, onUpdate, 'add2')
      history.execute(cmd2)

      expect(history.canRedo()).toBe(false)
    })
  })

  describe('undo', () => {
    it('should undo last executed command', () => {
      const cmd = new TestCommand(segments, onUpdate, 'add1')
      history.execute(cmd)

      const result = history.undo()
      expect(result).toBe(true)
      expect(segments.length).toBe(0)
      expect(history.canUndo()).toBe(false)
      expect(history.canRedo()).toBe(true)
      expect(updateCount).toBe(2)
    })

    it('should return false when undo stack is empty', () => {
      const result = history.undo()
      expect(result).toBe(false)
    })
  })

  describe('redo', () => {
    it('should redo after undo', () => {
      const cmd = new TestCommand(segments, onUpdate, 'add1')
      history.execute(cmd)
      history.undo()

      const result = history.redo()
      expect(result).toBe(true)
      expect(segments.length).toBe(1)
      expect(history.canUndo()).toBe(true)
      expect(history.canRedo()).toBe(false)
      expect(updateCount).toBe(3)
    })

    it('should return false when redo stack is empty', () => {
      const result = history.redo()
      expect(result).toBe(false)
    })
  })

  describe('maxDepth configuration', () => {
    it('should have default maxDepth of 1000', () => {
      const defaultHistory = new CommandHistory()
      expect(defaultHistory.maxDepth).toBe(1000)
    })

    it('should allow setting maxDepth via constructor', () => {
      const customHistory = new CommandHistory(500)
      expect(customHistory.maxDepth).toBe(500)
    })

    it('should allow changing maxDepth via setter', () => {
      history.maxDepth = 200
      expect(history.maxDepth).toBe(200)
    })

    it('should enforce minimum maxDepth of 1', () => {
      history.maxDepth = 0
      expect(history.maxDepth).toBe(1)

      history.maxDepth = -5
      expect(history.maxDepth).toBe(1)
    })
  })

  describe('boundary: 1100 commands with maxDepth 1000', () => {
    it('should keep undo stack length <= maxDepth when executing 1100 commands', () => {
      const boundedHistory = new CommandHistory(1000)
      const localSegments: EditableToolpathSegment[] = []
      for (let i = 0; i < 1100; i++) {
        const cmd = new TestCommand(localSegments, onUpdate, `cmd${i}`)
        boundedHistory.execute(cmd)
      }

      expect(boundedHistory.getUndoCount()).toBe(1000)
      expect(boundedHistory.getUndoCount()).toBeLessThanOrEqual(boundedHistory.maxDepth)
    })

    it('should discard oldest commands when exceeding maxDepth (FIFO)', () => {
      const boundedHistory = new CommandHistory(10)
      const localSegments: EditableToolpathSegment[] = []
      for (let i = 0; i < 15; i++) {
        const cmd = new TestCommand(localSegments, onUpdate, `cmd${i}`)
        boundedHistory.execute(cmd)
      }

      // The first 5 commands (cmd0-cmd4) should be discarded
      expect(boundedHistory.getUndoCount()).toBe(10)
      const descs = boundedHistory.getUndoDescriptions()
      expect(descs[0]).toBe('Test: cmd5')
      expect(descs[9]).toBe('Test: cmd14')
    })

    it('should only allow undoing the most recent maxDepth operations', () => {
      const boundedHistory = new CommandHistory(10)
      const localSegments: EditableToolpathSegment[] = []
      for (let i = 0; i < 15; i++) {
        const cmd = new TestCommand(localSegments, onUpdate, `cmd${i}`)
        boundedHistory.execute(cmd)
      }

      // Should be able to undo exactly 10 times
      for (let i = 0; i < 10; i++) {
        expect(boundedHistory.canUndo()).toBe(true)
        boundedHistory.undo()
      }

      expect(boundedHistory.canUndo()).toBe(false)
      expect(localSegments.length).toBe(5) // Only cmd0-cmd4 remain
    })
  })

  describe('max size', () => {
    it('should discard oldest commands when exceeding max size', () => {
      const smallHistory = new CommandHistory(3)
      for (let i = 0; i < 5; i++) {
        const cmd = new TestCommand(segments, onUpdate, `add${i}`)
        smallHistory.execute(cmd)
      }

      expect(smallHistory.getUndoCount()).toBe(3)
      expect(segments.length).toBe(5)

      for (let i = 0; i < 3; i++) {
        smallHistory.undo()
      }

      expect(segments.length).toBe(2)
      expect(smallHistory.canUndo()).toBe(false)
    })
  })

  describe('memory warning event', () => {
    it('should trigger memory-warning event when undo stack reaches 90% of maxDepth', () => {
      const warnHistory = new CommandHistory(10)
      const localSegments: EditableToolpathSegment[] = []
      let warningCount = 0
      let warningDetail: { current: number; max: number; percentage: number } | null = null

      warnHistory.on('memory-warning', (detail) => {
        warningCount++
        warningDetail = detail
      })

      // 90% of 10 is 9, so at 9 commands the warning should trigger
      for (let i = 0; i < 9; i++) {
        const cmd = new TestCommand(localSegments, onUpdate, `cmd${i}`)
        warnHistory.execute(cmd)
      }

      expect(warningCount).toBe(1)
      expect(warningDetail).not.toBeNull()
      expect(warningDetail!.current).toBe(9)
      expect(warningDetail!.max).toBe(10)
      expect(warningDetail!.percentage).toBe(90)
    })

    it('should only trigger warning once per fill cycle', () => {
      const warnHistory = new CommandHistory(10)
      const localSegments: EditableToolpathSegment[] = []
      let warningCount = 0

      warnHistory.on('memory-warning', () => {
        warningCount++
      })

      // Execute 15 commands (warning should trigger once at 9)
      for (let i = 0; i < 15; i++) {
        const cmd = new TestCommand(localSegments, onUpdate, `cmd${i}`)
        warnHistory.execute(cmd)
      }

      expect(warningCount).toBe(1)
    })

    it('should reset warning trigger flag after undo', () => {
      const warnHistory = new CommandHistory(10)
      const localSegments: EditableToolpathSegment[] = []
      let warningCount = 0

      warnHistory.on('memory-warning', () => {
        warningCount++
      })

      // Fill to 90%
      for (let i = 0; i < 9; i++) {
        warnHistory.execute(new TestCommand(localSegments, onUpdate, `cmd${i}`))
      }
      expect(warningCount).toBe(1)

      // Undo and redo back
      warnHistory.undo()
      warnHistory.redo()

      // Warning should trigger again since we're back at 90%
      expect(warningCount).toBe(2)
    })

    it('should reset warning when maxDepth is changed', () => {
      const warnHistory = new CommandHistory(10)
      const localSegments: EditableToolpathSegment[] = []
      let warningCount = 0

      warnHistory.on('memory-warning', () => {
        warningCount++
      })

      // Fill to 90%
      for (let i = 0; i < 9; i++) {
        warnHistory.execute(new TestCommand(localSegments, onUpdate, `cmd${i}`))
      }
      expect(warningCount).toBe(1)

      // Increase maxDepth so current count is below 90%
      warnHistory.maxDepth = 20

      // Add more commands to reach new 90% threshold (18)
      for (let i = 9; i < 18; i++) {
        warnHistory.execute(new TestCommand(localSegments, onUpdate, `cmd${i}`))
      }

      expect(warningCount).toBe(2)
    })
  })

  describe('clear', () => {
    it('should clear all stacks', () => {
      const cmd = new TestCommand(segments, onUpdate, 'add1')
      history.execute(cmd)
      history.undo()
      history.clear()

      expect(history.canUndo()).toBe(false)
      expect(history.canRedo()).toBe(false)
      expect(history.getUndoCount()).toBe(0)
      expect(history.getRedoCount()).toBe(0)
    })

    it('should reset warning triggered flag', () => {
      const warnHistory = new CommandHistory(10)
      const localSegments: EditableToolpathSegment[] = []
      let warningCount = 0

      warnHistory.on('memory-warning', () => {
        warningCount++
      })

      // Fill to 90%
      for (let i = 0; i < 9; i++) {
        warnHistory.execute(new TestCommand(localSegments, onUpdate, `cmd${i}`))
      }
      expect(warningCount).toBe(1)

      // Clear and refill
      warnHistory.clear()
      for (let i = 0; i < 9; i++) {
        warnHistory.execute(new TestCommand(localSegments, onUpdate, `cmd${i}`))
      }

      // Warning should trigger again
      expect(warningCount).toBe(2)
    })

    it('should dispatch stack-cleared event', () => {
      const warnHistory = new CommandHistory(10)
      let clearedCount = 0

      warnHistory.on('stack-cleared', () => {
        clearedCount++
      })

      warnHistory.execute(new TestCommand(segments, onUpdate, 'add1'))
      warnHistory.clear()

      expect(clearedCount).toBe(1)
    })
  })

  describe('getDescriptions', () => {
    it('should list undo stack descriptions', () => {
      const cmd1 = new TestCommand(segments, onUpdate, 'cmd1')
      const cmd2 = new TestCommand(segments, onUpdate, 'cmd2')
      history.execute(cmd1)
      history.execute(cmd2)

      const descs = history.getUndoDescriptions()
      expect(descs).toEqual(['Test: cmd1', 'Test: cmd2'])
    })
  })

  describe('clear', () => {
    it('should clear all stacks', () => {
      const cmd = new TestCommand(segments, onUpdate, 'add1')
      history.execute(cmd)
      history.clear()

      expect(history.canUndo()).toBe(false)
      expect(history.canRedo()).toBe(false)
      expect(history.getUndoCount()).toBe(0)
    })
  })
})
