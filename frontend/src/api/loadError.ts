import { ApiError } from './errors'

/**
 * 列表/投影类页面统一加载失败分类（R3，09-11 整改）。
 *
 * 后端 household-card / stats / notifications 端点均已挂载，旧「合同未就绪」
 * 占位语义删除。现在按真实失败原因给可行动文案：
 * - 404：端点未部署或部署偏斜（该版本后端没有此路由）；
 * - 403：当前账号无权读取该空间的这份投影；
 * - 503：服务维护/暂不可用；
 * - status 0（网络失败）：网络异常或前端/后端部署偏斜；
 * - 其余：统一可重试错误态。
 *
 * 所有文案都不泄露后端内部细节（路径、错误堆栈、数据库信息）。
 */
export interface LoadErrorCopy {
  title: string
  text: string
}

export function describeLoadError(error: unknown, subject: string): LoadErrorCopy {
  if (error instanceof ApiError) {
    if (error.status === 404) {
      return {
        title: `${subject}服务未部署`,
        text: '当前后端没有提供该服务接口，可能是版本不一致的部署。请联系管理员确认前后端版本，再重新加载。',
      }
    }
    if (error.status === 403) {
      return {
        title: `没有权限查看${subject}`,
        text: '你当前的身份无权读取这部分数据。如需访问，请联系空间管理员调整权限。',
      }
    }
    if (error.status === 503) {
      return {
        title: `${subject}服务维护中`,
        text: '该服务暂时不可用，稍后会自动恢复。请稍后重试。',
      }
    }
    if (error.status === 0) {
      return {
        title: `网络异常，${subject}加载失败`,
        text: '无法连接到服务。请检查网络后重试；若持续失败，可能是部署配置问题，请联系管理员。',
      }
    }
  }
  return {
    title: `${subject}暂时无法加载`,
    text: '网络或服务暂时不可用，请稍后重试。',
  }
}
