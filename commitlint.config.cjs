module.exports = {
  // 注意：默认解析器已完整支持 conventional commits（type(scope): subject + body + footer/BREAKING）。
  // 不再引入 parserPreset 'conventional-changelog-conventionalcommits'——该包未随根依赖安装，
  // 且本配置所用规则（type/scope/subject/header-max）不依赖其增强解析。
  rules: {
    'type-enum': [
      2,
      'always',
      [
        'feat',
        'fix',
        'docs',
        'style',
        'refactor',
        'test',
        'chore',
        'perf',
        'ci',
        'build',
        'revert'
      ]
    ],
    // scope-enum：对齐仓库实际使用（历史提交覆盖 30+ scope）+ 约定常见 scope。
    'scope-enum': [
      2,
      'always',
      [
        'frontend',
        'backend',
        'rust',
        'python',
        'ai',
        'api',
        'ci',
        'deps',
        'docs',
        'git',
        'config',
        'scripts',
        'test',
        'tests',
        'db',
        'tasks',
        'services',
        'plugins',
        'budget',
        'postprocessor',
        'process_planning',
        'dxf',
        'cad',
        'gcode',
        'simulation',
        'research',
        'ui',
        'tauri',
        'engineering',
        'quality',
        'rag',
        'agents',
        'core',
        'cleanup',
        'deploy',
        'devops',
        'desktop',
        'mypy',
        'refactor',
        'readme',
        'tools',
        'workflow',
        'data',
        'geometry-validation'
      ]
    ],
    'subject-empty': [2, 'never'],
    'type-empty': [2, 'never'],
    'type-case': [2, 'always', 'lower-case'],
    'subject-case': [0],
    'header-max-length': [2, 'always', 100]
  },
  prompt: {
    messages: {
      type: '请选择变更类型:',
      scope: '请选择变更范围:',
      customScope: '请输入变更范围:',
      subject: '填写简短描述(最多100字):\n',
      body: '填写详细描述 (按回车跳过):\n',
      breaking: '列出任何 BREAKING CHANGES (按回车跳过):\n',
      footer: '添加关联的 issue 编号 (按回车跳过):\n'
    },
    types: [
      { value: 'feat', name: 'feat:     新功能' },
      { value: 'fix', name: 'fix:      修复Bug' },
      { value: 'docs', name: 'docs:     文档变更' },
      { value: 'style', name: 'style:    代码格式(不影响代码运行的变动)' },
      { value: 'refactor', name: 'refactor: 重构(既不是新增功能，也不是修改bug的代码变动)' },
      { value: 'test', name: 'test:     增加或修改测试' },
      { value: 'chore', name: 'chore:    其它修改，不在上述范围中的其它类型' },
      { value: 'perf', name: 'perf:     性能优化' },
      { value: 'ci', name: 'ci:       CI配置和脚本' },
      { value: 'build', name: 'build:    影响构建系统或外部依赖的修改' },
      { value: 'revert', name: 'revert:   回退某个提交' }
    ],
    useEmoji: false,
    confirmColorize: true,
    upperCaseSubject: false,
    breaklineNumber: 72,
    breaklineMessage: 'BREAKING CHANGE'
  }
}
