import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { triggerFileDownload } from '@/utils/download'

describe('download', () => {
  describe('triggerFileDownload', () => {
    let clickSpy: ReturnType<typeof vi.fn>
    let appendChildSpy: ReturnType<typeof vi.fn>
    let removeSpy: ReturnType<typeof vi.fn>
    let createObjectURLSpy: ReturnType<typeof vi.fn>
    let revokeObjectURLSpy: ReturnType<typeof vi.fn>
    let originalCreateElement: typeof document.createElement

    beforeEach(() => {
      clickSpy = vi.fn()
      appendChildSpy = vi.fn()
      removeSpy = vi.fn()
      createObjectURLSpy = vi.fn().mockReturnValue('blob:http://localhost/fake-url')
      revokeObjectURLSpy = vi.fn()

      vi.stubGlobal('URL', {
        createObjectURL: createObjectURLSpy,
        revokeObjectURL: revokeObjectURLSpy,
      })

      originalCreateElement = document.createElement.bind(document)
      vi.spyOn(document, 'createElement').mockImplementation((tagName: string) => {
        const el = originalCreateElement(tagName)
        el.click = clickSpy
        el.remove = removeSpy
        return el
      })
      vi.spyOn(document.body, 'appendChild').mockImplementation(appendChildSpy as any)
    })

    afterEach(() => {
      vi.restoreAllMocks()
      vi.unstubAllGlobals()
    })

    it('使用字符串 URL 触发下载', () => {
      const url = 'https://example.com/file.pdf'
      triggerFileDownload(url, 'report.pdf')

      expect(appendChildSpy).toHaveBeenCalled()
      expect(clickSpy).toHaveBeenCalled()
      expect(removeSpy).toHaveBeenCalled()
      // 字符串 URL 不应调用 createObjectURL / revokeObjectURL
      expect(createObjectURLSpy).not.toHaveBeenCalled()
      expect(revokeObjectURLSpy).not.toHaveBeenCalled()
    })

    it('使用 Blob 对象触发下载并释放对象 URL', () => {
      const blob = new Blob(['content'], { type: 'text/plain' })
      triggerFileDownload(blob, 'data.txt')

      expect(createObjectURLSpy).toHaveBeenCalledWith(blob)
      expect(appendChildSpy).toHaveBeenCalled()
      expect(clickSpy).toHaveBeenCalled()
      expect(removeSpy).toHaveBeenCalled()
      expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:http://localhost/fake-url')
    })

    it('为链接设置 download 属性', () => {
      triggerFileDownload('https://example.com/file.zip', 'archive.zip')

      expect(appendChildSpy).toHaveBeenCalled()
      const linkEl = appendChildSpy.mock.calls[0][0]
      expect(linkEl.getAttribute('download')).toBe('archive.zip')
    })
  })
})
