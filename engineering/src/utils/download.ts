/**
 * 浏览器端文件下载工具
 */

/**
 * 触发浏览器下载
 * @param urlOrBlob - 下载URL或Blob对象
 * @param filename - 下载文件名
 */
export function triggerFileDownload(urlOrBlob: string | Blob, filename: string): void {
  const link = document.createElement('a')

  if (typeof urlOrBlob === 'string') {
    link.href = urlOrBlob
  } else {
    link.href = URL.createObjectURL(urlOrBlob)
  }

  link.setAttribute('download', filename)
  document.body.appendChild(link)
  link.click()
  link.remove()

  // Revoke blob URL to free memory
  if (typeof urlOrBlob !== 'string') {
    URL.revokeObjectURL(link.href)
  }
}
