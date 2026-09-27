#!/usr/bin/env node

/**
 * API 文档自动生成脚本
 * 从 OpenAPI 规范文件生成 Markdown 文档
 */

import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

const OPENAPI_PATH = path.resolve(__dirname, '../../docs/api/openapi.json')
const OUTPUT_DIR = path.resolve(__dirname, '../docs/api')

function loadOpenAPI() {
  try {
    const content = fs.readFileSync(OPENAPI_PATH, 'utf-8')
    return JSON.parse(content)
  } catch (error) {
    console.error('无法加载 OpenAPI 规范文件:', error.message)
    process.exit(1)
  }
}

function generateEndpointDoc(method, path, endpoint, spec) {
  const lines = []
  const title = `${method.toUpperCase()} ${path}`
  
  lines.push(`## ${title}`)
  lines.push('')
  
  if (endpoint.summary) {
    lines.push(`**摘要**: ${endpoint.summary}`)
    lines.push('')
  }
  
  if (endpoint.description) {
    lines.push(`${endpoint.description}`)
    lines.push('')
  }
  
  // 请求参数
  if (endpoint.parameters && endpoint.parameters.length > 0) {
    lines.push('### 请求参数')
    lines.push('')
    lines.push('| 参数名 | 位置 | 类型 | 必填 | 描述 |')
    lines.push('|--------|------|------|------|------|')
    
    endpoint.parameters.forEach(param => {
      const required = param.required ? '是' : '否'
      const description = param.description || '-'
      const type = param.schema?.type || param.type || 'string'
      lines.push(`| ${param.name} | ${param.in} | ${type} | ${required} | ${description} |`)
    })
    lines.push('')
  }
  
  // 请求体
  if (endpoint.requestBody) {
    lines.push('### 请求体')
    lines.push('')
    const content = endpoint.requestBody.content
    if (content['application/json']?.schema) {
      const schema = content['application/json'].schema
      if (schema.$ref) {
        const schemaName = schema.$ref.split('/').pop()
        lines.push(`引用 Schema: \`${schemaName}\``)
      }
    }
    lines.push('')
  }
  
  // 响应
  if (endpoint.responses) {
    lines.push('### 响应')
    lines.push('')
    Object.entries(endpoint.responses).forEach(([code, response]) => {
      lines.push(`**${code}** - ${response.description || '无描述'}`)
      lines.push('')
    })
  }
  
  lines.push('---')
  lines.push('')
  
  return lines.join('\n')
}

function generateAPIDocs() {
  console.log('开始生成 API 文档...')
  
  const spec = loadOpenAPI()
  const output = []
  
  // 文档头部
  output.push('# API 参考文档')
  output.push('')
  output.push(`> 版本: ${spec.info?.version || '未知'}`)
  output.push(`> 生成时间: ${new Date().toISOString()}`)
  output.push('')
  
  if (spec.info?.description) {
    output.push(`${spec.info.description}`)
    output.push('')
  }
  
  // 基础信息
  if (spec.servers && spec.servers.length > 0) {
    output.push('## 服务器地址')
    output.push('')
    spec.servers.forEach(server => {
      output.push(`- ${server.url}${server.description ? ` - ${server.description}` : ''}`)
    })
    output.push('')
  }
  
  // 按标签分组生成文档
  const endpointsByTag = {}
  
  Object.entries(spec.paths || {}).forEach(([path, methods]) => {
    Object.entries(methods).forEach(([method, endpoint]) => {
      if (['get', 'post', 'put', 'delete', 'patch'].includes(method)) {
        const tags = endpoint.tags || ['未分类']
        tags.forEach(tag => {
          if (!endpointsByTag[tag]) {
            endpointsByTag[tag] = []
          }
          endpointsByTag[tag].push({ method, path, endpoint })
        })
      }
    })
  })
  
  // 生成每个标签的文档
  Object.entries(endpointsByTag).forEach(([tag, endpoints]) => {
    output.push(`## ${tag}`)
    output.push('')
    
    endpoints.forEach(({ method, path, endpoint }) => {
      output.push(generateEndpointDoc(method, path, endpoint, spec))
    })
  })
  
  // 写入文件
  const outputFile = path.join(OUTPUT_DIR, 'generated-api-reference.md')
  fs.writeFileSync(outputFile, output.join('\n'), 'utf-8')
  
  console.log(`✓ API 文档已生成: ${outputFile}`)
  console.log(`  共生成 ${Object.values(endpointsByTag).flat().length} 个端点文档`)
}

// 执行生成
generateAPIDocs()
