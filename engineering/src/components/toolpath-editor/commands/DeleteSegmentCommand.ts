import { BaseCommand } from './BaseCommand'
import type { EditableToolpathSegment } from '../types/editor'

export class DeleteSegmentCommand extends BaseCommand {
  private segmentId: string
  private deletedSegment: EditableToolpathSegment | null = null
  private deletedIndex: number = -1

  constructor(
    segments: EditableToolpathSegment[],
    onUpdate: () => void,
    segmentId: string,
  ) {
    super(segments, onUpdate)
    this.segmentId = segmentId
  }

  override execute(): void {
    this.deletedIndex = this.segments.findIndex((s) => s.id === this.segmentId)
    if (this.deletedIndex === -1) return
    this.deletedSegment = { ...this.segments[this.deletedIndex] }
    this.segments[this.deletedIndex].isDeleted = true
    this.onUpdate()
  }

  override undo(): void {
    if (this.deletedIndex === -1 || !this.deletedSegment) return
    this.segments[this.deletedIndex] = { ...this.deletedSegment }
    this.onUpdate()
  }

  override getDescription(): string {
    return `Delete segment ${this.segmentId.slice(0, 8)}`
  }
}
