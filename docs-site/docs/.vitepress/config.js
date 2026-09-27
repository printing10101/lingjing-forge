import { defineConfig } from 'vitepress'

export default defineConfig({
  title: '灵境制造',
  description: '桌面级制造物理 AI - 专业文档中心',
  lang: 'zh-CN',
  
  head: [
    ['link', { rel: 'icon', href: '/favicon.ico' }],
    ['meta', { name: 'theme-color', content: '#3eaf7c' }],
    ['meta', { name: 'og:type', content: 'website' }],
    ['meta', { name: 'og:title', content: '灵境制造文档' }],
    ['meta', { name: 'og:description', content: '桌面级制造物理 AI 专业文档' }]
  ],

  themeConfig: {
    logo: '/logo.svg',
    
    nav: [
      { text: '首页', link: '/' },
      { text: '用户指南', link: '/user-guide/快速入门' },
      { text: '开发者文档', link: '/development/开发环境搭建' },
      { text: 'API 参考', link: '/api/README' },
      { text: '更新日志', link: '/changelog/概述' }
    ],

    sidebar: {
      '/user-guide/': [
        {
          text: '用户指南',
          items: [
            { text: '快速入门', link: '/user-guide/快速入门' },
            { text: '安装指南', link: '/user-guide/安装指南' },
            { text: '功能详解', link: '/user-guide/功能详解' },
            { text: '安全须知', link: '/user-guide/安全须知' },
            { text: '故障排查', link: '/user-guide/故障排查' }
          ]
        }
      ],
      
      '/development/': [
        {
          text: '开发者文档',
          items: [
            { text: '开发环境搭建', link: '/development/开发环境搭建' },
            { text: '架构概述', link: '/development/架构概述' },
            { text: '贡献指南', link: '/development/贡献指南' },
            { text: '测试指南', link: '/development/测试指南' }
          ]
        }
      ],
      
      '/api/': [
        {
          text: 'API 参考',
          items: [
            { text: 'API 概述', link: '/api/README' },
            { text: '错误码说明', link: '/api/error-codes' },
            { text: '使用示例', link: '/api/examples' }
          ]
        }
      ],
      
      '/ai/': [
        {
          text: 'AI 功能',
          items: [
            { text: '贝叶斯 LNN 指南', link: '/ai/bayesian-lnn-guide' },
            { text: '主动学习触发器', link: '/ai/active-learning-triggers' },
            { text: '自动重训练指南', link: '/ai/auto-retrain-guide' }
          ]
        }
      ],
      
      '/simulation/': [
        {
          text: '仿真模块',
          items: [
            { text: '颤振分析', link: '/simulation/chatter-usage' },
            { text: '切削力仿真', link: '/simulation/cutting-force-usage' },
            { text: '集成指南', link: '/simulation/integration-guide' }
          ]
        }
      ],
      
      '/integrations/': [
        {
          text: '系统集成',
          items: [
            { text: 'OPC UA 集成', link: '/integrations/opcua-usage' },
            { text: 'MTConnect 集成', link: '/integrations/mtconnect-usage' }
          ]
        }
      ],
      
      '/wiki/': [
        {
          text: '项目 Wiki',
          items: [
            { text: '项目概览', link: '/wiki/01-项目概览' },
            { text: '整体架构', link: '/wiki/02-整体架构' },
            { text: '目录结构', link: '/wiki/03-目录结构与代码地图' },
            { text: '后端核心模块', link: '/wiki/04-后端核心模块' },
            { text: 'AI LNN 引擎', link: '/wiki/05-AI-LNN推理引擎' },
            { text: '业务能力模块', link: '/wiki/06-业务能力模块' },
            { text: '工程与任务系统', link: '/wiki/07-工程与任务系统' },
            { text: '安全与认证', link: '/wiki/08-安全与认证' },
            { text: '前端架构', link: '/wiki/09-前端架构' },
            { text: '数据与基础设施', link: '/wiki/10-数据与基础设施' },
            { text: '部署与运行', link: '/wiki/11-部署与运行' },
            { text: '关键 API 索引', link: '/wiki/12-关键API索引' }
          ]
        }
      ],
      
      '/changelog/': [
        {
          text: '更新日志',
          items: [
            { text: '概述', link: '/changelog/概述' },
            { text: 'v2.8.0', link: '/changelog/v2.8.0' },
            { text: 'v2.7.0', link: '/changelog/v2.7.0' },
            { text: 'v2.6.0', link: '/changelog/v2.6.0' },
            { text: 'v2.5.0', link: '/changelog/v2.5.0' },
            { text: 'v2.4.0', link: '/changelog/v2.4.0' },
            { text: 'v2.3.0', link: '/changelog/v2.3.0' },
            { text: 'v2.2.1', link: '/changelog/v2.2.1' },
            { text: 'v2.1.0', link: '/changelog/v2.1.0' },
            { text: 'v2.0.0', link: '/changelog/v2.0.0' },
            { text: 'v1.12.1', link: '/changelog/v1.12.1' },
            { text: 'v1.12.0', link: '/changelog/v1.12.0' },
            { text: 'v1.11.0', link: '/changelog/v1.11.0' },
            { text: 'v1.10.0', link: '/changelog/v1.10.0' }
          ]
        }
      ]
    },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/printing10101/Virtual-Realm-Manufacturing' }
    ],

    footer: {
      message: '基于 Apache 2.0 许可证发布',
      copyright: 'Copyright © 2024-2026 灵境制造团队'
    },

    search: {
      provider: 'local',
      options: {
        translations: {
          button: {
            buttonText: '搜索文档',
            buttonAriaLabel: '搜索文档'
          },
          modal: {
            noResultsText: '无法找到相关结果',
            resetButtonTitle: '清除查询条件',
            footer: {
              selectText: '选择',
              navigateText: '切换',
              closeText: '关闭'
            }
          }
        }
      }
    },

    editLink: {
      pattern: 'https://github.com/printing10101/Virtual-Realm-Manufacturing/edit/main/docs-site/docs/:path',
      text: '在 GitHub 上编辑此页'
    },

    lastUpdated: {
      text: '最后更新于',
      formatOptions: {
        dateStyle: 'full',
        timeStyle: 'medium'
      }
    }
  },

  markdown: {
    lineNumbers: true,
    anchor: {
      permalink: true
    },
    toc: {
      level: [2, 3, 4]
    }
  },

  vite: {
    server: {
      port: 5173,
      host: '0.0.0.0'
    },
    build: {
      outDir: '../dist',
      assetsDir: 'assets'
    }
  }
})
