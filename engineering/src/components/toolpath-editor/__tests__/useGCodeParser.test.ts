import { describe, it, expect } from 'vitest'
import {
  parseGCodeText,
  exportGCodeText,
  validateGCodeExport,
  generateSegmentId,
} from '../composables/useGCodeParser'
import type { EditableToolpathSegment } from '../types/editor'

describe('useGCodeParser', () => {
  describe('generateSegmentId', () => {
    it('should generate unique IDs', () => {
      const id1 = generateSegmentId()
      const id2 = generateSegmentId()
      expect(id1).not.toBe(id2)
      expect(id1.length).toBeGreaterThan(10)
    })
  })

  describe('parseGCodeText', () => {
    it('should parse basic G01 linear move', () => {
      const gcode = 'G01 X10.0 Y20.0 Z-5.0 F500.0'
      const segments = parseGCodeText(gcode)

      expect(segments).toHaveLength(1)
      expect(segments[0].type).toBe('linear')
      expect(segments[0].endPoint).toEqual([10, 20, -5])
      expect(segments[0].feedRate).toBe(500)
    })

    it('should parse G00 rapid move', () => {
      const gcode = 'G00 X50.0 Y50.0'
      const segments = parseGCodeText(gcode)

      expect(segments).toHaveLength(1)
      expect(segments[0].type).toBe('rapid')
    })

    it('should parse G02 clockwise arc', () => {
      const gcode = 'G02 X30.0 Y30.0 R15.0 F300.0'
      const segments = parseGCodeText(gcode)

      expect(segments).toHaveLength(1)
      expect(segments[0].type).toBe('arc')
      expect(segments[0].feedRate).toBe(300)
    })

    it('should parse G03 counterclockwise arc', () => {
      const gcode = 'G03 X-10.0 Y-20.0 R10.0'
      const segments = parseGCodeText(gcode)

      expect(segments).toHaveLength(1)
      expect(segments[0].type).toBe('arc')
    })

    it('should parse multiple segments maintaining continuity', () => {
      const gcode = [
        'G00 X0.0 Y0.0 Z50.0',
        'G01 Z-2.0 F300.0',
        'G01 X50.0 Y0.0 F800.0',
        'G01 X50.0 Y50.0 F800.0',
        'G00 Z50.0',
      ].join('\n')

      const segments = parseGCodeText(gcode)
      expect(segments).toHaveLength(5)

      expect(segments[1].startPoint).toEqual([0, 0, 50])
      expect(segments[1].endPoint).toEqual([0, 0, -2])

      expect(segments[2].startPoint).toEqual([0, 0, -2])
      expect(segments[2].endPoint).toEqual([50, 0, -2])
    })

    it('should skip comment lines and block numbers', () => {
      const gcode = [
        '%',
        'O0001 (TEST)',
        '(POST: Fanuc)',
        'N10 G01 X10.0 Y10.0 F500.0',
        'N20 G01 X20.0 Y20.0 F500.0',
      ].join('\n')

      const segments = parseGCodeText(gcode)
      expect(segments).toHaveLength(2)
      expect(segments[0].blockNumber).toBe(1)
      expect(segments[1].blockNumber).toBe(2)
    })

    it('should handle empty G-code gracefully', () => {
      const segments = parseGCodeText('')
      expect(segments).toHaveLength(0)
    })

    it('should extract spindle speed S and tool T', () => {
      const gcode = 'G01 X10.0 Y10.0 F500.0 S8000 T02'
      const segments = parseGCodeText(gcode)

      expect(segments[0].spindleSpeed).toBe(8000)
    })

    it('should handle arc G-code', () => {
      const gcode = 'G02 X50.0 Y50.0 R25.0 F400.0'
      const segments = parseGCodeText(gcode)

      expect(segments).toHaveLength(1)
      expect(segments[0].type).toBe('arc')
    })

    it('should handle negative coordinates', () => {
      const gcode = 'G01 X-50.0 Y-25.0 Z-10.0 F300.0'
      const segments = parseGCodeText(gcode)

      expect(segments[0].endPoint).toEqual([-50, -25, -10])
    })
  })

  describe('exportGCodeText', () => {
    const sampleSegments: EditableToolpathSegment[] = [
      {
        id: '1', type: 'rapid', startPoint: [0, 0, 0], endPoint: [0, 0, 50],
        feedRate: 10000, spindleSpeed: 8000, toolId: 1, blockNumber: 1,
        gCode: 'G00 Z50.000', isDeleted: false,
      },
      {
        id: '2', type: 'linear', startPoint: [0, 0, 50], endPoint: [10, 10, 0],
        feedRate: 500, spindleSpeed: 8000, toolId: 1, blockNumber: 2,
        gCode: 'G01 X10.000 Y10.000 Z0.000 F500', isDeleted: false,
      },
    ]

    it('should export Fanuc format with header and footer', () => {
      const gcode = exportGCodeText(sampleSegments, 'fanuc', 1)

      expect(gcode).toContain('%')
      expect(gcode).toContain('O0001')
      expect(gcode).toContain('G21 G17 G40')
      expect(gcode).toContain('M30')
      expect(gcode).toContain('G01 X10.000 Y10.000 Z0.000 F500')
    })

    it('should export Siemens format with block numbers', () => {
      const gcode = exportGCodeText(sampleSegments, 'siemens', 1)

      expect(gcode).toContain('POST: Siemens 840D')
    })

    it('should export Heidenhain format', () => {
      const gcode = exportGCodeText(sampleSegments, 'heidenhain', 1)

      expect(gcode).toContain('BEGIN PGM')
      expect(gcode).toContain('END PGM')
      expect(gcode).toContain('BLK FORM')
    })

    it('should exclude deleted segments', () => {
      const deletedSegments = [
        { ...sampleSegments[0] },
        { ...sampleSegments[1], isDeleted: true },
      ]
      const gcode = exportGCodeText(deletedSegments, 'fanuc', 1)

      expect(gcode).toContain('X0.000 Y0.000 Z50.000')
      expect(gcode).not.toContain('X10.000 Y10.000 Z0.000')
    })

    it('should include program number in filename format', () => {
      const gcode = exportGCodeText(sampleSegments, 'fanuc', 42)
      expect(gcode).toContain('O0042')
    })
  })

  describe('validateGCodeExport', () => {
    it('should validate valid segments as valid', () => {
      const validSegment: EditableToolpathSegment = {
        id: '1', type: 'linear', startPoint: [0, 0, 0], endPoint: [10, 10, 0],
        feedRate: 500, spindleSpeed: 8000, toolId: 1, blockNumber: 1,
        gCode: 'G01 X10 Y10', isDeleted: false,
      }

      const result = validateGCodeExport([validSegment])
      expect(result.valid).toBe(true)
      expect(result.errors).toHaveLength(0)
    })

    it('should reject empty segments', () => {
      const result = validateGCodeExport([])
      expect(result.valid).toBe(false)
      expect(result.errors).toContain('No active segments to export')
    })

    it('should detect invalid feed rates', () => {
      const badSegment: EditableToolpathSegment = {
        id: '1', type: 'linear', startPoint: [0, 0, 0], endPoint: [10, 10, 0],
        feedRate: 5, spindleSpeed: 8000, toolId: 1, blockNumber: 1,
        gCode: 'G01', isDeleted: false,
      }

      const result = validateGCodeExport([badSegment])
      expect(result.valid).toBe(false)
      expect(result.errors.some((e) => e.includes('feed rate'))).toBe(true)
    })

    it('should warn about large coordinate values', () => {
      const largeSegment: EditableToolpathSegment = {
        id: '1', type: 'linear', startPoint: [0, 0, 0], endPoint: [20000, 0, 0],
        feedRate: 500, spindleSpeed: 8000, toolId: 1, blockNumber: 1,
        gCode: 'G01', isDeleted: false,
      }

      const result = validateGCodeExport([largeSegment])
      expect(result.warnings.length).toBeGreaterThan(0)
    })

    it('should skip deleted segments in validation', () => {
      const deletedSegment: EditableToolpathSegment = {
        id: '1', type: 'linear', startPoint: [0, 0, 0], endPoint: [10, 10, 0],
        feedRate: 5, spindleSpeed: 8000, toolId: 1, blockNumber: 1,
        gCode: 'G01', isDeleted: true,
      }

      const result = validateGCodeExport([deletedSegment])
      expect(result.valid).toBe(false)
      expect(result.errors).toContain('No active segments to export')
    })
  })
})
