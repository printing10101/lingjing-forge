import type { EditableToolpathSegment } from '../types/editor'

export interface Command {
  execute(): void
  undo(): void
  getDescription(): string
}

export class BaseCommand implements Command {
  protected segments: EditableToolpathSegment[]
  protected onUpdate: () => void

  constructor(segments: EditableToolpathSegment[], onUpdate: () => void) {
    this.segments = segments
    this.onUpdate = onUpdate
  }

  execute(): void {}

  undo(): void {}

  getDescription(): string {
    return 'Unknown command'
  }
}
