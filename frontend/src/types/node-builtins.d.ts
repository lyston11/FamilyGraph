/**
 * 前端测试用的最小 node 内置模块声明。
 *
 * 前端运行时**不使用** node 内置模块，所以本项目没有安装 `@types/node`；但测试在
 * node 中执行，个别源级契约测试需要读取源码文件（例如断言全局 CSS 里没有某条规则）。
 * 这里只声明用到的最小签名，避免为一个断言引入整套类型依赖。
 */
declare module 'node:fs' {
  export function readFileSync(path: string, encoding: 'utf8'): string
}

declare const process: {
  cwd(): string
}
