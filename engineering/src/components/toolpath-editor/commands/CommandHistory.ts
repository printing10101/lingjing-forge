import type { Command } from './BaseCommand'

export type CommandHistoryEvents = {
  'memory-warning': { current: number; max: number; percentage: number }
  'stack-cleared': void
}

export class CommandHistory {
  private undoStack: Command[] = []
  private redoStack: Command[] = []
  private _maxDepth: number
  private warningTriggered = false
  private eventTarget = new EventTarget()

  constructor(maxDepth: number = 1000) {
    this._maxDepth = maxDepth
  }

  get maxDepth(): number {
    return this._maxDepth
  }

  set maxDepth(value: number) {
    this._maxDepth = Math.max(1, value)
    this.warningTriggered = false
    this._checkMemoryWarning()
  }

  execute(command: Command): void {
    command.execute()
    this.undoStack.push(command)
    this.redoStack = []

    if (this.undoStack.length > this._maxDepth) {
      this.undoStack.shift()
    }

    this._checkMemoryWarning()
  }

  undo(): boolean {
    const command = this.undoStack.pop()
    if (!command) return false

    command.undo()
    this.redoStack.push(command)
    this.warningTriggered = false
    return true
  }

  redo(): boolean {
    const command = this.redoStack.pop()
    if (!command) return false

    command.execute()
    this.undoStack.push(command)
    this._checkMemoryWarning()
    return true
  }

  canUndo(): boolean {
    return this.undoStack.length > 0
  }

  canRedo(): boolean {
    return this.redoStack.length > 0
  }

  getUndoCount(): number {
    return this.undoStack.length
  }

  getRedoCount(): number {
    return this.redoStack.length
  }

  getUndoDescriptions(): string[] {
    return this.undoStack.map((c) => c.getDescription())
  }

  clear(): void {
    this.undoStack = []
    this.redoStack = []
    this.warningTriggered = false
    this.eventTarget.dispatchEvent(new CustomEvent('stack-cleared'))
  }

  on<K extends keyof CommandHistoryEvents>(event: K, callback: (detail: CommandHistoryEvents[K]) => void): void {
    this.eventTarget.addEventListener(event, ((e: Event) => callback((e as CustomEvent).detail)) as EventListener)
  }

  off<K extends keyof CommandHistoryEvents>(event: K, callback: (detail: CommandHistoryEvents[K]) => void): void {
    this.eventTarget.removeEventListener(event, ((e: Event) => callback((e as CustomEvent).detail)) as EventListener)
  }

  private _checkMemoryWarning(): void {
    if (this.warningTriggered) return

    const threshold = Math.floor(this._maxDepth * 0.9)
    if (this.undoStack.length >= threshold) {
      this.warningTriggered = true
      const percentage = Math.round((this.undoStack.length / this._maxDepth) * 100)
      this.eventTarget.dispatchEvent(
        new CustomEvent('memory-warning', {
          detail: {
            current: this.undoStack.length,
            max: this._maxDepth,
            percentage,
          },
        })
      )
    }
  }
}
