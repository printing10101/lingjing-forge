import type { EditableToolpathSegment } from '../types/editor'
import { DEFAULT_FEED_RATE } from '../types/editor'

export function generateSegmentId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

export function parseGCodeText(gcode: string): EditableToolpathSegment[] {
  const lines = gcode.trim().split('\n').filter((l) => l.trim())
  const segments: EditableToolpathSegment[] = []

  let currentBlockNumber = 1
  let lastPoint: [number, number, number] | null = null

  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('%') || trimmed.startsWith('(') || trimmed.startsWith(';') || /^O\d+/.test(trimmed)) {
      continue
    }

    let parseLine = trimmed
    if (/^N\d+\s/.test(parseLine)) {
      parseLine = parseLine.replace(/^N\d+\s/, '')
    }

    const coords = extractCoords(parseLine)
    const feedRate = extractValue(parseLine, 'F') || DEFAULT_FEED_RATE
    const spindleSpeed = extractValue(parseLine, 'S') || 8000
    const toolId = extractValue(parseLine, 'T') || 1
    const segmentType = determineSegmentType(parseLine)

    const startPoint: [number, number, number] = lastPoint || [0, 0, 0]
    const endPoint: [number, number, number] = [
      coords.x ?? startPoint[0],
      coords.y ?? startPoint[1],
      coords.z ?? startPoint[2],
    ]

    if (segmentType !== 'dwell') {
      lastPoint = endPoint
    }

    segments.push({
      id: generateSegmentId(),
      type: segmentType,
      startPoint: [...startPoint] as [number, number, number],
      endPoint: [...endPoint] as [number, number, number],
      feedRate,
      spindleSpeed,
      toolId,
      blockNumber: currentBlockNumber,
      gCode: trimmed,
      isDeleted: false,
    })

    currentBlockNumber++
  }

  return segments
}

function extractCoords(line: string): { x?: number; y?: number; z?: number } {
  const coords: { x?: number; y?: number; z?: number } = {}
  const patterns: [string, 'x' | 'y' | 'z'][] = [
    ['X', 'x'], ['Y', 'y'], ['Z', 'z'],
  ]
  for (const [letter, key] of patterns) {
    const match = line.match(new RegExp(`${letter}(-?\\d+\\.?\\d*)`, 'i'))
    if (match) {
      coords[key] = parseFloat(match[1])
    }
  }
  return coords
}

function extractValue(line: string, letter: string): number | null {
  const match = line.match(new RegExp(`${letter}(\\d+\\.?\\d*)`, 'i'))
  return match ? parseFloat(match[1]) : null
}

function determineSegmentType(line: string): 'rapid' | 'linear' | 'arc' | 'dwell' {
  const upper = line.toUpperCase()
  if (/G0[^0-9]|G00/.test(upper)) return 'rapid'
  if (/G0?2|G0?3/.test(upper)) return 'arc'
  if (/G0?4/.test(upper)) return 'dwell'
  if (/G0?1/.test(upper) || /[XYZ]/.test(upper)) return 'linear'
  return 'rapid'
}

export function exportGCodeText(
  segments: EditableToolpathSegment[],
  controller: 'fanuc' | 'siemens' | 'heidenhain' = 'fanuc',
  programNumber: number = 1,
): string {
  const active = segments.filter((s) => !s.isDeleted)
  const lines: string[] = []

  lines.push(...formatHeader(controller, programNumber))

  for (const seg of active) {
    lines.push(formatSegmentLine(seg, controller))
  }

  lines.push('')
  switch (controller) {
    case 'fanuc':
      lines.push('M09')
      lines.push('M05')
      lines.push('G00 G91 G28 Z0.')
      lines.push('G00 G91 G28 X0. Y0.')
      lines.push('G90')
      lines.push('M30')
      lines.push('%')
      break
    case 'siemens':
      lines.push('M09')
      lines.push('M05')
      lines.push('G00 Z50.000')
      lines.push('G00 X0. Y0.')
      lines.push('M30')
      break
    case 'heidenhain':
      lines.push('M09')
      lines.push('M05')
      lines.push('L  Z+50.000 R0 FMAX')
      lines.push('L  X+0 Y+0 R0 FMAX')
      lines.push('M30')
      lines.push(`END PGM ${String(programNumber).padStart(4, '0')} MM`)
      break
  }

  return lines.join('\n')
}

