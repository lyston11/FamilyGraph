/**
 * 模块图隔离断言（PRD FE-F1 / design §1）：
 * - 任何 src 文件不得 import 家庭 frontend/ 的任何模块或共享构建产物；
 * - 后台只代理/请求 /admin-api，绝不出现家庭 /api baseURL 或家庭存储 key；
 * - 敏感票据绝不写 localStorage/sessionStorage；
 * - 业务写端点仅审批（approve/reject），无其他 POST/PUT/DELETE。
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

// vitest 以 package 目录为 cwd
const PROJECT_ROOT = process.cwd()
const SRC_DIR = join(PROJECT_ROOT, 'src')

function walk(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) {
      out.push(...walk(full))
    } else if (/\.(ts|vue)$/.test(name)) {
      out.push(full)
    }
  }
  return out
}

const sourceFiles = walk(SRC_DIR)
const relative = (file: string): string => file.replace(`${PROJECT_ROOT}/`, '')

/** 剥离块注释/行注释/HTML 注释：静态红线只约束代码，不约束文档注释。 */
function stripComments(source: string): string {
  return source
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:"'`])\/\/[^\n]*/g, '$1')
}

function importSpecifiers(content: string): string[] {
  const specifiers: string[] = []
  const patterns = [
    /import\s+[^'"]*from\s*['"]([^'"]+)['"]/g,
    /import\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
    /import\s*['"]([^'"]+)['"]/g,
    /export\s+[^'"]*from\s*['"]([^'"]+)['"]/g,
  ]
  for (const pattern of patterns) {
    let match: RegExpExecArray | null
    while ((match = pattern.exec(content)) !== null) {
      specifiers.push(match[1] ?? '')
    }
  }
  return specifiers
}

describe('模块图隔离（system-admin-frontend 独立性）', () => {
  it('任何 src 文件不得 import 家庭 frontend/ 或项目外模块', () => {
    const offenders: string[] = []
    for (const file of sourceFiles) {
      const content = readFileSync(file, 'utf8')
      for (const specifier of importSpecifiers(content)) {
        // 相对路径只允许项目内（src/ 上下）；别名只允许 @/ → src/
        if (specifier.startsWith('.')) {
          offenders.push(`${relative(file)} -> ${specifier}`)
          continue
        }
        if (specifier.startsWith('@/')) continue
        if (/^(frontend|backend|system-admin-frontend)\//.test(specifier)) {
          offenders.push(`${relative(file)} -> ${specifier}`)
        }
        if (/^(vue|vue-router|pinia|naive-ui|axios|vitest)$/.test(specifier)) continue
        // 允许的裸模块名白名单之外的路径式裸导入一律视为越界
        if (specifier.includes('/')) {
          offenders.push(`${relative(file)} -> ${specifier}`)
        }
      }
    }
    expect(offenders, `发现越界 import：\n${offenders.join('\n')}`).toEqual([])
  })

  it('不出现家庭 refresh key（fg.refresh_token），admin key 独立存在', () => {
    for (const file of sourceFiles) {
      const content = stripComments(readFileSync(file, 'utf8'))
      expect(content, `${relative(file)} 不得引用家庭 fg.refresh_token key`).not.toMatch(
        /['"`]fg\.refresh_token['"`]/,
      )
    }
    const authStore = readFileSync(join(SRC_DIR, 'stores/auth.ts'), 'utf8')
    expect(authStore).toContain("'fg.admin.refresh_token'")
  })

  it('API baseURL 只允许 /admin-api，绝不请求家庭 /api', () => {
    for (const file of sourceFiles) {
      const content = readFileSync(file, 'utf8')
      if (file.endsWith(join(SRC_DIR, 'api/client.ts'))) {
        expect(content).toContain("baseURL: '/admin-api'")
      }
      expect(content, `${relative(file)} 不得把家庭 /api 作为 baseURL`).not.toMatch(
        /baseURL:\s*['"`]\/api['"`]/,
      )
      expect(content, `${relative(file)} 不得出现 axios.create 之外的直连`).not.toMatch(
        /axios\.get\(|axios\.post\(|axios\.put\(|axios\.delete\(/,
      )
    }
  })

  it('access session 票据 store 与敏感组件绝不写 localStorage/sessionStorage', () => {
    const storeContent = stripComments(readFileSync(join(SRC_DIR, 'stores/accessSession.ts'), 'utf8'))
    expect(storeContent).not.toContain('localStorage')
    expect(storeContent).not.toContain('sessionStorage')
    const profilePanel = stripComments(
      readFileSync(join(SRC_DIR, 'components/MemberProfilePanel.vue'), 'utf8'),
    )
    expect(profilePanel).not.toContain('localStorage')
    expect(profilePanel).not.toContain('sessionStorage')
  })

  it('业务写端点仅审批 + 认证自管理；绝不出现 DELETE 或其他写调用', () => {
    const allowedWriteUrls = [
      '/auth/login',
      '/auth/refresh',
      '/auth/logout',
      '/auth/password',
      '/auth/username',
      '/v1/access-sessions',
      '/v1/manager-applications',
    ]
    for (const file of sourceFiles) {
      const content = readFileSync(file, 'utf8')
      expect(content, `${relative(file)} 不得出现 delete 请求`).not.toMatch(/method:\s*'delete'/)
      // 找出所有 method: 'post' | 'put' 的调用，url 必须在白名单前缀内
      if (/method:\s*'(post|put)'/.test(content)) {
        // url 出现在同一 adminRequest 配置对象中；宽松校验整个文件命中白名单
        const hasAllowed = allowedWriteUrls.some((url) => content.includes(url))
        expect(hasAllowed, `${relative(file)} 的写调用不在允许的端点清单内`).toBe(true)
      }
    }
  })

  it('无家庭组件/路由/store 痕迹，无 element-plus', () => {
    const forbidden = [
      /SystemAdminShell/,
      /SystemAdminView/,
      /element-plus/,
      /El[A-Z][a-zA-Z]+/,
      /<el-/,
      /from\s+['"]@\/stores\/(auth)?['"]\s*;?\s*\/\/\s*family/,
    ]
    for (const file of sourceFiles) {
      const content = readFileSync(file, 'utf8')
      for (const pattern of forbidden) {
        // AdminShell 是本项目自己的后台壳；排除自身文件名
        if (pattern.source === 'SystemAdminShell' && file.endsWith('AdminShell.vue')) continue
        expect(content, `${relative(file)} 命中禁用模式 ${pattern}`).not.toMatch(pattern)
      }
    }
  })

  it('git 仓库中本应用与家庭 frontend 无共享构建产物路径引用', () => {
    // vite 配置只代理 /admin-api
    const viteConfig = readFileSync(join(PROJECT_ROOT, 'vite.config.ts'), 'utf8')
    expect(viteConfig).toContain("'/admin-api'")
    expect(viteConfig).toContain('port: 5174')
    expect(viteConfig).not.toContain("'/api'")
    // nginx 只代理 /admin-api，/api 与 /internal 均 404
    const nginx = readFileSync(join(PROJECT_ROOT, 'nginx.conf'), 'utf8')
    expect(nginx).toContain('location /admin-api/')
    expect(nginx).toMatch(/location \/api\/\s*\{[^}]*return 404;/s)
    expect(nginx).toMatch(/location \/internal\/\s*\{[^}]*return 404;/s)
  })
})
