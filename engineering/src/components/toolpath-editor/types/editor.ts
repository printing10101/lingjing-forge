export interface EditableToolpathSegment {
  id: string
  type: 'rapid' | 'linear' | 'arc' | 'dwell'
  startPoint: [number, number, number]
  endPoint: [number, number, number]
  feedRate: number
  spindleSpeed: number
  toolId: number
  blockNumber: number
  gCode: string
  isDeleted: boolean
}

export interface ToolpathEditState {
  segments: EditableToolpathSegment[]
  originalSegments: EditableToolpathSegment[]
  selectedSegmentId: string | null
  hoveredSegmentId: string | null
  isDirty: boolean
}

export type GCodeController = 'fanuc' | 'siemens' | 'heidenhain'

export interface GCodeExportOptions {
  controller: GCodeController
  programNumber: number
  safeZHeight: number
  spindleSpeed: number
}

export interface ContextMenuPosition {
  x: number
  y: number
}

export interface ContextMenuAction {
  action: 'delete' | 'adjust-feed'
  segmentId: string
}

export const DEFAULT_FEED_RATE = 500
export const MIN_FEED_RATE = 10
export const MAX_FEED_RATE = 50000
export const FEED_RATE_STEP = 10