function formatHeader(controller: string, programNumber: number): string[] {
  const dateStr = new Date().toISOString().slice(0, 10)
  switch (controller) {
    case 'fanuc':
      return [
        '%',
        `O${String(programNumber).padStart(4, '0')} (PROGRAM ${programNumber} - ${dateStr})`,
        '(POST: Fanuc 0i-MF)',
        'G21 G17 G40 G49 G80 G90 G94',
        'G00 G91 G28 Z0.',
        'G00 G91 G28 X0. Y0.',
        'G00 G90 G54 X0. Y0.',
        'G00 G43 Z50.000 H00',
        'M03 S8000',
        'M08',
        '',
      ]
    case 'siemens':
      return [
        `; PROGRAM ${programNumber} - ${dateStr}`,
        '; POST: Siemens 840D',
        'G17 G40 G90 G94',
        'G00 Z50.000',
        'G00 X0. Y0.',
        'M03 S8000',
        'M08',
        '',
      ]
    case 'heidenhain':
      return [
        `0  BEGIN PGM ${String(programNumber).padStart(4, '0')} MM`,
        '1  BLK FORM 0.1 Z X+0 Y+0 Z-50',
        '2  BLK FORM 0.2 X+100 Y+100 Z+0',
        `3  ; PROGRAM ${programNumber} - ${dateStr}`,
        '4  ; POST: Heidenhain TNC',
        '5  TOOL CALL 1 Z S8000',
        '6  L  Z+50.000 R0 FMAX',
        '7  L  X+0 Y+0 R0 FMAX',
        '8  M08',
        '',
      ]
    default:
      return formatHeader('fanuc', programNumber)
  }
}

function formatSegmentLine(seg: EditableToolpathSegment, controller: string): string {
  const fmt = (v: number) => v.toFixed(3)
  const [ex, ey, ez] = seg.endPoint

  let gCode = ''
  switch (seg.type) {
    case 'rapid':
      gCode = 'G00'
      break
    case 'linear':
      gCode = 'G01'
      break
    case 'arc':
      gCode = 'G02'
      break
    case 'dwell':
      gCode = 'G04'
      break
  }

  const parts: string[] = [gCode]
  if (seg.type !== 'dwell') {
    parts.push(`X${fmt(ex)}`)
    parts.push(`Y${fmt(ey)}`)
    parts.push(`Z${fmt(ez)}`)
    parts.push(`F${seg.feedRate.toFixed(0)}`)
  } else {
    parts.push(`P${seg.feedRate.toFixed(0)}`)
  }

  if (controller === 'siemens') {
    return `N${String(seg.blockNumber * 10).padStart(5, '0')} ${parts.join(' ')}`
  }

  return parts.join(' ')
}

export function validateGCodeExport(segments: EditableToolpathSegment[]): {
  valid: boolean
  errors: string[]
  warnings: string[]
} {
  const errors: string[] = []
  const warnings: string[] = []
  const active = segments.filter((s) => !s.isDeleted)

  if (active.length === 0) {
    errors.push('No active segments to export')
    return { valid: false, errors, warnings }
  }

  for (const seg of active) {
    if (seg.feedRate < 10 || seg.feedRate > 50000) {
      errors.push(`Block ${seg.blockNumber}: Invalid feed rate ${seg.feedRate}`)
    }
    if (seg.spindleSpeed < 0 || seg.spindleSpeed > 50000) {
      warnings.push(`Block ${seg.blockNumber}: Unusual spindle speed ${seg.spindleSpeed}`)
    }
    const [ex, ey, ez] = seg.endPoint
    if (Math.abs(ex) > 10000 || Math.abs(ey) > 10000 || Math.abs(ez) > 10000) {
      warnings.push(`Block ${seg.blockNumber}: Large coordinate values detected`)
    }
  }

  return { valid: errors.length === 0, errors, warnings }
}
